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

    Subclasses auto-register via __init_subclass__.

    V2.12.1 post-review (codex): dual-dict registry — one keyed by
    `module.class` (unique, authoritative) and one keyed by `class_name`
    (backward-compat for callers that look up by `__name__`). A name
    collision logs a warning and the name-keyed entry is overwritten, but
    the full-key dict preserves every class so `resolve_class()` can
    disambiguate. Prior version only had the name-keyed dict and silently
    overwrote duplicates with no log or recovery path.
    """

    # Authoritative: "module.class" → class (unique). Every registered subclass
    # is in here — used for exhaustive iteration and disambiguation.
    _registry_by_key: dict[str, type] = {}
    # Backward-compat: "class_name" → class. Last-write-wins on collision,
    # with a logged warning. Preserved so existing callers
    # (ez/api/routes/portfolio.py, ez/agent/tools.py) that look up by name
    # continue to work.
    _registry: dict[str, type] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, '__abstractmethods__', None):
            key = f"{cls.__module__}.{cls.__name__}"
            # Full-key dedup: replacing the same class is fine (hot-reload);
            # a genuinely different class under the same key is impossible
            # unless someone monkey-patches sys.modules. Just assign.
            PortfolioStrategy._registry_by_key[key] = cls

            # Name-keyed collision: log warning if a different class already
            # occupies this name. Tests and hot-reload scenarios rely on this
            # being non-fatal; real duplicates should be caught by code review
            # and the warning surfaces them.
            name = cls.__name__
            existing = PortfolioStrategy._registry.get(name)
            if existing is not None and existing is not cls:
                import logging
                logging.getLogger(__name__).warning(
                    "PortfolioStrategy name collision: '%s' previously "
                    "registered by %s.%s, now replaced by %s.%s. "
                    "Use resolve_class(key) with the full 'module.class' "
                    "key to disambiguate.",
                    name,
                    existing.__module__, existing.__name__,
                    cls.__module__, cls.__name__,
                )
            PortfolioStrategy._registry[name] = cls

    @classmethod
    def get_registry(cls) -> dict[str, type]:
        """Public accessor for the name-keyed registry (backward-compat).

        Returns a copy of the name-keyed dict; on collision, only the last
        registered class is visible here. Use `resolve_class()` or iterate
        `_registry_by_key.items()` for full disambiguation.
        """
        return dict(cls._registry)

    @classmethod
    def resolve_class(cls, name: str) -> type:
        """Resolve a strategy identifier to a class using three-stage matching.

        Mirrors `Strategy.resolve_class()` so both sides of the app use
        consistent resolution semantics.

        Order:
        1. Exact key match (`module.class`) — unambiguous
        2. Unique class-name match — backward-compat
        3. Ambiguous name → raises ValueError with all candidate keys

        Raises:
            KeyError: name not found
            ValueError: multiple classes share this __name__
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
                f"PortfolioStrategy name '{name}' is ambiguous — "
                f"multiple classes registered: {keys}. "
                f"Please submit the full key instead."
            )
        raise KeyError(name)

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

    @property
    def factor(self) -> CrossSectionalFactor:
        """Public accessor for the wrapped factor.

        V2.13 round 5 codex-H: engine.run_portfolio_backtest warmup check
        at ez/portfolio/engine.py:101 walks ``getattr(strategy, 'factor')``
        to discover the factor's ``warmup_period``. Before this property
        was added, engine read ``None`` for TopNRotation (the factor was
        stored as private ``_factor``) and silently skipped the warmup
        validation. Combined with the default ``lookback_days=252``,
        MLAlpha(train_window > 252) received truncated history on early
        rebalances with no warning. Exposing the factor here lets the
        engine's existing defensive check do its job.
        """
        return self._factor

    @property
    def lookback_days(self) -> int:
        """Required history length in trading days.

        V2.13 round 5 codex-H: derived from the inner factor's
        ``warmup_period`` so MLAlpha with a long ``train_window`` gets
        enough history. Floor at 252 (the prior default) to avoid
        regressions for short-warmup factors like MomentumRank. Add a
        20-day buffer for upstream ``pct_change`` / ``rolling`` warmup
        inside feature functions.
        """
        factor_warmup = int(getattr(self._factor, "warmup_period", 0) or 0)
        return max(252, factor_warmup + 20)

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

    @property
    def factors(self) -> list[CrossSectionalFactor]:
        """Public accessor for the wrapped factors list.

        See TopNRotation.factor docstring for the engine-side warmup
        check that reads this attribute. V2.13 round 5 codex-H.
        """
        return list(self._factors)

    @property
    def lookback_days(self) -> int:
        """Required history length — max warmup across all factors + buffer.

        V2.13 round 5 codex-H: MLAlpha(train_window=500) wrapped in
        MultiFactorRotation needs ≥ 500 rows of history. Previously this
        strategy inherited default 252 and silently truncated.
        """
        if not self._factors:
            return 252
        max_warmup = max(
            int(getattr(f, "warmup_period", 0) or 0) for f in self._factors
        )
        return max(252, max_warmup + 20)

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
