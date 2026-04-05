"""V2.13 Phase 1: MLAlpha base class unit tests.

Covers:
- Module/class import path
- Inheritance from CrossSectionalFactor
- Constructor parameter acceptance + validation
- V1 safety layer: estimator whitelist + n_jobs runtime enforcement

Real sklearn integration tests (deepcopy, end-to-end walk-forward) are in
tests/test_portfolio/test_ml_alpha_sklearn.py — this file uses sklearn
only for simple construction smoke and does NOT train any models.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd
import pytest


def test_mlalpha_import():
    """MLAlpha can be imported from ez.portfolio.ml_alpha."""
    from ez.portfolio.ml_alpha import MLAlpha
    assert MLAlpha is not None


def test_mlalpha_is_cross_sectional_factor():
    """MLAlpha inherits from CrossSectionalFactor so it's compatible with
    the existing factor pipeline (TopNRotation, CrossSectionalEvaluator,
    AlphaCombiner, registry, etc.)."""
    from ez.portfolio.ml_alpha import MLAlpha
    from ez.portfolio.cross_factor import CrossSectionalFactor
    assert issubclass(MLAlpha, CrossSectionalFactor)


def test_unsupported_estimator_error_importable():
    """UnsupportedEstimatorError is part of the public API so callers can
    catch it and show a friendly message."""
    from ez.portfolio.ml_alpha import UnsupportedEstimatorError
    assert issubclass(UnsupportedEstimatorError, TypeError)


def test_mlalpha_init_requires_four_callables():
    """MLAlpha must be constructed with model_factory, feature_fn,
    target_fn (three callables) plus sizing parameters."""
    from ez.portfolio.ml_alpha import MLAlpha
    from sklearn.linear_model import Ridge

    def feature_fn(df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"ret1": df["adj_close"].pct_change(1)}).dropna()

    def target_fn(df: pd.DataFrame) -> pd.Series:
        return df["adj_close"].pct_change(5).shift(-5)

    alpha = MLAlpha(
        name="test_mlalpha",
        model_factory=lambda: Ridge(alpha=1.0),
        feature_fn=feature_fn,
        target_fn=target_fn,
        train_window=120,
        retrain_freq=20,
        purge_days=5,
        embargo_days=2,
    )
    assert alpha.name == "test_mlalpha"
    assert alpha.warmup_period == 120 + 5 + 2  # train_window + purge + embargo


def test_mlalpha_fresh_instance_has_no_fitted_state():
    """A newly-constructed MLAlpha must have _current_model is None and
    _retrain_count == 0. This is what makes strategy_factory() per-fold
    isolation work in portfolio_walk_forward — each fold's factory call
    returns a fresh instance with zero prior state."""
    from ez.portfolio.ml_alpha import MLAlpha
    from sklearn.linear_model import Ridge

    alpha = MLAlpha(
        name="t",
        model_factory=lambda: Ridge(alpha=1.0),
        feature_fn=lambda df: pd.DataFrame({"f": df["adj_close"].pct_change(1)}).dropna(),
        target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
        train_window=60, retrain_freq=20, purge_days=5,
    )
    assert alpha._current_model is None
    assert alpha._last_retrain_date is None
    assert alpha._retrain_count == 0


class TestMLAlphaValidation:
    """Constructor must reject invalid size parameters early. These are
    simple ValueError checks — the whitelist / n_jobs safety layer is
    tested in TestMLAlphaEstimatorWhitelist."""

    @pytest.fixture
    def valid_kwargs(self):
        from sklearn.linear_model import Ridge
        return dict(
            name="x",
            model_factory=lambda: Ridge(),
            feature_fn=lambda df: pd.DataFrame({"f": df["adj_close"].pct_change(1)}).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=60,
            retrain_freq=20,
            purge_days=5,
            embargo_days=0,
        )

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_train_window_must_be_positive(self, valid_kwargs, bad):
        from ez.portfolio.ml_alpha import MLAlpha
        valid_kwargs["train_window"] = bad
        with pytest.raises(ValueError, match="train_window"):
            MLAlpha(**valid_kwargs)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_retrain_freq_must_be_positive(self, valid_kwargs, bad):
        from ez.portfolio.ml_alpha import MLAlpha
        valid_kwargs["retrain_freq"] = bad
        with pytest.raises(ValueError, match="retrain_freq"):
            MLAlpha(**valid_kwargs)

    @pytest.mark.parametrize("bad", [-1, -5])
    def test_purge_days_must_be_non_negative(self, valid_kwargs, bad):
        from ez.portfolio.ml_alpha import MLAlpha
        valid_kwargs["purge_days"] = bad
        with pytest.raises(ValueError, match="purge_days"):
            MLAlpha(**valid_kwargs)

    @pytest.mark.parametrize("bad", [-1, -3])
    def test_embargo_days_must_be_non_negative(self, valid_kwargs, bad):
        from ez.portfolio.ml_alpha import MLAlpha
        valid_kwargs["embargo_days"] = bad
        with pytest.raises(ValueError, match="embargo_days"):
            MLAlpha(**valid_kwargs)

    def test_purge_days_zero_allowed(self, valid_kwargs):
        """purge_days=0 is allowed (no purge window). User must then
        ensure their target_fn doesn't look forward, otherwise there's
        no protection against label leakage."""
        from ez.portfolio.ml_alpha import MLAlpha
        valid_kwargs["purge_days"] = 0
        alpha = MLAlpha(**valid_kwargs)
        assert alpha.warmup_period == 60  # train_window + 0 + 0

    def test_embargo_days_zero_default(self, valid_kwargs):
        """embargo_days defaults to 0 when not provided."""
        from ez.portfolio.ml_alpha import MLAlpha
        del valid_kwargs["embargo_days"]
        alpha = MLAlpha(**valid_kwargs)
        assert alpha._embargo_days == 0


class TestMLAlphaEstimatorWhitelist:
    """V1 safety: only whitelisted sklearn estimator classes are accepted,
    and n_jobs=1 is enforced at construction.

    This is the PRIMARY safety layer. Phase 4's AST literal n_jobs check
    is only a nice-to-have early warning; the runtime check here catches
    dynamic / wrapped / **kwargs / variable-bound n_jobs values that a
    pure AST scan cannot see.
    """

    @pytest.fixture
    def base_kwargs(self):
        return dict(
            name="x",
            feature_fn=lambda df: pd.DataFrame({"f": df["adj_close"]}),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=60,
            retrain_freq=20,
            purge_days=5,
        )

    def test_ridge_is_accepted(self, base_kwargs):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge
        alpha = MLAlpha(model_factory=lambda: Ridge(alpha=1.0), **base_kwargs)
        assert alpha is not None

    def test_lasso_is_accepted(self, base_kwargs):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Lasso
        alpha = MLAlpha(model_factory=lambda: Lasso(alpha=0.1), **base_kwargs)
        assert alpha is not None

    def test_linear_regression_is_accepted(self, base_kwargs):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import LinearRegression
        alpha = MLAlpha(model_factory=lambda: LinearRegression(), **base_kwargs)
        assert alpha is not None

    def test_elastic_net_is_accepted(self, base_kwargs):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import ElasticNet
        alpha = MLAlpha(model_factory=lambda: ElasticNet(alpha=0.1), **base_kwargs)
        assert alpha is not None

    def test_decision_tree_regressor_is_accepted(self, base_kwargs):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.tree import DecisionTreeRegressor
        alpha = MLAlpha(
            model_factory=lambda: DecisionTreeRegressor(max_depth=3, random_state=0),
            **base_kwargs,
        )
        assert alpha is not None

    def test_gradient_boosting_regressor_is_accepted(self, base_kwargs):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.ensemble import GradientBoostingRegressor
        alpha = MLAlpha(
            model_factory=lambda: GradientBoostingRegressor(n_estimators=5, random_state=0),
            **base_kwargs,
        )
        assert alpha is not None

    def test_random_forest_n_jobs_1_accepted(self, base_kwargs):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.ensemble import RandomForestRegressor
        alpha = MLAlpha(
            model_factory=lambda: RandomForestRegressor(n_jobs=1, n_estimators=5, random_state=0),
            **base_kwargs,
        )
        assert alpha is not None

    def test_random_forest_n_jobs_minus_one_rejected(self, base_kwargs):
        """n_jobs=-1 must raise at construction, BEFORE any fit() runs."""
        from ez.portfolio.ml_alpha import MLAlpha, UnsupportedEstimatorError
        from sklearn.ensemble import RandomForestRegressor
        with pytest.raises(UnsupportedEstimatorError, match="n_jobs"):
            MLAlpha(
                model_factory=lambda: RandomForestRegressor(n_jobs=-1),
                **base_kwargs,
            )

    def test_random_forest_n_jobs_2_rejected(self, base_kwargs):
        """n_jobs=2 (or any value != 1/None) must raise."""
        from ez.portfolio.ml_alpha import MLAlpha, UnsupportedEstimatorError
        from sklearn.ensemble import RandomForestRegressor
        with pytest.raises(UnsupportedEstimatorError, match="n_jobs"):
            MLAlpha(
                model_factory=lambda: RandomForestRegressor(n_jobs=2),
                **base_kwargs,
            )

    def test_random_forest_n_jobs_none_accepted(self, base_kwargs):
        """n_jobs=None (sklearn default for some estimators) is treated
        as 'not set' and allowed through. sklearn's own joblib layer will
        run in single-process mode when n_jobs is None."""
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.ensemble import RandomForestRegressor
        alpha = MLAlpha(
            model_factory=lambda: RandomForestRegressor(n_jobs=None, n_estimators=5, random_state=0),
            **base_kwargs,
        )
        assert alpha is not None

    def test_non_whitelisted_sklearn_class_rejected(self, base_kwargs):
        """sklearn has many estimators. MLAlpha V1 whitelists only a
        small verified subset. SVR is NOT on V1's list — verify rejection
        with a user-visible message mentioning the whitelist."""
        from ez.portfolio.ml_alpha import MLAlpha, UnsupportedEstimatorError
        from sklearn.svm import SVR
        with pytest.raises(UnsupportedEstimatorError, match="whitelist"):
            MLAlpha(model_factory=lambda: SVR(), **base_kwargs)

    def test_arbitrary_python_object_rejected(self, base_kwargs):
        """A plain Python object with a .fit method is not an sklearn
        estimator and is rejected."""
        from ez.portfolio.ml_alpha import MLAlpha, UnsupportedEstimatorError

        class FakeEstimator:
            def fit(self, X, y): return self
            def predict(self, X): return np.zeros(len(X))

        with pytest.raises(UnsupportedEstimatorError):
            MLAlpha(model_factory=lambda: FakeEstimator(), **base_kwargs)

    def test_user_subclass_of_ridge_rejected(self, base_kwargs):
        """Even a Ridge subclass is rejected — type identity, not isinstance.
        This blocks users from monkey-patching fit() via inheritance as a
        sandbox bypass."""
        from ez.portfolio.ml_alpha import MLAlpha, UnsupportedEstimatorError
        from sklearn.linear_model import Ridge

        class MyRidge(Ridge):
            pass

        with pytest.raises(UnsupportedEstimatorError, match="whitelist"):
            MLAlpha(model_factory=lambda: MyRidge(), **base_kwargs)

    def test_unsupported_error_message_lists_allowed_classes(self, base_kwargs):
        """The error message must tell the user which classes ARE allowed,
        so they can pick a substitute. Otherwise the user has to read the
        source to find the whitelist."""
        from ez.portfolio.ml_alpha import MLAlpha, UnsupportedEstimatorError
        from sklearn.svm import SVR
        try:
            MLAlpha(model_factory=lambda: SVR(), **base_kwargs)
            pytest.fail("Expected UnsupportedEstimatorError")
        except UnsupportedEstimatorError as e:
            msg = str(e)
            # At least Ridge should be mentioned
            assert "Ridge" in msg
            # The message should mention the word "whitelist"
            assert "whitelist" in msg.lower()


def _make_universe_df(n_days: int = 200, n_stocks: int = 5, seed: int = 42):
    """Build a deterministic multi-stock universe for MLAlpha testing."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    data = {}
    for i in range(n_stocks):
        # Different drift per stock so rankings vary
        prices = 100 * np.cumprod(1 + rng.normal(0.0003 * (i + 1), 0.012, n_days))
        data[f"S{i:02d}"] = pd.DataFrame({
            "open": prices, "high": prices * 1.005, "low": prices * 0.995,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
        }, index=dates)
    return data, dates


class TestMLAlphaLazyRetrain:
    """MLAlpha must call _retrain on first compute call and skip
    subsequent calls within retrain_freq."""

    def test_compute_triggers_retrain_on_first_call(self):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge

        data, dates = _make_universe_df(n_days=200)
        alpha = MLAlpha(
            name="t",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=lambda df: pd.DataFrame({
                "ret1": df["adj_close"].pct_change(1),
                "ret5": df["adj_close"].pct_change(5),
            }).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=60,
            retrain_freq=20,
            purge_days=5,
        )
        assert alpha._retrain_count == 0
        assert alpha._current_model is None

        # First compute at ~150 bars in — enough data for a 60-day train
        # window plus 5-day purge.
        dt = datetime(2022, 8, 1)
        alpha.compute(data, dt)

        assert alpha._retrain_count == 1
        assert alpha._current_model is not None
        assert alpha._last_retrain_date == dt.date()

    def test_compute_skips_retrain_within_freq(self):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge

        data, dates = _make_universe_df(n_days=200)
        alpha = MLAlpha(
            name="t",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=lambda df: pd.DataFrame({
                "f": df["adj_close"].pct_change(1),
            }).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=60,
            retrain_freq=20,
            purge_days=5,
        )
        alpha.compute(data, datetime(2022, 8, 1))
        assert alpha._retrain_count == 1

        # 9 days later — within retrain_freq=20 — must NOT retrain
        alpha.compute(data, datetime(2022, 8, 10))
        assert alpha._retrain_count == 1

        # 31 days later — beyond retrain_freq=20 — must retrain
        alpha.compute(data, datetime(2022, 9, 1))
        assert alpha._retrain_count == 2


class TestBuildTrainingPanel:
    """Training panel must exclude samples within purge+embargo window."""

    def _make_alpha(self, purge=5, embargo=0, train_window=60):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge
        return MLAlpha(
            name="t",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=lambda df: pd.DataFrame({
                "ret1": df["adj_close"].pct_change(1),
            }).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=train_window,
            retrain_freq=20,
            purge_days=purge,
            embargo_days=embargo,
        )

    def test_training_panel_not_empty(self):
        alpha = self._make_alpha()
        data, dates = _make_universe_df(200)
        X, y = alpha._build_training_panel(data, dates[150].date())
        assert X is not None
        assert y is not None
        assert len(X) > 0
        assert len(X) == len(y)
        assert X.shape[1] == 1  # one feature: ret1

    def test_training_panel_excludes_purge_window(self):
        """With purge_days=5 and prediction_date=dates[150], the training
        panel must NOT include any feature dates >= dates[145] (by
        trading-day arithmetic purge_days is a CALENDAR day offset, so
        we check against prediction_date - 5 calendar days)."""
        alpha = self._make_alpha(purge=5)
        data, dates = _make_universe_df(200)
        prediction_date = dates[150].date()
        purge_cutoff = prediction_date - timedelta(days=5)

        X, y = alpha._build_training_panel(data, prediction_date)
        assert X is not None
        # X is a MultiIndex (date, symbol)
        max_date = max(X.index.get_level_values("date")).date()
        assert max_date < purge_cutoff, (
            f"Training panel has sample at {max_date} which is >= "
            f"purge cutoff {purge_cutoff} — feature-label leakage risk"
        )

    def test_training_panel_respects_train_window(self):
        """Training panel's per-symbol slice must only contain the last
        train_window rows (after purge exclusion)."""
        alpha = self._make_alpha(train_window=30, purge=5)
        data, dates = _make_universe_df(200)
        X, y = alpha._build_training_panel(data, dates[150].date())
        assert X is not None
        # Expect at most 30 dates × 5 symbols = 150 rows (minus NaN from
        # pct_change warmup and dropna on the target side). Upper bound
        # is lenient: 30 * 5 = 150.
        assert 0 < len(X) <= 30 * 5, f"Expected ≤150 rows, got {len(X)}"

    def test_training_panel_embargo_adds_to_purge(self):
        """embargo_days=3 extends the purge window by 3 additional days."""
        alpha = self._make_alpha(purge=5, embargo=3)
        data, dates = _make_universe_df(200)
        prediction_date = dates[150].date()
        purge_embargo_cutoff = prediction_date - timedelta(days=5 + 3)

        X, y = alpha._build_training_panel(data, prediction_date)
        assert X is not None
        max_date = max(X.index.get_level_values("date")).date()
        assert max_date < purge_embargo_cutoff


class TestMLAlphaPredict:
    """compute() returns a Series indexed by symbol with model predictions."""

    def test_compute_returns_series_with_symbols_as_index(self):
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge

        data, dates = _make_universe_df(n_days=200, n_stocks=4)
        alpha = MLAlpha(
            name="t",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=lambda df: pd.DataFrame({
                "ret1": df["adj_close"].pct_change(1),
                "ret5": df["adj_close"].pct_change(5),
            }).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=60,
            retrain_freq=20,
            purge_days=5,
        )
        scores = alpha.compute(data, datetime(2022, 8, 1))
        assert isinstance(scores, pd.Series)
        assert len(scores) == 4
        assert set(scores.index) == {"S00", "S01", "S02", "S03"}
        assert scores.notna().all()
        assert np.isfinite(scores.values).all()

    def test_compute_predictions_vary_across_symbols(self):
        """Sanity: with stocks that have different drift, predictions
        should NOT all be equal (otherwise the model learned nothing)."""
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge

        data, dates = _make_universe_df(n_days=200, n_stocks=5)
        alpha = MLAlpha(
            name="t",
            model_factory=lambda: Ridge(alpha=0.01),  # low regularization
            feature_fn=lambda df: pd.DataFrame({
                "ret5": df["adj_close"].pct_change(5),
                "ret20": df["adj_close"].pct_change(20),
            }).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=80,
            retrain_freq=20,
            purge_days=5,
        )
        scores = alpha.compute(data, datetime(2022, 8, 1))
        assert len(scores) == 5
        # Not all predictions are identical
        assert scores.nunique() > 1


class TestMLAlphaAntiLookahead:
    """The single most important MLAlpha test class. Any failure here
    means the framework is lying about walk-forward — V2.13 cannot ship.
    """

    def test_engine_slice_enforces_strict_less_than(self):
        """Upstream invariant: slice_universe_data must exclude the
        current date. MLAlpha relies on this for the first anti-lookahead
        layer (the second layer is purge+embargo inside MLAlpha itself)."""
        from ez.portfolio.universe import slice_universe_data

        dates = pd.date_range("2022-01-03", periods=10, freq="B")
        df = pd.DataFrame({
            "open": range(10), "high": range(10), "low": range(10),
            "close": range(10), "adj_close": range(10), "volume": [1.0] * 10,
        }, index=dates)
        universe_data = {"S00": df}

        sliced = slice_universe_data(universe_data, dates[5].date(), lookback_days=20)
        assert "S00" in sliced
        sliced_df = sliced["S00"]
        max_date = sliced_df.index.max()
        assert max_date.date() < dates[5].date(), (
            f"slice_universe_data must use strict `<`, but returned data "
            f"with max date {max_date.date()} >= target {dates[5].date()}"
        )

    def test_compute_never_uses_data_at_or_after_current_date(self):
        """The single most important test. Construct a series where a
        HUGE outlier exists at a future date. Call compute() at a date
        BEFORE the outlier. The model must not see the outlier. A model
        that leaked would produce predictions dominated by the outlier's
        massive feature/target values.
        """
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge

        n_days = 400
        dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
        # Flat series with tiny noise
        rng = np.random.default_rng(0)
        prices_base = 100 + rng.normal(0, 0.01, n_days)
        # Inject HUGE outlier at index 250 — price jumps to 10000
        prices_with_outlier = prices_base.copy()
        prices_with_outlier[250] = 10_000

        data = {
            "S00": pd.DataFrame({
                "open": prices_with_outlier, "high": prices_with_outlier,
                "low": prices_with_outlier, "close": prices_with_outlier,
                "adj_close": prices_with_outlier,
                "volume": np.ones(n_days) * 1e6,
            }, index=dates),
        }

        alpha = MLAlpha(
            name="t",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=lambda df: pd.DataFrame({
                "ret1": df["adj_close"].pct_change(1),
            }).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=100,
            retrain_freq=20,
            purge_days=5,
        )

        # Compute at day 240 — before the outlier at 250
        # purge_days=5 means training only sees features with date <
        # dates[240].date() - 5 calendar days. With B-freq dates (5
        # business days = 7 calendar days), the cutoff at index 240 is
        # approximately around index 235. The outlier at index 250 is
        # far beyond this cutoff, so it must be excluded.
        prediction_date = dates[240].to_pydatetime()
        scores = alpha.compute(data, prediction_date)

        # The model should not have seen the outlier. Ridge trained on
        # near-constant data with tiny daily returns would predict
        # forward 5-day returns near 0 (magnitude ~0.01 * 5 ~ 0.05).
        # If the model leaked the outlier, its learned coefficient on
        # ret1 would be huge, and the current feature row (near the
        # tail of the anti-lookahead slice) would multiply out to a
        # massive prediction.
        assert "S00" in scores.index, (
            "Model did not produce a prediction for S00 — possibly "
            "because _build_training_panel skipped the symbol."
        )
        assert abs(scores["S00"]) < 1.0, (
            f"MLAlpha prediction {scores['S00']} is too large — "
            f"the model likely leaked the outlier at dates[250]={dates[250]} "
            f"into the training panel. Prediction date was {prediction_date.date()}, "
            f"purge cutoff should be approximately {(prediction_date.date() - timedelta(days=5))}."
        )

    def test_build_training_panel_never_contains_prediction_date(self):
        """Stricter form of the outlier test: verify directly that
        _build_training_panel returns X with all date levels < cutoff,
        for multiple prediction dates and multiple purge values."""
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge

        data, dates = _make_universe_df(n_days=300)

        for purge in [0, 3, 5, 10]:
            for pred_idx in [100, 150, 200, 250]:
                alpha = MLAlpha(
                    name="t",
                    model_factory=lambda: Ridge(alpha=1.0),
                    feature_fn=lambda df: pd.DataFrame({
                        "ret1": df["adj_close"].pct_change(1),
                    }).dropna(),
                    target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
                    train_window=60,
                    retrain_freq=20,
                    purge_days=purge,
                )
                pred_date = dates[pred_idx].date()
                X, y = alpha._build_training_panel(data, pred_date)
                if X is None:
                    continue
                max_panel_date = max(X.index.get_level_values("date")).date()
                cutoff = pred_date - timedelta(days=purge)
                assert max_panel_date < cutoff, (
                    f"purge={purge}, pred_idx={pred_idx}: "
                    f"panel max={max_panel_date}, cutoff={cutoff}"
                )
