"""V2.9 P3: PortfolioStrategy — stateful, anti-lookahead portfolio strategy ABC.

Canonical interface (Codex #2 frozen):
    generate_weights(universe_data, date, prev_weights, prev_returns) → dict[str, float]

Engine guarantees: universe_data sliced to [date-lookback, date-1].
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from ez.portfolio.cross_factor import CrossSectionalFactor


class PortfolioStrategy(ABC):
    """Base class for portfolio strategies.

    Subclasses auto-register via __init_subclass__. Access registry via _registry.
    """

    _registry: dict[str, type] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, '__abstractmethods__', None):
            PortfolioStrategy._registry[cls.__name__] = cls

    @classmethod
    def get_registry(cls) -> dict[str, type]:
        """Public accessor for the strategy registry."""
        return dict(cls._registry)

    def __init__(self, **params):
        self.state: dict = {}
        self._params = params

    @property
    def lookback_days(self) -> int:
        """How many trading days of history the engine should provide.

        Override for longer history. Must be >= warmup_period of all factors used.
        """
        return 252

    @abstractmethod
    def generate_weights(
        self,
        universe_data: dict[str, pd.DataFrame],
        date: datetime,
        prev_weights: dict[str, float],
        prev_returns: dict[str, float],
    ) -> dict[str, float]:
        """Return target weights {symbol: weight}.

        - universe_data: already sliced to [date-lookback, date-1] by engine.
        - self.state: freely maintained across rebalance calls.
        - Weights must be >= 0 (long-only), sum <= 1.0 (remainder is cash).
        """
        ...

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {}

    @classmethod
    def get_description(cls) -> str:
        return cls.__doc__ or ""


class TopNRotation(PortfolioStrategy):
    """Select top-N stocks by a cross-sectional factor, equal weight."""

    def __init__(self, factor: CrossSectionalFactor, top_n: int = 10, **params):
        super().__init__(**params)
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self._factor = factor
        self._top_n = top_n

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "top_n": {"type": "int", "default": 10, "min": 1, "max": 200, "label": "持仓数"},
            "factor": {"type": "select", "default": "momentum_rank_20", "label": "排名因子"},
        }

    @classmethod
    def get_description(cls) -> str:
        return "按截面因子排名选 Top-N 等权持仓"

    def generate_weights(self, universe_data, date, prev_weights, prev_returns):
        scores = self._factor.compute(universe_data, date)
        valid = scores.dropna()
        if len(valid) < 1:
            return {}
        n = min(self._top_n, len(valid))
        top = valid.nlargest(n).index
        w = 1.0 / n
        return {sym: w for sym in top}


class MultiFactorRotation(PortfolioStrategy):
    """Combine multiple factors (equal-weight z-score), select top-N."""

    def __init__(self, factors: list[CrossSectionalFactor], top_n: int = 10, **params):
        super().__init__(**params)
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self._factors = factors
        self._top_n = top_n

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "top_n": {"type": "int", "default": 10, "min": 1, "max": 200, "label": "持仓数"},
            "factors": {"type": "multi_select", "default": ["momentum_rank_20"], "label": "因子组合"},
        }

    @classmethod
    def get_description(cls) -> str:
        return "多因子等权 Z-Score 合成排名选 Top-N"

    def generate_weights(self, universe_data, date, prev_weights, prev_returns):
        all_scores = []
        for f in self._factors:
            s = f.compute(universe_data, date)
            if not s.empty:
                # z-score normalize
                mean, std = s.mean(), s.std()
                if std > 0:
                    s = (s - mean) / std
                all_scores.append(s)

        if not all_scores:
            return {}

        combined = pd.concat(all_scores, axis=1).mean(axis=1).dropna()
        if combined.empty:
            return {}

        n = min(self._top_n, len(combined))
        top = combined.nlargest(n).index
        w = 1.0 / n
        return {sym: w for sym in top}
