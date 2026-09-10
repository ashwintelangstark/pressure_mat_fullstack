/*
 * 20x20 FSR Pressure Matrix Firmware - Serial Monitor 20x20 Grid Edition
 * 
 * Hardware Pinout:
 *   - Column Address: s0=18, s1=19, s2=21, s3=22
 *   - Column Enables: EN_MUX_C=16 (Cols 0-15), EN_MUX_D=4 (Cols 16-19)
 *   - Row Address:    w0=25, w1=26, w2=27, w3=14
 *   - Row Enables:    EN_MUX_A=13 (Rows 0-15), EN_MUX_B=17 (Rows 16-19)
 *   - Signal Pins:    DRIVE_PIN=32 (3.3V Output Drive)
 *                     SENSE_PIN=33 (ADC1_CH5 with internal INPUT_PULLDOWN)
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
int touchThreshold = 4;           // Low noise threshold for instant touch
const int FRAME_DELAY_MS = 60;    // Clean refresh rate for Serial Monitor display

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

  int s1 = analogRead(SENSE_PIN);
  delayMicroseconds(6);
  int s2 = analogRead(SENSE_PIN);

  return (s1 + s2) >> 1;
}

void calibrate() {
  for (byte r = 0; r < ROWS; r++) {
    for (byte c = 0; c < COLS; c++) {
      baseline[r][c] = 0;
    }
  }

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

  touchThreshold = max(4, maxNoise + 2);

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
      if (val >= 1 && val <= 350) {
        touchThreshold = val;
      }
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

  Serial.println("--- 20x20 Pressure Matrix ---");

  for (byte r = 0; r < ROWS; r++) {
    selectRow(r);

    for (byte c = 0; c < COLS; c++) {
      selectCol(c);

      int raw = readSensoredCell();
      int delta = raw - baseline[r][c];

      int val = 0;
      if (delta > touchThreshold) {
        // Map delta to readable 1..150 pressure intensity number
        val = constrain(((delta - touchThreshold) * 140) / 120 + 1, 1, 150);
      }

      if (val < 10) {
        Serial.print("  ");
      } else if (val < 100) {
        Serial.print(" ");
      }
      Serial.print(val);
      Serial.print(" ");
    }
    Serial.println();
  }

  digitalWrite(EN_MUX_A, HIGH);
  digitalWrite(EN_MUX_B, HIGH);
  digitalWrite(EN_MUX_C, HIGH);
  digitalWrite(EN_MUX_D, HIGH);

  delay(FRAME_DELAY_MS);
}
