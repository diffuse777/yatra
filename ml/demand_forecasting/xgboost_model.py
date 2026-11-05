"""XGBoost model configuration and factory for passenger demand regression."""

from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_XGB_CONFIG: Dict[str, Any] = {
    "n_estimators": 250,
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.85,
    "colsample_bytree": 0.85,
    "min_child_weight": 3,
    "reg_alpha": 0.05,
    "reg_lambda": 1.0,
    "early_stopping_rounds": 25,
    "random_state": 42,
}


def build_xgb_regressor(config: Optional[Dict[str, Any]] = None):
    """Create an XGBRegressor with configurable hyperparameters."""
    try:
        from xgboost import XGBRegressor
    except ImportError as e:
        raise ImportError(
            "xgboost is not installed. Run: pip install xgboost"
        ) from e

    cfg = {**DEFAULT_XGB_CONFIG, **(config or {})}
    return XGBRegressor(
        n_estimators=int(cfg["n_estimators"]),
        max_depth=int(cfg["max_depth"]),
        learning_rate=float(cfg["learning_rate"]),
        subsample=float(cfg["subsample"]),
        colsample_bytree=float(cfg["colsample_bytree"]),
        min_child_weight=float(cfg["min_child_weight"]),
        reg_alpha=float(cfg["reg_alpha"]),
        reg_lambda=float(cfg["reg_lambda"]),
        objective="reg:squarederror",
        tree_method="hist",
        random_state=int(cfg["random_state"]),
        n_jobs=2,
    )
