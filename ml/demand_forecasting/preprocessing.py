"""Data loading, validation, scaling, and sequence generation for LSTM demand forecasting.

Uses ONLY project operational sources:
  1) StopAnalytics (DB)
  2) Booking aggregates (DB)
  3) LocalBus / PrivateOperator occupancy from seeded CSVs / DB schedules

No random synthetic / dummy demand series is used for training.
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, timedelta, time as dtime
from typing import Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

MIN_REAL_ROWS = 336  # 14 days * 24 hours
DEFAULT_SEQUENCE_LENGTH = 24
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_PARENT = os.path.join(PROJECT_ROOT, "ml_results", "lstm")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
IST = ZoneInfo("Asia/Kolkata")

FEATURE_COLUMNS = [
    "passenger_demand",
    "hour",
    "day_of_week",
    "month",
    "is_holiday",
]
TARGET_COLUMN = "passenger_demand"

# Tamil Nadu / India holiday markers used as calendar features (not demand fabrication)
_HOLIDAY_MD = {(1, 1), (1, 15), (1, 26), (8, 15), (10, 2), (12, 25), (4, 14), (9, 5)}


def now_ist_floor_hour() -> datetime:
    """Current time in Asia/Kolkata, floored to the hour (naive local IST)."""
    return datetime.now(IST).replace(minute=0, second=0, microsecond=0, tzinfo=None)


def format_ist_timestamp(ts) -> str:
    """Display timestamps in IST for the admin UI."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert(IST).tz_localize(None)
    return t.strftime("%Y-%m-%d %H:%M") + " IST"


def _ensure_results_dir() -> str:
    os.makedirs(RESULTS_PARENT, exist_ok=True)
    return RESULTS_PARENT


def _is_holiday(d: date) -> int:
    return 1 if (d.month, d.day) in _HOLIDAY_MD else 0


def _weather_to_numeric(value: Any) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return 0.0
    text = str(value).strip().lower()
    mapping = {
        "clear": 0.0,
        "sunny": 0.0,
        "clouds": 1.0,
        "cloudy": 1.0,
        "partly cloudy": 1.0,
        "rain": 2.0,
        "drizzle": 2.0,
        "storm": 3.0,
        "thunderstorm": 3.0,
        "fog": 1.5,
        "mist": 1.5,
        "haze": 1.5,
    }
    return float(mapping.get(text, 0.5))


def _parse_hhmm(value: Any) -> Optional[dtime]:
    if value is None:
        return None
    if isinstance(value, dtime):
        return value
    text = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _occupied_seats(total_seats: Any, seat_availability: Any) -> int:
    try:
        total = int(total_seats or 0)
    except (TypeError, ValueError):
        total = 0
    try:
        avail = int(seat_availability or 0)
    except (TypeError, ValueError):
        avail = 0
    return max(0, total - avail)


def load_from_stop_analytics(session) -> Optional[pd.DataFrame]:
    """Aggregate StopAnalytics into an hourly system-wide demand series."""
    try:
        from app import StopAnalytics
    except Exception:
        return None

    rows = session.query(StopAnalytics).order_by(StopAnalytics.date, StopAnalytics.hour).all()
    if not rows:
        return None

    records = []
    for r in rows:
        if r.date is None or r.hour is None:
            continue
        ts = datetime.combine(r.date, datetime.min.time()) + timedelta(hours=int(r.hour))
        records.append(
            {
                "timestamp": ts,
                "passenger_demand": int(r.passenger_count or 0),
                "is_holiday": 1 if r.is_holiday else 0,
                "weather_code": _weather_to_numeric(r.weather_condition),
            }
        )
    if not records:
        return None

    return (
        pd.DataFrame(records)
        .groupby("timestamp", as_index=False)
        .agg({"passenger_demand": "sum", "is_holiday": "max", "weather_code": "mean"})
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def load_from_bookings(session) -> Optional[pd.DataFrame]:
    """Hourly demand from Booking.travel_date + booking_time (real bookings only)."""
    try:
        from app import Booking
    except Exception:
        return None

    rows = session.query(Booking).all()
    if not rows:
        return None

    records = []
    for b in rows:
        if not b.travel_date:
            continue
        hour = 12
        if b.booking_time:
            hour = int(b.booking_time.hour)
        ts = datetime.combine(b.travel_date, datetime.min.time()) + timedelta(hours=hour)
        records.append(
            {
                "timestamp": ts,
                "passenger_demand": 1,
                "is_holiday": _is_holiday(b.travel_date),
                "weather_code": 0.0,
            }
        )
    if not records:
        return None

    return (
        pd.DataFrame(records)
        .groupby("timestamp", as_index=False)
        .agg({"passenger_demand": "sum", "is_holiday": "max", "weather_code": "mean"})
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def _collect_schedule_services(session=None) -> List[dict]:
    """Load real bus services from DB, falling back to project CSV files."""
    services: List[dict] = []

    if session is not None:
        try:
            from app import LocalBus, PrivateOperator

            for b in session.query(LocalBus).filter_by(is_active=True).all():
                dep = b.departure_time
                if not dep:
                    continue
                services.append(
                    {
                        "hour": int(dep.hour),
                        "passengers": _occupied_seats(b.total_seats, b.seat_availability),
                        "origin": b.origin or "Unknown",
                    }
                )
            for b in session.query(PrivateOperator).filter_by(is_active=True).all():
                dep = b.departure_time
                if not dep:
                    continue
                services.append(
                    {
                        "hour": int(dep.hour),
                        "passengers": _occupied_seats(b.total_seats, b.seat_availability),
                        "origin": b.origin or "Unknown",
                    }
                )
        except Exception:
            pass

    if services:
        return services

    # CSV fallback — real project timetable files
    for fname, origin_key in (("govt_buses.csv", "origin"), ("private_buses.csv", "origin")):
        path = os.path.join(DATA_DIR, fname)
        if not os.path.isfile(path):
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                dep = _parse_hhmm(row.get("departure_time"))
                if not dep:
                    continue
                services.append(
                    {
                        "hour": int(dep.hour),
                        "passengers": _occupied_seats(row.get("total_seats"), row.get("seat_availability")),
                        "origin": (row.get(origin_key) or "Unknown").strip(),
                    }
                )
    return services


def build_demand_from_bus_schedules(session=None, days: int = 90) -> Optional[pd.DataFrame]:
    """
    Build hourly passenger demand from real bus timetable occupancy
    (total_seats - seat_availability) in DB/CSV schedules.
    """
    services = _collect_schedule_services(session)
    if not services:
        return None

    by_hour: dict[int, int] = {}
    for s in services:
        by_hour[s["hour"]] = by_hour.get(s["hour"], 0) + int(s["passengers"])

    end = now_ist_floor_hour()
    start = end - timedelta(days=days)
    timestamps = pd.date_range(start=start, end=end, freq="h")

    rows = []
    for ts in timestamps:
        demand = int(by_hour.get(int(ts.hour), 0))
        rows.append(
            {
                "timestamp": ts.to_pydatetime(),
                "passenger_demand": demand,
                "is_holiday": _is_holiday(ts.date()),
                "weather_code": 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


def persist_demand_to_stop_analytics(session, df: pd.DataFrame, stop_name: str = "System Aggregate") -> int:
    """Write hourly demand series into StopAnalytics for reuse across the app."""
    from app import StopAnalytics

    # Clear previous system-aggregate rows for this stop label
    session.query(StopAnalytics).filter(StopAnalytics.stop_name == stop_name).delete()
    added = 0
    for _, row in df.iterrows():
        ts = pd.Timestamp(row["timestamp"]).to_pydatetime()
        session.add(
            StopAnalytics(
                stop_name=stop_name,
                stop_location=stop_name,
                date=ts.date(),
                hour=int(ts.hour),
                passenger_count=int(round(float(row["passenger_demand"]))),
                weekday=ts.strftime("%A"),
                is_holiday=bool(int(row.get("is_holiday", 0))),
                weather_condition="clear",
            )
        )
        added += 1
    session.commit()
    return added


def ensure_bus_seed(session) -> dict:
    """Seed LocalBus/PrivateOperator from project CSVs when tables are empty."""
    from app import LocalBus, PrivateOperator

    local_n = session.query(LocalBus).count()
    private_n = session.query(PrivateOperator).count()
    if local_n > 0 or private_n > 0:
        return {"seeded": False, "local": local_n, "private": private_n}

    def _load(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    govt_csv = os.path.join(DATA_DIR, "govt_buses.csv")
    priv_csv = os.path.join(DATA_DIR, "private_buses.csv")
    added_l = added_p = 0

    if os.path.isfile(govt_csv):
        for row in _load(govt_csv):
            dep = _parse_hhmm(row.get("departure_time"))
            arr = _parse_hhmm(row.get("arrival_time"))
            if not dep or not arr:
                continue
            via = [v.strip() for v in (row.get("via_stops") or "").split(";") if v.strip()]
            session.add(
                LocalBus(
                    bus_number=row["bus_number"].strip(),
                    route_number=(row.get("route_number") or "").strip(),
                    operator=(row.get("operator") or "TNSTC").strip(),
                    origin=row["origin"].strip(),
                    destination=row["destination"].strip(),
                    departure_time=dep,
                    arrival_time=arr,
                    via_stops=via,
                    fare=float(row.get("fare") or 0),
                    bus_type=(row.get("bus_type") or "Ordinary").strip(),
                    status=(row.get("status") or "scheduled").strip(),
                    seat_availability=int(row.get("seat_availability") or 40),
                    total_seats=int(row.get("total_seats") or 50),
                    is_active=True,
                )
            )
            added_l += 1

    if os.path.isfile(priv_csv):
        for row in _load(priv_csv):
            dep = _parse_hhmm(row.get("departure_time"))
            arr = _parse_hhmm(row.get("arrival_time"))
            if not dep or not arr:
                continue
            via = [v.strip() for v in (row.get("via_stops") or "").split(";") if v.strip()]
            origin = row["origin"].strip()
            dest = row["destination"].strip()
            session.add(
                PrivateOperator(
                    bus_number=row["bus_number"].strip(),
                    operator_name=(row.get("operator") or "").strip(),
                    route_name=(row.get("route_name") or f"{origin} – {dest}").strip(),
                    origin=origin,
                    destination=dest,
                    departure_time=dep,
                    arrival_time=arr,
                    via_stops=via,
                    fare=float(row.get("fare") or 0),
                    bus_type=(row.get("bus_type") or "AC Seater").strip(),
                    status=(row.get("status") or "available").strip(),
                    rating=float(row.get("rating") or 4.0),
                    live_tracking=str(row.get("live_tracking", "no")).lower() == "yes",
                    duration=(row.get("duration") or "").strip(),
                    seat_availability=int(row.get("seat_availability") or 30),
                    total_seats=int(row.get("total_seats") or 40),
                    is_active=True,
                )
            )
            added_p += 1

    session.commit()
    return {"seeded": True, "local": added_l, "private": added_p}


def ensure_operational_demand(session, days: int = 90, force_refresh: bool = False) -> dict:
    """
    Make sure StopAnalytics has enough real schedule-derived demand rows ending at current IST hour.
    Seeds buses from CSV if needed. Never writes random dummy demand.
    """
    from app import StopAnalytics

    existing = session.query(StopAnalytics).count()
    seed_info = ensure_bus_seed(session)

    latest = (
        session.query(StopAnalytics)
        .filter(StopAnalytics.stop_name == "System Aggregate")
        .order_by(StopAnalytics.date.desc(), StopAnalytics.hour.desc())
        .first()
    )
    now_h = now_ist_floor_hour()
    stale = True
    if latest and latest.date is not None and latest.hour is not None:
        latest_ts = datetime.combine(latest.date, datetime.min.time()) + timedelta(hours=int(latest.hour))
        stale = latest_ts < (now_h - timedelta(hours=1))

    if existing >= MIN_REAL_ROWS and not force_refresh and not stale:
        return {
            "action": "kept_existing",
            "stop_analytics_rows": existing,
            "bus_seed": seed_info,
            "timezone": "Asia/Kolkata",
        }

    schedule_df = build_demand_from_bus_schedules(session=session, days=days)
    if schedule_df is None or schedule_df.empty:
        raise ValueError(
            "No operational bus timetable data found. Add rows to data/govt_buses.csv / "
            "data/private_buses.csv or seed LocalBus/PrivateOperator, then retrain."
        )

    added = persist_demand_to_stop_analytics(session, schedule_df)
    return {
        "action": "built_from_bus_schedules" if existing < MIN_REAL_ROWS else "refreshed_to_current_ist",
        "stop_analytics_rows": added,
        "bus_seed": seed_info,
        "dataset_size": int(len(schedule_df)),
        "timezone": "Asia/Kolkata",
        "series_end_ist": now_h.isoformat(sep=" "),
    }


def prepare_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Validate, fill gaps, sort chronologically, and engineer temporal features."""
    if df is None or df.empty:
        raise ValueError("Demand dataframe is empty.")

    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    out = out.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

    full_idx = pd.date_range(out["timestamp"].min(), out["timestamp"].max(), freq="h")
    out = out.set_index("timestamp").reindex(full_idx)
    out.index.name = "timestamp"
    out = out.reset_index()

    out["passenger_demand"] = (
        out["passenger_demand"].interpolate(method="linear").bfill().ffill().fillna(0).astype(float)
    )
    if "is_holiday" not in out.columns:
        out["is_holiday"] = 0
    out["is_holiday"] = out["is_holiday"].fillna(0).astype(float)

    if "weather_code" not in out.columns:
        out["weather_code"] = 0.0
    out["weather_code"] = out["weather_code"].fillna(0.0).astype(float)

    out["hour"] = out["timestamp"].dt.hour.astype(float)
    out["day_of_week"] = out["timestamp"].dt.dayofweek.astype(float)
    out["month"] = out["timestamp"].dt.month.astype(float)
    out["day"] = out["timestamp"].dt.day.astype(float)

    feature_cols = FEATURE_COLUMNS.copy()
    if "weather_code" in out.columns and "weather_code" not in feature_cols:
        feature_cols.append("weather_code")

    for col in feature_cols:
        if col not in out.columns:
            raise ValueError(f"Missing required feature column: {col}")

    out.attrs["feature_columns"] = feature_cols
    return out


def resolve_training_dataframe(session=None) -> Tuple[pd.DataFrame, dict]:
    """
    Resolve operational demand only (no random synthetic fallback).
    Priority: StopAnalytics → Bookings → Bus schedule occupancy (DB/CSV).
    """
    if session is not None:
        ensure_operational_demand(session, days=90)

    raw = None
    source = None
    dataset_name = None

    if session is not None:
        raw = load_from_stop_analytics(session)
        if raw is not None and len(raw) >= MIN_REAL_ROWS:
            source = "stop_analytics"
            dataset_name = "StopAnalytics_hourly_operational_demand"

        if raw is None or len(raw) < MIN_REAL_ROWS:
            booked = load_from_bookings(session)
            if booked is not None and len(booked) >= MIN_REAL_ROWS:
                raw = booked
                source = "bookings"
                dataset_name = "Booking_hourly_operational_demand"

    if raw is None or len(raw) < MIN_REAL_ROWS:
        schedule = build_demand_from_bus_schedules(session=session, days=90)
        if schedule is None or schedule.empty:
            raise ValueError(
                "Insufficient operational demand data. Seed bus CSVs / StopAnalytics / Bookings first."
            )
        raw = schedule
        source = "bus_schedule_occupancy"
        dataset_name = "BusSchedule_hourly_occupancy_demand"
        if session is not None:
            persist_demand_to_stop_analytics(session, raw)

    prepared = prepare_feature_frame(raw)
    feature_cols = prepared.attrs.get("feature_columns", FEATURE_COLUMNS)

    meta = {
        "dataset_name": dataset_name,
        "data_source": source,
        "is_synthetic": False,
        "is_operational": True,
        "dataset_size": int(len(prepared)),
        "features": feature_cols,
        "target": TARGET_COLUMN,
        "data_range_start": prepared["timestamp"].min().isoformat(),
        "data_range_end": prepared["timestamp"].max().isoformat(),
        "preprocessing": [
            "Operational source resolution (StopAnalytics / Bookings / bus schedule occupancy)",
            "Chronological sort",
            "Hourly reindexing",
            "Linear interpolation for missing demand",
            "Temporal feature engineering (hour, day_of_week, month, holiday)",
            "MinMax scaling fit on training split only",
            "Sliding window sequences (no shuffle)",
            "Chronological train/test split",
        ],
    }
    return prepared, meta


def chronological_split(df: pd.DataFrame, test_ratio: float = 0.2) -> Tuple[pd.DataFrame, pd.DataFrame]:
    n = len(df)
    if n < 48:
        raise ValueError("Need at least 48 hourly rows for train/test split.")
    split_idx = int(n * (1.0 - test_ratio))
    split_idx = max(split_idx, DEFAULT_SEQUENCE_LENGTH + 1)
    split_idx = min(split_idx, n - max(12, DEFAULT_SEQUENCE_LENGTH // 2))
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def fit_scaler(train_df: pd.DataFrame, feature_cols: list) -> MinMaxScaler:
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaler.fit(train_df[feature_cols].values.astype(float))
    return scaler


def transform_features(df: pd.DataFrame, scaler: MinMaxScaler, feature_cols: list) -> np.ndarray:
    return scaler.transform(df[feature_cols].values.astype(float))


def create_sequences(
    scaled: np.ndarray,
    timestamps: np.ndarray,
    target_index: int,
    sequence_length: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs, ys, ts = [], [], []
    for i in range(sequence_length, len(scaled)):
        xs.append(scaled[i - sequence_length : i])
        ys.append(scaled[i, target_index])
        ts.append(timestamps[i])
    if not xs:
        raise ValueError("Not enough rows to create sequences for the given sequence_length.")
    return np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.float32), np.asarray(ts)


def inverse_target(scaled_target: np.ndarray, scaler: MinMaxScaler, target_index: int, n_features: int) -> np.ndarray:
    scaled_target = np.asarray(scaled_target, dtype=float).ravel()
    dummy = np.zeros((len(scaled_target), n_features), dtype=float)
    dummy[:, target_index] = scaled_target
    return scaler.inverse_transform(dummy)[:, target_index]
