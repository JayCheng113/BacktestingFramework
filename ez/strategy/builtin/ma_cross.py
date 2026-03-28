"""MA Crossover strategy — reference implementation for agents.

[EXTENSION] — freely modifiable.
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import MA
from ez.strategy.base import Strategy


class MACrossStrategy(Strategy):
    """Buy when short MA crosses above long MA, sell when it crosses below."""

    def __init__(self, short_period: int = 5, long_period: int = 20):
        self.short_period = short_period
        self.long_period = long_period

    @classmethod
    def get_description(cls) -> str:
        return "均线交叉策略: 短期均线上穿长期均线时买入，下穿时卖出。趋势跟踪型策略。"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "short_period": {"type": "int", "default": 5, "min": 2, "max": 60, "label": "Short MA"},
            "long_period": {"type": "int", "default": 20, "min": 5, "max": 250, "label": "Long MA"},
        }

    def required_factors(self) -> list[Factor]:
        return [MA(period=self.short_period), MA(period=self.long_period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        short_col = f"ma_{self.short_period}"
        long_col = f"ma_{self.long_period}"
        return (data[short_col] > data[long_col]).astype(float)
