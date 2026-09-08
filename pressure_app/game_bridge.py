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


def unpack_frame(packet):
    mat = np.zeros((20, 20), dtype=int)
    for row in range(20):
        offset = row * 3
        if offset + 2 >= len(packet):
            break
        b0 = packet[offset]
        b1 = packet[offset + 1]
        b2 = packet[offset + 2]
        for bit in range(7):
            if (b0 >> bit) & 0x01:
                mat[row, bit] = 1
            if (b1 >> bit) & 0x01:
                mat[row, 7 + bit] = 1
        for bit in range(6):
            if (b2 >> bit) & 0x01:
                mat[row, 14 + bit] = 1
    return mat


def filter_noise_and_compute_pressure(raw_mat):
    """
    Direct 1:1 touch mapping: Any cell pressed on the mat immediately displays at full pressure.
    """
    total_active = int(np.count_nonzero(raw_mat))
    if total_active == 0:
        return np.zeros((20, 20), dtype=float), 0

    clean_mat = raw_mat.astype(float) * 100.0
    return clean_mat, total_active


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

    # Automatically Zero Calibrate baseline on startup and set recommended sensitivity
    try:
        time.sleep(0.3)
        ser.write(b'c\n')  # Zero baseline across all 400 cells
        ser.flush()
        time.sleep(0.8)
        ser.write(b't15\n')  # Default Recommended threshold (Responsive touch)
        ser.flush()
        time.sleep(0.1)
        ser.reset_input_buffer()
        print("Initialized mat baseline (Zeroed) & set recommended sensitivity (15).")
    except Exception as ie:
        print(f"Init warning: {ie}")

    pressure_data = np.zeros((20, 20), dtype=float)
    frame_url = f"{SERVER_URL}/api/game/{patient_id}/frame/"
    last_post = 0.0
    buffer = bytearray()
    err_count = 0

    print(f"Connected! Streaming live readings to {frame_url}. Press Ctrl+C to stop.")

    try:
        while True:
            # Check for dynamic hardware commands from UI
            cmd_files = list(Path(__file__).parent.glob("cmd_*.txt"))
            for cf in cmd_files:
                try:
                    cmd_txt = cf.read_text().strip()
                    if cmd_txt:
                        ser.write((cmd_txt + "\n").encode())
                        ser.flush()
                        print(f"Dispatched hardware command from {cf.name} to ESP32: '{cmd_txt}'")
                        if cmd_txt.startswith('c'):
                            pressure_data.fill(0.0)
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
            latest_raw_mat = None

            # Process all complete 62-byte frames in buffer (0xFF + 60 data bytes + 0xFE)
            while len(buffer) >= 62:
                if buffer[0] == 0xFF:
                    if buffer[61] == 0xFE:
                        # Valid frame found
                        packet = buffer[1:61]
                        latest_raw_mat = unpack_frame(packet)
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

            if latest_raw_mat is not None:
                pressure_data, active_count = filter_noise_and_compute_pressure(latest_raw_mat)

            if now - last_post >= POST_INTERVAL:
                active_count = int(np.count_nonzero(pressure_data > 0))
                cop = calculate_cop(pressure_data) if active_count > 0 else None
                total_pressure = float(np.sum(pressure_data))

                # Generate 20-integer bitmask representation
                matrix_bitmask = [
                    int(sum((1 << c) for c in range(20) if pressure_data[r, c] > 0))
                    for r in range(20)
                ]

                if not hasattr(main, 'last_log_t'):
                    main.last_log_t = 0.0
                    main.total_frames = 0
                    main.nonzero_frames = 0
                main.total_frames += 1
                if active_count > 0:
                    main.nonzero_frames += 1

                if now - main.last_log_t >= 3.0:
                    print(f"Bridge Telemetry: {main.total_frames} frames ({main.nonzero_frames} with touch), active_count={active_count}, buf_len={len(buffer)}")
                    main.last_log_t = now

                if cop and active_count > 0:
                    cop_x, cop_y, _ = cop
                    is_touching = True
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
