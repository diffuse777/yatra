"""Train Hybrid LSTM + XGBoost demand forecasting with stacking meta-model.

Architecture (implemented exactly as documented in metadata):

  Historical hourly operational demand (same series as LSTM)
                │
    ┌───────────┴───────────┐
    ▼                       ▼
  LSTM sequences      XGBoost tabular features
    │                       │
    ▼                       ▼
  LSTM prediction     XGBoost prediction
    │                       │
    └───────────┬───────────┘
                ▼
     Meta-model (Ridge / LinearRegression)
     inputs: lstm_pred, xgb_pred, hour, day_of_week, is_holiday
                │
                ▼
        Final hybrid prediction

Leakage prevention:
  - Chronological 70% / 15% / 15% train / validation / test split (no shuffle).
  - Scaler / XGB preprocessor fit on train only.
  - Base models trained on train only.
  - Meta-model trained ONLY on validation-period base predictions.
  - Test targets never enter meta-model fitting.
  - Lag features are past-only (shift).

Standalone LSTM / XGBoost pages remain untouched. Hybrid saves its own
branch artifacts under ml_results/hybrid/ because the standalone XGBoost
model is trip-level and is not row-compatible with the hourly LSTM series.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, TensorDataset

from .hybrid_model import DEFAULT_HYBRID_CONFIG, build_meta_model, meta_feature_names
from .lstm_model import build_lstm_model
from .metrics import compute_all_metrics
from .preprocessing import (
    TARGET_COLUMN,
    fit_scaler,
    format_ist_timestamp,
    inverse_target,
    resolve_training_dataframe,
    transform_features,
)
from .xgboost_model import build_xgb_regressor

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_DIR = os.path.join(PROJECT_ROOT, "ml_results", "hybrid")
ROUTE_LABEL = "System-wide"

XGB_NUMERIC = [
    "hour",
    "day_of_week",
    "month",
    "is_weekend",
    "is_holiday",
    "lag_1",
    "lag_24",
    "rolling_mean_3",
]


def _ensure_results_dir() -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    return RESULTS_DIR


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, (np.floating, np.float32, np.float64)):
        v = float(obj)
        if np.isnan(v) or np.isinf(v):
            return None
        return v
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


def _add_tabular_features(df: pd.DataFrame) -> pd.DataFrame:
    """Past-only lag / rolling features on the shared hourly series."""
    out = df.copy().sort_values("timestamp").reset_index(drop=True)
    out["is_weekend"] = (out["day_of_week"] >= 5).astype(float)
    out["lag_1"] = out[TARGET_COLUMN].shift(1)
    out["lag_24"] = out[TARGET_COLUMN].shift(24)
    out["rolling_mean_3"] = out[TARGET_COLUMN].shift(1).rolling(window=3, min_periods=1).mean()
    out = out.dropna(subset=["lag_1"]).reset_index(drop=True)
    out["lag_24"] = out["lag_24"].fillna(out["lag_1"])
    out["rolling_mean_3"] = out["rolling_mean_3"].fillna(out["lag_1"])
    return out


def _chronological_three_way(
    df: pd.DataFrame,
    train_ratio: float,
    val_ratio: float,
    seq_len: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    n = len(df)
    if n < seq_len + 80:
        raise ValueError(
            f"Need at least {seq_len + 80} hourly rows for hybrid train/val/test; got {n}."
        )
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))
    train_end = max(seq_len + 40, min(train_end, n - 40))
    val_end = max(train_end + 20, min(val_end, n - 20))
    train_df = df.iloc[:train_end].copy()
    val_df = df.iloc[train_end:val_end].copy()
    test_df = df.iloc[val_end:].copy()
    periods = {
        "train_start": format_ist_timestamp(train_df["timestamp"].iloc[0]),
        "train_end": format_ist_timestamp(train_df["timestamp"].iloc[-1]),
        "validation_start": format_ist_timestamp(val_df["timestamp"].iloc[0]),
        "validation_end": format_ist_timestamp(val_df["timestamp"].iloc[-1]),
        "test_start": format_ist_timestamp(test_df["timestamp"].iloc[0]),
        "test_end": format_ist_timestamp(test_df["timestamp"].iloc[-1]),
        "train_rows": int(len(train_df)),
        "validation_rows": int(len(val_df)),
        "test_rows": int(len(test_df)),
    }
    return train_df, val_df, test_df, periods


def _train_lstm_branch(
    full_df: pd.DataFrame,
    train_df: pd.DataFrame,
    feature_cols: List[str],
    cfg: Dict[str, Any],
) -> Tuple[Any, Any, int]:
    """Train LSTM on train period only; scaler fit on train only."""
    device = torch.device("cpu")
    seq_len = int(cfg["sequence_length"])
    scaler = fit_scaler(train_df, feature_cols)
    target_index = feature_cols.index(TARGET_COLUMN)

    train_scaled = transform_features(train_df, scaler, feature_cols)
    X_all, y_all = [], []
    for i in range(seq_len, len(train_scaled)):
        X_all.append(train_scaled[i - seq_len : i])
        y_all.append(train_scaled[i, target_index])
    X_all = np.asarray(X_all, dtype=np.float32)
    y_all = np.asarray(y_all, dtype=np.float32)
    if len(X_all) < 20:
        raise ValueError("Insufficient LSTM training sequences in the hybrid train period.")

    # Internal chronological early-stopping slice from end of train (not meta-val)
    n = len(X_all)
    val_n = max(5, int(n * float(cfg["lstm_internal_val_ratio"])))
    val_n = min(val_n, n // 5)
    X_fit, y_fit = X_all[:-val_n], y_all[:-val_n]
    X_es, y_es = X_all[-val_n:], y_all[-val_n:]

    loader = DataLoader(
        TensorDataset(
            torch.tensor(X_fit, dtype=torch.float32),
            torch.tensor(y_fit, dtype=torch.float32),
        ),
        batch_size=int(cfg["lstm_batch_size"]),
        shuffle=False,
    )

    lstm_cfg = {
        "sequence_length": seq_len,
        "lstm_units_1": cfg["lstm_units_1"],
        "lstm_units_2": cfg["lstm_units_2"],
        "dropout": cfg["dropout"],
    }
    model = build_lstm_model(n_features=len(feature_cols), config=lstm_cfg).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(cfg["lstm_learning_rate"]))
    criterion = torch.nn.MSELoss()
    best_val = float("inf")
    best_state = None
    patience = int(cfg["lstm_early_stopping_patience"])

    es_x = torch.tensor(X_es, dtype=torch.float32)
    es_y = torch.tensor(y_es, dtype=torch.float32)

    for _ in range(int(cfg["lstm_epochs"])):
        model.train()
        for xb, yb in loader:
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            vpred = model(es_x)
            vloss = float(criterion(vpred, es_y).item())
        if vloss + 1e-8 < best_val:
            best_val = vloss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience = int(cfg["lstm_early_stopping_patience"])
        else:
            patience -= 1
            if patience <= 0:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    return model, scaler, target_index


def _lstm_predict_indices(
    model,
    scaler,
    feature_cols: List[str],
    target_index: int,
    full_df: pd.DataFrame,
    indices: np.ndarray,
    seq_len: int,
) -> np.ndarray:
    """Predict demand for absolute row indices using preceding seq_len hours."""
    device = torch.device("cpu")
    scaled = transform_features(full_df, scaler, feature_cols)
    preds = []
    model.eval()
    with torch.no_grad():
        for idx in indices:
            idx = int(idx)
            if idx < seq_len:
                preds.append(np.nan)
                continue
            window = scaled[idx - seq_len : idx]
            x = torch.tensor(window.reshape(1, seq_len, len(feature_cols)), dtype=torch.float32)
            scaled_pred = float(model(x).cpu().numpy().ravel()[0])
            demand = float(
                inverse_target([scaled_pred], scaler, target_index, len(feature_cols))[0]
            )
            preds.append(max(0.0, demand))
    return np.asarray(preds, dtype=float)


def _train_xgb_branch(
    train_df: pd.DataFrame,
    cfg: Dict[str, Any],
):
    feature_cols = list(XGB_NUMERIC)
    X_train = train_df[feature_cols].astype(float).values
    y_train = train_df[TARGET_COLUMN].astype(float).values

    n = len(X_train)
    val_n = max(10, int(n * 0.1))
    val_n = min(val_n, max(10, n // 5))
    X_fit, y_fit = X_train[:-val_n], y_train[:-val_n]
    X_es, y_es = X_train[-val_n:], y_train[-val_n:]

    xgb_cfg = {
        "n_estimators": cfg["n_estimators"],
        "max_depth": cfg["max_depth"],
        "learning_rate": cfg["learning_rate"],
        "subsample": cfg["subsample"],
        "colsample_bytree": cfg["colsample_bytree"],
        "min_child_weight": cfg["min_child_weight"],
        "reg_alpha": cfg["reg_alpha"],
        "reg_lambda": cfg["reg_lambda"],
        "early_stopping_rounds": cfg["xgb_early_stopping_rounds"],
        "random_state": 42,
    }
    model = build_xgb_regressor(xgb_cfg)
    fit_kwargs: Dict[str, Any] = {}
    try:
        model.set_params(early_stopping_rounds=int(cfg["xgb_early_stopping_rounds"]))
        fit_kwargs["eval_set"] = [(X_es, y_es)]
        fit_kwargs["verbose"] = False
    except Exception:
        fit_kwargs = {"verbose": False}
    try:
        model.fit(X_fit, y_fit, **fit_kwargs)
    except TypeError:
        model.fit(X_fit, y_fit)
    return model, feature_cols


def _xgb_predict(model, feature_cols: List[str], df: pd.DataFrame) -> np.ndarray:
    X = df[feature_cols].astype(float).values
    return np.maximum(model.predict(X), 0.0)


def _build_meta_matrix(
    lstm_pred: np.ndarray,
    xgb_pred: np.ndarray,
    df: pd.DataFrame,
    feature_names: List[str],
) -> np.ndarray:
    cols = {
        "lstm_pred": lstm_pred,
        "xgb_pred": xgb_pred,
        "hour": df["hour"].astype(float).values,
        "day_of_week": df["day_of_week"].astype(float).values,
        "is_holiday": df["is_holiday"].astype(float).values,
    }
    return np.column_stack([cols[name] for name in feature_names])


def _pick_best_model(comparison: Dict[str, dict]) -> Dict[str, Any]:
    """Choose best by lowest MAE; ties broken by RMSE then higher R²."""
    order = ["lstm", "xgboost", "hybrid"]
    best = None
    best_key = None
    for key in order:
        m = comparison.get(key) or {}
        mae = m.get("mae")
        if mae is None or (isinstance(mae, float) and np.isnan(mae)):
            continue
        cand = (float(mae), float(m.get("rmse") or 1e9), -float(m.get("r2") if m.get("r2") == m.get("r2") else -1e9))
        if best is None or cand < best:
            best = cand
            best_key = key
    labels = {"lstm": "LSTM", "xgboost": "XGBoost", "hybrid": "Hybrid"}
    return {
        "best_model": labels.get(best_key, "—") if best_key else "—",
        "best_key": best_key,
        "criterion": "Lowest test MAE (then RMSE, then highest R2)",
    }


def _hybrid_delta(base: dict, hybrid: dict, metric: str) -> Optional[float]:
    if not base or not hybrid:
        return None
    b, h = base.get(metric), hybrid.get(metric)
    if b is None or h is None:
        return None
    if metric == "r2":
        return round(float(h) - float(b), 4)
    return round(float(b) - float(h), 4)  # positive = hybrid improved (error reduced)


def train_hybrid_pipeline(session=None, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Full hybrid stacking pipeline. Does not modify ml_results/lstm or ml_results/xgboost.
    """
    cfg = {**DEFAULT_HYBRID_CONFIG, **(config or {})}
    out_dir = _ensure_results_dir()
    seq_len = int(cfg["sequence_length"])
    meta_feats = meta_feature_names(cfg)

    # ── 1. Shared operational hourly dataset (compatible with LSTM) ─────────
    raw_df, data_meta = resolve_training_dataframe(session=session)
    lstm_feature_cols = list(data_meta["features"])
    if TARGET_COLUMN not in lstm_feature_cols:
        lstm_feature_cols = [TARGET_COLUMN] + [c for c in lstm_feature_cols if c != TARGET_COLUMN]

    df = _add_tabular_features(raw_df)
    # After lag drop, re-align indices for absolute positioning
    df = df.reset_index(drop=True)

    train_df, val_df, test_df, periods = _chronological_three_way(
        df,
        train_ratio=float(cfg["train_ratio"]),
        val_ratio=float(cfg["val_ratio"]),
        seq_len=seq_len,
    )

    train_idx = train_df.index.to_numpy()
    val_idx = val_df.index.to_numpy()
    test_idx = test_df.index.to_numpy()

    # LSTM needs seq_len history; keep val/test rows that have enough history in full df
    val_ok = val_idx[val_idx >= seq_len]
    test_ok = test_idx[test_idx >= seq_len]
    if len(val_ok) < 15:
        raise ValueError("Hybrid validation period too small after sequence alignment.")
    if len(test_ok) < 15:
        raise ValueError("Hybrid test period too small after sequence alignment.")

    val_df_aligned = df.loc[val_ok].copy()
    test_df_aligned = df.loc[test_ok].copy()

    # ── 2. Train base models on train period only ───────────────────────────
    lstm_model, scaler, target_index = _train_lstm_branch(
        full_df=df,
        train_df=train_df,
        feature_cols=lstm_feature_cols,
        cfg=cfg,
    )
    xgb_model, xgb_feature_cols = _train_xgb_branch(train_df, cfg)

    # ── 3. Validation predictions → meta-model (no test leakage) ────────────
    lstm_val = _lstm_predict_indices(
        lstm_model, scaler, lstm_feature_cols, target_index, df, val_ok, seq_len
    )
    xgb_val = _xgb_predict(xgb_model, xgb_feature_cols, val_df_aligned)
    y_val = val_df_aligned[TARGET_COLUMN].astype(float).values

    mask_val = np.isfinite(lstm_val) & np.isfinite(xgb_val)
    if int(mask_val.sum()) < 10:
        raise ValueError("Not enough valid validation predictions to train the meta-model.")

    X_meta_val = _build_meta_matrix(lstm_val[mask_val], xgb_val[mask_val], val_df_aligned.iloc[mask_val], meta_feats)
    meta_model = build_meta_model(cfg)
    meta_model.fit(X_meta_val, y_val[mask_val])

    # ── 4. Test predictions ─────────────────────────────────────────────────
    lstm_test = _lstm_predict_indices(
        lstm_model, scaler, lstm_feature_cols, target_index, df, test_ok, seq_len
    )
    xgb_test = _xgb_predict(xgb_model, xgb_feature_cols, test_df_aligned)
    y_test = test_df_aligned[TARGET_COLUMN].astype(float).values

    mask_test = np.isfinite(lstm_test) & np.isfinite(xgb_test)
    lstm_test = lstm_test[mask_test]
    xgb_test = xgb_test[mask_test]
    y_test = y_test[mask_test]
    test_df_final = test_df_aligned.iloc[mask_test].copy()

    X_meta_test = _build_meta_matrix(lstm_test, xgb_test, test_df_final, meta_feats)
    hybrid_test = np.maximum(meta_model.predict(X_meta_test), 0.0)

    metrics_lstm = compute_all_metrics(y_test, lstm_test)
    metrics_xgb = compute_all_metrics(y_test, xgb_test)
    metrics_hybrid = compute_all_metrics(y_test, hybrid_test)

    comparison_metrics = {
        "lstm": metrics_lstm,
        "xgboost": metrics_xgb,
        "hybrid": metrics_hybrid,
    }
    best = _pick_best_model(comparison_metrics)

    # Improvement of hybrid vs each base (positive = better for error metrics)
    improvement = {
        "vs_lstm": {
            "mae": _hybrid_delta(metrics_lstm, metrics_hybrid, "mae"),
            "rmse": _hybrid_delta(metrics_lstm, metrics_hybrid, "rmse"),
            "mape": _hybrid_delta(metrics_lstm, metrics_hybrid, "mape"),
            "r2": _hybrid_delta(metrics_lstm, metrics_hybrid, "r2"),
        },
        "vs_xgboost": {
            "mae": _hybrid_delta(metrics_xgb, metrics_hybrid, "mae"),
            "rmse": _hybrid_delta(metrics_xgb, metrics_hybrid, "rmse"),
            "mape": _hybrid_delta(metrics_xgb, metrics_hybrid, "mape"),
            "r2": _hybrid_delta(metrics_xgb, metrics_hybrid, "r2"),
        },
        "note": (
            "For MAE/RMSE/MAPE, positive delta means hybrid error is lower (improved). "
            "For R², positive delta means hybrid R² is higher (improved)."
        ),
    }

    # ── 5. Prediction table ─────────────────────────────────────────────────
    rows: List[dict] = []
    for i in range(len(y_test)):
        actual = float(y_test[i])
        lp = float(lstm_test[i])
        xp = float(xgb_test[i])
        hp = float(hybrid_test[i])
        rows.append(
            {
                "timestamp": format_ist_timestamp(test_df_final["timestamp"].iloc[i]),
                "route": ROUTE_LABEL,
                "actual_demand": round(actual, 2),
                "lstm_prediction": round(lp, 2),
                "xgboost_prediction": round(xp, 2),
                "hybrid_prediction": round(hp, 2),
                "lstm_error": round(abs(actual - lp), 2),
                "xgboost_error": round(abs(actual - xp), 2),
                "hybrid_error": round(abs(actual - hp), 2),
            }
        )

    insights = _build_insights(comparison_metrics, best, improvement, rows, data_meta, periods)

    # ── 6. Persist artifacts ────────────────────────────────────────────────
    lstm_path = os.path.join(out_dir, "lstm_branch.pt")
    xgb_path = os.path.join(out_dir, "xgboost_branch.json")
    meta_path = os.path.join(out_dir, "meta_model.joblib")
    scaler_path = os.path.join(out_dir, "lstm_scaler.joblib")
    metrics_path = os.path.join(out_dir, "metrics.json")
    comparison_path = os.path.join(out_dir, "comparison.json")
    predictions_path = os.path.join(out_dir, "predictions.csv")
    info_path = os.path.join(out_dir, "training_information.json")
    dataset_csv = os.path.join(out_dir, "aligned_hourly_dataset.csv")

    torch.save(
        {
            "state_dict": lstm_model.state_dict(),
            "n_features": len(lstm_feature_cols),
            "config": {
                "sequence_length": seq_len,
                "lstm_units_1": cfg["lstm_units_1"],
                "lstm_units_2": cfg["lstm_units_2"],
                "dropout": cfg["dropout"],
            },
            "feature_columns": lstm_feature_cols,
            "target_index": target_index,
        },
        lstm_path,
    )
    try:
        xgb_model.save_model(xgb_path)
    except Exception:
        xgb_path = os.path.join(out_dir, "xgboost_branch.joblib")
        joblib.dump(xgb_model, xgb_path)

    joblib.dump(
        {
            "meta_model": meta_model,
            "meta_features": meta_feats,
            "meta_model_type": cfg.get("meta_model_type"),
            "xgb_feature_columns": xgb_feature_cols,
            "lstm_feature_columns": lstm_feature_cols,
            "sequence_length": seq_len,
            "route_label": ROUTE_LABEL,
            "config": cfg,
        },
        meta_path,
    )
    joblib.dump(
        {"scaler": scaler, "feature_columns": lstm_feature_cols, "target_index": target_index},
        scaler_path,
    )

    metrics_payload = {
        "lstm": metrics_lstm,
        "xgboost": metrics_xgb,
        "hybrid": metrics_hybrid,
        "evaluated_on": "chronological held-out test period (same rows for all three models)",
    }
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(_recursive_json(metrics_payload), f, indent=2)

    comparison_payload = {
        "metrics": comparison_metrics,
        "best": best,
        "improvement": improvement,
        "chart_series": {
            "labels": ["LSTM", "XGBoost", "Hybrid"],
            "mae": [metrics_lstm["mae"], metrics_xgb["mae"], metrics_hybrid["mae"]],
            "rmse": [metrics_lstm["rmse"], metrics_xgb["rmse"], metrics_hybrid["rmse"]],
            "mape": [metrics_lstm["mape"], metrics_xgb["mape"], metrics_hybrid["mape"]],
            "r2": [metrics_lstm["r2"], metrics_xgb["r2"], metrics_hybrid["r2"]],
        },
    }
    with open(comparison_path, "w", encoding="utf-8") as f:
        json.dump(_recursive_json(comparison_payload), f, indent=2)

    pd.DataFrame(rows).to_csv(predictions_path, index=False)

    export_cols = ["timestamp"] + [c for c in lstm_feature_cols if c in df.columns]
    for c in XGB_NUMERIC:
        if c not in export_cols and c in df.columns:
            export_cols.append(c)
    df[export_cols].to_csv(dataset_csv, index=False)

    metadata = {
        "trained_at": format_ist_timestamp(datetime.now()),
        "timezone": "Asia/Kolkata",
        "model_name": "Hybrid LSTM + XGBoost Demand Forecasting",
        "architecture": (
            "Historical hourly demand → LSTM branch + XGBoost branch → "
            "stacking meta-model → hybrid prediction"
        ),
        "hybrid_method": "stacking_meta_learning",
        "meta_model": {
            "type": str(cfg.get("meta_model_type", "Ridge")),
            "inputs": meta_feats,
            "training_data": "Validation-period base-model predictions only",
            "validation_strategy": (
                "Chronological validation slice after train; meta never sees test targets"
            ),
            "why_selected": (
                "Ridge/LinearRegression is a transparent, low-complexity stacker that learns "
                "how to blend LSTM sequence forecasts with XGBoost structured forecasts "
                "plus light temporal context, without overfitting small validation sets."
            ),
        },
        "base_models": {
            "lstm": {
                "role": "Capture temporal / sequential demand patterns",
                "architecture": "Input → LSTM → Dropout → LSTM → Dense → Demand",
                "parameters": {
                    "sequence_length": seq_len,
                    "lstm_units_1": cfg["lstm_units_1"],
                    "lstm_units_2": cfg["lstm_units_2"],
                    "dropout": cfg["dropout"],
                    "epochs": cfg["lstm_epochs"],
                },
                "input": "Scaled sliding windows of hourly demand + temporal features",
            },
            "xgboost": {
                "role": "Capture structured relationships among temporal / lag features",
                "parameters": {
                    "n_estimators": cfg["n_estimators"],
                    "max_depth": cfg["max_depth"],
                    "learning_rate": cfg["learning_rate"],
                },
                "features": xgb_feature_cols,
                "input": "Tabular hourly features (calendar + past-only lags)",
            },
        },
        "data": {
            **data_meta,
            "dataset_type": "REAL / OPERATIONAL" if not data_meta.get("is_synthetic") else "SYNTHETIC DEMO DATA",
            "alignment_key": "timestamp (hourly system-wide demand)",
            "route_label": ROUTE_LABEL,
            "why_not_standalone_xgboost_artifact": (
                "Standalone Admin→XGBoost uses trip-level BusSchedule_structured_trip_demand "
                "(timestamp+route). Standalone LSTM uses hourly system demand. Those prediction "
                "spaces are incompatible. Hybrid therefore trains both branches on the same "
                "hourly operational series with identical chronological partitions."
            ),
            "periods": periods,
            "xgb_features": xgb_feature_cols,
            "lstm_features": lstm_feature_cols,
        },
        "split_method": (
            f"Chronological {int(cfg['train_ratio']*100)}% train / "
            f"{int(cfg['val_ratio']*100)}% validation / "
            f"{int(round((1-cfg['train_ratio']-cfg['val_ratio'])*100))}% test; no shuffle"
        ),
        "leakage_prevention": [
            "No random shuffle of time series",
            "MinMaxScaler fit on train period only",
            "LSTM and XGBoost trained on train period only",
            "Meta-model trained only on validation-period base predictions",
            "Test targets never used to fit scaler, base models, or meta-model",
            "Lag / rolling features use shift (past only)",
        ],
        "train_samples": int(periods["train_rows"]),
        "validation_samples": int(len(val_ok)),
        "test_samples": int(len(y_test)),
        "config": cfg,
        "artifacts": {
            "lstm_branch": lstm_path,
            "xgboost_branch": xgb_path,
            "meta_model": meta_path,
            "lstm_scaler": scaler_path,
            "metrics": metrics_path,
            "comparison": comparison_path,
            "predictions": predictions_path,
            "dataset": dataset_csv,
        },
        "insights": insights,
        "best": best,
        "improvement": improvement,
        "reusable_interface": "ml.demand_forecasting.predict_hybrid.get_hybrid_forecast_service",
    }
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(_recursive_json(metadata), f, indent=2)

    return {
        "success": True,
        "metrics": metrics_payload,
        "comparison": comparison_payload,
        "metadata": metadata,
        "predictions": rows,
        "results_dir": out_dir,
    }


def _build_insights(comparison, best, improvement, rows, data_meta, periods) -> List[str]:
    insights: List[str] = []
    insights.append(
        f"Hybrid stacking used operational hourly demand ({data_meta.get('dataset_name')}) "
        f"labeled {('SYNTHETIC DEMO DATA' if data_meta.get('is_synthetic') else 'REAL / OPERATIONAL')}."
    )
    insights.append(
        f"Chronological periods — train {periods['train_start']}→{periods['train_end']}; "
        f"validation {periods['validation_start']}→{periods['validation_end']}; "
        f"test {periods['test_start']}→{periods['test_end']}."
    )
    insights.append(
        f"On the shared test set: LSTM MAE={comparison['lstm']['mae']}, "
        f"XGBoost MAE={comparison['xgboost']['mae']}, Hybrid MAE={comparison['hybrid']['mae']}."
    )
    insights.append(f"Best model by test MAE: {best.get('best_model')}.")

    vs_l = improvement.get("vs_lstm") or {}
    vs_x = improvement.get("vs_xgboost") or {}
    if vs_l.get("mae") is not None:
        if vs_l["mae"] > 0:
            insights.append(f"Hybrid reduced MAE vs LSTM by {vs_l['mae']}.")
        elif vs_l["mae"] < 0:
            insights.append(f"Hybrid increased MAE vs LSTM by {abs(vs_l['mae'])} (did not improve LSTM).")
        else:
            insights.append("Hybrid MAE matched LSTM on the test set.")
    if vs_x.get("mae") is not None:
        if vs_x["mae"] > 0:
            insights.append(f"Hybrid reduced MAE vs XGBoost by {vs_x['mae']}.")
        elif vs_x["mae"] < 0:
            insights.append(
                f"Hybrid increased MAE vs XGBoost by {abs(vs_x['mae'])} (did not improve XGBoost)."
            )

    if rows:
        worst = max(rows, key=lambda r: r["hybrid_error"])
        insights.append(
            f"Largest hybrid absolute error on test: {worst['hybrid_error']} at {worst['timestamp']} "
            f"({worst['route']})."
        )
    insights.append(
        "Meta-model was fit only on validation-period base predictions; test labels were never used "
        "for stacking weights."
    )
    return insights
