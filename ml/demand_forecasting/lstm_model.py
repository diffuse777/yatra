"""Configurable PyTorch LSTM architecture for passenger demand forecasting.

TensorFlow is not available on some Python versions (e.g. 3.14); PyTorch provides
an equivalent real LSTM implementation compatible with this environment.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import torch
import torch.nn as nn


DEFAULT_LSTM_CONFIG: Dict[str, Any] = {
    "sequence_length": 24,
    "lstm_units_1": 64,
    "lstm_units_2": 32,
    "dropout": 0.2,
    "batch_size": 32,
    "epochs": 40,
    "learning_rate": 0.001,
    "validation_split": 0.1,
    "early_stopping_patience": 6,
    "forecast_horizon": 24,
}


class DemandLSTM(nn.Module):
    """
    Input → LSTM → Dropout → LSTM → Dense → Demand Prediction
    """

    def __init__(self, n_features: int, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        cfg = {**DEFAULT_LSTM_CONFIG, **(config or {})}
        self.lstm1 = nn.LSTM(
            input_size=n_features,
            hidden_size=int(cfg["lstm_units_1"]),
            batch_first=True,
        )
        self.drop1 = nn.Dropout(float(cfg["dropout"]))
        self.lstm2 = nn.LSTM(
            input_size=int(cfg["lstm_units_1"]),
            hidden_size=int(cfg["lstm_units_2"]),
            batch_first=True,
        )
        self.drop2 = nn.Dropout(float(cfg["dropout"]))
        self.head = nn.Sequential(
            nn.Linear(int(cfg["lstm_units_2"]), 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm1(x)
        out = self.drop1(out)
        out, _ = self.lstm2(out)
        out = self.drop2(out[:, -1, :])
        return self.head(out).squeeze(-1)


def build_lstm_model(n_features: int, config: Optional[Dict[str, Any]] = None) -> DemandLSTM:
    return DemandLSTM(n_features=n_features, config=config)
