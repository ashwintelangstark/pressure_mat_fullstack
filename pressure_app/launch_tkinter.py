import sys
import tkinter as tk
from tkinter_app import PressureVisualizationApp

if __name__ == "__main__":
    root = tk.Tk()
    
    # Get patient ID from command line arguments
    if len(sys.argv) > 1:
        patient_id = sys.argv[1]
        print(f"Starting visualization for patient: {patient_id}")  # Debug print
    else:
        print("Error: No patient ID provided in command line arguments")
        print(f"Received arguments: {sys.argv}")  # Debug print
        sys.exit(1)
        
    app = PressureVisualizationApp(root, patient_id)  # Pass patient_id here
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()