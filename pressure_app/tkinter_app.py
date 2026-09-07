import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.colors import LinearSegmentedColormap
import serial
import serial.tools.list_ports
from collections import deque
from scipy.ndimage import center_of_mass
import sys
import requests
from datetime import datetime
import cv2
import os
from PIL import Image
import io
import traceback 

class PressureVisualizationApp:
    def __init__(self, root, patient_id):
        self.root = root
        self.current_patient_id = patient_id

        self.root.title(f"Pressure Visualization System - Patient {patient_id}")
        
        # Initialize colormap
        self.cmap = LinearSegmentedColormap.from_list(
            'pressure_gradient',
            [
                (1, 1, 1),      # white
                (0, 0, 1),      # blue
                (0, 1, 1),      # cyan
                (0, 1, 0),      # green
                (1, 1, 0),      # yellow
                (1, 0, 0)       # red
            ],
            N=256
        )
        
        # Constants
        self.CELL_SIZE_MM = 25.4
        self.HISTORY_LENGTH = 5
        self.PRESSURE_MIN = 0
        self.PRESSURE_MAX = 200
        
        # Serial connection variables
        self.serial_port = None
        self.ser = None
        self.baud_rate = 115200
        
        # Data variables
        self.pressure_data = np.zeros((20, 20))
        self.previous_pressure = np.zeros((20, 20))
        self.display_data = np.zeros((20, 20))
        self.movement_history = deque(maxlen=self.HISTORY_LENGTH)

        # COP history
        self.cop_history = deque(maxlen=30)

        self.total_displacement = [0, 0]
        self.frame_count = 0
        
        # Recording variables
        self.recording = False
        self.recorded_data = []
        # self.current_patient_id = ""
        self.session_notes = "Session Recording"
        self.last_save_time = None

        # Video recording variables
        self.video_writer = None
        self.video_filepath = ""
        self.recording_fps = 2  # Frames per second for video
        self.video_frame_size = (800, 600)  # Match your figure size
        
        # Setup GUI
        self.setup_gui()
        
        # Windows-specific window focus handling
        if sys.platform == "win32":
            self.root.attributes('-topmost', True)
            self.root.after(100, lambda: self.root.attributes('-topmost', False))
        
        # Start serial connection
        self.connect_serial()
        
    def setup_gui(self):
        """Setup the main application GUI"""
        # Main frame
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Control panel
        self.control_frame = ttk.LabelFrame(self.main_frame, text="Controls", padding="10")
        self.control_frame.grid(row=0, column=0, sticky=tk.N)
        
        # Serial port selection
        ttk.Label(self.control_frame, text="Serial Port:").grid(row=0, column=0, sticky=tk.W)
        self.port_combobox = ttk.Combobox(self.control_frame)
        self.port_combobox.grid(row=0, column=1, sticky=(tk.W, tk.E))
        self.refresh_ports()
        
        # Connect button
        self.connect_btn = ttk.Button(self.control_frame, text="Connect", command=self.connect_serial)
        self.connect_btn.grid(row=0, column=2, padx=5)
        
        # Disconnect button
        self.disconnect_btn = ttk.Button(self.control_frame, text="Disconnect", command=self.disconnect_serial, state=tk.DISABLED)
        self.disconnect_btn.grid(row=0, column=3, padx=5)

        # Status label
        self.status_label = ttk.Label(self.control_frame, text="Status: Disconnected")
        self.status_label.grid(row=1, column=0, columnspan=4, sticky=tk.W)
        
        # Recording controls
        self.record_btn = ttk.Button(
            self.control_frame, 
            text="Start Recording", 
            command=self.toggle_recording,
            state=tk.DISABLED
        )
        self.record_btn.grid(row=4, column=0, columnspan=2, pady=5)
        
        self.save_btn = ttk.Button(
            self.control_frame, 
            text="Save Session", 
            command=self.save_session,
            state=tk.DISABLED
        )
        self.save_btn.grid(row=4, column=2, columnspan=2, pady=5)

        # Video recording indicator
        self.video_status = ttk.Label(self.control_frame, text="Video: OFF", foreground="red")
        self.video_status.grid(row=5, column=0, columnspan=4, pady=5)
        
        # Visualization frame
        self.viz_frame = ttk.Frame(self.main_frame)
        self.viz_frame.grid(row=0, column=1, rowspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.main_frame.rowconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=1)
        self.viz_frame.rowconfigure(0, weight=1)
        self.viz_frame.columnconfigure(0, weight=1)
        
        # Matplotlib figure
        self.fig, self.ax = plt.subplots(figsize=(8, 6), facecolor='white')
        self.ax.set_facecolor('white')
        self.ax.set_title('Pressure Visualization', pad=20, color='black', fontsize=14)
        
        # Initialize image
        self.im = self.ax.imshow(
            self.display_data,
            cmap=self.cmap,
            vmin=self.PRESSURE_MIN,
            vmax=self.PRESSURE_MAX,
            interpolation='gaussian',
            alpha=0.9
        )

        self.ax.axvline(
            x=9.5,
            color='blue',
            linestyle='--',
            linewidth=2,
            alpha=0.7
        )
        
        # Grid lines
        self.ax.grid(
            which='minor',
            color='lightgray',
            linestyle='-',
            linewidth=0.3,
            alpha=0.3
        )
        
        # Colorbar
        self.cbar = self.fig.colorbar(self.im, ax=self.ax, pad=0.02)
        self.cbar.set_label('Pressure (kPa)', color='black')
        self.cbar.ax.yaxis.set_tick_params(color='black')
        plt.setp(self.cbar.ax.get_yticklabels(), color='black')

        # COP marker
        self.cop_marker, = self.ax.plot(
            10, 10,
            marker='o',
            markersize=10,
            color='red'
        )

        self.cop_trail, = self.ax.plot(
        [],
        [],
        color='dodgerblue',
        linewidth=4,
        linestyle='-'
    )

        self.cop_trail, = self.ax.plot(
            [],
            [],
            color='dodgerblue',
            linewidth=2,
            alpha=0.7
        )

        # COP text
        self.cop_text = self.ax.text(
            0.02,
            0.88,
            'COP: X=0, Y=0',
            transform=self.ax.transAxes,
            color='black',
            fontsize=11,
            bbox=dict(facecolor='white', edgecolor='black', alpha=0.7)
        )
        
        # Information text
        self.info_text = self.ax.text(
            0.02,
            0.95,
            'Displacement: X=0.0mm, Y=0.0mm\nMovement: None',
            transform=self.ax.transAxes,
            color='black',
            fontsize=11,
            bbox=dict(
                facecolor='#EAF4FF',
                edgecolor='blue',
                alpha=0.95,
                boxstyle='round,pad=0.4'
            )
        )
        
        # Recording status text
        self.recording_text = self.ax.text(0.02, 0.02, 
                                          'Recording: OFF',
                                          transform=self.ax.transAxes, color='black',
                                          fontsize=11, bbox=dict(facecolor='white', edgecolor='black', alpha=0.95))

        self.balance_text = self.ax.text(
            0.02,
            0.80,
            'Left: 50% | Right: 50%',
            transform=self.ax.transAxes,
            color='yellow',
            fontsize=11,
            bbox=dict(facecolor='black', alpha=0.7)
        )
        
        # Embed matplotlib figure in Tkinter
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.viz_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        
        # Start update loop
        self.update_visualization()

    
    def start_video_recording(self):
        """Initialize video recording"""
        if not hasattr(self, 'current_patient_id') or not self.current_patient_id:
            raise ValueError("No patient ID available for recording")
        os.makedirs('media/patient_videos', exist_ok=True)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.video_filepath = f"media/patient_videos/patient_{self.current_patient_id}_{timestamp}.mp4"
        
        # Initialize video writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.video_writer = cv2.VideoWriter(
            self.video_filepath,
            fourcc,
            self.recording_fps,
            self.video_frame_size
        )

        if not self.video_writer.isOpened():
            raise RuntimeError("Could not open video writer")
        self.video_status.config(text="Video: RECORDING", foreground="green")
    
    def stop_video_recording(self):
        """Finalize video recording"""
        if self.video_writer:
            self.video_writer.release()
            self.video_writer = None
            self.video_status.config(text="Video: READY TO SAVE", foreground="orange")
    
    def save_video_to_server(self):
        """Send the recorded video to the Django server"""
        if not os.path.exists(self.video_filepath):
            return False
            
        try:
            with open(self.video_filepath, 'rb') as video_file:
                files = {'video': (os.path.basename(self.video_filepath), video_file)}
                data = {
                    'patient_id': self.current_patient_id,
                    'timestamp': datetime.now().isoformat(),
                    'notes': self.session_notes
                }
                response = requests.post(
                    'http://localhost:8000/api/save_video/',
                    files=files,
                    data=data
                )
                
            if response.status_code == 200:
                print("Video successfully uploaded to server")
                os.remove(self.video_filepath)  # Clean up local file
                return True
            else:
                print(f"Server returned status code: {response.status_code}")
                print(f"Response: {response.text}")
                return False
        except Exception as e:
            print(f"Error saving video: {e}")
            return False
    
    def capture_frame(self):
        """Capture current frame for video recording"""
        if not self.video_writer:
            return
            
        try:
            # Draw the canvas to update the figure
            self.canvas.draw()
            
            # Get the figure as an image using matplotlib's renderer
            fig = self.fig
            fig.canvas.draw()
            
            # Get the RGBA buffer from the figure
            buf = fig.canvas.buffer_rgba()
            img = np.asarray(buf)
            
            # Convert RGBA to BGR for OpenCV
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            
            # Write to video
            self.video_writer.write(img_bgr)
        except Exception as e:
            print(f"Error capturing frame: {e}")

        
    def refresh_ports(self):
        """Refresh available serial ports"""
        ports = serial.tools.list_ports.comports()
        port_names = [port.device for port in ports]
        self.port_combobox['values'] = port_names
        if port_names:
            self.port_combobox.set(port_names[0])
            
    def connect_serial(self):
        """Connect to selected serial port"""
        port = self.port_combobox.get()
        if not port:
            return
            
        try:
            self.ser = serial.Serial(port, self.baud_rate, timeout=0.001)
            self.serial_port = port
            self.status_label.config(text=f"Status: Connected to {port}")
            self.connect_btn.config(state=tk.DISABLED)
            self.disconnect_btn.config(state=tk.NORMAL)
            self.record_btn.config(state=tk.NORMAL)
            print(f"Connected to {port}")
        except serial.SerialException as e:
            self.status_label.config(text=f"Error: {str(e)}")
            
    def disconnect_serial(self):
        """Disconnect from serial port"""
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.serial_port = None
        self.status_label.config(text="Status: Disconnected")
        self.connect_btn.config(state=tk.NORMAL)
        self.disconnect_btn.config(state=tk.DISABLED)
        self.record_btn.config(state=tk.DISABLED)

    def toggle_recording(self):
        """Toggle recording state with proper state management"""
        if not self.recording:
            # Start recording
            try:
                self.recording = True
                self.recorded_data = []  # Clear previous data
                self.frame_count = 0  # Reset frame counter
                self.total_displacement = [0, 0]  # Reset displacement
                self.movement_history = deque(maxlen=self.HISTORY_LENGTH)  # Reset movement history

                # Update UI
                self.record_btn.config(text="Stop Recording")
                self.save_btn.config(state=tk.DISABLED)
                self.recording_text.set_text(f'Recording: ON\nPatient: {self.current_patient_id}')
                self.status_label.config(text=f"Recording session for patient {self.current_patient_id}")
                # self.video_status.config(text="Video: OFF", foreground="red")
                
                # Start video recording
                try:
                    self.start_video_recording()
                except Exception as e:
                    print(f"Video recording failed to start: {e}")
                    self.video_status.config(text="Video: FAILED", foreground="red")
                
                print(f"Recording started for patient {self.current_patient_id}")
                
            except Exception as e:
                self.recording = False
                messagebox.showerror("Error", f"Failed to start recording: {str(e)}")
                print(f"Recording start error: {traceback.format_exc()}")
        else:
            # Stop recording
            try:
                self.recording = False
                
                # Update UI
                self.record_btn.config(text="Start Recording")
                # self.save_btn.config(state=tk.NORMAL)  # Enable save button
                self.recording_text.set_text('Recording: OFF')
                # self.status_label.config(text="Recording complete - ready to save")
                
                # Stop video recording if it was running
                if hasattr(self, 'video_writer') and self.video_writer:
                    self.stop_video_recording()
                
                # Verify we have data to save
                if not self.recorded_data:
                    messagebox.showwarning("Warning", "No data recorded during this session")
                    self.save_btn.config(state=tk.DISABLED)
                    self.status_label.config(text="Recording stopped - no data")
                else:
                    self.save_btn.config(state=tk.NORMAL)
                    self.status_label.config(text=f"Recording complete - {len(self.recorded_data)} frames captured")
                
                print(f"Recording stopped. {len(self.recorded_data)} frames captured")
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to stop recording: {str(e)}")
                print(f"Recording stop error: {traceback.format_exc()}")
        
    def save_session(self):
        """Handle session saving with comprehensive validation"""
        # Validate recording data
        if not self.recorded_data:
            messagebox.showerror("Error", "No data recorded to save")
            return
            
        if not hasattr(self, 'current_patient_id') or not self.current_patient_id:
            messagebox.showerror("Error", "No patient ID associated with this session")
            return
            
        # Prevent rapid consecutive saves
        current_time = datetime.now()
        if self.last_save_time and (current_time - self.last_save_time).seconds < 5:
            messagebox.showwarning("Warning", "Please wait a few seconds before saving again")
            return
            
        try:
            # Prepare session data
            session_data = {
                'patient_id': self.current_patient_id,
                'session_notes': self.session_notes,
                'readings': self.recorded_data,
                'timestamp': current_time.isoformat()
            }
            
            # Update UI for saving state
            self.status_label.config(text="Saving session data...")
            self.save_btn.config(state=tk.DISABLED)
            self.root.update()  # Force UI refresh
            
            # Save data to server
            response = requests.post(
                'http://localhost:8000/api/save_session/',
                json=session_data,
                headers={'Content-Type': 'application/json'},
                timeout=10  # 10 second timeout
            )
            
            # Handle response
            if response.status_code == 200:
                self.last_save_time = current_time
                messagebox.showinfo("Success", "Session saved successfully")
                
                # Reset recording state but keep UI ready
                self.recorded_data = []
                self.status_label.config(text="Session saved successfully")
                
                # Handle video saving if recording was done
                if hasattr(self, 'video_filepath') and os.path.exists(self.video_filepath):
                    try:
                        video_success = self.save_video_to_server()
                        if not video_success:
                            messagebox.showwarning("Warning", "Session data saved but video upload failed")
                    except Exception as e:
                        messagebox.showwarning("Warning", f"Session data saved but video upload failed: {str(e)}")
                
            else:
                error_msg = f"Server error ({response.status_code})"
                try:
                    error_details = response.json()
                    if 'error' in error_details:
                        error_msg += f": {error_details['error']}"
                except:
                    error_msg += f": {response.text}"
                    
                messagebox.showerror("Error", error_msg)
                
        except requests.exceptions.RequestException as e:
            messagebox.showerror("Connection Error", f"Failed to connect to server: {str(e)}")
        except Exception as e:
            messagebox.showerror("Error", f"Unexpected error: {str(e)}")
            print(traceback.format_exc())
        finally:
            # Always re-enable save button (user can try again)
            self.save_btn.config(state=tk.NORMAL)
            self.root.update()

    def calculate_cop(self):
        """Calculate Center of Pressure"""
        total_pressure = np.sum(self.pressure_data)

        if total_pressure <= 0:
            return None

        y_indices, x_indices = np.indices(self.pressure_data.shape)

        cop_x = np.sum(x_indices * self.pressure_data) / total_pressure
        cop_y = np.sum(y_indices * self.pressure_data) / total_pressure

        return cop_x, cop_y    
    
    def calculate_displacement(self, current, previous):
        """Calculate displacement in millimeters"""
        if np.sum(previous) < 100 or np.sum(current) < 100:
            return 0, 0
            
        current_norm = current / np.sum(current)
        previous_norm = previous / np.sum(previous)
        
        curr_com = center_of_mass(current_norm)
        prev_com = center_of_mass(previous_norm)
        
        dx = (curr_com[1] - prev_com[1]) * self.CELL_SIZE_MM
        dy = (curr_com[0] - prev_com[0]) * self.CELL_SIZE_MM
        
        return dx, dy
        
    def detect_movement(self, dx, dy):
        """Determine movement direction with magnitude"""
        magnitude = np.sqrt(dx**2 + dy**2)
        if magnitude < 2.0:
            return "No significant movement"
            
        angle = np.arctan2(dy, dx) * 180 / np.pi
        
        if -45 <= angle < 45:
            direction = "RIGHT"
        elif 45 <= angle < 135:
            direction = "BACKWARD"
        elif -135 <= angle < -45:
            direction = "FORWARD"
        else:
            direction = "LEFT"
            
        return f"{direction} ({magnitude:.1f}mm)"
        
    def process_pressure_data(self, packet):
        """Convert binary data to pressure values"""
        for row in range(20):
            for byte_idx in range(3):
                if (row*3 + byte_idx) >= len(packet):
                    return
                    
                byte = packet[row*3 + byte_idx]
                for bit in range(8):
                    col = byte_idx*8 + bit
                    if col < 20:
                        if (byte >> bit) & 0x01:
                            dist = np.sqrt((row-9.5)**2 + (col-9.5)**2)
                            self.pressure_data[row,col] = max(0, 200 - dist*15)
                        else:
                            self.pressure_data[row,col] = 0

    def update_visualization(self):
        """Update the visualization with new data"""
        if self.ser and self.ser.is_open:
            try:
                data = self.ser.read(self.ser.in_waiting or 1)

                if data and b'\xFF' in data and b'\xFE' in data:
                    start_idx = data.index(b'\xFF')
                    end_idx = data.index(b'\xFE')

                    if end_idx > start_idx:
                        packet = data[start_idx+1:end_idx]
                        if len(packet) >= 50:
                            self.previous_pressure = self.pressure_data.copy()
                            self.process_pressure_data(packet)
                            self.display_data = 0.7 * self.pressure_data + 0.3 * self.display_data
                            self.im.set_array(self.display_data)

                            left_pressure = np.sum(self.pressure_data[:, :10])
                            right_pressure = np.sum(self.pressure_data[:, 10:])

                            total_pressure = left_pressure + right_pressure

                            if total_pressure > 0:
                                left_pct = left_pressure * 100 / total_pressure
                                right_pct = right_pressure * 100 / total_pressure

                                self.balance_text.set_text(
                                    f'Left: {left_pct:.1f}% | Right: {right_pct:.1f}%'
                                )

                            # Calculate COP
                            cop = self.calculate_cop()

                            if cop:
                                cop_x, cop_y = cop

                                # Move marker
                                self.cop_marker.set_data([cop_x], [cop_y])

                                # Store trail history
                                self.cop_history.append((cop_x, cop_y))

                                if len(self.cop_history) > 1:
                                    xs = [p[0] for p in self.cop_history]
                                    ys = [p[1] for p in self.cop_history]

                                    self.cop_trail.set_data(xs, ys)

                                if len(self.cop_history) > 1:
                                    xs = [p[0] for p in self.cop_history]
                                    ys = [p[1] for p in self.cop_history]

                                    self.cop_trail.set_data(xs, ys)

                                # Update text
                                self.cop_text.set_text(
                                    f'COP: X={cop_x:.1f}, Y={cop_y:.1f}'
                                )

                            movement_status = "No significant movement"
                            
                            if self.frame_count > 0:
                                dx, dy = self.calculate_displacement(self.pressure_data, self.previous_pressure)
                                self.movement_history.append((dx, dy))
                                
                                if len(self.movement_history) >= 3:
                                    avg_dx = np.mean([m[0] for m in self.movement_history])
                                    avg_dy = np.mean([m[1] for m in self.movement_history])
                                    self.total_displacement[0] += avg_dx
                                    self.total_displacement[1] += avg_dy
                                    
                                    movement_status = self.detect_movement(avg_dx, avg_dy)
                                    self.info_text.set_text(
                                        f'Displacement: X={self.total_displacement[0]:.1f}mm, Y={self.total_displacement[1]:.1f}mm\n'
                                        f'Movement: {movement_status}'
                                    )
                            
                            # Record data if in recording mode
                            if self.recording and self.frame_count % 5 == 0:  # Record every 5 frames
                                reading = {
                                    'timestamp': datetime.now().isoformat(),
                                    'pressure_data': self.pressure_data.tolist(),
                                    'displacement_x': self.total_displacement[0],
                                    'displacement_y': self.total_displacement[1],
                                    'movement_status': movement_status
                                }
                                self.recorded_data.append(reading)

                            # Capture frame for video
                                self.capture_frame()
                            
                            self.frame_count += 1
                            
            except Exception as e:
                print(f"Error in update_visualization: {e}")
                print(traceback.format_exc())

                
        # Redraw canvas
        self.canvas.draw()
        
        # Schedule next update
        self.root.after(20, self.update_visualization)
        
    def on_close(self):
        """Clean up on application close"""
        try:
            # Stop and save any ongoing recording
            if self.recording:
                self.toggle_recording()
                
            # Release video writer if active
            if self.video_writer:
                self.video_writer.release()
                
            # Close serial connection
            if self.ser and self.ser.is_open:
                self.ser.close()
                
            # Cancel any pending updates
            if hasattr(self, 'update_job'):
                self.root.after_cancel(self.update_job)
                
        except Exception as e:
            print(f"Cleanup error: {e}")
        finally:
            self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    
    # Get patient ID from command line arguments
    if len(sys.argv) > 1:
        patient_id = sys.argv[1]
        print(f"Starting application for patient: {patient_id}")
    else:
        print("Error: No patient ID provided")
        sys.exit(1)
        
    app = PressureVisualizationApp(root, patient_id)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()