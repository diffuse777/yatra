/*
 * ============================================================================
 * Yatra Saarthi — Arduino Uno / Nano + GPS
 * ============================================================================
 * Connections:
 *   GPS VCC -> 5V   GND -> GND   TX -> Pin 8   RX -> Pin 9
 *   SoftwareSerial(8, 9)  =  (Arduino RX, Arduino TX)
 *
 * Serial Monitor must be 115200 baud (Tools menu).
 *
 * If you ONLY see "PARSE_RMC: ON" and nothing else:
 *   1) GPS needs sky view — first fix can take 5–15 minutes (indoors = often never).
 *   2) Confirm GPS TX -> Arduino pin 8.
 *   3) Try GPS_BAUD 38400 below if your module was configured for 38400 (rare).
 *   4) Set DEBUG_SHOW_RMC_RAW 1 — you should see [RMC raw] lines; if status is V,
 *      there is no fix yet. A = valid fix -> then FIX and YATRA lines appear.
 *
 * WEBSITE / “LIVE MAP” (important):
 *   The Uno has no Wi‑Fi and no built‑in internet. Serial / USB only sends data to whatever
 *   is plugged in — there is no path to your Flask server over the air from a stock Uno.
 *
 * USB + PC (works with StartYatraGPS.bat / serial bridge):
 *   GPS fixes appear as YATRA,... lines; the Python bridge POSTs them to the website.
 *   Close Serial Monitor first; only one program may use the COM port.
 *
 * BATTERY ONLY (no USB to a computer):
 *   The sketch and GPS still run — you can get a fix outdoors — but the website will NOT
 *   update, because nothing is forwarding data off the board. Adding a Wi‑Fi radio is not
 *   included on Arduino Uno; that requires extra hardware or a Wi‑Fi board (see ESP32
 *   example in firmware/esp32_rajp_mdu_gps/ if you later add one).
 * ============================================================================
 */

#include <Arduino.h>
#include <SoftwareSerial.h>
#include <string.h>

SoftwareSerial gpsSerial(8, 9);

// Most NEO-6M modules default to 9600. Change to 38400 only if you know yours uses it.
#define GPS_BAUD 9600

#define PARSE_RMC 1
#define OUTPUT_BRIDGE_LINE 1
// Print each $GPRMC/$GNRMC line so you can see ,V (no fix) vs ,A (fix).
#define DEBUG_SHOW_RMC_RAW 1

#if PARSE_RMC
static char lineBuf[120];
static uint8_t lineLen = 0;

static bool parseRmcLine(const char *line, double *lat, double *lon, float *speedKmh) {
  if (strncmp(line, "$GPRMC", 6) != 0 && strncmp(line, "$GNRMC", 6) != 0) {
    return false;
  }
  char buf[120];
  strncpy(buf, line, sizeof(buf) - 1);
  buf[sizeof(buf) - 1] = '\0';

  char *tok = strtok(buf, ",");
  char *fields[14] = {0};
  int n = 0;
  while (tok && n < 14) {
    fields[n++] = tok;
    tok = strtok(NULL, ",");
  }
  if (n < 8) return false;
  if (!fields[2] || fields[2][0] != 'A') return false;
  if (!fields[3] || !fields[4] || !fields[5] || !fields[6]) return false;

  double latRaw = atof(fields[3]);
  char hemiLat = fields[4][0];
  double lonRaw = atof(fields[5]);
  char hemiLon = fields[6][0];

  int latDeg = (int)(latRaw / 100);
  double latMin = latRaw - latDeg * 100;
  *lat = latDeg + latMin / 60.0;
  if (hemiLat == 'S') *lat = -*lat;

  int lonDeg = (int)(lonRaw / 100);
  double lonMin = lonRaw - lonDeg * 100;
  *lon = lonDeg + lonMin / 60.0;
  if (hemiLon == 'W') *lon = -*lon;

  float knots = atof(fields[7]);
  *speedKmh = knots * 1.852f;
  return true;
}
#endif

void setup() {
  Serial.begin(115200);
  gpsSerial.begin(GPS_BAUD);
  delay(300);
  Serial.println(F("=== Yatra Saarthi GPS (Uno) | SoftwareSerial(8,9) ==="));
  Serial.print(F("GPS serial baud: "));
  Serial.println(GPS_BAUD);
#if PARSE_RMC
  Serial.println(F("PARSE_RMC: ON"));
#else
  Serial.println(F("PARSE_RMC: OFF (raw NMEA passthrough)"));
#endif
#if OUTPUT_BRIDGE_LINE && PARSE_RMC
  Serial.println(F("BRIDGE: ON (YATRA, lines when fix is valid)"));
#endif
#if DEBUG_SHOW_RMC_RAW && PARSE_RMC
  Serial.println(F("DEBUG: [RMC raw] lines show GPS talk; A=fix, V=no fix yet"));
#endif
  Serial.println(F("--- Put GPS at window/outdoors; wait for satellites ---"));
}

void loop() {
  static unsigned long lastHintMs = 0;
  static unsigned long lastAnyByteMs = 0;
  static bool sawByte = false;

#if PARSE_RMC
  while (gpsSerial.available()) {
    sawByte = true;
    lastAnyByteMs = millis();
    char c = (char)gpsSerial.read();
    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      lineLen = 0;
#if DEBUG_SHOW_RMC_RAW
      if (lineBuf[0] == '$' &&
          (strncmp(lineBuf, "$GPRMC", 6) == 0 || strncmp(lineBuf, "$GNRMC", 6) == 0)) {
        Serial.print(F("[RMC raw] "));
        Serial.println(lineBuf);
      }
#endif
      double lat, lon;
      float spd;
      if (lineBuf[0] == '$' && parseRmcLine(lineBuf, &lat, &lon, &spd)) {
        Serial.print(F("FIX "));
        Serial.print(lat, 6);
        Serial.print(F(" , "));
        Serial.print(lon, 6);
        Serial.print(F(" | "));
        Serial.print(spd, 1);
        Serial.println(F(" km/h"));
#if OUTPUT_BRIDGE_LINE
        Serial.print(F("YATRA,"));
        Serial.print(lat, 7);
        Serial.print(F(","));
        Serial.print(lon, 7);
        Serial.print(F(","));
        Serial.println(spd, 2);
#endif
      }
    } else if (c != '\r' && lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = (uint8_t)c;
    }
  }

  unsigned long now = millis();
  if (!sawByte && now > 5000 && now - lastHintMs > 12000) {
    lastHintMs = now;
    Serial.println(F("[Hint] No bytes on pin 8. Check GPS TX->Pin8, GND, 5V. Wrong baud? Try GPS_BAUD 38400."));
  } else if (sawByte && now - lastAnyByteMs > 30000 && now - lastHintMs > 15000) {
    lastHintMs = now;
    Serial.println(F("[Hint] GPS went quiet. Reset module or check wiring."));
  }
#else
  while (gpsSerial.available()) {
    Serial.write(gpsSerial.read());
  }
#endif
}
