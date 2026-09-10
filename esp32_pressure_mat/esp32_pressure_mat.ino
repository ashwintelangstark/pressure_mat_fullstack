/*
 * 20x20 FSR Pressure Matrix Firmware
 * Serial Monitor 20x20 Grid Edition
 *
 * FIXED:
 * - Removes false values from untouched sensors
 * - Detects pressure when ADC value DECREASES
 * - Uses per-cell baseline
 * - Uses noise filtering
 * - Keeps 1-second display interval
 */

#include <Arduino.h>

// =========================================================
// PIN CONFIGURATION
// =========================================================

// Column Address
const byte s0 = 18;
const byte s1 = 19;
const byte s2 = 21;
const byte s3 = 22;

// Column MUX Enable
const byte EN_MUX_C = 16;
const byte EN_MUX_D = 4;

// Row Address
const byte w0 = 25;
const byte w1 = 26;
const byte w2 = 27;
const byte w3 = 14;

// Row MUX Enable
const byte EN_MUX_A = 13;
const byte EN_MUX_B = 17;

// Signal
const byte DRIVE_PIN = 32;
const byte SENSE_PIN = 33;


// =========================================================
// MATRIX
// =========================================================

const byte ROWS = 20;
const byte COLS = 20;

int baseline[ROWS][COLS];


// =========================================================
// SETTINGS
// =========================================================

const int SETTLE_TIME_US = 65;

// Minimum ADC change required to detect pressure
int touchThreshold = 12;

// How much extra filtering is applied
const int FILTER_MARGIN = 4;

// Display interval
const int FRAME_DELAY_MS = 1000;


// =========================================================
// SELECT ROW
// =========================================================

void selectRow(byte r)
{
    digitalWrite(EN_MUX_A, HIGH);
    digitalWrite(EN_MUX_B, HIGH);

    byte addr = (r < 16) ? r : (r - 16);

    digitalWrite(w0, (addr >> 0) & 1);
    digitalWrite(w1, (addr >> 1) & 1);
    digitalWrite(w2, (addr >> 2) & 1);
    digitalWrite(w3, (addr >> 3) & 1);

    delayMicroseconds(4);

    if (r < 16)
    {
        digitalWrite(EN_MUX_A, LOW);
    }
    else
    {
        digitalWrite(EN_MUX_B, LOW);
    }
}


// =========================================================
// SELECT COLUMN
// =========================================================

void selectCol(byte c)
{
    digitalWrite(EN_MUX_C, HIGH);
    digitalWrite(EN_MUX_D, HIGH);

    byte addr = (c < 16) ? c : (c - 16);

    digitalWrite(s0, (addr >> 0) & 1);
    digitalWrite(s1, (addr >> 1) & 1);
    digitalWrite(s2, (addr >> 2) & 1);
    digitalWrite(s3, (addr >> 3) & 1);

    delayMicroseconds(4);

    if (c < 16)
    {
        digitalWrite(EN_MUX_C, LOW);
    }
    else
    {
        digitalWrite(EN_MUX_D, LOW);
    }
}


// =========================================================
// READ SENSOR
// =========================================================

int readSensoredCell()
{
    delayMicroseconds(SETTLE_TIME_US);

    int reading1 = analogRead(SENSE_PIN);

    delayMicroseconds(6);

    int reading2 = analogRead(SENSE_PIN);

    return (reading1 + reading2) / 2;
}


// =========================================================
// DISABLE ALL MUX
// =========================================================

void disableAllMux()
{
    digitalWrite(EN_MUX_A, HIGH);
    digitalWrite(EN_MUX_B, HIGH);
    digitalWrite(EN_MUX_C, HIGH);
    digitalWrite(EN_MUX_D, HIGH);
}


// =========================================================
// CALIBRATION
// =========================================================

void calibrate()
{
    Serial.println();
    Serial.println("=================================");
    Serial.println("CALIBRATING PRESSURE MAT");
    Serial.println("KEEP THE MAT COMPLETELY FREE");
    Serial.println("DO NOT TOUCH ANY SENSOR");
    Serial.println("=================================");

    delay(1000);

    // Clear baseline
    for (byte r = 0; r < ROWS; r++)
    {
        for (byte c = 0; c < COLS; c++)
        {
            baseline[r][c] = 0;
        }
    }


    // -----------------------------------------------------
    // Take 20 samples per sensor
    // -----------------------------------------------------

    const int CALIBRATION_SAMPLES = 20;

    for (int sample = 0; sample < CALIBRATION_SAMPLES; sample++)
    {
        for (byte r = 0; r < ROWS; r++)
        {
            selectRow(r);

            for (byte c = 0; c < COLS; c++)
            {
                selectCol(c);

                baseline[r][c] += readSensoredCell();
            }
        }

        delay(2);
    }


    // -----------------------------------------------------
    // Calculate average baseline
    // -----------------------------------------------------

    for (byte r = 0; r < ROWS; r++)
    {
        for (byte c = 0; c < COLS; c++)
        {
            baseline[r][c] =
                baseline[r][c] / CALIBRATION_SAMPLES;
        }
    }


    // -----------------------------------------------------
    // Measure actual noise
    // -----------------------------------------------------

    int maximumNoise = 0;

    for (int sample = 0; sample < 5; sample++)
    {
        for (byte r = 0; r < ROWS; r++)
        {
            selectRow(r);

            for (byte c = 0; c < COLS; c++)
            {
                selectCol(c);

                int current = readSensoredCell();

                int difference =
                    abs(current - baseline[r][c]);

                if (difference > maximumNoise)
                {
                    maximumNoise = difference;
                }
            }
        }
    }


    // -----------------------------------------------------
    // Set safe threshold
    // -----------------------------------------------------

    touchThreshold =
        max(12, maximumNoise + FILTER_MARGIN);


    disableAllMux();

    Serial.print("Maximum detected noise: ");
    Serial.println(maximumNoise);

    Serial.print("Touch threshold: ");
    Serial.println(touchThreshold);

    Serial.println("Calibration complete.");
    Serial.println();
}


// =========================================================
// SERIAL COMMANDS
// =========================================================

void handleSerialCommands()
{
    while (Serial.available())
    {
        char cmd = Serial.read();

        // Recalibrate
        if (cmd == 'c' || cmd == 'C')
        {
            calibrate();
        }

        // Manually change threshold
        else if (cmd == 't' || cmd == 'T')
        {
            int value = Serial.parseInt();

            if (value >= 1 && value <= 500)
            {
                touchThreshold = value;

                Serial.print("Touch threshold changed to: ");
                Serial.println(touchThreshold);
            }
        }
    }
}


// =========================================================
// SETUP
// =========================================================

void setup()
{
    Serial.begin(115200);

    delay(500);

    Serial.println();
    Serial.println("ESP32 20x20 PRESSURE MAT");
    Serial.println("Firmware Starting...");


    // -----------------------------------------------------
    // MUX OUTPUT PINS
    // -----------------------------------------------------

    const byte outputPins[] =
    {
        s0,
        s1,
        s2,
        s3,

        w0,
        w1,
        w2,
        w3,

        EN_MUX_A,
        EN_MUX_B,
        EN_MUX_C,
        EN_MUX_D
    };


    for (byte i = 0; i < sizeof(outputPins); i++)
    {
        pinMode(outputPins[i], OUTPUT);

        digitalWrite(outputPins[i], HIGH);
    }


    // -----------------------------------------------------
    // DRIVE
    // -----------------------------------------------------

    pinMode(DRIVE_PIN, OUTPUT);

    digitalWrite(DRIVE_PIN, HIGH);


    // -----------------------------------------------------
    // ADC
    // -----------------------------------------------------

    pinMode(SENSE_PIN, INPUT);

    analogReadResolution(12);

    analogSetPinAttenuation(
        SENSE_PIN,
        ADC_11db
    );


    // -----------------------------------------------------
    // CALIBRATE
    // -----------------------------------------------------

    calibrate();

    Serial.println("PRESSURE MAT READY.");
    Serial.println();
}


// =========================================================
// MAIN LOOP
// =========================================================

void loop()
{
    handleSerialCommands();

    Serial.println("---------------------------------------------");
    Serial.println("20x20 PRESSURE MATRIX");
    Serial.println("---------------------------------------------");


    // =====================================================
    // SCAN MATRIX
    // =====================================================

    for (byte r = 0; r < ROWS; r++)
    {
        selectRow(r);

        for (byte c = 0; c < COLS; c++)
        {
            selectCol(c);

            int raw = readSensoredCell();


            // -------------------------------------------------
            // IMPORTANT
            //
            // Your FSR configuration normally produces a
            // LOWER ADC value when pressure is applied.
            //
            // Therefore:
            //
            // pressure = baseline - raw
            //
            // NOT:
            //
            // raw - baseline
            // -------------------------------------------------

            int pressureChange =
                baseline[r][c] - raw;


            int value = 0;


            // -------------------------------------------------
            // PRESSURE DETECTION
            // -------------------------------------------------

            if (pressureChange > touchThreshold)
            {
                value =
                    ((pressureChange - touchThreshold) * 140) / 300 + 1;

                value =
                    constrain(value, 1, 150);
            }
            else
            {
                value = 0;
            }


            // -------------------------------------------------
            // PRINT GRID
            // -------------------------------------------------

            if (value < 10)
            {
                Serial.print("  ");
            }
            else if (value < 100)
            {
                Serial.print(" ");
            }

            Serial.print(value);
            Serial.print(" ");
        }

        Serial.println();
    }


    // =====================================================
    // DISABLE MUX
    // =====================================================

    disableAllMux();


    // =====================================================
    // WAIT 1 SECOND
    // =====================================================

    delay(FRAME_DELAY_MS);
}
