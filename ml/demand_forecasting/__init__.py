"""Passenger demand forecasting (LSTM, XGBoost, Hybrid stacking)."""

from .train_lstm import train_lstm_pipeline, RESULTS_DIR
from .predict_lstm import load_lstm_results, has_trained_lstm, run_realtime_inference
from .train_xgboost import train_xgboost_pipeline, RESULTS_DIR as XGB_RESULTS_DIR
from .predict_xgboost import load_xgboost_results, has_trained_xgboost, run_realtime_xgboost_inference
from .train_hybrid import train_hybrid_pipeline, RESULTS_DIR as HYBRID_RESULTS_DIR
from .predict_hybrid import (
    load_hybrid_results,
    has_trained_hybrid,
    get_hybrid_forecast_service,
)

__all__ = [
    "train_lstm_pipeline",
    "load_lstm_results",
    "has_trained_lstm",
    "run_realtime_inference",
    "RESULTS_DIR",
    "train_xgboost_pipeline",
    "load_xgboost_results",
    "has_trained_xgboost",
    "run_realtime_xgboost_inference",
    "XGB_RESULTS_DIR",
    "train_hybrid_pipeline",
    "load_hybrid_results",
    "has_trained_hybrid",
    "get_hybrid_forecast_service",
    "HYBRID_RESULTS_DIR",
]
