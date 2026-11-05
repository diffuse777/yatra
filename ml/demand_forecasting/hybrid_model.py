"""Hybrid stacking configuration: LSTM + XGBoost → meta-model → final demand."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sklearn.linear_model import LinearRegression, Ridge

DEFAULT_HYBRID_CONFIG: Dict[str, Any] = {
    # Chronological partitions of the aligned hourly series
    "train_ratio": 0.70,
    "val_ratio": 0.15,
    # Remaining ~0.15 is the held-out test set
    "sequence_length": 24,
    # LSTM branch (trained inside hybrid on the shared split)
    "lstm_units_1": 64,
    "lstm_units_2": 32,
    "dropout": 0.2,
    "lstm_epochs": 25,
    "lstm_batch_size": 32,
    "lstm_learning_rate": 0.001,
    "lstm_early_stopping_patience": 5,
    "lstm_internal_val_ratio": 0.1,
    # XGBoost branch (tabular features on the same hourly rows)
    "n_estimators": 200,
    "max_depth": 5,
    "learning_rate": 0.05,
    "subsample": 0.85,
    "colsample_bytree": 0.85,
    "min_child_weight": 3,
    "reg_alpha": 0.05,
    "reg_lambda": 1.0,
    "xgb_early_stopping_rounds": 20,
    # Meta-model
    "meta_model_type": "Ridge",
    "meta_alpha": 1.0,
    "meta_features": [
        "lstm_pred",
        "xgb_pred",
        "hour",
        "day_of_week",
        "is_holiday",
    ],
}


def build_meta_model(config: Optional[Dict[str, Any]] = None):
    """
    Stacking meta-regressor.

    Ridge is preferred over plain OLS for small validation sets (mild L2
    regularization). LinearRegression is used if meta_model_type requests it.
    """
    cfg = {**DEFAULT_HYBRID_CONFIG, **(config or {})}
    kind = str(cfg.get("meta_model_type", "Ridge")).strip().lower()
    if kind in ("linear", "linearregression", "ols"):
        return LinearRegression()
    return Ridge(alpha=float(cfg.get("meta_alpha", 1.0)))


def meta_feature_names(config: Optional[Dict[str, Any]] = None) -> list:
    cfg = {**DEFAULT_HYBRID_CONFIG, **(config or {})}
    return list(cfg.get("meta_features") or DEFAULT_HYBRID_CONFIG["meta_features"])
