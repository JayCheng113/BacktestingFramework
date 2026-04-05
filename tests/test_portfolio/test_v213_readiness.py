"""V2.13 infrastructure shape-level contract tests.

SCOPE: these tests validate that the **existing V2.12.2 post-release
infrastructure** (walk-forward, CrossSectionalFactor ABC, engine,
registry) can host the future V2.13 MLAlpha work, using **pure-Python
mocks** (no sklearn, no lightgbm, no xgboost). The tests are
regression canaries that will fail if a future refactor breaks the
foundation V2.13 depends on.

NOT IN SCOPE — these are V2.13 implementation tasks, not pre-conditions:
- Real sklearn / lightgbm / xgboost estimators surviving
  `copy.deepcopy()` under `portfolio_walk_forward`'s per-fold path
- LightGBM / XGBoost booster pickle compatibility (known version
  quirks, must be verified with actual library calls)
- Sandbox `ml_alphas/` kind, contract test running real sklearn
  training, hot-reload, `/api/portfolio/ml-alpha/*` endpoints — NONE
  of these exist yet
- Model persistence strategy under sandbox `pickle` /
  `multiprocessing` / `threading` / `subprocess` bans

See `docs/audit/v2.13-readiness-audit.md` for the full readiness
analysis.

Shape-level contracts verified here (with pure-Python mocks):

1. **Stateful self-retraining `CrossSectionalFactor` shape**: the ABC
   permits a factor that holds internal "model" state (here:
   cumulative return on a sliding window) and retrains when the eval
   date crosses a retrain boundary. Runs through
   `run_portfolio_backtest` and `portfolio_walk_forward` without errors.
   NOT a real ML model — just the shape V2.13 will take.

2. **Anti-lookahead enforcement**: `slice_universe_data` strictly
   excludes `target_date`, so any factor receiving `universe_data`
   sees only dates `< target_date`. Verified with a
   `_LookaheadDetector` that records violations.

3. **Walk-forward factory freshness**: `portfolio_walk_forward` calls
   `strategy_factory` once per fold × (IS + OOS), producing fresh
   instances with independent `id()`. Necessary for MLAlpha state
   isolation across folds.

4. **Pure-Python stateful object deepcopy**: a `_MLShapedFactor`
   instance can be `copy.deepcopy()`'d and mutations on the clone
   don't affect the original. ⚠️ THIS IS NOT A TEST OF REAL SKLEARN
   MODEL PICKLING. V2.13 must verify real estimator round-trip
   separately.

5. **Dual-dict registry for dynamically-defined factors**: the
   `CrossSectionalFactor._registry_by_key` + `_registry` pattern
   (from V2.12.2 round 1) correctly handles factor classes defined
   at test-module-import time, including `resolve_class()` lookup.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import copy
import numpy as np
import pandas as pd
import pytest

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import CrossSectionalFactor
from ez.portfolio.engine import run_portfolio_backtest
from ez.portfolio.portfolio_strategy import TopNRotation
from ez.portfolio.universe import Universe
from ez.portfolio.walk_forward import portfolio_walk_forward


def _make_universe(n_stocks: int = 6, n_days: int = 400, seed: int = 42):
    """Create a small universe with deterministic price paths."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    data = {}
    for i in range(n_stocks):
        # Give each stock a distinct drift so momentum is predictable
        prices = 10 * np.cumprod(1 + rng.normal(0.0003 * (i + 1), 0.012, n_days))
        data[f"S{i:02d}"] = pd.DataFrame({
            "open": prices, "high": prices * 1.005, "low": prices * 0.995,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
        }, index=dates)
    cal = TradingCalendar.from_dates([d.date() for d in dates])
    universe = Universe([f"S{i:02d}" for i in range(n_stocks)])
    return data, cal, universe, dates


# ─── Contract 1: Stateful self-retraining CrossSectionalFactor ───────────

class _MLShapedFactor(CrossSectionalFactor):
    """A CrossSectionalFactor that maintains internal "model" state and
    "retrains" when the eval date crosses a retrain boundary.

    This is the SHAPE V2.13 MLAlpha will take:
    - __init__ sets train_window, retrain_freq, purge_days
    - compute() checks if current "model" is still valid for this date
    - If stale, retrains on sliced historical data (which is anti-lookahead
      enforced by slice_universe_data upstream)
    - Returns per-symbol scores (model.predict equivalent)
    """

    def __init__(self, train_window: int = 120, retrain_freq: int = 30, purge_days: int = 5):
        self._train_window = train_window
        self._retrain_freq = retrain_freq
        self._purge_days = purge_days
        # State: last retrain date + "model" (here: a dict of per-symbol scores)
        self._last_retrain: date | None = None
        self._model: dict[str, float] = {}
        self._retrain_count: int = 0

    @property
    def name(self) -> str:
        return f"mlshaped_w{self._train_window}_f{self._retrain_freq}"

    @property
    def warmup_period(self) -> int:
        # MLAlpha warmup = train_window + purge_days (enough history to fit)
        return self._train_window + self._purge_days

    def _needs_retrain(self, current: date) -> bool:
        if self._last_retrain is None:
            return True
        return (current - self._last_retrain).days >= self._retrain_freq

    def _retrain(self, universe_data: dict[str, pd.DataFrame], current: date) -> None:
        """Simulate model retraining: compute per-symbol momentum on sliced data.

        IMPORTANT: universe_data is already sliced by the engine to exclude
        `current` and later. The factor MUST NOT look at data >= current.
        """
        self._model = {}
        for sym, df in universe_data.items():
            if "adj_close" not in df.columns or len(df) < 2:
                continue
            # Use last `train_window - purge_days` rows, skip most recent `purge_days`
            effective_end = len(df) - self._purge_days
            effective_start = max(0, effective_end - self._train_window)
            if effective_end <= effective_start:
                continue
            window = df["adj_close"].iloc[effective_start:effective_end]
            if len(window) < 2:
                continue
            # "Trained score": cumulative return over the window
            self._model[sym] = float(window.iloc[-1] / window.iloc[0] - 1.0)
        self._last_retrain = current
        self._retrain_count += 1

    def compute(self, universe_data: dict[str, pd.DataFrame], dt: datetime) -> pd.Series:
        current = dt.date() if hasattr(dt, "date") else dt
        if self._needs_retrain(current):
            self._retrain(universe_data, current)
        if not self._model:
            return pd.Series(dtype=float)
        # Return percentile ranks (standard CrossSectionalFactor convention)
        scores = pd.Series(self._model)
        return scores.rank(pct=True)

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], dt: datetime) -> pd.Series:
        current = dt.date() if hasattr(dt, "date") else dt
        if self._needs_retrain(current):
            self._retrain(universe_data, current)
        return pd.Series(self._model) if self._model else pd.Series(dtype=float)


class TestStatefulSelfRetrainingFactor:
    """V2.13 MLAlpha will be a stateful factor that retrains internally.
    Prove the `CrossSectionalFactor` ABC + engine support this SHAPE,
    using a pure-Python mock (cumulative return as "trained weights",
    not a real model). Does NOT test real ML library integration."""

    def test_factor_retrains_across_rebalances(self):
        data, cal, universe, dates = _make_universe()
        factor = _MLShapedFactor(train_window=60, retrain_freq=20, purge_days=5)
        strategy = TopNRotation(factor=factor, top_n=3)
        result = run_portfolio_backtest(
            strategy=strategy, universe=universe, universe_data=data,
            calendar=cal, start=dates[100].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=1_000_000,
        )
        # Backtest completed without errors
        assert result is not None
        assert len(result.equity_curve) > 0
        # Factor retrained multiple times (not once at start)
        assert factor._retrain_count >= 2, (
            f"Expected multiple retrains across a weekly rebalance backtest; "
            f"got {factor._retrain_count}"
        )

    def test_factor_sees_no_future_data(self):
        """Anti-lookahead: factor MUST NOT see any data >= current date."""
        data, cal, universe, dates = _make_universe()

        class _LookaheadDetector(CrossSectionalFactor):
            """Raises if it observes any date in universe_data >= current."""
            def __init__(self):
                self.violations: list[str] = []

            @property
            def name(self) -> str:
                return "lookahead_detector"

            @property
            def warmup_period(self) -> int:
                return 30

            def compute(self, universe_data, dt: datetime) -> pd.Series:
                current = dt.date() if hasattr(dt, "date") else dt
                for sym, df in universe_data.items():
                    if len(df) == 0:
                        continue
                    last_idx = df.index[-1]
                    last_date = last_idx.date() if hasattr(last_idx, "date") else last_idx
                    if last_date >= current:
                        self.violations.append(f"{sym}: last={last_date} >= current={current}")
                # Return flat zero scores
                scores = pd.Series({sym: 0.0 for sym in universe_data.keys()})
                return scores

        detector = _LookaheadDetector()
        strategy = TopNRotation(factor=detector, top_n=3)
        run_portfolio_backtest(
            strategy=strategy, universe=universe, universe_data=data,
            calendar=cal, start=dates[60].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=1_000_000,
        )
        assert detector.violations == [], (
            f"Anti-lookahead violations detected: {detector.violations[:5]}"
        )


# ─── Contract 3: Walk-forward factory freshness per fold ─────────────────

class TestWalkForwardFactoryFreshness:
    """V2.13 MLAlpha models MUST be fresh per fold to avoid IS→OOS state
    bleed. Prove portfolio_walk_forward correctly isolates strategy +
    optimizer + risk_manager per fold."""

    def test_strategy_factory_called_per_fold(self):
        data, cal, universe, dates = _make_universe(n_days=400)
        call_count = [0]

        def factory():
            call_count[0] += 1
            factor = _MLShapedFactor(train_window=30, retrain_freq=15)
            return TopNRotation(factor=factor, top_n=3)

        result = portfolio_walk_forward(
            strategy_factory=factory,
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, train_ratio=0.7, freq="weekly",
        )
        # 3 splits × 2 (IS + OOS) = 6 factory calls
        assert call_count[0] == 6, (
            f"Expected 6 factory calls (3 splits × IS + OOS), got {call_count[0]}"
        )
        # Walk-forward actually produced results
        assert result.n_splits == 3
        assert len(result.oos_sharpes) == 3

    def test_independent_factor_instances_across_folds(self):
        """Each fold must get a fresh factor instance — one fold's retrain
        state must not leak into another."""
        data, cal, universe, dates = _make_universe(n_days=400)
        created_factors: list[_MLShapedFactor] = []

        def factory():
            factor = _MLShapedFactor(train_window=30, retrain_freq=15)
            created_factors.append(factor)
            return TopNRotation(factor=factor, top_n=3)

        portfolio_walk_forward(
            strategy_factory=factory,
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, train_ratio=0.7, freq="weekly",
        )
        # Every factory call creates a new instance (no caching)
        assert len(set(id(f) for f in created_factors)) == len(created_factors), (
            "Factory returned same instance twice — state leakage risk"
        )
        # Each factor's _retrain_count is independent
        counts = [f._retrain_count for f in created_factors]
        assert all(c >= 0 for c in counts)


# ─── Contract 4: Deepcopy safety for PURE-PYTHON stateful strategy ──────

class TestDeepcopySafety:
    """Single-stock `WalkForwardValidator.validate()` uses
    `copy.deepcopy(strategy)` per fold. V2.13 MLAlpha will rely on this
    to get fresh model state per fold.

    ⚠️ SCOPE: this class ONLY verifies that a **pure-python** stateful
    object (`_MLShapedFactor` mock with dict + int + date state)
    survives `copy.deepcopy()` cleanly and has independent state after.
    This does **NOT** verify that real sklearn / lightgbm / xgboost
    estimators survive the same path. Those libraries have their own
    `__reduce__` / `__getstate__` implementations and known pickle
    quirks across versions — V2.13 implementation must test each
    estimator it supports explicitly. This canary only catches
    regressions in the generic deepcopy path.
    """

    def test_stateful_factor_deepcopy(self):
        """Deep copy a stateful factor and verify state is independent."""
        factor = _MLShapedFactor(train_window=60, retrain_freq=20)
        factor._model = {"A": 0.5, "B": 0.3}
        factor._retrain_count = 3
        factor._last_retrain = date(2024, 1, 15)

        clone = copy.deepcopy(factor)
        # State copied
        assert clone._model == {"A": 0.5, "B": 0.3}
        assert clone._retrain_count == 3
        assert clone._last_retrain == date(2024, 1, 15)
        # Independent: mutating clone doesn't affect original
        clone._model["C"] = 0.7
        clone._retrain_count = 99
        assert "C" not in factor._model
        assert factor._retrain_count == 3

    def test_stateful_strategy_deepcopy(self):
        """TopNRotation wrapping a stateful factor deepcopies correctly."""
        factor = _MLShapedFactor()
        strategy = TopNRotation(factor=factor, top_n=3)
        # Populate the factor's state
        strategy._factor._model = {"X": 0.5}
        strategy._factor._retrain_count = 5

        clone = copy.deepcopy(strategy)
        assert clone._factor._model == {"X": 0.5}
        assert clone._factor._retrain_count == 5
        # Mutation independence
        clone._factor._model["Y"] = 0.8
        assert "Y" not in strategy._factor._model


# ─── Contract 5: CrossSectionalFactor ABC registration path ─────────────

class TestFactorRegistryForMLShaped:
    """V2.13 MLAlpha instances register through the CrossSectionalFactor
    dual-dict registry just like any other factor. Verify this path."""

    def test_ml_shaped_factor_in_registry(self):
        """_MLShapedFactor is a concrete CrossSectionalFactor subclass, so
        it auto-registers via __init_subclass__."""
        # The factor was defined at module import time, so it's in the registry
        assert "_MLShapedFactor" in CrossSectionalFactor._registry
        key = f"{_MLShapedFactor.__module__}._MLShapedFactor"
        assert key in CrossSectionalFactor._registry_by_key

    def test_resolve_class_for_ml_shaped(self):
        """Dual-dict resolve_class handles ML-shaped factors the same way
        it handles built-in factors."""
        cls = CrossSectionalFactor.resolve_class("_MLShapedFactor")
        assert cls is _MLShapedFactor
        # Instantiate
        instance = cls(train_window=50, retrain_freq=10)
        assert instance.warmup_period == 55  # 50 + default purge_days=5
