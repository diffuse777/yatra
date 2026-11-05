"""Load persisted LSTM artifacts and run real-time inference on latest operational data."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
import torch

from .lstm_model import build_lstm_model
from .preprocessing import (
    RESULTS_PARENT,
    TARGET_COLUMN,
    create_sequences,
    format_ist_timestamp,
    inverse_target,
    now_ist_floor_hour,
    resolve_training_dataframe,
    transform_features,
    ensure_operational_demand,
)

RESULTS_DIR = RESULTS_PARENT


def has_trained_lstm() -> bool:
    meta = os.path.join(RESULTS_DIR, "metadata.json")
    model_pt = os.path.join(RESULTS_DIR, "lstm_model.pt")
    return os.path.isfile(meta) and os.path.isfile(model_pt)


def _read_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_lstm_results() -> Dict[str, Any]:
    """Return saved metrics/history/metadata (training artifacts). Does not retrain."""
    if not has_trained_lstm():
        return {
            "trained": False,
            "metrics": None,
            "comparison": [],
            "forecast": [],
            "history": None,
            "metadata": None,
        }

    metrics = _read_json(os.path.join(RESULTS_DIR, "metrics.json")) or {}
    predictions = _read_json(os.path.join(RESULTS_DIR, "predictions.json")) or {}
    history = _read_json(os.path.join(RESULTS_DIR, "training_history.json"))
    metadata = _read_json(os.path.join(RESULTS_DIR, "metadata.json")) or {}

    return {
        "trained": True,
        "metrics": metrics,
        "comparison": predictions.get("comparison", []),
        "forecast": predictions.get("forecast", []),
        "history": history,
        "metadata": metadata,
    }


def _load_model_and_scaler():
    model_path = os.path.join(RESULTS_DIR, "lstm_model.pt")
    scaler_path = os.path.join(RESULTS_DIR, "scaler.joblib")
    if not os.path.isfile(model_path) or not os.path.isfile(scaler_path):
        raise FileNotFoundError("Trained LSTM model/scaler not found. Train the model first.")

    bundle = torch.load(model_path, map_location="cpu", weights_only=False)
    scaler_bundle = joblib.load(scaler_path)
    scaler = scaler_bundle["scaler"]
    feature_cols = list(scaler_bundle["feature_columns"])
    target_index = int(scaler_bundle["target_index"])
    cfg = bundle.get("config") or {}
    n_features = int(bundle.get("n_features") or len(feature_cols))

    model = build_lstm_model(n_features=n_features, config=cfg)
    model.load_state_dict(bundle["state_dict"])
    model.eval()
    return model, scaler, feature_cols, target_index, cfg


def run_realtime_inference(session=None, recent_hours: int = 48, forecast_horizon: int = 24) -> Dict[str, Any]:
    """
    Real-time inference using the saved model + latest operational demand series.

    Returns:
      - live_comparison: last N hours Actual vs Predicted (where actual exists)
      - live_forecast: next forecast_horizon hours from "now"
      - generated_at: UTC timestamp of this inference run
    """
    if not has_trained_lstm():
        return {
            "ok": False,
            "error": "Model not trained yet.",
            "generated_at": format_ist_timestamp(now_ist_floor_hour()),
            "timezone": "Asia/Kolkata",
            "live_comparison": [],
            "live_forecast": [],
        }

    # Keep operational series aligned to the current IST hour
    if session is not None:
        ensure_operational_demand(session, days=90, force_refresh=False)

    model, scaler, feature_cols, target_index, cfg = _load_model_and_scaler()
    seq_len = int(cfg.get("sequence_length", 24))
    horizon = int(forecast_horizon or cfg.get("forecast_horizon", 24))

    df, data_meta = resolve_training_dataframe(session=session)
    for col in feature_cols:
        if col not in df.columns:
            df[col] = 0.0

    scaled = transform_features(df, scaler, feature_cols)
    X, y, ts = create_sequences(scaled, df["timestamp"].values, target_index, seq_len)

    with torch.no_grad():
        y_pred_scaled = model(torch.tensor(X, dtype=torch.float32)).cpu().numpy().ravel()

    y_true = inverse_target(y, scaler, target_index, len(feature_cols))
    y_pred = np.maximum(inverse_target(y_pred_scaled, scaler, target_index, len(feature_cols)), 0.0)

    comparison_all: List[Dict[str, Any]] = []
    for i in range(len(y_true)):
        comparison_all.append(
            {
                "timestamp": format_ist_timestamp(ts[i]),
                "actual_demand": round(float(y_true[i]), 2),
                "predicted_demand": round(float(y_pred[i]), 2),
            }
        )

    live_comparison = comparison_all[-int(recent_hours) :] if recent_hours else comparison_all

    window = scaled[-seq_len:].copy()
    last_ts = pd.Timestamp(df["timestamp"].iloc[-1]).to_pydatetime()
    now_hour = now_ist_floor_hour()
    # Forecast starts from the next hour after the later of series-end / current IST hour
    forecast_anchor = max(last_ts, now_hour)

    forecast_scaled = []
    for step in range(horizon):
        with torch.no_grad():
            x_in = torch.tensor(window.reshape(1, seq_len, len(feature_cols)), dtype=torch.float32)
            next_scaled_demand = float(model(x_in).cpu().numpy().ravel()[0])
        forecast_scaled.append(next_scaled_demand)

        next_ts = forecast_anchor + timedelta(hours=step + 1)
        raw_next = {col: 0.0 for col in feature_cols}
        raw_next[TARGET_COLUMN] = float(
            inverse_target([next_scaled_demand], scaler, target_index, len(feature_cols))[0]
        )
        if "hour" in feature_cols:
            raw_next["hour"] = float(next_ts.hour)
        if "day_of_week" in feature_cols:
            raw_next["day_of_week"] = float(next_ts.weekday())
        if "month" in feature_cols:
            raw_next["month"] = float(next_ts.month)
        if "is_holiday" in feature_cols:
            raw_next["is_holiday"] = 0.0
        if "weather_code" in feature_cols:
            raw_next["weather_code"] = float(window[-1][feature_cols.index("weather_code")])

        next_scaled_row = scaler.transform(
            np.array([[raw_next[c] for c in feature_cols]], dtype=float)
        )[0]
        window = np.vstack([window[1:], next_scaled_row])

    forecast_demand = np.maximum(
        inverse_target(np.asarray(forecast_scaled), scaler, target_index, len(feature_cols)),
        0.0,
    )
    live_forecast = [
        {
            "timestamp": format_ist_timestamp(forecast_anchor + timedelta(hours=i + 1)),
            "forecasted_demand": round(float(val), 2),
        }
        for i, val in enumerate(forecast_demand)
    ]

    return {
        "ok": True,
        "generated_at": format_ist_timestamp(datetime.now()),
        "timezone": "Asia/Kolkata",
        "data_source": data_meta.get("data_source"),
        "dataset_name": data_meta.get("dataset_name"),
        "is_synthetic": bool(data_meta.get("is_synthetic")),
        "series_end": format_ist_timestamp(last_ts),
        "now_ist": format_ist_timestamp(now_hour),
        "live_comparison": live_comparison,
        "live_forecast": live_forecast,
        "recent_hours": int(recent_hours),
        "forecast_horizon": horizon,
    }
