"""Train LSTM demand forecasting model and persist artifacts under ml_results/lstm/."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import joblib
import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, TensorDataset

from .lstm_model import DEFAULT_LSTM_CONFIG, build_lstm_model
from .metrics import compute_all_metrics
from .preprocessing import (
    RESULTS_PARENT,
    TARGET_COLUMN,
    chronological_split,
    create_sequences,
    fit_scaler,
    inverse_target,
    resolve_training_dataframe,
    transform_features,
    _ensure_results_dir,
)

RESULTS_DIR = RESULTS_PARENT


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    if isinstance(obj, (np.integer, np.int32, np.int64)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (datetime, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj


def _recursive_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _recursive_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_recursive_json(v) for v in obj]
    return _json_safe(obj)


def _chronological_val_split(X, y, val_ratio: float):
    n = len(X)
    if n < 10:
        return X, y, X[:0], y[:0]
    split = int(n * (1.0 - val_ratio))
    split = max(1, min(split, n - 1))
    return X[:split], y[:split], X[split:], y[split:]


def train_lstm_pipeline(session=None, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Full LSTM pipeline. Does not shuffle time series.
    Saves model, scaler, metrics, predictions, history, and metadata.
    """
    cfg = {**DEFAULT_LSTM_CONFIG, **(config or {})}
    out_dir = _ensure_results_dir()
    device = torch.device("cpu")

    df, data_meta = resolve_training_dataframe(session=session)
    feature_cols = list(data_meta["features"])
    if TARGET_COLUMN not in feature_cols:
        feature_cols = [TARGET_COLUMN] + [c for c in feature_cols if c != TARGET_COLUMN]

    train_df, test_df = chronological_split(df, test_ratio=0.2)
    scaler = fit_scaler(train_df, feature_cols)

    train_scaled = transform_features(train_df, scaler, feature_cols)
    test_scaled = transform_features(test_df, scaler, feature_cols)

    target_index = feature_cols.index(TARGET_COLUMN)
    seq_len = int(cfg["sequence_length"])

    X_train, y_train, _ts_train = create_sequences(
        train_scaled, train_df["timestamp"].values, target_index, seq_len
    )
    X_test, y_test, ts_test = create_sequences(
        test_scaled, test_df["timestamp"].values, target_index, seq_len
    )

    X_tr, y_tr, X_val, y_val = _chronological_val_split(
        X_train, y_train, float(cfg["validation_split"])
    )

    train_loader = DataLoader(
        TensorDataset(
            torch.tensor(X_tr, dtype=torch.float32),
            torch.tensor(y_tr, dtype=torch.float32),
        ),
        batch_size=int(cfg["batch_size"]),
        shuffle=False,
    )
    val_x = torch.tensor(X_val, dtype=torch.float32) if len(X_val) else None
    val_y = torch.tensor(y_val, dtype=torch.float32) if len(y_val) else None

    model = build_lstm_model(n_features=len(feature_cols), config=cfg).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(cfg["learning_rate"]))
    criterion = torch.nn.MSELoss()

    history = {"loss": [], "val_loss": [], "mae": [], "val_mae": []}
    best_val = float("inf")
    best_state = None
    patience_left = int(cfg["early_stopping_patience"])

    for _epoch in range(int(cfg["epochs"])):
        model.train()
        batch_losses = []
        batch_maes = []
        for xb, yb in train_loader:
            xb = xb.to(device)
            yb = yb.to(device)
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            optimizer.step()
            batch_losses.append(float(loss.item()))
            batch_maes.append(float(torch.mean(torch.abs(pred.detach() - yb)).item()))

        train_loss = float(np.mean(batch_losses)) if batch_losses else 0.0
        train_mae = float(np.mean(batch_maes)) if batch_maes else 0.0
        history["loss"].append(train_loss)
        history["mae"].append(train_mae)

        if val_x is not None and len(val_x):
            model.eval()
            with torch.no_grad():
                vpred = model(val_x.to(device))
                vloss = float(criterion(vpred, val_y.to(device)).item())
                vmae = float(torch.mean(torch.abs(vpred - val_y.to(device))).item())
            history["val_loss"].append(vloss)
            history["val_mae"].append(vmae)
            if vloss + 1e-8 < best_val:
                best_val = vloss
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                patience_left = int(cfg["early_stopping_patience"])
            else:
                patience_left -= 1
                if patience_left <= 0:
                    break
        else:
            history["val_loss"].append(train_loss)
            history["val_mae"].append(train_mae)
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        y_pred_scaled = model(torch.tensor(X_test, dtype=torch.float32).to(device)).cpu().numpy().ravel()

    y_true = inverse_target(y_test, scaler, target_index, len(feature_cols))
    y_pred = inverse_target(y_pred_scaled, scaler, target_index, len(feature_cols))
    y_pred = np.maximum(y_pred, 0.0)
    metrics = compute_all_metrics(y_true, y_pred)

    # Recursive multi-step forecast
    full_scaled = transform_features(df, scaler, feature_cols)
    window = full_scaled[-seq_len:].copy()
    forecast_horizon = int(cfg["forecast_horizon"])
    forecast_scaled = []
    last_ts = pd.Timestamp(df["timestamp"].iloc[-1])

    model.eval()
    for step in range(forecast_horizon):
        with torch.no_grad():
            x_in = torch.tensor(window.reshape(1, seq_len, len(feature_cols)), dtype=torch.float32)
            next_scaled_demand = float(model(x_in).cpu().numpy().ravel()[0])
        forecast_scaled.append(next_scaled_demand)

        next_ts = last_ts + pd.Timedelta(hours=step + 1)
        raw_next = {col: 0.0 for col in feature_cols}
        raw_next[TARGET_COLUMN] = float(
            inverse_target([next_scaled_demand], scaler, target_index, len(feature_cols))[0]
        )
        if "hour" in feature_cols:
            raw_next["hour"] = float(next_ts.hour)
        if "day_of_week" in feature_cols:
            raw_next["day_of_week"] = float(next_ts.dayofweek)
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

    forecast_demand = inverse_target(
        np.asarray(forecast_scaled), scaler, target_index, len(feature_cols)
    )
    forecast_demand = np.maximum(forecast_demand, 0.0)
    forecast_rows = [
        {
            "timestamp": (last_ts + pd.Timedelta(hours=i + 1)).isoformat(),
            "forecasted_demand": round(float(val), 2),
        }
        for i, val in enumerate(forecast_demand)
    ]

    comparison_rows = [
        {
            "timestamp": pd.Timestamp(ts_test[i]).isoformat(),
            "actual_demand": round(float(y_true[i]), 2),
            "predicted_demand": round(float(y_pred[i]), 2),
        }
        for i in range(len(y_true))
    ]

    model_path = os.path.join(out_dir, "lstm_model.pt")
    scaler_path = os.path.join(out_dir, "scaler.joblib")
    metrics_path = os.path.join(out_dir, "metrics.json")
    predictions_path = os.path.join(out_dir, "predictions.json")
    history_path = os.path.join(out_dir, "training_history.json")
    meta_path = os.path.join(out_dir, "metadata.json")
    dataset_csv = os.path.join(out_dir, "training_series.csv")

    torch.save(
        {
            "state_dict": model.state_dict(),
            "n_features": len(feature_cols),
            "config": cfg,
        },
        model_path,
    )
    joblib.dump(
        {"scaler": scaler, "feature_columns": feature_cols, "target_index": target_index},
        scaler_path,
    )

    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(_recursive_json(metrics), f, indent=2)
    with open(predictions_path, "w", encoding="utf-8") as f:
        json.dump(
            _recursive_json({"comparison": comparison_rows, "forecast": forecast_rows}),
            f,
            indent=2,
        )
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    metadata = {
        "trained_at": datetime.utcnow().isoformat() + "Z",
        "framework": "PyTorch",
        "architecture": "Input → LSTM → Dropout → LSTM → Dense → Demand",
        "config": cfg,
        "data": data_meta,
        "train_samples": int(len(X_train)),
        "test_samples": int(len(X_test)),
        "feature_columns": feature_cols,
        "artifacts": {
            "model": model_path,
            "scaler": scaler_path,
            "metrics": metrics_path,
            "predictions": predictions_path,
            "history": history_path,
        },
        "insights": _build_insights(metrics, comparison_rows, forecast_rows, data_meta),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(_recursive_json(metadata), f, indent=2)

    df[["timestamp"] + feature_cols].to_csv(dataset_csv, index=False)

    return {
        "success": True,
        "metrics": metrics,
        "metadata": metadata,
        "comparison": comparison_rows,
        "forecast": forecast_rows,
        "history": history,
        "results_dir": out_dir,
    }


def _build_insights(metrics, comparison_rows, forecast_rows, data_meta) -> list:
    insights = []
    source = data_meta.get("data_source") or "operational"
    insights.append(
        f"Training used operational project data ({source} → {data_meta.get('dataset_name')}). "
        "No random synthetic demand series was used."
    )

    if metrics.get("mae") is not None:
        insights.append(
            f"On the held-out chronological test window, mean absolute error was {metrics['mae']} passengers."
        )
    if metrics.get("r2") is not None and metrics["r2"] == metrics["r2"]:
        insights.append(
            f"R² on the test set was {metrics['r2']}, indicating how much demand variance the LSTM explained."
        )

    if forecast_rows:
        peak = max(forecast_rows, key=lambda r: r["forecasted_demand"])
        insights.append(
            f"Near-term forecast peak of {peak['forecasted_demand']} passengers at {peak['timestamp']} "
            "can inform extra bus allocation and crowd alerts."
        )

    insights.append(
        "Yatra Saarthi can use these live forecasts in AI Crowd Intelligence and timetable planning "
        "to anticipate peak hours on Tamil Nadu corridors."
    )
    return insights
