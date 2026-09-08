"""
game_bridge.py

Reads the 20x20 pressure mat over serial, computes the Center of Pressure (COP),
and POSTs each reading to the Django server so the "Collect the Stars" browser
game can track foot movements in real time.

Usage:
    python game_bridge.py <patient_id> [serial_port]
"""
import sys
import time
from pathlib import Path
import numpy as np
import serial
import serial.tools.list_ports
import requests

SERVER_URL = "http://localhost:8000"
BAUD_RATE = 115200
POST_INTERVAL = 0.05  # 20 updates/sec for smooth synchronized real-time display


def pick_port():
    ports = serial.tools.list_ports.comports()
    if not ports:
        return None
    # Prefer USB serial ports if available
    for p in ports:
        dev = p.device.lower()
        desc = (p.description or "").lower()
        if "usb" in dev or "cp210" in desc or "ch340" in desc or "uart" in desc:
            return p.device
    return ports[0].device


def process_pressure_data(packet, pressure_data):
    """
    Unpack 60-byte binary packet into 20x20 boolean/pressure matrix (7-bit clean).
    Each row has 3 bytes:
      Byte 0: cols 0..6  (Bits 0..6, Values: 0..127)
      Byte 1: cols 7..13 (Bits 0..6, Values: 0..127)
      Byte 2: cols 14..19 (Bits 0..5, Values: 0..63)
    """
    pressure_data.fill(0.0)
    for row in range(20):
        row_offset = row * 3
        if row_offset + 2 >= len(packet):
            break
        b0 = packet[row_offset]
        b1 = packet[row_offset + 1]
        b2 = packet[row_offset + 2]

        for bit in range(7):
            if (b0 >> bit) & 0x01:
                pressure_data[row, bit] = 100.0
            if (b1 >> bit) & 0x01:
                pressure_data[row, 7 + bit] = 100.0
        for bit in range(6):
            if (b2 >> bit) & 0x01:
                pressure_data[row, 14 + bit] = 100.0


def calculate_cop(pressure_data):
    total_pressure = float(np.sum(pressure_data))
    if total_pressure <= 0:
        return None
    y_indices, x_indices = np.indices(pressure_data.shape)
    cop_x = float(np.sum(x_indices * pressure_data) / total_pressure)
    cop_y = float(np.sum(y_indices * pressure_data) / total_pressure)
    return cop_x, cop_y, total_pressure


def main():
    if len(sys.argv) < 2:
        print("Usage: python game_bridge.py <patient_id> [serial_port]")
        sys.exit(1)

    patient_id = sys.argv[1]
    raw_port = sys.argv[2] if len(sys.argv) > 2 else None
    port = raw_port if (raw_port and raw_port.lower() != 'auto') else pick_port()

    if not port:
        print("No serial port found. Connect the pressure mat and try again.")
        sys.exit(1)

    print(f"Connecting to {port} at {BAUD_RATE} baud for patient {patient_id}...")
    ser = None
    last_err = None
    for attempt in range(6):
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=0.02)
            break
        except Exception as e:
            last_err = e
            print(f"Port {port} busy or unavailable (attempt {attempt+1}/6): {e}")
            time.sleep(0.5)

    if ser is None:
        print(f"Failed to open port {port} after 6 attempts: {last_err}")
        sys.exit(1)

    # Initialize mat baseline calibration and optimal threshold
    try:
        time.sleep(0.5)
        ser.write(b'c\n')
        time.sleep(0.4)
        ser.write(b't45\n')
        time.sleep(0.1)
        ser.reset_input_buffer()
        print("Calibrated mat baseline and set threshold to 45.")
    except Exception as ie:
        print(f"Init warning: {ie}")

    pressure_data = np.zeros((20, 20), dtype=float)
    frame_url = f"{SERVER_URL}/api/game/{patient_id}/frame/"
    last_post = 0.0
    buffer = bytearray()
    cmd_file = Path(__file__).parent / f"cmd_{patient_id}.txt"
    err_count = 0

    print(f"Connected! Streaming live readings to {frame_url}. Press Ctrl+C to stop.")

    try:
        while True:
            # Check for dynamic hardware commands (e.g. 'c' for recalibrate, 't30' for threshold)
            if cmd_file.exists():
                try:
                    cmd_txt = cmd_file.read_text().strip()
                    if cmd_txt:
                        ser.write(cmd_txt.encode())
                        ser.flush()
                        print(f"Dispatched hardware command to ESP32: '{cmd_txt}'")
                    cmd_file.unlink(missing_ok=True)
                except Exception as ce:
                    print(f"Command error: {ce}")

            try:
                waiting = ser.in_waiting
                chunk = ser.read(waiting if waiting > 0 else 1)
                err_count = 0
            except Exception as e:
                err_count += 1
                if err_count % 20 == 1:
                    print(f"Serial read warning ({err_count}): {e}")
                time.sleep(0.05)
                if err_count > 40:
                    print(f"Port {port} disconnected. Attempting reconnect...")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                    for attempt in range(10):
                        try:
                            ser = serial.Serial(port, BAUD_RATE, timeout=0.02)
                            print(f"Reconnected to {port} successfully!")
                            err_count = 0
                            break
                        except Exception:
                            time.sleep(0.5)
                    if ser is None:
                        print("Reconnection failed.")
                        time.sleep(1.0)
                continue

            if chunk:
                buffer.extend(chunk)

            # Process all complete 62-byte frames in buffer (0xFF + 60 data bytes + 0xFE)
            while len(buffer) >= 62:
                if buffer[0] == 0xFF:
                    if buffer[61] == 0xFE:
                        # Valid frame found!
                        packet = buffer[1:61]
                        process_pressure_data(packet, pressure_data)
                        del buffer[:62]
                    else:
                        del buffer[0]
                else:
                    try:
                        next_header = buffer.index(0xFF)
                        del buffer[:next_header]
                    except ValueError:
                        buffer.clear()
                        break

            now = time.time()
            if now - last_post >= POST_INTERVAL:
                cop = calculate_cop(pressure_data)

                # Generate compact 20-integer bitmask representation of the matrix
                matrix_bitmask = [
                    int(sum((1 << c) for c in range(20) if pressure_data[r, c] > 0))
                    for r in range(20)
                ]
                active_count = int(np.count_nonzero(pressure_data))

                if cop:
                    cop_x, cop_y, total_pressure = cop
                    left_pressure = float(np.sum(pressure_data[:, :10]))
                    right_pressure = float(np.sum(pressure_data[:, 10:]))
                    total = left_pressure + right_pressure
                    left_pct = (left_pressure / total * 100) if total > 0 else 50.0
                    right_pct = (right_pressure / total * 100) if total > 0 else 50.0

                    payload = {
                        'x': float(cop_x),
                        'y': float(cop_y),
                        'left_pct': left_pct,
                        'right_pct': right_pct,
                        'total_pressure': float(total_pressure),
                        'peak_pressure': 100.0,
                        'active_count': active_count,
                        'matrix': matrix_bitmask,
                        'port': port,
                        'touching': True,
                    }
                else:
                    payload = {
                        'x': None,
                        'y': None,
                        'left_pct': 50.0,
                        'right_pct': 50.0,
                        'port': port,
                        'total_pressure': 0.0,
                        'peak_pressure': 0.0,
                        'active_count': 0,
                        'matrix': [0] * 20,
                        'touching': False,
                    }

                try:
                    requests.post(frame_url, json=payload, timeout=0.3)
                except requests.exceptions.RequestException:
                    pass

                last_post = now

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if ser and ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()
