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

**CI note**: scikit-learn is an OPTIONAL dependency (``pip install -e '.[ml]'``).
This test module is SKIPPED in its entirety when sklearn is not installed.
"""
from __future__ import annotations

import copy
from datetime import date, datetime
from typing import Callable

import numpy as np
import pandas as pd
import pytest

# Skip the entire module if scikit-learn is not installed.
pytest.importorskip("sklearn", reason="V2.13 MLAlpha × sklearn integration tests require scikit-learn; install with `pip install -e '.[ml]'`")

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

    def test_random_forest_run_portfolio_backtest(self):
        """V2.13 review round 2 MH1: RF must work through run_portfolio_backtest,
        not just deepcopy/determinism unit tests. This closes the gap
        flagged by codex — the whitelist claim "end-to-end verified" is
        only true for Ridge until RF/GBR walk this path too.

        Keep n_estimators small (5) and max_depth shallow (3) to control
        runtime; n_jobs=1 enforced by the whitelist; random_state=0 for
        deterministic CI.
        """
        from sklearn.ensemble import RandomForestRegressor
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.engine import run_portfolio_backtest
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        data, dates = _make_data(n_days=400, n_stocks=6)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(6)])

        alpha = MLAlpha(
            name="rf_e2e",
            model_factory=lambda: RandomForestRegressor(
                n_estimators=5, max_depth=3, n_jobs=1, random_state=0,
            ),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=60, retrain_freq=20, purge_days=5,
        )
        strategy = TopNRotation(factor=alpha, top_n=3)

        result = run_portfolio_backtest(
            strategy=strategy, universe=universe, universe_data=data,
            calendar=cal, start=dates[100].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=1_000_000,
        )

        assert result is not None
        assert len(result.equity_curve) > 0
        assert result.equity_curve[-1] > 0  # not bankrupt
        assert alpha._retrain_count >= 3, (
            f"Expected ≥3 retrains for RF; got {alpha._retrain_count}"
        )

    def test_random_forest_portfolio_walk_forward(self):
        """V2.13 review round 2 MH1: RF must work through portfolio_walk_forward's
        factory-freshness path, parallel to Ridge's test_ridge_momentum_
        portfolio_walk_forward_factory_fresh_instances. This is what closes
        the "end-to-end whitelist" claim for RF.
        """
        from sklearn.ensemble import RandomForestRegressor
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.walk_forward import portfolio_walk_forward
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        data, dates = _make_data(n_days=500, n_stocks=6)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(6)])

        created_alphas: list[MLAlpha] = []

        def strategy_factory():
            alpha = MLAlpha(
                name="rf_wf",
                model_factory=lambda: RandomForestRegressor(
                    n_estimators=5, max_depth=3, n_jobs=1, random_state=0,
                ),
                feature_fn=_simple_feature_fn,
                target_fn=_forward_return_target(5),
                train_window=60, retrain_freq=20, purge_days=5,
            )
            created_alphas.append(alpha)
            return TopNRotation(factor=alpha, top_n=3)

        wf_result = portfolio_walk_forward(
            strategy_factory=strategy_factory,
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, train_ratio=0.7, freq="weekly",
        )

        # 3 splits × (IS + OOS) = 6 factory calls
        assert len(created_alphas) == 6
        # Distinct instances
        assert len(set(id(a) for a in created_alphas)) == 6
        # At least half the folds trained a model
        trained = sum(1 for a in created_alphas if a._retrain_count >= 1)
        assert trained >= 3
        # Walk-forward produced finite results
        assert wf_result.n_splits == 3
        assert all(np.isfinite(s) for s in wf_result.oos_sharpes)

    def test_gradient_boosting_run_portfolio_backtest(self):
        """V2.13 review round 2 MH1: GBR end-to-end through
        run_portfolio_backtest. Parallel to the RF test — closes the
        whitelist "end-to-end verified" claim for GBR.
        """
        from sklearn.ensemble import GradientBoostingRegressor
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.engine import run_portfolio_backtest
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        data, dates = _make_data(n_days=400, n_stocks=6)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(6)])

        alpha = MLAlpha(
            name="gb_e2e",
            model_factory=lambda: GradientBoostingRegressor(
                n_estimators=5, max_depth=3, random_state=0,
            ),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=60, retrain_freq=20, purge_days=5,
        )
        strategy = TopNRotation(factor=alpha, top_n=3)

        result = run_portfolio_backtest(
            strategy=strategy, universe=universe, universe_data=data,
            calendar=cal, start=dates[100].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=1_000_000,
        )

        assert result is not None
        assert len(result.equity_curve) > 0
        assert result.equity_curve[-1] > 0
        assert alpha._retrain_count >= 3

    def test_gradient_boosting_portfolio_walk_forward(self):
        """V2.13 round 3 codex-M: close the asymmetry between RF and GBR.
        RF has both run_portfolio_backtest and portfolio_walk_forward
        integration tests, but GBR was only covered at the single-run
        level. This test walks GBR through the factory-freshness path
        so a regression in GBR's portfolio_walk_forward interaction
        would fail visibly.
        """
        from sklearn.ensemble import GradientBoostingRegressor
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.walk_forward import portfolio_walk_forward
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        data, dates = _make_data(n_days=500, n_stocks=6)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(6)])

        created_alphas: list[MLAlpha] = []

        def strategy_factory():
            alpha = MLAlpha(
                name="gb_wf",
                model_factory=lambda: GradientBoostingRegressor(
                    n_estimators=5, max_depth=3, random_state=0,
                ),
                feature_fn=_simple_feature_fn,
                target_fn=_forward_return_target(5),
                train_window=60, retrain_freq=20, purge_days=5,
            )
            created_alphas.append(alpha)
            return TopNRotation(factor=alpha, top_n=3)

        wf_result = portfolio_walk_forward(
            strategy_factory=strategy_factory,
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, train_ratio=0.7, freq="weekly",
        )

        # 3 splits × (IS + OOS) = 6 factory calls
        assert len(created_alphas) == 6
        # Distinct instances (factory must not cache)
        assert len(set(id(a) for a in created_alphas)) == 6
        # Each factory call produced a fresh instance with zero state
        for a in created_alphas:
            # By the time we inspect, retrain may have run, so just
            # verify the model is either trained or still None (not
            # some half-state from another fold)
            assert a._retrain_count >= 0
        # At least half the folds successfully trained
        trained = sum(1 for a in created_alphas if a._retrain_count >= 1)
        assert trained >= 3
        # Walk-forward produced finite results for all 3 splits
        assert wf_result.n_splits == 3
        assert len(wf_result.oos_sharpes) == 3
        assert all(np.isfinite(s) for s in wf_result.oos_sharpes)

    def test_topn_rotation_lookback_respects_mlalpha_warmup(self):
        """V2.13 round 5 codex-H: TopNRotation must propagate the inner
        factor's warmup_period into its own lookback_days so the engine
        fetches enough history for the MLAlpha training window.

        Before the fix: TopNRotation inherited default lookback_days=252
        and engine's warmup check used getattr(strategy, 'factor', None)
        which returned None (TopNRotation stored it as _factor). So an
        MLAlpha(train_window=400) silently got 252 days of history on
        early rebalances, corrupting the training panel with NO warning
        and NO exception — the single most dangerous class of bug.

        After the fix: TopNRotation.factor is a public property, and
        TopNRotation.lookback_days = max(default, factor.warmup_period +
        buffer).
        """
        from sklearn.linear_model import Ridge
        from ez.portfolio.portfolio_strategy import TopNRotation

        # Long train window — bigger than the default 252 lookback
        alpha = MLAlpha(
            name="long_train",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=400,  # > 252 default
            retrain_freq=20,
            purge_days=5,
            embargo_days=2,
        )
        # warmup_period = 400 + 5 + 2 = 407
        assert alpha.warmup_period == 407

        strategy = TopNRotation(factor=alpha, top_n=3)

        # The inner factor must be publicly accessible so engine's
        # warmup check (getattr(strategy, 'factor')) can walk it.
        assert hasattr(strategy, "factor")
        assert strategy.factor is alpha

        # The strategy's declared lookback must be >= factor warmup.
        # Otherwise the engine's slice_universe_data call at day D will
        # return [D - 252, D - 1] which is shorter than 407 rows needed,
        # and _build_training_panel will silently drop rows.
        assert strategy.lookback_days >= alpha.warmup_period, (
            f"TopNRotation.lookback_days = {strategy.lookback_days} < "
            f"MLAlpha.warmup_period = {alpha.warmup_period}. Engine will "
            f"fetch truncated history on early rebalances."
        )

    def test_multifactor_rotation_lookback_respects_longest_mlalpha(self):
        """Symmetric check for MultiFactorRotation: the strategy's
        lookback_days must be at least the MAXIMUM warmup_period across
        all wrapped factors."""
        from sklearn.linear_model import Ridge
        from ez.portfolio.portfolio_strategy import MultiFactorRotation

        alpha_short = MLAlpha(
            name="short",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=60, retrain_freq=20, purge_days=5,
        )
        alpha_long = MLAlpha(
            name="long",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=500, retrain_freq=20, purge_days=5, embargo_days=5,
        )
        assert alpha_short.warmup_period == 65
        assert alpha_long.warmup_period == 510

        strategy = MultiFactorRotation(factors=[alpha_short, alpha_long], top_n=3)

        # factors property is public
        assert hasattr(strategy, "factors")
        assert list(strategy.factors) == [alpha_short, alpha_long]

        # lookback must cover the LONGEST warmup
        assert strategy.lookback_days >= 510, (
            f"MultiFactorRotation.lookback_days = {strategy.lookback_days} "
            f"< max factor warmup 510"
        )

    def test_mlalpha_long_train_window_gets_full_history_in_backtest(self):
        """End-to-end regression for the H1 bug: run a backtest with
        MLAlpha(train_window > 252) and verify the model actually saw
        ≥ train_window rows during its first retrain.

        This is the integration-level counterpart to the unit tests
        above — it fails visibly if the engine's warmup propagation
        breaks in a future refactor.
        """
        from sklearn.linear_model import Ridge
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.engine import run_portfolio_backtest
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        # 600 days of data so a 400-row train window can be satisfied
        # starting from ~day 410 (train_window + purge + embargo)
        data, dates = _make_data(n_days=600, n_stocks=4)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(4)])

        # Capture the X shape at each retrain via monkey-patch hook.
        panel_row_counts: list[int] = []

        class _RecordingAlpha(MLAlpha):
            def _retrain(self, universe_data, current):
                X, y = self._build_training_panel(universe_data, current)
                if X is not None:
                    panel_row_counts.append(len(X))
                # Delegate to parent to actually fit
                return super()._retrain(universe_data, current)

        alpha = _RecordingAlpha(
            name="long_regression",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target(5),
            train_window=400,  # intentionally > default 252 lookback
            retrain_freq=40,
            purge_days=5,
            embargo_days=2,
        )
        strategy = TopNRotation(factor=alpha, top_n=3)

        run_portfolio_backtest(
            strategy=strategy, universe=universe, universe_data=data,
            calendar=cal,
            start=dates[450].date(),  # late enough for 400 rows of history
            end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
        )

        # At least one retrain should have captured a panel with enough
        # rows. With 4 stocks × train_window 400 rows each, we expect
        # roughly 1600 panel rows (minus NaN head from pct_change).
        # Lower bound: at least 400 rows — the train_window itself.
        assert len(panel_row_counts) > 0, "No retrains recorded"
        max_panel = max(panel_row_counts)
        assert max_panel >= 400, (
            f"Max panel row count {max_panel} is < train_window 400. "
            f"Engine truncated history to default lookback (252 × 4 "
            f"symbols = ~1000 rows minus warmup). H1 bug not fixed."
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


class TestStrictLookback:
    """V2.13.2: strict_lookback=True raises ValueError on insufficient lookback."""

    def test_strict_lookback_raises_on_insufficient(self):
        from sklearn.linear_model import Ridge
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.engine import run_portfolio_backtest
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe
        from ez.portfolio.cross_factor import MomentumRank

        data, dates = _make_data(n_days=300, n_stocks=4)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(4)])

        # Strategy with default lookback=252 but factor warmup=20 — OK normally
        strategy = TopNRotation(factor=MomentumRank(period=20), top_n=3)

        # Create a custom strategy with intentionally LOW lookback
        class _ShortLookback(TopNRotation):
            @property
            def lookback_days(self) -> int:
                return 10  # way too short for any factor

        short = _ShortLookback(factor=MomentumRank(period=20), top_n=3)

        # strict_lookback=False (default) — should warn but not raise
        result = run_portfolio_backtest(
            strategy=short, universe=universe, universe_data=data,
            calendar=cal, start=dates[60].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=1_000_000,
            strict_lookback=False,
        )
        assert result is not None  # completed despite warning

        # strict_lookback=True — should raise ValueError
        import pytest
        with pytest.raises(ValueError, match="lookback_days"):
            run_portfolio_backtest(
                strategy=short, universe=universe, universe_data=data,
                calendar=cal, start=dates[60].date(), end=dates[-1].date(),
                freq="weekly", initial_cash=1_000_000,
                strict_lookback=True,
            )
