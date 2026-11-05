"""Send a fake GPS fix to local or deployed Yatra Saarthi (for testing the map)."""
import argparse
import json
import os
import sys

try:
    import urllib.request
except ImportError:
    sys.exit(1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=os.environ.get("YATRA_BASE", "http://127.0.0.1:5000"))
    p.add_argument("--key", default=os.environ.get("GPS_API_KEY", "yatra-gps-dev-key-change-me"))
    p.add_argument("--lat", type=float, default=9.65)
    p.add_argument("--lng", type=float, default=77.85)
    p.add_argument("--speed", type=float, default=42.0)
    args = p.parse_args()

    base = args.url.rstrip("/")
    payload = {
        "api_key": args.key,
        "latitude": args.lat,
        "longitude": args.lng,
        "speed_kmh": args.speed,
        "bus_number": "TN55L 9003",
        "route_origin": "Rajapalayam",
        "route_destination": "Madurai",
        "device_id": "simulator",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/gps/update",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        print(r.status, r.read().decode())


if __name__ == "__main__":
    main()
