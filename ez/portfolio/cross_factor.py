"""V2.9 P2: CrossSectionalFactor — cross-sectional factor ABC and builtins.

Canonical interface (Codex #6 frozen):
    compute(universe_data: dict[str, pd.DataFrame], date: datetime) → pd.Series[symbol → score]
    compute_raw(universe_data, date) → pd.Series[symbol → raw_value]  (V2.11.1)

Input universe_data is engine-sliced to [date-lookback, date-1]. Strategy cannot see future data.

V2.11.1: Added compute_raw() for neutralization and factor combination.
  compute_raw() returns raw values (not ranked). compute() returns percentile rank (backward compat).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class CrossSectionalFactor(ABC):
    """Base class for cross-sectional factors.

    Subclasses auto-register via __init_subclass__. Access registry via get_registry().

    V2.12.2 codex: dual-dict registry mirrors Factor/PortfolioStrategy — one
    keyed by `module.class` (authoritative) and one keyed by `__name__`
    (backward compat). Name collision logs a warning; use `resolve_class()`
    to disambiguate. Prior version silently overwrote on collision.
    """

    # Authoritative: "module.class" → class (unique)
    _registry_by_key: dict[str, type] = {}
    # Backward-compat: "class_name" → class (last-write-wins with warning)
    _registry: dict[str, type] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, '__abstractmethods__', None):
            key = f"{cls.__module__}.{cls.__name__}"
            CrossSectionalFactor._registry_by_key[key] = cls

            name = cls.__name__
            existing = CrossSectionalFactor._registry.get(name)
            if existing is not None and existing is not cls:
                # V2.19.0 codex round-2 S7: suppress noise from guard probes.
                if not cls.__module__.startswith("_guard_probe."):
                    import logging
                    logging.getLogger(__name__).warning(
                        "CrossSectionalFactor name collision: '%s' previously "
                        "registered by %s.%s, now replaced by %s.%s. Use "
                        "CrossSectionalFactor.resolve_class() with the full "
                        "'module.class' key to disambiguate.",
                        name,
                        existing.__module__, existing.__name__,
                        cls.__module__, cls.__name__,
                    )
            CrossSectionalFactor._registry[name] = cls

    @classmethod
    def get_registry(cls) -> dict[str, type]:
        return dict(cls._registry)

    @classmethod
    def resolve_class(cls, name: str) -> type:
        """Resolve a cross-factor identifier using three-stage matching.

        Order: exact module.class key → unique __name__ match → ValueError.
        """
        exact = cls._registry_by_key.get(name)
        if exact is not None:
            return exact
        matches = [(k, c) for k, c in cls._registry_by_key.items() if c.__name__ == name]
        if len(matches) == 1:
            return matches[0][1]
        if len(matches) > 1:
            keys = [k for k, _ in matches]
            raise ValueError(
                f"CrossSectionalFactor name '{name}' is ambiguous — "
                f"multiple classes registered: {keys}. Submit the full "
                f"module.class key."
            )
        raise KeyError(name)

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    def warmup_period(self) -> int:
        return 0

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Compute raw (un-ranked) factor scores.

        Used by neutralization and AlphaCombiner to access pre-rank values.
        Default implementation returns compute() result (backward compatible for
        user-defined factors that only implement compute()).

        New factors should override this to return raw values, and have compute()
        call compute_raw() then rank.
        """
        return self.compute(universe_data, date)

    @abstractmethod
    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Compute cross-sectional factor scores (percentile rank).

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

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period or "adj_close" not in df.columns:
                continue
            close = df["adj_close"]
            ret = (close.iloc[-1] - close.iloc[-self._period]) / close.iloc[-self._period]
            scores[sym] = ret
        return pd.Series(scores) if scores else pd.Series(dtype=float)

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        raw = self.compute_raw(universe_data, date)
        return raw.rank(pct=True) if len(raw) > 0 else raw


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

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period or "volume" not in df.columns:
                continue
            scores[sym] = df["volume"].iloc[-self._period:].mean()
        return pd.Series(scores) if scores else pd.Series(dtype=float)

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        raw = self.compute_raw(universe_data, date)
        return raw.rank(pct=True) if len(raw) > 0 else raw


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

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period + 1 or "adj_close" not in df.columns:
                continue
            vol = df["adj_close"].pct_change().iloc[-self._period:].std()
            scores[sym] = -vol  # lower vol → higher score
        return pd.Series(scores) if scores else pd.Series(dtype=float)

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        raw = self.compute_raw(universe_data, date)
        return raw.rank(pct=True) if len(raw) > 0 else raw
