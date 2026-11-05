/*
 * Yatra Saarthi — live bus GPS (TN55L 9003 · Rajapalayam to Madurai)
 *
 * Use this on the bus if you have WiFi (HTTPS to Vercel).
 *
 * Arduino Uno users: same GPS module, but Uno uses SoftwareSerial(8,9) — see
 *   firmware/arduino_uno_gps_test/arduino_uno_gps_test.ino
 *   Uno cannot do HTTPS; use tools/serial_bridge_yatra.py over USB, or use this ESP32 board.
 *
 * This ESP32 sketch: GPS module TX -> GPIO16, GPS RX -> GPIO17 (3.3 V logic).
 *
 * Set WIFI_SSID, WIFI_PASS, API_URL, GPS_API_KEY (must match server / Vercel env).
 */

#include <WiFi.h>
#include <string.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>

// ---- WiFi ----
const char *WIFI_SSID = "YOUR_WIFI_SSID";
const char *WIFI_PASS = "YOUR_WIFI_PASSWORD";

// ---- Yatra Saarthi API ----
const char *API_URL   = "https://yatrasaarthi.vercel.app/api/gps/update";
const char *GPS_API_KEY = "yatra-gps-dev-key-change-me";  // same as server GPS_API_KEY

const char *ROUTE_ORIGIN = "Rajapalayam";
const char *ROUTE_DEST   = "Madurai";
const char *BUS_NUMBER   = "TN55L 9003";
const char *DEVICE_ID    = "esp32-bus1";

// GPS UART2: connect GPS TX -> GPIO16, GPS RX -> GPIO17 (3.3V logic)
#define GPS_RX 16
#define GPS_TX 17

HardwareSerial GPSSerial(2);
unsigned long lastPost = 0;
const unsigned long POST_INTERVAL_MS = 20000;

static bool parseRmc(const char *line, double *lat, double *lon, float *speedKmh) {
  if (strncmp(line, "$GPRMC", 6) != 0 && strncmp(line, "$GNRMC", 6) != 0) return false;
  char buf[120];
  strncpy(buf, line, sizeof(buf) - 1);
  buf[sizeof(buf) - 1] = 0;
  char *tok = strtok(buf, ",");
  int i = 0;
  char *fields[14] = {0};
  while (tok && i < 14) {
    fields[i++] = tok;
    tok = strtok(NULL, ",");
  }
  if (i < 8) return false;
  if (fields[2][0] != 'A') return false;

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

void postGps(double lat, double lng, float speedKmh) {
  if (WiFi.status() != WL_CONNECTED) return;

  WiFiClientSecure client;
  client.setInsecure();

  HTTPClient http;
  if (!http.begin(client, API_URL)) {
    Serial.println("HTTP begin failed");
    return;
  }
  http.addHeader("Content-Type", "application/json");

  char body[384];
  snprintf(body, sizeof(body),
           "{\"api_key\":\"%s\",\"latitude\":%.7f,\"longitude\":%.7f,"
           "\"speed_kmh\":%.2f,\"bus_number\":\"%s\","
           "\"route_origin\":\"%s\",\"route_destination\":\"%s\","
           "\"device_id\":\"%s\"}",
           GPS_API_KEY, lat, lng, speedKmh, BUS_NUMBER,
           ROUTE_ORIGIN, ROUTE_DEST, DEVICE_ID);

  int code = http.POST(body);
  Serial.printf("POST %d\n", code);
  if (code > 0) Serial.println(http.getString());
  http.end();
}

void setup() {
  Serial.begin(115200);
  GPSSerial.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi OK");
}

void loop() {
  static char line[120];
  static int li = 0;

  while (GPSSerial.available()) {
    char c = (char)GPSSerial.read();
    if (c == '\n') {
      line[li] = 0;
      li = 0;
      if (line[0] == '$') {
        double lat, lon;
        float spd;
        if (parseRmc(line, &lat, &lon, &spd)) {
          Serial.printf("Fix: %.6f, %.6f  %.1f km/h\n", lat, lon, spd);
          unsigned long now = millis();
          if (now - lastPost >= POST_INTERVAL_MS) {
            lastPost = now;
            postGps(lat, lon, spd);
          }
        }
      }
    } else if (c != '\r' && li < (int)sizeof(line) - 1) {
      line[li++] = c;
    }
  }
}
