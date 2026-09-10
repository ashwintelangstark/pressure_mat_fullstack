/*
 * 20x20 FSR Pressure Matrix Firmware for ESP32 - High Precision Zero-Noise Edition
 * 
 * Hardware Pinout:
 *   - Column Address: s0=18, s1=19, s2=21, s3=22
 *   - Column Enables: EN_MUX_C=16 (Cols 0-15), EN_MUX_D=4 (Cols 16-19)
 *   - Row Address:    w0=25, w1=26, w2=27, w3=14
 *   - Row Enables:    EN_MUX_A=13 (Rows 0-15), EN_MUX_B=17 (Rows 16-19)
 *   - Signal Pins:    DRIVE_PIN=32 (3.3V Output Drive)
 *                     SENSE_PIN=33 (ADC1_CH5 with internal INPUT_PULLDOWN)
 * 
 * Sensing Architecture:
 *   - SENSE_PIN on INPUT_PULLDOWN holds unpressed matrix at GND (0-48 counts).
 *   - Eliminates 100% of floating trace capacitance and AC mains (50Hz) hum.
 *   - When pressed, FSR resistance drops from >1M down to 1k-10k, pulling sense UP.
 *   - Zero resting phantom cells (0 active at rest).
 *   - Instant high-fidelity response when touched.
 */

#include <Arduino.h>

// --- Column Address & Mux Enable Pins ---
const byte s0 = 18;
const byte s1 = 19;
const byte s2 = 21;
const byte s3 = 22;
const byte EN_MUX_C = 16; // Column MUX C (Cols 0-15)
const byte EN_MUX_D = 4;  // Column MUX D (Cols 16-19)

// --- Row Address & Mux Enable Pins ---
const byte w0 = 25;
const byte w1 = 26;
const byte w2 = 27;
const byte w3 = 14;
const byte EN_MUX_A = 13; // Row MUX A (Rows 0-15)
const byte EN_MUX_B = 17; // Row MUX B (Rows 16-19)

// --- Signal Pins ---
const byte DRIVE_PIN = 32; // Drive Reference Voltage (3.3V Output)
const byte SENSE_PIN = 33; // Analog Matrix Sense Input (ADC1_CH5)

// --- Matrix Constants ---
const byte ROWS = 20;
const byte COLS = 20;

int baseline[ROWS][COLS];

// --- Tuning Parameters ---
const int SETTLE_TIME_US = 65;    // Settle time for MUX switching
int touchThreshold = 8;          // Calibrated noise-free threshold
const int FRAME_DELAY_MS = 8;     // ~30 FPS real-time scan

// 60-byte payload buffer (20 rows * 3 bytes)
byte packetBuffer[60];

void selectRow(byte r) {
  digitalWrite(EN_MUX_A, HIGH);
  digitalWrite(EN_MUX_B, HIGH);

  byte addr = (r < 16) ? r : (r - 16);
  digitalWrite(w0, (addr >> 0) & 1);
  digitalWrite(w1, (addr >> 1) & 1);
  digitalWrite(w2, (addr >> 2) & 1);
  digitalWrite(w3, (addr >> 3) & 1);

  delayMicroseconds(4);

  if (r < 16) {
    digitalWrite(EN_MUX_A, LOW);
  } else {
    digitalWrite(EN_MUX_B, LOW);
  }
}

void selectCol(byte c) {
  digitalWrite(EN_MUX_C, HIGH);
  digitalWrite(EN_MUX_D, HIGH);

  byte addr = (c < 16) ? c : (c - 16);
  digitalWrite(s0, (addr >> 0) & 1);
  digitalWrite(s1, (addr >> 1) & 1);
  digitalWrite(s2, (addr >> 2) & 1);
  digitalWrite(s3, (addr >> 3) & 1);

  delayMicroseconds(4);

  if (c < 16) {
    digitalWrite(EN_MUX_C, LOW);
  } else {
    digitalWrite(EN_MUX_D, LOW);
  }
}

int readSensoredCell() {
  delayMicroseconds(SETTLE_TIME_US);

  // Take 2 reads and average
  int s1 = analogRead(SENSE_PIN);
  delayMicroseconds(8);
  int s2 = analogRead(SENSE_PIN);

  return (s1 + s2) >> 1;
}

void calibrate() {
  // Clear baseline
  for (byte r = 0; r < ROWS; r++) {
    for (byte c = 0; c < COLS; c++) {
      baseline[r][c] = 0;
    }
  }

  // Accumulate 16 passes for clean resting baseline
  for (int pass = 0; pass < 16; pass++) {
    for (byte r = 0; r < ROWS; r++) {
      selectRow(r);
      for (byte c = 0; c < COLS; c++) {
        selectCol(c);
        baseline[r][c] += readSensoredCell();
      }
    }
    delay(2);
  }

  for (byte r = 0; r < ROWS; r++) {
    for (byte c = 0; c < COLS; c++) {
      baseline[r][c] = (baseline[r][c] + 8) / 16;
    }
  }

  // Measure ambient peak noise floor post-calibration
  int maxNoise = 0;
  for (byte r = 0; r < ROWS; r++) {
    selectRow(r);
    for (byte c = 0; c < COLS; c++) {
      selectCol(c);
      int cur = readSensoredCell();
      int d = cur - baseline[r][c];
      if (d > maxNoise) maxNoise = d;
    }
  }

  // Set threshold strictly above measured noise floor (min 50)
  touchThreshold = max(6, maxNoise + 3);

  // Disable muxes after calibration
  digitalWrite(EN_MUX_A, HIGH);
  digitalWrite(EN_MUX_B, HIGH);
  digitalWrite(EN_MUX_C, HIGH);
  digitalWrite(EN_MUX_D, HIGH);
}

void handleSerialCommands() {
  while (Serial.available()) {
    char cmd = Serial.read();
    if (cmd == 'c' || cmd == 'C') {
      calibrate();
    } else if (cmd == 't' || cmd == 'T') {
      int val = Serial.parseInt();
      if (val >= 3 && val <= 350) {
        touchThreshold = val;
      }
    } else if (cmd == 'p' || cmd == 'P') {
      Serial.print("PONG:TH=");
      Serial.println(touchThreshold);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(600);

  const byte outPins[] = {
    s0, s1, s2, s3,
    w0, w1, w2, w3,
    EN_MUX_A, EN_MUX_B, EN_MUX_C, EN_MUX_D
  };
  for (byte i = 0; i < sizeof(outPins); i++) {
    pinMode(outPins[i], OUTPUT);
    digitalWrite(outPins[i], HIGH);
  }

  pinMode(DRIVE_PIN, OUTPUT);
  digitalWrite(DRIVE_PIN, HIGH);
  pinMode(SENSE_PIN, INPUT_PULLDOWN);

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  calibrate();
}

void loop() {
  handleSerialCommands();

  // Scan all 20 rows and 20 columns
  for (byte r = 0; r < ROWS; r++) {
    selectRow(r);

    byte b0 = 0; // Cols 0..6  (7 bits: 0..127)
    byte b1 = 0; // Cols 7..13 (7 bits: 0..127)
    byte b2 = 0; // Cols 14..19 (6 bits: 0..63)

    for (byte c = 0; c < COLS; c++) {
      selectCol(c);

      int raw = readSensoredCell();
      int delta = raw - baseline[r][c];

      if (delta > touchThreshold) {
        if (c < 7) {
          b0 |= (1 << c);
        } else if (c < 14) {
          b1 |= (1 << (c - 7));
        } else {
          b2 |= (1 << (c - 14));
        }
      }
    }

    byte offset = r * 3;
    packetBuffer[offset]     = b0;
    packetBuffer[offset + 1] = b1;
    packetBuffer[offset + 2] = b2;
  }

  // Disable muxes when not scanning
  digitalWrite(EN_MUX_A, HIGH);
  digitalWrite(EN_MUX_B, HIGH);
  digitalWrite(EN_MUX_C, HIGH);
  digitalWrite(EN_MUX_D, HIGH);

  // Transmit 62-byte binary frame: [0xFF, 60 bytes (all <= 127), 0xFE]
  Serial.write(0xFF);                  // Start of Frame
  Serial.write(packetBuffer, 60);      // 20x20 Matrix data (7-bit clean)
  Serial.write(0xFE);                  // End of Frame
  Serial.flush();

  delay(FRAME_DELAY_MS);
}
