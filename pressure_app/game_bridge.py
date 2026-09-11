"""
game_bridge.py

Ultra-Low Latency, High-Accuracy Serial Bridge for 20x20 FSR Pressure Mat.
Multi-threaded architecture: Serial reader thread operates continuously without blocking,
streaming zero-latency pressure frames to the Django server.

Usage:
    python game_bridge.py <patient_id> [serial_port]
"""
import sys
import time
import threading
from pathlib import Path
import numpy as np
import serial
import serial.tools.list_ports
import requests

SERVER_URL = "http://localhost:8000"
BAUD_RATE = 115200
POST_INTERVAL = 0.035  # ~28 FPS stream rate matching physical scan updates
DEFAULT_THRESHOLD = 2  # Calibrated zero-noise floor: 0 noise at rest, instant touch response

http_session = requests.Session()


def pick_port():
    ports = serial.tools.list_ports.comports()
    if not ports:
        return None
    for p in ports:
        dev = p.device.lower()
        desc = (p.description or "").lower()
        if "debug" in dev or "bluetooth" in dev:
            continue
        if "usb" in dev or "esp32" in dev or "cp210" in desc or "ch340" in desc or "uart" in desc:
            return p.device
    for p in ports:
        dev = p.device.lower()
        if "debug" not in dev and "bluetooth" not in dev:
            return p.device
    return ports[0].device


def unpack_frame(packet):
    """
    Unpack 400-byte continuous analog pressure payload (20x20 matrix).
    Each byte is 0..127 representing cell pressure intensity.
    Supports both 400-byte analog and legacy 60-byte bitmask packets.
    """
    if len(packet) == 400:
        arr = np.frombuffer(packet, dtype=np.uint8).astype(int)
        return arr.reshape((20, 20))
    elif len(packet) == 60:
        mat = np.zeros((20, 20), dtype=int)
        for row in range(20):
            offset = row * 3
            if offset >= len(packet):
                break
            b0 = packet[offset]
            b1 = packet[offset + 1] if offset + 1 < len(packet) else 0
            b2 = packet[offset + 2] if offset + 2 < len(packet) else 0
            for bit in range(7):
                if (b0 >> bit) & 0x01:
                    mat[row, bit] = 50
                if (b1 >> bit) & 0x01:
                    mat[row, 7 + bit] = 50
            for bit in range(6):
                if (b2 >> bit) & 0x01:
                    mat[row, 14 + bit] = 50
        return mat
    return np.zeros((20, 20), dtype=int)


def extract_latest_frame(buffer):
    """
    Scan buffer for 0xFE preceded by 0xFF (400-byte analog payload or 60-byte payload).
    """
    for i in range(len(buffer) - 1, 400, -1):
        if buffer[i] == 0xFE and buffer[i - 401] == 0xFF:
            payload = buffer[i - 400 : i]
            del buffer[: i + 1]
            return payload
    for i in range(len(buffer) - 1, 60, -1):
        if buffer[i] == 0xFE and buffer[i - 61] == 0xFF:
            payload = buffer[i - 60 : i]
            del buffer[: i + 1]
            return payload
    if len(buffer) > 1000:
        del buffer[:-512]
    return None


_smooth_cop_x = None
_smooth_cop_y = None


def calculate_cop(pressure_data):
    global _smooth_cop_x, _smooth_cop_y
    total_pressure = float(np.sum(pressure_data))
    if total_pressure <= 0:
        _smooth_cop_x = None
        _smooth_cop_y = None
        return None

    y_indices, x_indices = np.indices(pressure_data.shape)
    raw_cop_x = float(np.sum(x_indices * pressure_data) / total_pressure)
    raw_cop_y = float(np.sum(y_indices * pressure_data) / total_pressure)

    alpha = 0.80  # Real-time tracking responsiveness
    if _smooth_cop_x is None or _smooth_cop_y is None:
        _smooth_cop_x = raw_cop_x
        _smooth_cop_y = raw_cop_y
    else:
        _smooth_cop_x = alpha * raw_cop_x + (1.0 - alpha) * _smooth_cop_x
        _smooth_cop_y = alpha * raw_cop_y + (1.0 - alpha) * _smooth_cop_y

    return _smooth_cop_x, _smooth_cop_y, total_pressure


class BridgeState:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest_raw = None
        self.port = None
        self.connected = False
        self.running = True


def run_mat_calibration(ser, threshold=DEFAULT_THRESHOLD):
    """Perform baseline zero calibration with optimal quiet settle time."""
    try:
        ser.reset_input_buffer()
        time.sleep(0.05)
        ser.write(b"c\n")
        ser.flush()
        print(f"Sent 'c' calibration signal to ESP32 (threshold={threshold})...")
        time.sleep(1.4)  # Wait for 16-pass matrix median baseline accumulation
        ser.write(f"t{threshold}\n".encode())
        ser.flush()
        time.sleep(0.15)
        ser.reset_input_buffer()
        print(f"Mat zero-calibrated successfully! Threshold set to {threshold} (Zero resting noise baseline).")
    except Exception as e:
        print(f"Calibration error: {e}")


def serial_reader_thread(ser_ref, state, threshold_val):
    """Dedicated background serial reader thread for zero-latency frame ingestion."""
    buffer = bytearray()
    err_count = 0
    current_frame_rows = []
    text_acc = ""

    while state.running:
        ser = ser_ref[0]
        if ser is None or not ser.is_open:
            time.sleep(0.1)
            continue

        line_bytes = b""
        try:
            line_bytes = ser.readline()
            err_count = 0
        except Exception as e:
            err_msg = str(e)
            if 'returned no data' not in err_msg and 'Resource temporarily unavailable' not in err_msg:
                err_count += 1
                if err_count % 30 == 1:
                    print(f"Serial read warning ({err_count}): {e}")
                time.sleep(0.05)
                if err_count > 30:
                    print("Serial connection lost. Attempting reconnect...")
                    with state.lock:
                        state.connected = False
                        state.latest_raw = None
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser_ref[0] = None
                    for _ in range(12):
                        if not state.running:
                            break
                        try:
                            new_ser = serial.Serial(state.port, BAUD_RATE, timeout=0.5)
                            try:
                                new_ser.dtr = False
                                new_ser.rts = False
                            except Exception:
                                pass
                            time.sleep(0.8)
                            ser_ref[0] = new_ser
                            run_mat_calibration(new_ser, threshold=threshold_val[0])
                            with state.lock:
                                state.connected = True
                            print(f"Reconnected to serial port {state.port}!")
                            break
                        except Exception:
                            time.sleep(0.5)
                    continue

        if line_bytes:
            print(f"LINE READ ({len(line_bytes)}): {repr(line_bytes)}")
            text_acc += line_bytes.decode('utf-8', errors='ignore')
            if '\n' in text_acc:
                lines = text_acc.split('\n')
                text_acc = lines[-1]
                for l in lines[:-1]:
                    line_str = l.strip()
                    if '---' in line_str or 'matrix' in line_str.lower():
                        current_frame_rows = []
                    elif line_str:
                        parts = line_str.split()
                        if len(parts) == 20 and all(p.lstrip('-').isdigit() for p in parts):
                            row = [int(p) for p in parts]
                            current_frame_rows.append(row)
                            if len(current_frame_rows) == 20:
                                raw_mat = np.array(current_frame_rows, dtype=int)
                                with state.lock:
                                    state.latest_raw = raw_mat
                                    state.connected = True
                                current_frame_rows = []

            buffer.extend(line_bytes)
            payload = extract_latest_frame(buffer)
            if payload is not None:
                raw_mat = unpack_frame(payload)
                with state.lock:
                    state.latest_raw = raw_mat
                    state.connected = True


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
    for attempt in range(6):
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=0.5)
            try:
                ser.dtr = False
                ser.rts = False
            except Exception:
                pass
            time.sleep(0.8)
            break
        except Exception as e:
            print(f"Port {port} busy or unavailable (attempt {attempt+1}/6): {e}")
            time.sleep(0.5)

    if ser is None:
        print(f"Failed to open port {port} after 6 attempts.")
        sys.exit(1)

    threshold_val = [DEFAULT_THRESHOLD]
    run_mat_calibration(ser, threshold=threshold_val[0])

    state = BridgeState()
    state.port = port
    state.connected = True

    ser_ref = [ser]

    # Start non-blocking serial reader thread
    t = threading.Thread(target=serial_reader_thread, args=(ser_ref, state, threshold_val), daemon=True)
    t.start()

    frame_url = f"{SERVER_URL}/api/game/{patient_id}/frame/"
    print(f"Bridge Active! Streaming live pressure frames to {frame_url}...")

    last_post = 0.0
    last_log = time.time()
    total_frames = 0
    touch_frames = 0

    try:
        while True:
            # Handle UI dynamic hardware command signals
            cmd_files = list(Path(__file__).parent.glob("cmd_*.txt"))
            for cf in cmd_files:
                try:
                    cmd_txt = cf.read_text().strip()
                    if cmd_txt and ser_ref[0] and ser_ref[0].is_open:
                        ser_ref[0].write((cmd_txt + "\n").encode())
                        ser_ref[0].flush()
                        print(f"Dispatched command to ESP32: '{cmd_txt}'")
                        if cmd_txt.startswith('c'):
                            run_mat_calibration(ser_ref[0], threshold=threshold_val[0])
                        elif cmd_txt.startswith('t'):
                            try:
                                new_t = int(cmd_txt[1:])
                                threshold_val[0] = new_t
                                print(f"Threshold updated dynamically to {new_t}")
                            except Exception:
                                pass
                    cf.unlink(missing_ok=True)
                except Exception as ce:
                    print(f"Command error: {ce}")

            now = time.time()
            if now - last_post >= POST_INTERVAL:
                with state.lock:
                    raw_mat = state.latest_raw.copy() if state.latest_raw is not None else None
                    is_conn = state.connected

                if raw_mat is not None and is_conn:
                    active_count = int(np.count_nonzero(raw_mat > 0))

                    if active_count > 0:
                        active_mask = raw_mat > 0
                        pressure_map = np.where(active_mask, (raw_mat.astype(float) * 1.55).astype(int), 0).tolist()
                        total_pressure = float(np.sum(pressure_map))
                        peak_pressure = float(np.max(pressure_map))
                        touching = True

                        cop = calculate_cop(raw_mat)
                        cop_x = float(cop[0]) if cop else None
                        cop_y = float(cop[1]) if cop else None
                    else:
                        pressure_map = [[0] * 20 for _ in range(20)]
                        total_pressure = 0.0
                        peak_pressure = 0.0
                        touching = False
                        cop_x, cop_y = None, None

                    # Calculate left/right distribution
                    if active_count > 0:
                        left_cnt = int(np.count_nonzero(raw_mat[:, :10] > 0))
                        right_cnt = int(np.count_nonzero(raw_mat[:, 10:] > 0))
                        s = left_cnt + right_cnt
                        left_pct = (left_cnt / s * 100.0) if s > 0 else 50.0
                        right_pct = (right_cnt / s * 100.0) if s > 0 else 50.0
                    else:
                        left_pct = 50.0
                        right_pct = 50.0

                    matrix_bitmask = [
                        int(sum((1 << c) for c in range(20) if raw_mat[r, c] > 0))
                        for r in range(20)
                    ]

                    total_frames += 1
                    if touching:
                        touch_frames += 1

                    payload = {
                        'x': cop_x,
                        'y': cop_y,
                        'left_pct': left_pct,
                        'right_pct': right_pct,
                        'total_pressure': total_pressure,
                        'peak_pressure': peak_pressure,
                        'active_count': active_count,
                        'matrix': matrix_bitmask,
                        'pressure_map': pressure_map,
                        'port': port,
                        'touching': touching,
                        'connected': is_conn,
                    }

                    try:
                        http_session.post(frame_url, json=payload, timeout=0.2)
                    except Exception:
                        pass

                elif not is_conn:
                    # Report disconnected status to frontend
                    payload = {
                        'connected': False,
                        'active_count': 0,
                        'total_pressure': 0.0,
                        'touching': False,
                        'pressure_map': [[0] * 20 for _ in range(20)],
                        'port': port,
                    }
                    try:
                        http_session.post(frame_url, json=payload, timeout=0.2)
                    except Exception:
                        pass

                last_post = now

            if now - last_log >= 3.0:
                print(f"Bridge Telemetry: {total_frames} frames ({touch_frames} with touch), active_count={active_count if 'active_count' in locals() else 0}, conn={is_conn}")
                last_log = now

            time.sleep(0.008)

    except KeyboardInterrupt:
        print("Stopping bridge...")
    finally:
        state.running = False
        if ser_ref[0] and ser_ref[0].is_open:
            ser_ref[0].close()
        print("Bridge stopped.")


if __name__ == "__main__":
    main()
