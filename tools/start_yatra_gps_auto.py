"""
One-shot launcher: start Flask if needed, auto-pick USB serial, run GPS bridge.

Usage (from project folder):
  python tools/start_yatra_gps_auto.py

Close Arduino Serial Monitor before running. Requires: pip install pyserial
"""
import argparse
import os
import subprocess
import sys
import time

try:
    import urllib.request
except ImportError:
    urllib = None

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Install pyserial: pip install pyserial", file=sys.stderr)
    sys.exit(1)

# Same directory as this file — import bridge
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
from serial_bridge_yatra import run_bridge  # noqa: E402


def project_root():
    return os.path.dirname(_TOOLS_DIR)


def flask_reachable(base_url):
    if not urllib:
        return False
    try:
        urllib.request.urlopen(base_url.rstrip("/") + "/favicon.ico", timeout=2)
        return True
    except Exception:
        return False


def wait_for_flask(base_url, seconds=45):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if flask_reachable(base_url):
            return True
        time.sleep(0.4)
    return False


def start_flask_server():
    """Spawn app.py in a new console on Windows so logs stay visible."""
    root = project_root()
    app_py = os.path.join(root, "app.py")
    if not os.path.isfile(app_py):
        print("Could not find app.py at", app_py, file=sys.stderr)
        return None
    cmd = [sys.executable, app_py]
    kwargs = {"cwd": root}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    try:
        return subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        print("Could not start Flask:", e, file=sys.stderr)
        return None


def pick_serial_port():
    """Prefer Arduino / USB-UART; skip typical Bluetooth COMs when possible."""
    ports = list(list_ports.comports())
    if not ports:
        return None

    scored = []
    for p in ports:
        blob = " ".join(
            x for x in (p.device, p.description, p.manufacturer, p.hwid) if x
        ).lower()
        if "bluetooth" in blob and "com0com" not in blob:
            continue
        score = 0
        if "arduino" in blob:
            score += 25
        if "ch340" in blob or "wch.cn" in blob:
            score += 18
        if "cp210" in blob or "silicon labs" in blob:
            score += 18
        if "ftdi" in blob:
            score += 12
        if "usb" in blob and "serial" in blob:
            score += 6
        scored.append((score, p.device, p.description or ""))

    scored.sort(key=lambda x: -x[0])
    if scored:
        best = scored[0]
        if best[0] > 0:
            return best[1]

    for p in ports:
        d = (p.description or "").lower()
        if "bluetooth" in d:
            continue
        return p.device
    return ports[0].device


def main():
    ap = argparse.ArgumentParser(description="Auto-start Yatra website + GPS USB bridge")
    ap.add_argument(
        "--no-flask",
        action="store_true",
        help="Do not start app.py automatically (only run the USB bridge)",
    )
    ap.add_argument("--url", default=os.environ.get("YATRA_BASE", "http://127.0.0.1:5000"))
    ap.add_argument("--key", default=os.environ.get("GPS_API_KEY", "yatra-gps-dev-key-change-me"))
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--port", default=None, help="Force COM port (e.g. COM5); default: auto-detect")
    ap.add_argument(
        "--origin",
        default=os.environ.get("YATRA_GPS_ORIGIN", "Rajapalayam"),
    )
    ap.add_argument(
        "--destination",
        default=os.environ.get("YATRA_GPS_DEST", "Madurai"),
    )
    args = ap.parse_args()
    start_flask = not args.no_flask

    base = args.url.rstrip("/")
    print("Yatra Saarthi — auto GPS pipeline", flush=True)
    print("  Website:", base, flush=True)

    if start_flask and not flask_reachable(base):
        print("  Flask not responding — starting app.py in a new window…", flush=True)
        proc = start_flask_server()
        if proc is None:
            sys.exit(1)
        print("  Waiting for server…", flush=True)
        if not wait_for_flask(base):
            print(
                "  Timed out. Start Flask manually: python app.py",
                file=sys.stderr,
            )
            sys.exit(1)
        print("  Server is up.", flush=True)
    elif not flask_reachable(base):
        print(
            "  ERROR: Cannot reach",
            base,
            "— run: python app.py   or use --with-flask",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        print("  Flask already running.", flush=True)

    port = args.port or pick_serial_port()
    if not port:
        print("  No serial ports found. Plug in the Arduino USB cable.", file=sys.stderr)
        sys.exit(1)

    print("  Using serial port:", port, flush=True)
    print("  (Close Arduino Serial Monitor if the port is busy.)\n", flush=True)

    try:
        run_bridge(port, args.baud, base, args.key, args.origin, args.destination)
    except serial.SerialException:
        sys.exit(1)


if __name__ == "__main__":
    main()
