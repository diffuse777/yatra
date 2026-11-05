"""
Seed script — loads govt_buses.csv and private_buses.csv into the database.
Run once: python seed_buses.py
"""
import csv
import os
import sys
from datetime import datetime

# ── Bootstrap Flask app context ──────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from app import app, db, LocalBus, PrivateOperator

BASE_DIR  = os.path.join(os.path.dirname(__file__), 'data')
GOVT_CSV  = os.path.join(BASE_DIR, 'govt_buses.csv')
PRIV_CSV  = os.path.join(BASE_DIR, 'private_buses.csv')


def parse_time(s):
    return datetime.strptime(s.strip(), '%H:%M').time()


def load_csv(filepath):
    with open(filepath, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


with app.app_context():
    # ── wipe existing bus data (re-seed cleanly) ─────────────────────────────
    deleted_local   = LocalBus.query.delete()
    deleted_private = PrivateOperator.query.delete()
    db.session.commit()
    print(f'Cleared {deleted_local} govt bus rows, {deleted_private} private bus rows.')

    added_local = added_private = 0
    errors = []

    # ── Government buses ─────────────────────────────────────────────────────
    for i, row in enumerate(load_csv(GOVT_CSV), start=2):
        try:
            via_raw = row.get('via_stops', '').strip()
            via     = [s.strip() for s in via_raw.split(';') if s.strip()]
            entry   = LocalBus(
                bus_number       = row['bus_number'].strip(),
                route_number     = row['route_number'].strip(),
                operator         = row.get('operator', 'TNSTC').strip(),
                origin           = row['origin'].strip(),
                destination      = row['destination'].strip(),
                departure_time   = parse_time(row['departure_time']),
                arrival_time     = parse_time(row['arrival_time']),
                via_stops        = via,
                fare             = float(row.get('fare') or 0),
                bus_type         = row.get('bus_type', 'Ordinary').strip(),
                status           = row.get('status', 'scheduled').strip(),
                seat_availability= int(row.get('seat_availability') or 50),
                total_seats      = int(row.get('total_seats') or 50),
                is_active        = True,
            )
            db.session.add(entry)
            added_local += 1
        except Exception as e:
            errors.append(f'Govt row {i}: {e}')

    # ── Private buses ─────────────────────────────────────────────────────────
    for i, row in enumerate(load_csv(PRIV_CSV), start=2):
        try:
            via_raw  = row.get('via_stops', '').strip()
            via      = [s.strip() for s in via_raw.split(';') if s.strip()]
            origin   = row['origin'].strip()
            dest     = row['destination'].strip()
            rname    = row.get('route_name', '').strip() or f'{origin} – {dest}'
            entry    = PrivateOperator(
                operator_name    = row.get('operator', '').strip(),
                bus_number       = row['bus_number'].strip(),
                route_name       = rname,
                origin           = origin,
                destination      = dest,
                departure_time   = parse_time(row['departure_time']),
                arrival_time     = parse_time(row['arrival_time']),
                via_stops        = via,
                fare             = float(row.get('fare') or 0),
                bus_type         = row.get('bus_type', 'AC Seater').strip(),
                status           = row.get('status', 'available').strip(),
                rating           = float(row.get('rating') or 0),
                live_tracking    = (row.get('live_tracking', 'no').strip().lower() == 'yes'),
                duration         = row.get('duration', '').strip(),
                seat_availability= int(row.get('seat_availability') or 40),
                total_seats      = int(row.get('total_seats') or 40),
                is_active        = True,
            )
            db.session.add(entry)
            added_private += 1
        except Exception as e:
            errors.append(f'Private row {i}: {e}')

    db.session.commit()
    print(f'\n✅ Seeded {added_local} government bus entries  (TNSTC / SETC)')
    print(f'✅ Seeded {added_private} private bus entries  (KPN / SRS / VRL / Orange / Parveen …)')
    if errors:
        print('\n⚠️  Errors:')
        for e in errors:
            print('  -', e)
    else:
        print('\nNo errors. All rows loaded successfully.')
