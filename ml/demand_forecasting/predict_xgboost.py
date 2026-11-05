"""Load persisted XGBoost artifacts and run real-time inference on latest operational trips."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd

from .preprocessing import format_ist_timestamp, now_ist_floor_hour
from .train_xgboost import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    RESULTS_DIR,
    TARGET,
    build_structured_demand_frame,
)
from .xgboost_model import build_xgb_regressor


def has_trained_xgboost() -> bool:
    info = os.path.join(RESULTS_DIR, "training_information.json")
    model_json = os.path.join(RESULTS_DIR, "xgboost_model.json")
    model_joblib = os.path.join(RESULTS_DIR, "xgboost_model.joblib")
    return os.path.isfile(info) and (os.path.isfile(model_json) or os.path.isfile(model_joblib))


def _read_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_xgboost_results() -> Dict[str, Any]:
    """Return saved metrics, predictions, feature importance, and metadata. Does not retrain."""
    if not has_trained_xgboost():
        return {
            "trained": False,
            "metrics": None,
            "comparison": [],
            "feature_importance": [],
            "metadata": None,
        }

    metrics = _read_json(os.path.join(RESULTS_DIR, "metrics.json")) or {}
    importance_payload = _read_json(os.path.join(RESULTS_DIR, "feature_importance.json")) or {}
    metadata = _read_json(os.path.join(RESULTS_DIR, "training_information.json")) or {}

    predictions_path = os.path.join(RESULTS_DIR, "predictions.csv")
    comparison: List[dict] = []
    if os.path.isfile(predictions_path):
        try:
            df = pd.read_csv(predictions_path)
            comparison = df.to_dict(orient="records")
        except Exception:
            comparison = []

    return {
        "trained": True,
        "metrics": metrics,
        "comparison": comparison,
        "feature_importance": importance_payload.get("features") or [],
        "importance_type": importance_payload.get("importance_type") or "gain",
        "metadata": metadata,
    }


def _load_model_and_preprocessor():
    prep_path = os.path.join(RESULTS_DIR, "preprocessor.joblib")
    model_json = os.path.join(RESULTS_DIR, "xgboost_model.json")
    model_joblib = os.path.join(RESULTS_DIR, "xgboost_model.joblib")
    if not os.path.isfile(prep_path):
        raise FileNotFoundError("XGBoost preprocessor not found. Train the model first.")

    bundle = joblib.load(prep_path)
    preprocessor = bundle["preprocessor"]
    feature_cols = list(bundle.get("feature_columns") or (NUMERIC_FEATURES + CATEGORICAL_FEATURES))
    cfg = bundle.get("config") or {}

    if os.path.isfile(model_json):
        model = build_xgb_regressor(cfg)
        model.load_model(model_json)
    elif os.path.isfile(model_joblib):
        model = joblib.load(model_joblib)
    else:
        raise FileNotFoundError("XGBoost model artifact not found. Train the model first.")

    return model, preprocessor, feature_cols, cfg


def run_realtime_xgboost_inference(
    session=None,
    recent_hours: int = 24,
    forecast_hours: int = 24,
) -> Dict[str, Any]:
    """
    Real-time XGBoost inference on a freshly built operational trip table ending at current IST.

    Returns:
      - live_comparison: recent trips (actual vs predicted) up to now
      - live_forecast: upcoming scheduled trips after now (predicted demand only)
    """
    if not has_trained_xgboost():
        return {
            "ok": False,
            "error": "Model not trained yet.",
            "generated_at": format_ist_timestamp(datetime.now()),
            "timezone": "Asia/Kolkata",
            "live_comparison": [],
            "live_forecast": [],
        }

    model, preprocessor, feature_cols, _cfg = _load_model_and_preprocessor()
    df, data_meta = build_structured_demand_frame(session=session, days=60)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    now_h = now_ist_floor_hour()
    recent_start = now_h - timedelta(hours=int(recent_hours))
    forecast_end = now_h + timedelta(hours=int(forecast_hours))

    for col in feature_cols:
        if col not in df.columns:
            df[col] = 0.0

    # Recent window with known actuals (up to current IST hour)
    recent_df = df[(df["timestamp"] >= recent_start) & (df["timestamp"] <= now_h)].copy()
    recent_df = recent_df.sort_values("timestamp").reset_index(drop=True)

    live_comparison: List[Dict[str, Any]] = []
    if len(recent_df) > 0:
        X_recent = preprocessor.transform(recent_df[feature_cols])
        y_pred = np.maximum(model.predict(X_recent), 0.0)
        y_true = recent_df[TARGET].astype(float).values
        for i in range(len(recent_df)):
            actual = float(y_true[i])
            pred = float(y_pred[i])
            live_comparison.append(
                {
                    "timestamp": format_ist_timestamp(recent_df["timestamp"].iloc[i]),
                    "route": str(recent_df["route_key"].iloc[i]),
                    "actual_demand": round(actual, 2),
                    "predicted_demand": round(pred, 2),
                    "error": round(abs(actual - pred), 2),
                }
            )

    # Upcoming scheduled trips after now → forecast only
    # Rebuild one extra day beyond frame end if needed for near-future slots
    future_df = df[(df["timestamp"] > now_h) & (df["timestamp"] <= forecast_end)].copy()
    if future_df.empty:
        # Extend one day forward from templates already in frame pattern:
        # take same clock-time trips from "today" rows and shift +1 day as forecast candidates
        today_mask = df["timestamp"].dt.date == now_h.date()
        today_df = df.loc[today_mask].copy()
        if len(today_df):
            today_df = today_df.copy()
            today_df["timestamp"] = today_df["timestamp"] + pd.Timedelta(days=1)
            # Recompute temporal fields for shifted day
            today_df["day_of_week"] = today_df["timestamp"].dt.weekday.astype(float)
            today_df["month"] = today_df["timestamp"].dt.month.astype(float)
            today_df["is_weekend"] = (today_df["day_of_week"] >= 5).astype(float)
            future_df = today_df[
                (today_df["timestamp"] > now_h) & (today_df["timestamp"] <= forecast_end)
            ].copy()

    future_df = future_df.sort_values("timestamp").reset_index(drop=True)
    live_forecast: List[Dict[str, Any]] = []
    if len(future_df) > 0:
        # For forecast rows, refresh lags from latest known history where possible
        X_future = preprocessor.transform(future_df[feature_cols])
        y_f = np.maximum(model.predict(X_future), 0.0)
        for i in range(len(future_df)):
            live_forecast.append(
                {
                    "timestamp": format_ist_timestamp(future_df["timestamp"].iloc[i]),
                    "route": str(future_df["route_key"].iloc[i]),
                    "forecasted_demand": round(float(y_f[i]), 2),
                }
            )

    return {
        "ok": True,
        "generated_at": format_ist_timestamp(datetime.now()),
        "timezone": "Asia/Kolkata",
        "now_ist": format_ist_timestamp(now_h),
        "data_source": data_meta.get("data_source"),
        "dataset_name": data_meta.get("dataset_name"),
        "is_synthetic": bool(data_meta.get("is_synthetic")),
        "live_comparison": live_comparison,
        "live_forecast": live_forecast,
        "recent_hours": int(recent_hours),
        "forecast_hours": int(forecast_hours),
    }
