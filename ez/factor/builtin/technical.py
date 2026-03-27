"""Built-in technical indicators.

[EXTENSION] — freely modifiable. Add new indicators here.
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor


class MA(Factor):
    """Simple Moving Average."""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"ma_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        data[self.name] = data["adj_close"].rolling(window=self._period, min_periods=self._period).mean()
        return data


class EMA(Factor):
    """Exponential Moving Average."""

    def __init__(self, period: int = 12):
        self._period = period

    @property
    def name(self) -> str:
        return f"ema_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        data[self.name] = data["adj_close"].ewm(span=self._period, min_periods=self._period).mean()
        return data


class RSI(Factor):
    """Relative Strength Index."""

    def __init__(self, period: int = 14):
        self._period = period

    @property
    def name(self) -> str:
        return f"rsi_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period + 1

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        delta = data["adj_close"].diff()
        gain = delta.clip(lower=0).rolling(window=self._period, min_periods=self._period).mean()
        loss = (-delta.clip(upper=0)).rolling(window=self._period, min_periods=self._period).mean()
        rs = gain / loss.replace(0, float("nan"))
        data[self.name] = 100 - (100 / (1 + rs))
        return data


class MACD(Factor):
    """Moving Average Convergence Divergence."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self._fast = fast
        self._slow = slow
        self._signal = signal

    @property
    def name(self) -> str:
        return "macd"

    @property
    def warmup_period(self) -> int:
        return self._slow + self._signal

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        ema_fast = data["adj_close"].ewm(span=self._fast, min_periods=self._fast).mean()
        ema_slow = data["adj_close"].ewm(span=self._slow, min_periods=self._slow).mean()
        data["macd_line"] = ema_fast - ema_slow
        data["macd_signal"] = data["macd_line"].ewm(span=self._signal, min_periods=self._signal).mean()
        data["macd_hist"] = data["macd_line"] - data["macd_signal"]
        return data


class BOLL(Factor):
    """Bollinger Bands."""

    def __init__(self, period: int = 20, std_dev: float = 2.0):
        self._period = period
        self._std_dev = std_dev

    @property
    def name(self) -> str:
        return f"boll_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        mid = data["adj_close"].rolling(window=self._period, min_periods=self._period).mean()
        std = data["adj_close"].rolling(window=self._period, min_periods=self._period).std()
        data[f"boll_mid_{self._period}"] = mid
        data[f"boll_upper_{self._period}"] = mid + self._std_dev * std
        data[f"boll_lower_{self._period}"] = mid - self._std_dev * std
        return data
