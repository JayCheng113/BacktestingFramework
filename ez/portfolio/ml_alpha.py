"""V2.13 F8: MLAlpha — walk-forward ML factor framework.

MLAlpha is a stateful CrossSectionalFactor that holds a trained sklearn-
compatible model internally and retrains lazily when the current eval
date moves beyond the model's validity window.

Anti-lookahead is enforced at two layers:
1. The framework calls ``compute(sliced_data, dt)`` where ``sliced_data``
   already excludes dates ``>= dt`` (via ``slice_universe_data``, strict
   ``<``).
2. ``_build_training_panel()`` further excludes training samples whose
   forward-looking labels would leak into the prediction window via
   ``purge_days + embargo_days`` margin.

Model persistence is **in-memory only** (V1). Cross-fold sharing is NOT
supported.

In the portfolio walk-forward flow (``ez/portfolio/walk_forward.py``),
``portfolio_walk_forward`` calls ``strategy_factory()`` once per fold per
stage (IS, OOS) and the factory returns a brand-new ``MLAlpha`` instance
with ``_current_model = None`` — **the factory IS the isolation
mechanism, not ``copy.deepcopy``**.

The single-stock backtest walk-forward (``ez/backtest/walk_forward.py``)
does use ``copy.deepcopy(strategy)`` per fold, which is a separate code
path; MLAlpha supports both because the class is also deepcopy-safe as
a generic Python value (verified in
``tests/test_portfolio/test_ml_alpha_sklearn.py``).

Safety (V1):
- Hard whitelist of sklearn estimator classes at construction time.
  Adding a class to the whitelist requires (a) a deepcopy/determinism
  regression test, (b) a sandbox smoke test, (c) an explicit plan entry.
- ``n_jobs=1`` enforced via runtime inspection of the estimator instance
  returned by ``model_factory()`` — this catches dynamic / wrapped /
  ``**kwargs``-passed values that a pure AST scan cannot see.
- User code never writes models to disk (``pickle`` is blocked by the
  sandbox).

See ``docs/superpowers/plans/2026-04-06-v213-ml-alpha.md`` for design
rationale.
"""
from __future__ import annotations

from datetime import date as _date, datetime, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd

from ez.portfolio.cross_factor import CrossSectionalFactor


FeatureFn = Callable[[pd.DataFrame], pd.DataFrame]
TargetFn = Callable[[pd.DataFrame], pd.Series]
ModelFactory = Callable[[], Any]


class UnsupportedEstimatorError(TypeError):
    """Raised when MLAlpha is constructed with an estimator class that is
    not on the V1 whitelist, or with an instance whose ``n_jobs`` would
    trigger ``multiprocessing`` (blocked by the sandbox)."""


def _build_supported_estimator_set() -> frozenset[type]:
    """Construct the V1 whitelist of sklearn estimator classes.

    Built lazily (not at module import) so that ``import
    ez.portfolio.ml_alpha`` works even when sklearn is not installed —
    only the first ``MLAlpha`` construction triggers the sklearn import.

    Adding a class to this set is NOT free: it requires
    (1) a deepcopy/determinism regression test in
        ``test_ml_alpha_sklearn.py``;
    (2) a sandbox smoke test confirming import + fit under the sandbox;
    (3) a task in the plan explicitly calling out the addition.
    """
    try:
        from sklearn.linear_model import (
            Ridge,
            Lasso,
            LinearRegression,
            ElasticNet,
        )
        from sklearn.tree import DecisionTreeRegressor
        from sklearn.ensemble import (
            RandomForestRegressor,
            GradientBoostingRegressor,
        )
    except ImportError as e:
        raise ImportError(
            "scikit-learn>=1.5 is required for MLAlpha. "
            "Install with: pip install -e '.[ml]'"
        ) from e
    return frozenset({
        Ridge,
        Lasso,
        LinearRegression,
        ElasticNet,
        DecisionTreeRegressor,
        RandomForestRegressor,
        GradientBoostingRegressor,
    })


_SUPPORTED_ESTIMATOR_CACHE: frozenset[type] | None = None


def _assert_supported_estimator(instance: Any) -> None:
    """Enforce the V1 estimator whitelist + ``n_jobs=1`` rule at runtime.

    Uses ``type(instance)`` identity comparison rather than ``isinstance``
    to avoid accidentally accepting user subclasses that might override
    ``fit()`` with unsafe behavior. If an advanced user legitimately
    needs a subclass, they can add it to the whitelist explicitly.
    """
    global _SUPPORTED_ESTIMATOR_CACHE
    if _SUPPORTED_ESTIMATOR_CACHE is None:
        _SUPPORTED_ESTIMATOR_CACHE = _build_supported_estimator_set()

    cls = type(instance)
    if cls not in _SUPPORTED_ESTIMATOR_CACHE:
        allowed = sorted(c.__name__ for c in _SUPPORTED_ESTIMATOR_CACHE)
        raise UnsupportedEstimatorError(
            f"Estimator class {cls.__module__}.{cls.__name__} is not on "
            f"the V1 MLAlpha whitelist. Supported classes: {allowed}. "
            f"If you need another estimator, add it to the whitelist with "
            f"(a) a deepcopy regression test, (b) a sandbox smoke test, "
            f"and (c) an explicit plan-file entry."
        )

    n_jobs = getattr(instance, "n_jobs", None)
    if n_jobs is not None and n_jobs != 1:
        raise UnsupportedEstimatorError(
            f"Estimator {cls.__name__} has n_jobs={n_jobs}, but the "
            f"sandbox blocks `multiprocessing`. Construct with "
            f"n_jobs=1 explicitly."
        )


class MLAlpha(CrossSectionalFactor):
    """Walk-forward ML factor. See module docstring for design.

    Args:
        name: Unique factor name (e.g., ``"ridge_momentum_v1"``).
        model_factory: Callable returning a fresh unfit estimator. MUST
            return a NEW instance each call — the framework will invoke
            it multiple times across retrain boundaries and at runtime
            whitelist-check time.
        feature_fn: Per-symbol feature extractor. Called with a
            single-symbol DataFrame (sliced to training window), returns
            a DataFrame of features indexed by date.
        target_fn: Per-symbol target extractor. Called with the same
            single-symbol DataFrame, returns a Series of forward-looking
            labels aligned to the feature dates.
        train_window: Number of trailing trading days to use for each
            retrain's training panel.
        retrain_freq: Retrain when current prediction date exceeds last
            retrain date by this many calendar days.
        purge_days: Exclude the last N training dates before the
            prediction date to prevent feature-label temporal overlap.
            MUST be at least the target's forward horizon.
        embargo_days: Additional buffer on top of purge_days. Defaults
            to 0 (purge is already the minimum safe gap).
    """

    def __init__(
        self,
        name: str,
        model_factory: ModelFactory,
        feature_fn: FeatureFn,
        target_fn: TargetFn,
        train_window: int,
        retrain_freq: int,
        purge_days: int,
        embargo_days: int = 0,
    ):
        if train_window <= 0:
            raise ValueError(f"train_window must be > 0, got {train_window}")
        if retrain_freq <= 0:
            raise ValueError(f"retrain_freq must be > 0, got {retrain_freq}")
        if purge_days < 0:
            raise ValueError(f"purge_days must be >= 0, got {purge_days}")
        if embargo_days < 0:
            raise ValueError(f"embargo_days must be >= 0, got {embargo_days}")

        self._name = name
        self._model_factory = model_factory
        self._feature_fn = feature_fn
        self._target_fn = target_fn
        self._train_window = train_window
        self._retrain_freq = retrain_freq
        self._purge_days = purge_days
        self._embargo_days = embargo_days

        # V1 safety: validate the estimator BEFORE any fit happens. Raises
        # UnsupportedEstimatorError if the class is not on the whitelist
        # or if n_jobs is set to something other than 1/None. This runs
        # once at construction so the user gets an immediate failure, not
        # a mysterious multiprocessing crash deep inside fit().
        _assert_supported_estimator(model_factory())

        # Runtime state. A fresh MLAlpha instance (from strategy_factory())
        # starts with _current_model=None, so portfolio walk-forward gets
        # per-fold isolation "for free". copy.deepcopy also works
        # (supported as a generic Python value) for single-stock
        # WalkForwardValidator compatibility.
        self._current_model: Any = None
        self._last_retrain_date: _date | None = None
        self._retrain_count: int = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def warmup_period(self) -> int:
        # Need at least train_window + purge + embargo days of history
        # before the first prediction date can be made.
        return self._train_window + self._purge_days + self._embargo_days

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Return per-symbol predictions at date ``date``.

        Placeholder for Phase 1 Task 1.4 — returns an empty Series so the
        skeleton tests can import and instantiate without NotImplementedError.
        """
        return pd.Series(dtype=float)

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Raw predictions (no ranking). Used by AlphaCombiner.

        Placeholder for Phase 1 Task 1.4 — same as ``compute()``.
        """
        return self.compute(universe_data, date)


# Prevent auto-registration: MLAlpha is a BASE class that users instantiate
# via subclasses in ml_alphas/ (or via direct constructor calls at the
# Python API level). The factor dropdown should only show concrete user
# subclasses, not the base class itself — the base class cannot be built
# with zero args and has no meaningful default model/feature/target fns.
#
# Mirrors ez/portfolio/alpha_combiner.py's pattern. Dual-dict registry:
# must pop from BOTH _registry (name) and _registry_by_key (module.class)
# to avoid leaving a zombie entry that resolve_class() could return.
CrossSectionalFactor._registry.pop("MLAlpha", None)
_mla_key = f"{MLAlpha.__module__}.MLAlpha"
CrossSectionalFactor._registry_by_key.pop(_mla_key, None)
