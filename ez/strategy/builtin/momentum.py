"""Time-series momentum strategy.

[EXTENSION] — freely modifiable.

Buy when N-day return is positive (uptrend), sell when negative.
Based on Moskowitz, Ooi, Pedersen (2012) — one of the most robust anomalies in finance.
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import Momentum
from ez.strategy.base import Strategy


class MomentumStrategy(Strategy):
    """Go long when recent momentum is positive, flat when negative."""

    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "lookback": {"type": "int", "default": 20, "min": 5, "max": 120, "label": "Lookback Days"},
        }

    def required_factors(self) -> list[Factor]:
        return [Momentum(period=self.lookback)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        col = f"momentum_{self.lookback}"
        return (data[col] > 0).astype(float)
