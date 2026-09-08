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


# Persistent HTTP session for ultra-low latency frame streaming
http_session = requests.Session()


def process_pressure_data(packet, last_touch_time, now):
    """
    Unpack 60-byte binary packet into 20x20 boolean matrix (7-bit clean).
    Each row has 3 bytes:
      Byte 0: cols 0..6  (Bits 0..6, Values: 0..127)
      Byte 1: cols 7..13 (Bits 0..6, Values: 0..127)
      Byte 2: cols 14..19 (Bits 0..5, Values: 0..63)

    Records exact timestamp when each sensor cell was touched.
    """
    for row in range(20):
        row_offset = row * 3
        if row_offset + 2 >= len(packet):
            break
        b0 = packet[row_offset]
        b1 = packet[row_offset + 1]
        b2 = packet[row_offset + 2]

        for bit in range(7):
            if (b0 >> bit) & 0x01:
                last_touch_time[row, bit] = now
            if (b1 >> bit) & 0x01:
                last_touch_time[row, 7 + bit] = now
        for bit in range(6):
            if (b2 >> bit) & 0x01:
                last_touch_time[row, 14 + bit] = now


def update_pressure_data(now, last_touch_time, pressure_data):
    """
    Time-based physiological persistence model:
      - 0 to 400ms: Peak pressure (100.0) - solid, reliable foot contact
      - 400ms to 900ms: Smooth thermal fade-out (100.0 -> 0.0)
      - > 900ms: Fully released (0.0)
    This guarantees footprints and gait remain clearly visible and continuous,
    completely immune to serial buffer draining or frame stutter.
    """
    for r in range(20):
        for c in range(20):
            t_last = last_touch_time[r, c]
            if t_last <= 0.0:
                pressure_data[r, c] = 0.0
                continue
            age = now - t_last
            if age < 0.40:
                pressure_data[r, c] = 100.0
            elif age < 0.90:
                pressure_data[r, c] = 100.0 * (1.0 - (age - 0.40) / 0.50)
            else:
                pressure_data[r, c] = 0.0


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

    # Set high-sensitivity threshold (15 = responsive touch, above noise floor <= 10)
    try:
        time.sleep(0.2)
        ser.write(b't15\n')
        ser.flush()
        time.sleep(0.1)
        ser.reset_input_buffer()
        print("Set mat sensitivity threshold to 15 (responsive touch).")
    except Exception as ie:
        print(f"Init warning: {ie}")

    pressure_data = np.zeros((20, 20), dtype=float)
    last_touch_time = np.zeros((20, 20), dtype=float)
    frame_url = f"{SERVER_URL}/api/game/{patient_id}/frame/"
    last_post = 0.0
    buffer = bytearray()
    cmd_file = Path(__file__).parent / f"cmd_{patient_id}.txt"
    err_count = 0

    last_valid_cop = None
    last_cop_time = 0.0

    print(f"Connected! Streaming live readings to {frame_url}. Press Ctrl+C to stop.")

    try:
        while True:
            # Check for dynamic hardware commands (e.g. 'c' for recalibrate, 't15' for threshold)
            cmd_files = list(Path(__file__).parent.glob("cmd_*.txt"))
            for cf in cmd_files:
                try:
                    cmd_txt = cf.read_text().strip()
                    if cmd_txt:
                        ser.write((cmd_txt + "\n").encode())
                        ser.flush()
                        print(f"Dispatched hardware command from {cf.name} to ESP32: '{cmd_txt}'")
                    cf.unlink(missing_ok=True)
                except Exception as ce:
                    print(f"Command error on {cf.name}: {ce}")

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

            now = time.time()

            # Process all complete 62-byte frames in buffer (0xFF + 60 data bytes + 0xFE)
            while len(buffer) >= 62:
                if buffer[0] == 0xFF:
                    if buffer[61] == 0xFE:
                        # Valid frame found!
                        packet = buffer[1:61]
                        process_pressure_data(packet, last_touch_time, now)
                        del buffer[:62]
                    else:
                        try:
                            next_idx = buffer.index(0xFF, 1)
                            del buffer[:next_idx]
                        except ValueError:
                            buffer.clear()
                            break
                else:
                    try:
                        next_header = buffer.index(0xFF)
                        del buffer[:next_header]
                    except ValueError:
                        buffer.clear()
                        break

            if now - last_post >= POST_INTERVAL:
                # Update continuous time-based pressure decay
                update_pressure_data(now, last_touch_time, pressure_data)

                cop = calculate_cop(pressure_data)
                active_count = int(np.count_nonzero(pressure_data >= 15.0))
                total_pressure = float(np.sum(pressure_data))

                # Generate 20-integer bitmask representation for backward compatibility
                matrix_bitmask = [
                    int(sum((1 << c) for c in range(20) if pressure_data[r, c] >= 15.0))
                    for r in range(20)
                ]

                if not hasattr(main, 'last_log_t'):
                    main.last_log_t = 0.0
                    main.total_frames = 0
                    main.nonzero_frames = 0
                main.total_frames += 1
                if active_count > 0:
                    main.nonzero_frames += 1

                if now - main.last_log_t >= 2.0:
                    print(f"Bridge Telemetry: {main.total_frames} frames ({main.nonzero_frames} with touch), active_count={active_count}, buf_len={len(buffer)}")
                    main.last_log_t = now

                if cop and active_count > 0:
                    cop_x, cop_y, _ = cop
                    last_valid_cop = (cop_x, cop_y)
                    last_cop_time = now
                    is_touching = True
                elif last_valid_cop and (now - last_cop_time < 0.65):
                    # Natural stride continuity: hold last position briefly while shifting weight
                    cop_x, cop_y = last_valid_cop
                    is_touching = (active_count > 0)
                else:
                    cop_x, cop_y = None, None
                    is_touching = False

                left_pressure = float(np.sum(pressure_data[:, :10]))
                right_pressure = float(np.sum(pressure_data[:, 10:]))
                sum_sides = left_pressure + right_pressure
                left_pct = (left_pressure / sum_sides * 100.0) if sum_sides > 0 else 50.0
                right_pct = (right_pressure / sum_sides * 100.0) if sum_sides > 0 else 50.0

                payload = {
                    'x': float(cop_x) if cop_x is not None else None,
                    'y': float(cop_y) if cop_y is not None else None,
                    'left_pct': left_pct,
                    'right_pct': right_pct,
                    'total_pressure': total_pressure,
                    'peak_pressure': 100.0 if is_touching else 0.0,
                    'active_count': active_count,
                    'matrix': matrix_bitmask,
                    'pressure_map': [[int(round(v)) for v in row] for row in pressure_data],
                    'port': port,
                    'touching': is_touching,
                }

                try:
                    http_session.post(frame_url, json=payload, timeout=0.25)
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
