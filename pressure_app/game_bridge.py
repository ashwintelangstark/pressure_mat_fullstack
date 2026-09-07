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
import numpy as np
import serial
import serial.tools.list_ports
import requests

SERVER_URL = "http://localhost:8000"
BAUD_RATE = 115200
POST_INTERVAL = 0.08  # ~12 updates/sec for smooth responsive gameplay


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
    Unpack 60-byte binary packet into 20x20 boolean/pressure matrix.
    Each row is represented by 3 bytes (24 bits, first 20 bits are columns 0..19).
    """
    pressure_data.fill(0.0)
    for row in range(20):
        row_offset = row * 3
        if row_offset + 2 >= len(packet):
            break
        b0 = packet[row_offset]
        b1 = packet[row_offset + 1]
        b2 = packet[row_offset + 2]

        for bit in range(8):
            # Columns 0..7
            if (b0 >> bit) & 0x01:
                pressure_data[row, bit] = 100.0
            # Columns 8..15
            if (b1 >> bit) & 0x01:
                pressure_data[row, 8 + bit] = 100.0
            # Columns 16..19
            if bit < 4 and ((b2 >> bit) & 0x01):
                pressure_data[row, 16 + bit] = 100.0


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

    pressure_data = np.zeros((20, 20), dtype=float)
    frame_url = f"{SERVER_URL}/api/game/{patient_id}/frame/"
    last_post = 0.0
    buffer = bytearray()

    print(f"Connected! Streaming live readings to {frame_url}. Press Ctrl+C to stop.")

    try:
        while True:
            try:
                waiting = ser.in_waiting
                chunk = ser.read(waiting if waiting > 0 else 1)
            except Exception as e:
                print(f"Serial read error: {e}")
                time.sleep(0.05)
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
                        # Corrupted or misaligned, search for next 0xFF
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
                        'port': port,
                        'touching': True,
                    }
                else:
                    # Heartbeat so web UI knows mat is connected and live
                    payload = {
                        'x': None,
                        'y': None,
                        'port': port,
                        'total_pressure': 0.0,
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
