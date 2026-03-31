"""Built-in technical indicators.

[EXTENSION] — freely modifiable. Add new indicators here.
"""
from __future__ import annotations

import pandas as pd

from ez.core import ts_ops
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
        data[self.name] = ts_ops.rolling_mean(data["adj_close"], self._period)
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
        data[self.name] = ts_ops.ewm_mean(data["adj_close"], self._period)
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
        delta = ts_ops.diff(data["adj_close"])
        gain = ts_ops.rolling_mean(delta.clip(lower=0), self._period)
        loss = ts_ops.rolling_mean((-delta.clip(upper=0)), self._period)
        # Edge cases: loss=0 (pure up)→RSI=100, gain=0 (pure down)→RSI=0, flat→RSI=50
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        # Preserve warmup NaN, then handle 0/0 and 0/x cases
        warmup_mask = gain.isna() | loss.isna()
        rsi[loss == 0] = 100.0                    # pure uptrend
        rsi[(gain == 0) & (loss == 0)] = 50.0     # flat price = neutral
        rsi[(gain == 0) & (loss > 0)] = 0.0       # pure downtrend
        rsi[warmup_mask] = float('nan')            # restore warmup NaN
        data[self.name] = rsi
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
        ema_fast = ts_ops.ewm_mean(data["adj_close"], self._fast)
        ema_slow = ts_ops.ewm_mean(data["adj_close"], self._slow)
        data["macd_line"] = ema_fast - ema_slow
        data["macd_signal"] = ts_ops.ewm_mean(data["macd_line"], self._signal)
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
        mid = ts_ops.rolling_mean(data["adj_close"], self._period)
        std = ts_ops.rolling_std(data["adj_close"], self._period)
        data[f"boll_mid_{self._period}"] = mid
        data[f"boll_upper_{self._period}"] = mid + self._std_dev * std
        data[f"boll_lower_{self._period}"] = mid - self._std_dev * std
        return data


class Momentum(Factor):
    """N-day return as momentum factor."""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"momentum_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        data[self.name] = ts_ops.pct_change(data["adj_close"], self._period)
        return data


class VWAP(Factor):
    """Rolling Volume Weighted Average Price."""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"vwap_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        # Use adj_close for split-adjusted consistency; scale high/low by adj ratio
        adj_ratio = data["adj_close"] / data["close"].replace(0, float('nan')) if "adj_close" in data.columns else 1
        adj_high = data["high"] * adj_ratio
        adj_low = data["low"] * adj_ratio
        typical_price = (adj_high + adj_low + data["adj_close"]) / 3
        tp_vol = typical_price * data["volume"]
        data[self.name] = (
            tp_vol.rolling(self._period).sum()
            / data["volume"].rolling(self._period).sum()
        )
        return data


class OBV(Factor):
    """On Balance Volume — cumulative volume with direction from price changes."""

    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "obv"

    @property
    def warmup_period(self) -> int:
        return 1

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        import numpy as np

        data = data.copy()
        sign = np.sign(ts_ops.diff(data["adj_close"]))
        data[self.name] = (data["volume"] * sign).cumsum()
        return data


class ATR(Factor):
    """Average True Range — rolling mean of True Range (volatility measure)."""

    def __init__(self, period: int = 14):
        self._period = period

    @property
    def name(self) -> str:
        return f"atr_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period + 1

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        # Use split-adjusted OHLC for consistency
        adj_ratio = data["adj_close"] / data["close"].replace(0, float('nan')) if "adj_close" in data.columns else 1
        adj_high = data["high"] * adj_ratio
        adj_low = data["low"] * adj_ratio
        prev_close = data["adj_close"].shift(1)
        tr = pd.concat([
            adj_high - adj_low,
            (adj_high - prev_close).abs(),
            (adj_low - prev_close).abs(),
        ], axis=1).max(axis=1)
        data[self.name] = ts_ops.rolling_mean(tr, self._period)
        return data
