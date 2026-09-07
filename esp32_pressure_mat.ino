/*
 * 20x20 FSR Matrix Firmware for ESP32
 * 
 * Integrated for Pressure Visualization & "Collect the Stars" Game
 * 
 * Communication Protocol:
 *   - Baud Rate: 115200
 *   - Frame Format: [0xFF, 60 Data Bytes, 0xFE] (62 bytes total)
 *   - 60 Data Bytes = 20 rows * 3 bytes/row
 *       Byte 0: Cols 0..7  (Bit 0 = Col 0, ..., Bit 7 = Col 7)
 *       Byte 1: Cols 8..15 (Bit 0 = Col 8, ..., Bit 7 = Col 15)
 *       Byte 2: Cols 16..19 (Bit 0 = Col 16, ..., Bit 3 = Col 19)
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
const byte R_SIG_PIN = 32; // Row Analog Sense (ADC)
const byte C_SIG_PIN = 33; // Column Drive (VCC)

// --- Matrix Constants ---
const byte ROWS = 20;
const byte COLS = 20;
int baseline[ROWS][COLS];

// --- Tuning Parameters ---
const int SETTLE_TIME = 60;       // Microseconds to allow signal to settle
const int TOUCH_THRESHOLD = 50;   // ADC counts above baseline to trigger touch
const int FRAME_DELAY_MS = 10;    // Delay between frame scans (~40-50 FPS)

// 60-byte payload buffer (20 rows * 3 bytes)
byte packetBuffer[60];

void selectRow(byte r) {
  // Disable both row multiplexers (Active LOW)
  digitalWrite(EN_MUX_A, HIGH);
  digitalWrite(EN_MUX_B, HIGH);

  byte addr = (r < 16) ? r : (r - 16);
  digitalWrite(w0, (addr >> 0) & 1);
  digitalWrite(w1, (addr >> 1) & 1);
  digitalWrite(w2, (addr >> 2) & 1);
  digitalWrite(w3, (addr >> 3) & 1);

  // Enable correct MUX
  if (r < 16) {
    digitalWrite(EN_MUX_A, LOW);
  } else {
    digitalWrite(EN_MUX_B, LOW);
  }
}

void selectCol(byte c) {
  // Disable both column multiplexers (Active LOW)
  digitalWrite(EN_MUX_C, HIGH);
  digitalWrite(EN_MUX_D, HIGH);

  byte addr = (c < 16) ? c : (c - 16);
  digitalWrite(s0, (addr >> 0) & 1);
  digitalWrite(s1, (addr >> 1) & 1);
  digitalWrite(s2, (addr >> 2) & 1);
  digitalWrite(s3, (addr >> 3) & 1);

  // Enable correct MUX
  if (c < 16) {
    digitalWrite(EN_MUX_C, LOW);
  } else {
    digitalWrite(EN_MUX_D, LOW);
  }
}

void calibrate() {
  for (byte r = 0; r < ROWS; r++) {
    selectRow(r);
    for (byte c = 0; c < COLS; c++) {
      selectCol(c);
      delayMicroseconds(SETTLE_TIME);

      long sum = 0;
      for (int i = 0; i < 4; i++) {
        sum += analogRead(R_SIG_PIN);
        delayMicroseconds(20);
      }
      baseline[r][c] = sum / 4;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);

  // Initialize all multiplexer control pins
  const byte outPins[] = {
    s0, s1, s2, s3,
    w0, w1, w2, w3,
    EN_MUX_A, EN_MUX_B, EN_MUX_C, EN_MUX_D
  };
  for (byte i = 0; i < sizeof(outPins); i++) {
    pinMode(outPins[i], OUTPUT);
    digitalWrite(outPins[i], HIGH); // Disable all MUXes initially
  }

  pinMode(R_SIG_PIN, INPUT);
  pinMode(C_SIG_PIN, OUTPUT);
  digitalWrite(C_SIG_PIN, HIGH); // Drive column with reference voltage

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  // Calibrate mat baseline (keep mat free of pressure during boot)
  calibrate();
}

void loop() {
  // Check for serial commands (e.g. 'c' to recalibrate baseline)
  if (Serial.available()) {
    char cmd = Serial.read();
    if (cmd == 'c' || cmd == 'C') {
      calibrate();
    }
  }

  // Scan all 20 rows and 20 columns
  for (byte r = 0; r < ROWS; r++) {
    selectRow(r);

    byte b0 = 0;
    byte b1 = 0;
    byte b2 = 0;

    for (byte c = 0; c < COLS; c++) {
      selectCol(c);
      delayMicroseconds(SETTLE_TIME);

      int raw = analogRead(R_SIG_PIN);
      int net = raw - baseline[r][c];

      if (net > TOUCH_THRESHOLD) {
        if (c < 8) {
          b0 |= (1 << c);
        } else if (c < 16) {
          b1 |= (1 << (c - 8));
        } else {
          b2 |= (1 << (c - 16));
        }
      }
    }

    // Safeguard: Ensure data bytes do not collide with framing markers (0xFF and 0xFE)
    if (b0 >= 0xFE) b0 = 0xFD;
    if (b1 >= 0xFE) b1 = 0xFD;
    if (b2 >= 0xFE) b2 = 0xFD;

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

  // Transmit 62-byte binary frame: [0xFF, 60 bytes, 0xFE]
  Serial.write(0xFF);                  // Start of Frame
  Serial.write(packetBuffer, 60);      // 20x20 Bit-packed matrix data
  Serial.write(0xFE);                  // End of Frame
  Serial.flush();

  delay(FRAME_DELAY_MS);
}
