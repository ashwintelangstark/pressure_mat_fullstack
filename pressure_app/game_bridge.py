"""
game_bridge.py

Headless companion to tkinter_app.py. Instead of drawing a matplotlib
window, it just reads the pressure mat over serial, computes the center of
pressure the same way tkinter_app.py does, and POSTs each reading to the
Django server so a browser-based game can poll it in near real time.

Usage:
    python game_bridge.py <patient_id> [serial_port]

If serial_port is omitted, the first available port is used (same
auto-detect behaviour as the desktop app's port list).
"""
import sys
import time
import numpy as np
import serial
import serial.tools.list_ports
import requests

SERVER_URL = "http://localhost:8000"
BAUD_RATE = 115200
POST_INTERVAL = 0.1  # seconds - ~10 updates/sec is plenty for a stepping game


def pick_port():
    ports = serial.tools.list_ports.comports()
    if not ports:
        return None
    return ports[0].device


def process_pressure_data(packet, pressure_data):
    """Same bit-unpacking logic used in tkinter_app.py."""
    for row in range(20):
        for byte_idx in range(3):
            if (row * 3 + byte_idx) >= len(packet):
                return
            byte = packet[row * 3 + byte_idx]
            for bit in range(8):
                col = byte_idx * 8 + bit
                if col < 20:
                    if (byte >> bit) & 0x01:
                        dist = np.sqrt((row - 9.5) ** 2 + (col - 9.5) ** 2)
                        pressure_data[row, col] = max(0, 200 - dist * 15)
                    else:
                        pressure_data[row, col] = 0


def calculate_cop(pressure_data):
    total_pressure = np.sum(pressure_data)
    if total_pressure <= 0:
        return None
    y_indices, x_indices = np.indices(pressure_data.shape)
    cop_x = np.sum(x_indices * pressure_data) / total_pressure
    cop_y = np.sum(y_indices * pressure_data) / total_pressure
    return cop_x, cop_y, total_pressure


def main():
    if len(sys.argv) < 2:
        print("Usage: python game_bridge.py <patient_id> [serial_port]")
        sys.exit(1)

    patient_id = sys.argv[1]
    raw_port = sys.argv[2] if len(sys.argv) > 2 else None
    port = raw_port if (raw_port and raw_port.lower() != 'auto') else pick_port()

    if not port:
        print("No serial port found. Connect the mat and try again.")
        sys.exit(1)

    print(f"Connecting to {port} at {BAUD_RATE} baud for patient {patient_id}...")
    ser = serial.Serial(port, BAUD_RATE, timeout=0.001)

    pressure_data = np.zeros((20, 20))
    frame_url = f"{SERVER_URL}/api/game/{patient_id}/frame/"
    last_post = 0.0

    print(f"Streaming live readings to {frame_url}. Press Ctrl+C to stop.")

    try:
        while True:
            data = ser.read(ser.in_waiting or 1)

            if data and b'\xFF' in data and b'\xFE' in data:
                start_idx = data.index(b'\xFF')
                end_idx = data.index(b'\xFE')

                if end_idx > start_idx:
                    packet = data[start_idx + 1:end_idx]
                    if len(packet) >= 50:
                        process_pressure_data(packet, pressure_data)

                        now = time.time()
                        if now - last_post >= POST_INTERVAL:
                            cop = calculate_cop(pressure_data)

                            if cop:
                                cop_x, cop_y, total_pressure = cop
                                left_pressure = float(np.sum(pressure_data[:, :10]))
                                right_pressure = float(np.sum(pressure_data[:, 10:]))
                                total = left_pressure + right_pressure
                                left_pct = (left_pressure / total * 100) if total > 0 else 50
                                right_pct = (right_pressure / total * 100) if total > 0 else 50

                                payload = {
                                    'x': float(cop_x),
                                    'y': float(cop_y),
                                    'left_pct': left_pct,
                                    'right_pct': right_pct,
                                    'total_pressure': float(total_pressure),
                                    'port': port,
                                }
                                try:
                                    requests.post(frame_url, json=payload, timeout=0.5)
                                except requests.exceptions.RequestException as e:
                                    print(f"Could not reach server: {e}")

                            last_post = now
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if ser.is_open:
            ser.close()


if __name__ == "__main__":
    main()
