"""V2.13 Phase 1: MLAlpha × real sklearn integration tests.

These tests use actual sklearn estimators (not pure-Python mocks) to
resolve two open questions from the readiness audit:

1. Does a trained MLAlpha survive `copy.deepcopy()` with bit-identical
   predictions? (needed for single-stock WalkForwardValidator
   compatibility + as a generic Python-value sanity check)
2. Does a trained MLAlpha produce consistent results when driven by
   `portfolio_walk_forward`'s `strategy_factory()` per-fold-fresh-instance
   path? (this is the actual path used by all portfolio-level WF tests
   and the portfolio UI — portfolio walk-forward does NOT use deepcopy)

If any test here fails, V2.13 Phase 1 cannot ship — the foundational
assumption that sklearn models work through BOTH the deepcopy code path
(single-stock) and the factory code path (portfolio) is broken.

Task 1.10 builds on Task 1.9 by adding the end-to-end
run_portfolio_backtest + portfolio_walk_forward integration.
"""
from __future__ import annotations

import copy
from datetime import date, datetime
from typing import Callable

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.ml_alpha import MLAlpha


def _make_data(n_days: int = 300, n_stocks: int = 5, seed: int = 42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    data = {}
    for i in range(n_stocks):
        # Distinct drift per stock so rankings are meaningful
        prices = 100 * np.cumprod(1 + rng.normal(0.0003 * (i + 1), 0.01, n_days))
        data[f"S{i:02d}"] = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
        }, index=dates)
    return data, dates


def _simple_feature_fn(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "ret1": df["adj_close"].pct_change(1),
        "ret5": df["adj_close"].pct_change(5),
        "ret20": df["adj_close"].pct_change(20),
    }).dropna()


def _forward_return_target(horizon: int = 5) -> Callable:
    def _fn(df: pd.DataFrame) -> pd.Series:
        return df["adj_close"].pct_change(horizon).shift(-horizon)
    return _fn


# ─── Deepcopy round-trip tests ────────────────────────────────────────

class TestMLAlphaRidgeDeepcopy:
    """Real Ridge estimator survives copy.deepcopy with fitted state.

    This test class validates the single-stock WF path compatibility
    (ez/backtest/walk_forward.py uses copy.deepcopy per fold) and acts
    as a generic "MLAlpha is a well-behaved Python value" guarantee.
    Portfolio walk-forward does NOT use deepcopy — see
    TestMLAlphaEndToEndBacktest in Task 1.10 for that pathway.
    """

    def test_unfit_alpha_deepcopy(self):
        from sklearn.linear_model import Ridge
        alpha = MLAlpha(
            name="ridge_unfit",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=60, retrain_freq=20, purge_days=5,
        )
        clone = copy.deepcopy(alpha)
        assert clone._current_model is None
        assert clone._retrain_count == 0
        assert clone._last_retrain_date is None
        # Clone must be a different instance
        assert clone is not alpha

    def test_fit_alpha_deepcopy_preserves_predictions(self):
        """Fit a Ridge model, deepcopy, verify predictions are identical.

        This is the critical test for single-stock WF compatibility: after
        deepcopy, the clone's model must produce the same predictions as
        the original on the same data.
        """
        from sklearn.linear_model import Ridge

        data, dates = _make_data(n_days=300)
        alpha = MLAlpha(
            name="ridge_fit",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=100, retrain_freq=20, purge_days=5,
        )
        scores_original = alpha.compute(data, dates[150].to_pydatetime())
        assert alpha._retrain_count == 1
        assert alpha._current_model is not None
        assert len(scores_original) > 0

        clone = copy.deepcopy(alpha)

        # Clone has INDEPENDENT but EQUIVALENT model
        assert clone._current_model is not alpha._current_model
        assert clone._retrain_count == 1
        assert clone._last_retrain_date == alpha._last_retrain_date

        # Predictions on the same data at the same date must be identical
        scores_clone = clone.compute(data, dates[150].to_pydatetime())
        pd.testing.assert_series_equal(scores_original, scores_clone)

        # Mutating clone does not affect original
        clone._retrain_count = 999
        assert alpha._retrain_count == 1

    def test_deepcopy_independent_retrain(self):
        """After deepcopy, retraining one instance must not affect the
        other's state."""
        from sklearn.linear_model import Ridge

        data, dates = _make_data(n_days=300)
        alpha = MLAlpha(
            name="ridge_fit",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=80, retrain_freq=20, purge_days=5,
        )
        alpha.compute(data, dates[100].to_pydatetime())
        clone = copy.deepcopy(alpha)

        # Advance the original past retrain_freq — triggers retrain
        alpha.compute(data, dates[130].to_pydatetime())
        assert alpha._retrain_count == 2

        # Clone still has its original state
        assert clone._retrain_count == 1
        assert clone._last_retrain_date == dates[100].date()


class TestMLAlphaRandomForestDeepcopy:
    """RandomForestRegressor with n_jobs=1 survives deepcopy."""

    def test_fit_rf_alpha_deepcopy(self):
        from sklearn.ensemble import RandomForestRegressor

        data, dates = _make_data(n_days=300)
        alpha = MLAlpha(
            name="rf",
            model_factory=lambda: RandomForestRegressor(
                n_estimators=20, max_depth=5, n_jobs=1, random_state=42,
            ),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=100, retrain_freq=20, purge_days=5,
        )
        scores_orig = alpha.compute(data, dates[150].to_pydatetime())
        assert alpha._current_model is not None

        clone = copy.deepcopy(alpha)
        # sklearn RF stores decision trees in estimators_ — must survive
        assert len(clone._current_model.estimators_) == 20

        scores_clone = clone.compute(data, dates[150].to_pydatetime())
        pd.testing.assert_series_equal(scores_orig, scores_clone)


class TestMLAlphaGradientBoostingDeepcopy:
    """GradientBoostingRegressor survives deepcopy."""

    def test_fit_gb_alpha_deepcopy(self):
        from sklearn.ensemble import GradientBoostingRegressor

        data, dates = _make_data(n_days=300)
        alpha = MLAlpha(
            name="gb",
            model_factory=lambda: GradientBoostingRegressor(
                n_estimators=10, max_depth=3, random_state=0,
            ),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=100, retrain_freq=20, purge_days=5,
        )
        scores_orig = alpha.compute(data, dates[150].to_pydatetime())

        clone = copy.deepcopy(alpha)
        scores_clone = clone.compute(data, dates[150].to_pydatetime())
        pd.testing.assert_series_equal(scores_orig, scores_clone)


# ─── Determinism across instances (stronger than test_ml_alpha.py version)

class TestMLAlphaCrossInstanceDeterminism:
    """Two independently constructed MLAlpha instances must produce the
    same predictions when given the same data. This is a stronger
    property than intra-instance determinism — it means our use of
    sklearn doesn't leak process-wide state (e.g., global thread pools).
    """

    def test_two_fresh_ridge_instances_produce_same_predictions(self):
        from sklearn.linear_model import Ridge

        data, dates = _make_data(n_days=300)

        def build():
            return MLAlpha(
                name="ridge",
                model_factory=lambda: Ridge(alpha=1.0),
                feature_fn=_simple_feature_fn,
                target_fn=_forward_return_target(5),
                train_window=80, retrain_freq=20, purge_days=5,
            )

        a1 = build()
        a2 = build()
        s1 = a1.compute(data, dates[150].to_pydatetime())
        s2 = a2.compute(data, dates[150].to_pydatetime())
        pd.testing.assert_series_equal(s1, s2)

    def test_two_fresh_rf_instances_produce_same_predictions(self):
        from sklearn.ensemble import RandomForestRegressor

        data, dates = _make_data(n_days=300)

        def build():
            return MLAlpha(
                name="rf",
                model_factory=lambda: RandomForestRegressor(
                    n_estimators=10, max_depth=3, n_jobs=1, random_state=123,
                ),
                feature_fn=_simple_feature_fn,
                target_fn=_forward_return_target(5),
                train_window=80, retrain_freq=20, purge_days=5,
            )

        a1 = build()
        a2 = build()
        s1 = a1.compute(data, dates[150].to_pydatetime())
        s2 = a2.compute(data, dates[150].to_pydatetime())
        pd.testing.assert_series_equal(s1, s2)


# ─── End-to-end: run_portfolio_backtest + portfolio_walk_forward ────

class TestMLAlphaEndToEndBacktest:
    """The critical integration tests. MLAlpha must work through both the
    single-run `run_portfolio_backtest` path and the multi-fold
    `portfolio_walk_forward` path. The walk-forward test specifically
    exercises the strategy_factory() per-fold-fresh-instance isolation
    mechanism — which is how V2.13 resolves audit open Q#1 for the
    portfolio code path (portfolio WF does NOT use copy.deepcopy).
    """

    def test_ridge_momentum_run_portfolio_backtest(self):
        from sklearn.linear_model import Ridge
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.engine import run_portfolio_backtest
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        data, dates = _make_data(n_days=400, n_stocks=8)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(8)])

        alpha = MLAlpha(
            name="ridge_mom",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=60, retrain_freq=20, purge_days=5,
        )
        strategy = TopNRotation(factor=alpha, top_n=3)

        result = run_portfolio_backtest(
            strategy=strategy,
            universe=universe,
            universe_data=data,
            calendar=cal,
            start=dates[100].date(),
            end=dates[-1].date(),
            freq="weekly",
            initial_cash=1_000_000,
        )

        # Backtest completed without errors
        assert result is not None
        assert len(result.equity_curve) > 0
        # Not bankrupt
        assert result.equity_curve[-1] > 0
        # Alpha retrained multiple times across the weekly rebalance schedule.
        # ~300 days / retrain_freq=20 ≈ 15 max retrains (weekly rebalance
        # hits retrain boundary every ~3 rebalances given freq=20 calendar
        # days). Lower bound 5 is very conservative.
        assert alpha._retrain_count >= 5, (
            f"Expected ≥5 retrains over ~300 days of weekly rebalance; "
            f"got {alpha._retrain_count}"
        )

    def test_ridge_momentum_portfolio_walk_forward_factory_fresh_instances(self):
        """THE critical integration test. Validates that MLAlpha works
        end-to-end through portfolio_walk_forward's factory-based per-fold
        isolation mechanism.

        Resolves audit open Q#1 for the portfolio path: portfolio walk-
        forward does NOT use copy.deepcopy. It calls strategy_factory()
        once per fold per stage (IS + OOS), expecting a fresh strategy
        instance each time. MLAlpha's per-fold isolation comes entirely
        from the factory returning a new MLAlpha with _current_model=None.
        """
        from sklearn.linear_model import Ridge
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.walk_forward import portfolio_walk_forward
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        data, dates = _make_data(n_days=500, n_stocks=8)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(8)])

        created_alphas: list[MLAlpha] = []

        def strategy_factory():
            alpha = MLAlpha(
                name="ridge_mom_wf",
                model_factory=lambda: Ridge(alpha=1.0),
                feature_fn=_simple_feature_fn,
                target_fn=_forward_return_target(5),
                train_window=60, retrain_freq=20, purge_days=5,
            )
            created_alphas.append(alpha)
            return TopNRotation(factor=alpha, top_n=3)

        wf_result = portfolio_walk_forward(
            strategy_factory=strategy_factory,
            universe=universe,
            universe_data=data,
            calendar=cal,
            start=dates[60].date(),
            end=dates[-1].date(),
            n_splits=3,
            train_ratio=0.7,
            freq="weekly",
        )

        # 3 splits × (IS + OOS) = 6 factory calls
        assert len(created_alphas) == 6, (
            f"Expected 6 factory calls (3 splits × IS + OOS), got {len(created_alphas)}"
        )

        # Every alpha is a distinct instance — factory must not cache
        assert len(set(id(a) for a in created_alphas)) == 6, (
            "strategy_factory returned the same MLAlpha instance twice — "
            "this breaks per-fold isolation and is the bug Task 1.10 guards against."
        )

        # Each alpha retrained at least once during its fold (retrain_count >= 1)
        # UNLESS the fold had too little data — we allow 0 if a fold is very short,
        # but at least HALF of the alphas must have trained.
        trained = sum(1 for a in created_alphas if a._retrain_count >= 1)
        assert trained >= 3, (
            f"Fewer than half of MLAlpha instances retrained — expected ≥3 of 6, "
            f"got {trained}. retrain_counts: {[a._retrain_count for a in created_alphas]}"
        )

        # Walk-forward produced results
        assert wf_result.n_splits == 3
        assert len(wf_result.is_sharpes) == 3
        assert len(wf_result.oos_sharpes) == 3
        # All OOS Sharpes finite (may be negative — we only assert no crash/NaN)
        assert all(np.isfinite(s) for s in wf_result.oos_sharpes), (
            f"NaN OOS Sharpes: {wf_result.oos_sharpes}"
        )

    def test_walk_forward_fresh_instances_have_no_cross_fold_state_bleed(self):
        """Stronger form: verify that each fold's MLAlpha instance starts
        with _current_model=None (no state bleed from prior folds). This
        guards against a future refactor that accidentally caches the
        factory output."""
        from sklearn.linear_model import Ridge
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.walk_forward import portfolio_walk_forward
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        data, dates = _make_data(n_days=500, n_stocks=6)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(6)])

        initial_states: list[tuple] = []

        def strategy_factory():
            alpha = MLAlpha(
                name="ridge",
                model_factory=lambda: Ridge(alpha=1.0),
                feature_fn=_simple_feature_fn,
                target_fn=_forward_return_target(5),
                train_window=60, retrain_freq=20, purge_days=5,
            )
            # Capture the initial state IMMEDIATELY after construction —
            # before walk_forward has a chance to call compute()
            initial_states.append((
                alpha._current_model is None,
                alpha._retrain_count,
                alpha._last_retrain_date,
            ))
            return TopNRotation(factor=alpha, top_n=3)

        portfolio_walk_forward(
            strategy_factory=strategy_factory,
            universe=universe,
            universe_data=data,
            calendar=cal,
            start=dates[60].date(),
            end=dates[-1].date(),
            n_splits=3,
            train_ratio=0.7,
            freq="weekly",
        )

        # Every factory call produced a fresh MLAlpha with zero state
        assert len(initial_states) == 6
        for idx, (is_none, count, last_dt) in enumerate(initial_states):
            assert is_none is True, f"Fold call {idx}: _current_model was not None"
            assert count == 0, f"Fold call {idx}: _retrain_count was {count}"
            assert last_dt is None, f"Fold call {idx}: _last_retrain_date was {last_dt}"
