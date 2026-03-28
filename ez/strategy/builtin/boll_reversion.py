"""Bollinger Band mean-reversion strategy.

[EXTENSION] — freely modifiable.

Buy when price drops below lower band (oversold), sell when price rises above upper band (overbought).
Between bands, hold current position.
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import BOLL
from ez.strategy.base import Strategy


class BollReversionStrategy(Strategy):
    """Mean reversion: buy at lower band, sell at upper band."""

    def __init__(self, period: int = 20, std_dev: float = 2.0):
        self.period = period
        self.std_dev = std_dev

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "period": {"type": "int", "default": 20, "min": 5, "max": 120, "label": "BOLL Period"},
            "std_dev": {"type": "float", "default": 2.0, "min": 0.5, "max": 4.0, "label": "Std Dev"},
        }

    def required_factors(self) -> list[Factor]:
        return [BOLL(period=self.period, std_dev=self.std_dev)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        upper = data[f"boll_upper_{self.period}"]
        lower = data[f"boll_lower_{self.period}"]
        close = data["adj_close"]

        # Build position: 1.0 when below lower (buy), 0.0 when above upper (sell), hold between
        signals = pd.Series(index=data.index, dtype=float)
        position = 0.0
        for i in range(len(data)):
            if pd.notna(lower.iloc[i]) and pd.notna(upper.iloc[i]):
                if close.iloc[i] < lower.iloc[i]:
                    position = 1.0  # buy: oversold
                elif close.iloc[i] > upper.iloc[i]:
                    position = 0.0  # sell: overbought
                # else: hold current position
            signals.iloc[i] = position
        return signals
