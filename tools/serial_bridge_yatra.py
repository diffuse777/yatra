"""
Read YATRA,lat,lon,speed lines from Arduino Uno USB serial and POST to Yatra Saarthi GPS API.

The Arduino Uno only prints to USB — there is no Wi‑Fi on the board, so the website does
not update until this script POSTs fixes. Powering the Uno from a battery without USB to a
PC running this bridge cannot reach the server (hardware limitation, not the app).

1. Upload firmware/arduino_uno_gps_test/arduino_uno_gps_test.ino with OUTPUT_BRIDGE_LINE 1
2. pip install pyserial
3. Close Arduino Serial Monitor (it locks the COM port).
4. python tools/serial_bridge_yatra.py --port COM3

Default API: http://127.0.0.1:5000 — use --url for Vercel + set GPS_API_KEY env.
Use --origin / --destination to match your Plan Trip search (defaults: Rajapalayam, Madurai).
"""
import argparse
import os
import re
import sys
import time

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

try:
    import serial
except ImportError:
    print("Install pyserial: pip install pyserial", file=sys.stderr)
    sys.exit(1)

YATRA_RE = re.compile(r"^YATRA,([\d.\-]+),([\d.\-]+),([\d.\-]+)\s*$")


def run_bridge(port, baud, url, api_key, route_origin, route_destination):
    """Open serial port and POST each YATRA line until process exits (Ctrl+C)."""
    print(
        "\n  Yatra GPS bridge — uploads YATRA lines to the server.\n"
        "  If this fails to open the port: close Arduino Serial Monitor first.\n"
        f"  Route sent to API: {route_origin!r} -> {route_destination!r} (must match timetables search)\n",
        flush=True,
    )
    try:
        ser = serial.Serial(port, baud, timeout=0.5)
    except serial.SerialException as e:
        print("Could not open serial port:", e, file=sys.stderr)
        print("Tip: unplug/replug USB, check COM number, and close Serial Monitor.", file=sys.stderr)
        raise

    print("Listening for YATRA,... lines on", port, "->", url, flush=True)

    while True:
        try:
            line = ser.readline().decode("utf-8", errors="replace").strip()
        except serial.SerialException as e:
            print("Serial error:", e, flush=True)
            time.sleep(1)
            continue
        if not line:
            continue
        m = YATRA_RE.match(line)
        if not m:
            continue
        lat, lon, spd = m.group(1), m.group(2), m.group(3)
        try:
            code, body = post(
                url,
                api_key,
                lat,
                lon,
                spd,
                route_origin,
                route_destination,
            )
            print("OK — website GPS updated:", code, body[:200], flush=True)
        except urllib.error.HTTPError as e:
            print("HTTP", e.code, e.read().decode()[:300], flush=True)
        except Exception as e:
            print("POST failed:", e, flush=True)


def post(base_url, api_key, lat, lon, speed, route_origin, route_destination):
    import json
    base = base_url.rstrip("/")
    payload = {
        "api_key": api_key,
        "latitude": float(lat),
        "longitude": float(lon),
        "speed_kmh": float(speed),
        "bus_number": "TN55L 9003",
        "route_origin": route_origin,
        "route_destination": route_destination,
        "device_id": "arduino-uno-bridge",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/gps/update",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read().decode()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", required=True, help="Serial port e.g. COM3 or /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--url", default=os.environ.get("YATRA_BASE", "http://127.0.0.1:5000"))
    p.add_argument("--key", default=os.environ.get("GPS_API_KEY", "yatra-gps-dev-key-change-me"))
    p.add_argument(
        "--origin",
        default=os.environ.get("YATRA_GPS_ORIGIN", "Rajapalayam"),
        help="Must match Plan Trip origin (e.g. Rajapalayam or Krishnankovil)",
    )
    p.add_argument(
        "--destination",
        default=os.environ.get("YATRA_GPS_DEST", "Madurai"),
        help="Must match Plan Trip destination",
    )
    args = p.parse_args()

    try:
        run_bridge(
            args.port,
            args.baud,
            args.url,
            args.key,
            args.origin,
            args.destination,
        )
    except serial.SerialException:
        sys.exit(1)


if __name__ == "__main__":
    main()
