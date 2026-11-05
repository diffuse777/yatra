"""
Vercel serverless entry point for Yatra Saarthi Flask app.
Auto-seeds bus data from CSV files on every cold start.
"""
import sys
import os
import csv
from datetime import datetime

os.environ['VERCEL'] = '1'

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from app import app, db, create_admin_user
from app import LocalBus, PrivateOperator


def _t(s):
    """Parse HH:MM time string, return time object or None."""
    try:
        return datetime.strptime(s.strip(), '%H:%M').time()
    except Exception:
        return None


def seed_buses():
    """Load govt + private bus data from CSV files into the DB."""
    data_dir = os.path.join(parent_dir, 'data')

    # ── Government buses ─────────────────────────────────────────────────────
    govt_csv = os.path.join(data_dir, 'govt_buses.csv')
    if os.path.exists(govt_csv):
        with open(govt_csv, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                try:
                    via = [v.strip() for v in row.get('via_stops', '').split(';') if v.strip()]
                    b = LocalBus(
                        bus_number       = row['bus_number'].strip(),
                        route_number     = row['route_number'].strip(),
                        operator         = row['operator'].strip(),
                        origin           = row['origin'].strip(),
                        destination      = row['destination'].strip(),
                        departure_time   = _t(row['departure_time']),
                        arrival_time     = _t(row['arrival_time']),
                        via_stops        = via,
                        fare             = float(row.get('fare', 0) or 0),
                        bus_type         = row.get('bus_type', 'Ordinary').strip(),
                        status           = row.get('status', 'scheduled').strip(),
                        seat_availability= int(row.get('seat_availability', 40) or 40),
                        total_seats      = int(row.get('total_seats', 50) or 50),
                        is_active        = True,
                    )
                    db.session.add(b)
                except Exception as e:
                    print(f"Skipping govt row: {e}")
        db.session.commit()
        print("Govt buses seeded.")

    # ── Private buses ─────────────────────────────────────────────────────────
    pvt_csv = os.path.join(data_dir, 'private_buses.csv')
    if os.path.exists(pvt_csv):
        with open(pvt_csv, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                try:
                    via = [v.strip() for v in row.get('via_stops', '').split(';') if v.strip()]
                    b = PrivateOperator(
                        bus_number       = row['bus_number'].strip(),
                        route_number     = row['route_number'].strip(),
                        operator_name    = row['operator'].strip(),
                        route_name       = row.get('route_name', '').strip(),
                        origin           = row['origin'].strip(),
                        destination      = row['destination'].strip(),
                        departure_time   = _t(row['departure_time']),
                        arrival_time     = _t(row['arrival_time']),
                        via_stops        = via,
                        fare             = float(row.get('fare', 0) or 0),
                        bus_type         = row.get('bus_type', 'AC Seater').strip(),
                        status           = row.get('status', 'available').strip(),
                        rating           = float(row.get('rating', 4.0) or 4.0),
                        live_tracking    = str(row.get('live_tracking', 'no')).lower() == 'yes',
                        duration         = row.get('duration', '').strip(),
                        seat_availability= int(row.get('seat_availability', 30) or 30),
                        total_seats      = int(row.get('total_seats', 40) or 40),
                        is_active        = True,
                    )
                    db.session.add(b)
                except Exception as e:
                    print(f"Skipping pvt row: {e}")
        db.session.commit()
        print("Private buses seeded.")


def initialize_database():
    try:
        db.create_all()
        create_admin_user()

        # Seed buses only if tables are empty (avoids duplicate seeding)
        if LocalBus.query.count() == 0 and PrivateOperator.query.count() == 0:
            seed_buses()
            print("Bus data seeded successfully.")
        else:
            print(f"Bus data already present: {LocalBus.query.count()} govt, {PrivateOperator.query.count()} private.")

        return True
    except Exception as e:
        print(f"DB init error: {e}")
        import traceback
        traceback.print_exc()
        return False


with app.app_context():
    initialize_database()
