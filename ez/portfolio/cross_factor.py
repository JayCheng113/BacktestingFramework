"""V2.9 P2: CrossSectionalFactor — cross-sectional factor ABC and builtins.

Canonical interface (Codex #6 frozen):
    compute(universe_data: dict[str, pd.DataFrame], date: datetime) → pd.Series[symbol → score]

Input universe_data is engine-sliced to [date-lookback, date-1]. Strategy cannot see future data.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class CrossSectionalFactor(ABC):
    """Base class for cross-sectional factors."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    def warmup_period(self) -> int:
        return 0

    @abstractmethod
    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Compute cross-sectional factor scores.

        Args:
            universe_data: {symbol: DataFrame} sliced to [date-lookback, date-1].
            date: Current rebalance date (for reference only; data already sliced).

        Returns:
            Series mapping symbol → factor score for this date.
        """
        ...


class MomentumRank(CrossSectionalFactor):
    """N-day return percentile rank."""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"momentum_rank_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period or "adj_close" not in df.columns:
                continue
            close = df["adj_close"]
            ret = (close.iloc[-1] - close.iloc[-self._period]) / close.iloc[-self._period]
            scores[sym] = ret
        return pd.Series(scores).rank(pct=True) if scores else pd.Series(dtype=float)


class VolumeRank(CrossSectionalFactor):
    """Average volume percentile rank."""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"volume_rank_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period or "volume" not in df.columns:
                continue
            scores[sym] = df["volume"].iloc[-self._period:].mean()
        return pd.Series(scores).rank(pct=True) if scores else pd.Series(dtype=float)


class ReverseVolatilityRank(CrossSectionalFactor):
    """Reverse volatility rank (low vol → high score)."""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"reverse_vol_rank_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period + 1

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period + 1 or "adj_close" not in df.columns:
                continue
            vol = df["adj_close"].pct_change().iloc[-self._period:].std()
            scores[sym] = -vol  # lower vol → higher score
        return pd.Series(scores).rank(pct=True) if scores else pd.Series(dtype=float)
