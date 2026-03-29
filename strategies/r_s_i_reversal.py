"""User strategy: RSIReversal"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import MA
from ez.strategy.base import Strategy


class RSIReversal(Strategy):
    """Custom trading strategy"""

    def __init__(self, period: int = 20):
        self.period = period

    @classmethod
    def get_description(cls) -> str:
        return "Custom trading strategy"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "period": {"type": "int", "default": 20, "min": 5, "max": 120, "label": "Period"},
        }

    def required_factors(self) -> list[Factor]:
        return [MA(period=self.period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        col = f"ma_{self.period}"
        return (data["adj_close"] > data[col]).astype(float)
