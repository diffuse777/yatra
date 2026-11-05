"""Load hybrid artifacts and expose a reusable forecast service interface."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd

from .train_hybrid import RESULTS_DIR, ROUTE_LABEL, XGB_NUMERIC


def has_trained_hybrid() -> bool:
    info = os.path.join(RESULTS_DIR, "training_information.json")
    meta = os.path.join(RESULTS_DIR, "meta_model.joblib")
    return os.path.isfile(info) and os.path.isfile(meta)


def _read_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_hybrid_results() -> Dict[str, Any]:
    """Return persisted hybrid metrics, comparison, predictions, and metadata. Does not retrain."""
    if not has_trained_hybrid():
        return {
            "trained": False,
            "metrics": None,
            "comparison": None,
            "predictions": [],
            "metadata": None,
        }

    metrics = _read_json(os.path.join(RESULTS_DIR, "metrics.json")) or {}
    comparison = _read_json(os.path.join(RESULTS_DIR, "comparison.json")) or {}
    metadata = _read_json(os.path.join(RESULTS_DIR, "training_information.json")) or {}

    predictions: List[dict] = []
    pred_path = os.path.join(RESULTS_DIR, "predictions.csv")
    if os.path.isfile(pred_path):
        try:
            predictions = pd.read_csv(pred_path).to_dict(orient="records")
        except Exception:
            predictions = []

    return {
        "trained": True,
        "metrics": metrics,
        "comparison": comparison,
        "predictions": predictions,
        "metadata": metadata,
    }


def get_hybrid_forecast_service() -> Dict[str, Any]:
    """
    Clean reusable interface for future Crowd AI / NSGA-II consumers.

    Returns the latest hybrid test predictions and metrics without retraining.
    Does not implement NSGA-II.
    """
    results = load_hybrid_results()
    if not results.get("trained"):
        return {
            "ok": False,
            "error": "Hybrid model not trained yet. Train from Admin → Models → Hybrid.",
            "predictions": [],
            "metrics": None,
        }
    return {
        "ok": True,
        "model": "hybrid_lstm_xgboost",
        "route_label": ROUTE_LABEL,
        "metrics": (results.get("metrics") or {}).get("hybrid"),
        "comparison_metrics": results.get("metrics"),
        "predictions": results.get("predictions") or [],
        "best": (results.get("comparison") or {}).get("best"),
        "artifacts_dir": RESULTS_DIR,
    }


def load_hybrid_bundle() -> Dict[str, Any]:
    """Load meta-model bundle + base branch weights for offline reuse."""
    if not has_trained_hybrid():
        raise FileNotFoundError("Hybrid artifacts missing. Train the hybrid model first.")

    bundle = joblib.load(os.path.join(RESULTS_DIR, "meta_model.joblib"))
    scaler_bundle = joblib.load(os.path.join(RESULTS_DIR, "lstm_scaler.joblib"))

    lstm_path = os.path.join(RESULTS_DIR, "lstm_branch.pt")
    xgb_json = os.path.join(RESULTS_DIR, "xgboost_branch.json")
    xgb_joblib = os.path.join(RESULTS_DIR, "xgboost_branch.joblib")

    import torch
    from .lstm_model import build_lstm_model
    from .xgboost_model import build_xgb_regressor

    lstm_ckpt = torch.load(lstm_path, map_location="cpu", weights_only=False)
    lstm = build_lstm_model(
        n_features=int(lstm_ckpt["n_features"]),
        config=lstm_ckpt.get("config"),
    )
    lstm.load_state_dict(lstm_ckpt["state_dict"])
    lstm.eval()

    if os.path.isfile(xgb_json):
        xgb = build_xgb_regressor(bundle.get("config"))
        xgb.load_model(xgb_json)
    elif os.path.isfile(xgb_joblib):
        xgb = joblib.load(xgb_joblib)
    else:
        raise FileNotFoundError("Hybrid XGBoost branch artifact not found.")

    return {
        "meta": bundle["meta_model"],
        "meta_features": bundle["meta_features"],
        "xgb_feature_columns": bundle.get("xgb_feature_columns") or list(XGB_NUMERIC),
        "lstm_feature_columns": bundle["lstm_feature_columns"],
        "sequence_length": int(bundle["sequence_length"]),
        "scaler": scaler_bundle["scaler"],
        "target_index": scaler_bundle["target_index"],
        "lstm": lstm,
        "xgb": xgb,
        "config": bundle.get("config") or {},
    }
