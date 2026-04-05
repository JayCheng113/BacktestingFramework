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

from datetime import date, datetime
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
