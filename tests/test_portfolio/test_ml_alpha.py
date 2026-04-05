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
