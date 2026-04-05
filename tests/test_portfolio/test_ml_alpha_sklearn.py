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
