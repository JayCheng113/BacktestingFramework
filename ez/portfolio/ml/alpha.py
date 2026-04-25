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

See ``docs/internal/plans/2026-04-06-v213-ml-alpha.md`` for design
rationale.
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from ez.portfolio.cross_factor import CrossSectionalFactor

_logger = logging.getLogger(__name__)


def _to_date(x: Any) -> _date:
    """Normalize ``datetime``/``pd.Timestamp``/``date`` → python ``date``.

    Used internally to accept both ``datetime.datetime`` and
    ``datetime.date`` parameter values without shadow collisions with
    the ``date`` name imported from ``datetime``. ``datetime`` is a
    subclass of ``date``, so a strict ``isinstance(x, _date)`` check
    returns True for both — we use duck-typing on ``.date()`` instead.
    """
    # datetime (and pd.Timestamp) have a .date() method; date itself
    # does not. pd.Timestamp.date() returns a python date.
    if hasattr(x, "date") and callable(x.date):
        try:
            return x.date()
        except TypeError:
            pass  # on an actual date, .date is the class, not a method
    return x  # type: ignore[return-value]


FeatureFn = Callable[[pd.DataFrame], pd.DataFrame]
TargetFn = Callable[[pd.DataFrame], pd.Series]
ModelFactory = Callable[[], Any]


# numpy dtype kinds that are legitimate ML features/targets. Used by
# _retrain to reject datetime64 ('M') and timedelta64 ('m') BEFORE they
# hit np.asarray(dtype=float), which would silently coerce them to
# nanosecond-epoch floats (~1.65e18 for 2022 dates) and train the model
# on garbage. V2.13 round 4 reviewer C1.
#   'f' = floating, 'i' = signed int, 'u' = unsigned int, 'b' = bool
_NUMERIC_DTYPE_KINDS = frozenset({"f", "i", "u", "b"})


class UnsupportedEstimatorError(TypeError):
    """Raised when MLAlpha is constructed with an estimator class that is
    not on the V1 whitelist, or with an instance whose ``n_jobs`` would
    trigger ``multiprocessing`` (blocked by the sandbox)."""


def _build_supported_estimator_set() -> frozenset[type]:
    """Construct the estimator whitelist.

    Built lazily (not at module import) so that ``import
    ez.portfolio.ml.alpha`` works even when sklearn is not installed —
    only the first ``MLAlpha`` construction triggers the sklearn import.

    V1 core: 7 sklearn classes (always required).
    V2.14 extensions: LightGBM + XGBoost (optional, graceful skip).

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

    estimators: set[type] = {
        Ridge,
        Lasso,
        LinearRegression,
        ElasticNet,
        DecisionTreeRegressor,
        RandomForestRegressor,
        GradientBoostingRegressor,
    }

    # V2.14: LightGBM (optional — regressor only, classifier deferred
    # until a classification contract is defined)
    # NOTE: except Exception (not just ImportError) because lightgbm can raise
    # OSError when libomp.dylib is missing on macOS even if the package is installed.
    try:
        from lightgbm import LGBMRegressor
        estimators.add(LGBMRegressor)
    except Exception:
        pass

    # V2.14: XGBoost (optional — regressor only, classifier deferred)
    try:
        from xgboost import XGBRegressor
        estimators.add(XGBRegressor)
    except Exception:
        pass

    return frozenset(estimators)


def _assert_supported_estimator(instance: Any) -> None:
    """Enforce the estimator whitelist + ``n_jobs=1`` + GPU rejection.

    Uses ``type(instance)`` identity comparison rather than ``isinstance``
    to avoid accidentally accepting user subclasses that might override
    ``fit()`` with unsafe behavior. If an advanced user legitimately
    needs a subclass, they can add it to the whitelist explicitly.

    The whitelist is rebuilt on every call (no caching) so that libraries
    installed after the first MLAlpha construction are picked up without
    requiring a server restart. The cost is trivial (a few try/import).
    """
    supported = _build_supported_estimator_set()

    cls = type(instance)
    if cls not in supported:
        allowed = sorted(c.__name__ for c in supported)
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

    # V2.14: block GPU acceleration (XGBoost tree_method/device, LightGBM device_type)
    tree_method = getattr(instance, "tree_method", None)
    if tree_method and "gpu" in str(tree_method).lower():
        raise UnsupportedEstimatorError(
            f"Estimator {cls.__name__} uses GPU tree_method='{tree_method}'. "
            f"Only CPU methods are allowed in the sandbox."
        )
    device = getattr(instance, "device", None)
    if device and str(device).lower() in ("cuda", "gpu"):
        raise UnsupportedEstimatorError(
            f"Estimator {cls.__name__} uses device='{device}'. "
            f"Only CPU devices are allowed in the sandbox."
        )
    device_type = getattr(instance, "device_type", None)
    if device_type and "gpu" in str(device_type).lower():
        raise UnsupportedEstimatorError(
            f"Estimator {cls.__name__} uses device_type='{device_type}'. "
            f"Only CPU devices are allowed in the sandbox."
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
        train_window: Number of trailing **trading days** (data rows) to
            use for each retrain's training panel.
        retrain_freq: Retrain when current prediction date exceeds last
            retrain date by this many **calendar days**.
        purge_days: Number of **trading days** (data rows) to drop from
            the tail of the training panel before fitting. MUST be at
            least the target's forward horizon in trading-day units to
            prevent label leakage — if ``target_fn`` is
            ``df.pct_change(5).shift(-5)``, set ``purge_days >= 5``.
            The engine applies purge by positionally trimming the last
            N rows per symbol, matching the unit of ``shift(-k)`` used
            in typical target functions. This is NOT calendar days.
        embargo_days: Additional trading-day buffer on top of purge_days.
            Defaults to 0 (purge alone matches the minimum safe gap).
        feature_warmup_days: Number of **trading days** consumed by the
            ``feature_fn``'s own internal rolling/pct_change warmup.
            Defaults to 0. If your feature_fn uses ``rolling(100).std()``,
            set ``feature_warmup_days=100`` so that ``warmup_period``
            correctly includes this overhead and the engine fetches enough
            history. Without this, the first ``train_window`` rows after
            purge+embargo will be shortened by the feature NaN head,
            silently corrupting the model with fewer samples than you
            intended. A runtime warning fires if actual per-symbol rows
            fall below 90% of ``train_window`` (see ``_retrain``).
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
        feature_warmup_days: int = 0,
    ):
        # Callable-ness validation. Do this FIRST (before the sklearn
        # probe below) so that a None / str / int model_factory raises a
        # TypeError that explicitly names the parameter, not a generic
        # "'NoneType' object is not callable" from inside the probe.
        if not callable(model_factory):
            raise TypeError(
                f"model_factory must be callable (returning a fresh unfit "
                f"sklearn estimator), got {type(model_factory).__name__}"
            )
        if not callable(feature_fn):
            raise TypeError(
                f"feature_fn must be callable (DataFrame → DataFrame), "
                f"got {type(feature_fn).__name__}"
            )
        if not callable(target_fn):
            raise TypeError(
                f"target_fn must be callable (DataFrame → Series), "
                f"got {type(target_fn).__name__}"
            )

        if train_window <= 0:
            raise ValueError(f"train_window must be > 0, got {train_window}")
        if retrain_freq <= 0:
            raise ValueError(f"retrain_freq must be > 0, got {retrain_freq}")
        if purge_days < 0:
            raise ValueError(f"purge_days must be >= 0, got {purge_days}")
        if embargo_days < 0:
            raise ValueError(f"embargo_days must be >= 0, got {embargo_days}")
        if feature_warmup_days < 0:
            raise ValueError(
                f"feature_warmup_days must be >= 0, got {feature_warmup_days}"
            )

        self._name = name
        self._model_factory = model_factory
        self._feature_fn = feature_fn
        self._target_fn = target_fn
        self._train_window = train_window
        self._retrain_freq = retrain_freq
        self._purge_days = purge_days
        self._embargo_days = embargo_days
        self._feature_warmup_days = feature_warmup_days

        # V1 safety: validate the estimator BEFORE any fit happens. Raises
        # UnsupportedEstimatorError if the class is not on the whitelist
        # or if n_jobs is set to something other than 1/None. This runs
        # once at construction so the user gets an immediate failure, not
        # a mysterious multiprocessing crash deep inside fit().
        #
        # The model_factory() probe itself CAN raise — a config error,
        # missing sklearn optional dep, etc. We intentionally let that
        # propagate: at construction time, a broken factory is a hard
        # configuration error that should fail fast. (At retrain time,
        # a factory exception is wrapped and logged — see _retrain.)
        try:
            probe = model_factory()
        except Exception as e:
            raise TypeError(
                f"model_factory() raised {type(e).__name__}: {e}. "
                f"A valid model_factory must return a fresh unfit sklearn "
                f"estimator without errors."
            ) from e
        _assert_supported_estimator(probe)

        # V2.13 round 5 codex-MH: factory MUST return a NEW instance on
        # every call. MLAlpha's per-fold isolation mechanism is built on
        # `strategy_factory()` calling `model_factory()` to get a fresh
        # unfitted model at each retrain. If the user caches a singleton
        # and returns it repeatedly, the same Ridge instance is fit()'d
        # on fold 1, then refit on fold 2's data — but the clone in fold
        # 1 still references the SAME object, so any concurrent use or
        # later retrain sees fold-2 state. Cross-fold state bleed with
        # no exception and no warning.
        #
        # Detection: call factory twice and compare identity. This
        # catches the most common bug (module-level estimator used as
        # factory) but not every possible caching pattern (e.g., a
        # factory that returns a new instance on odd calls and cached
        # on even calls). For those, user error is unrecoverable at
        # construction time — we accept this limitation.
        try:
            probe2 = model_factory()
        except Exception as e:
            raise TypeError(
                f"model_factory()'s second call raised {type(e).__name__}: "
                f"{e}. A valid model_factory must return a fresh unfit "
                f"estimator on every call (called multiple times across "
                f"fold boundaries)."
            ) from e
        # V2.13 round 6 reviewer I2: probe2 must ALSO pass the whitelist.
        # A factory that returns Ridge on call 1 and SVR on call 2 would
        # otherwise silently pass __init__ and fail later at _retrain.
        # Fail fast at construction time — this matches the "hard config
        # error" tier of our exception handling strategy.
        _assert_supported_estimator(probe2)
        if probe is probe2:
            raise TypeError(
                f"model_factory() returned the SAME instance on two "
                f"consecutive calls (id={id(probe)}). MLAlpha's per-fold "
                f"isolation requires a new unfit estimator on each call. "
                f"Use `lambda: Ridge(alpha=1.0)` instead of `factory = "
                f"Ridge(alpha=1.0); model_factory=lambda: factory`."
            )

        # Runtime state. A fresh MLAlpha instance (from strategy_factory())
        # starts with _current_model=None, so portfolio walk-forward gets
        # per-fold isolation "for free". copy.deepcopy also works
        # (supported as a generic Python value) for single-stock
        # WalkForwardValidator compatibility.
        self._current_model: Any = None
        self._last_retrain_date: _date | None = None
        self._retrain_count: int = 0
        # One-shot warning flags to avoid log spam when the same user
        # mistake repeats across many symbols / rebalances. Each flag
        # gates a distinct diagnostic message; the first occurrence is
        # logged, subsequent occurrences are silenced.
        self._feature_type_warned: bool = False
        self._target_type_warned: bool = False
        self._feature_fn_exception_warned: bool = False
        self._target_fn_exception_warned: bool = False
        self._predict_feature_exception_warned: bool = False
        self._predict_feature_type_warned: bool = False
        self._predict_none_warned: bool = False
        self._predict_call_exception_warned: bool = False
        self._empty_panel_warned: bool = False
        self._empty_predict_warned: bool = False
        self._non_numeric_warned: bool = False
        # V2.13 round 5 codex-H: output index contract warnings
        self._feature_index_warned: bool = False
        self._target_index_warned: bool = False
        self._unsorted_index_warned: bool = False
        # V2.13 round 5 codex-H: feature schema drift (column order)
        self._feature_schema_drift_warned: bool = False
        self._trained_feature_cols: list[str] | None = None
        # V2.13 round 6 reviewer I1: runtime shortfall detection.
        # If the actual per-symbol panel rows fall below train_window
        # by > 10%, the feature_fn is eating more warmup than declared.
        self._train_shortfall_warned: bool = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def warmup_period(self) -> int:
        # Need at least train_window + purge + embargo + feature_warmup
        # TRADING days of history before the first prediction date can
        # be made. All four quantities are in the same unit (trading
        # days / data rows), so summing them is exact.
        #
        # feature_warmup_days covers the user's own rolling/pct_change
        # warmup inside feature_fn. Without it, rolling(100).std() would
        # eat 100 rows off the top of every training panel, silently
        # shrinking the effective train_window. V2.13 round 6 I1 fix.
        return (
            self._train_window
            + self._purge_days
            + self._embargo_days
            + self._feature_warmup_days
        )

    def diagnostics_snapshot(self) -> dict:
        """Public read-only snapshot of internal state for MLDiagnostics.

        Returns a plain dict (JSON-serializable) so that Phase 2's
        ``MLDiagnostics`` can observe retrain events, extract model
        attributes, and compute IS/OOS IC without directly touching
        any private ``_*`` attributes. This is the **only** interface
        Phase 2 should use to inspect MLAlpha internals.

        V2.13 Phase 2 design decision: centralizing private-attr access
        here means a future refactor of MLAlpha's internal state won't
        break MLDiagnostics — only this method needs updating.
        """
        model = self._current_model
        # Extract feature importance from the fitted model. sklearn's
        # linear models expose coef_, tree-based expose feature_importances_.
        importance: dict[str, float] = {}
        feature_cols = self._trained_feature_cols or []
        if model is not None and feature_cols:
            if hasattr(model, "coef_"):
                coefs = model.coef_
                if hasattr(coefs, "__len__") and len(coefs) == len(feature_cols):
                    importance = {
                        col: float(coefs[i]) for i, col in enumerate(feature_cols)
                    }
            elif hasattr(model, "feature_importances_"):
                imp = model.feature_importances_
                if hasattr(imp, "__len__") and len(imp) == len(feature_cols):
                    importance = {
                        col: float(imp[i]) for i, col in enumerate(feature_cols)
                    }
        return {
            "retrain_count": self._retrain_count,
            "last_retrain_date": (
                self._last_retrain_date.isoformat()
                if self._last_retrain_date is not None else None
            ),
            "has_model": self._current_model is not None,
            "feature_cols": list(feature_cols),
            "feature_importance": importance,
        }

    def config_dict(self) -> dict:
        """Return the MLAlpha's constructor configuration as a plain dict.

        Used by MLDiagnostics to create a fresh diagnostic copy with the
        same parameters without accessing private attributes directly.
        The factory callables are included by reference (they're closures,
        not serializable).
        """
        return {
            "name": self._name,
            "model_factory": self._model_factory,
            "feature_fn": self._feature_fn,
            "target_fn": self._target_fn,
            "train_window": self._train_window,
            "retrain_freq": self._retrain_freq,
            "purge_days": self._purge_days,
            "embargo_days": self._embargo_days,
            "feature_warmup_days": self._feature_warmup_days,
        }

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Return per-symbol predictions at ``date``.

        Lazy retrain: if the current model is stale (older than
        ``retrain_freq`` days from ``date``), build a fresh training
        panel from ``universe_data`` and fit a new model.

        The parameter is named ``date`` to match ``CrossSectionalFactor``'s
        ABC signature. We normalize internally via ``_to_date`` to avoid
        shadow confusion with the ``datetime.date`` type.
        """
        current = _to_date(date)

        if self._needs_retrain(current):
            self._retrain(universe_data, current)

        if self._current_model is None:
            return pd.Series(dtype=float)

        return self._predict(universe_data, current)

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Raw (un-ranked) predictions. Used by ``AlphaCombiner``.

        Note: for MLAlpha, ``compute_raw`` is semantically identical to
        ``compute`` — the model's ``predict()`` output is already raw
        continuous scores (e.g., expected 5-day forward return), not a
        rank. Downstream consumers like ``TopNRotation`` will rank/sort
        these scores themselves. This differs from ``MomentumRank`` etc.,
        where ``compute`` returns percentile ranks and ``compute_raw``
        returns the underlying momentum value.
        """
        return self.compute(universe_data, date)

    def _needs_retrain(self, current: _date) -> bool:
        if self._current_model is None or self._last_retrain_date is None:
            return True
        elapsed = (current - self._last_retrain_date).days
        return elapsed >= self._retrain_freq

    def _retrain(self, universe_data: dict[str, pd.DataFrame], current: _date) -> None:
        """Build a fresh training panel and fit a new model on it.

        Skips retrain if the panel is empty or has too few samples
        (< 10) — this can happen early in the universe data when not
        enough history has accumulated to satisfy the train_window
        after purge+embargo exclusion. A one-shot diagnostic warning
        is emitted so the user can distinguish "no signal" from "no data".

        Non-finite values (``inf``/``-inf``) are filtered before fit —
        ``isna()`` alone does NOT catch these. A very plausible user
        mistake (``pct_change`` on a price of 0 when a stock is
        suspended or delisted) produces ``inf`` that then crashes
        ``sklearn`` with ``ValueError("Input X contains infinity...")``.

        The factory and fit() are both wrapped: if either raises, the
        prior model is kept (or remains ``None`` if this is the first
        retrain) and a warning is logged. The backtest continues.
        """
        X, y = self._build_training_panel(universe_data, current)
        if X is None or y is None or len(X) < 10:
            if not self._empty_panel_warned:
                n_syms = len(universe_data)
                _logger.warning(
                    "MLAlpha[%s]: training panel at %s is empty or too "
                    "small (< 10 rows) across %d symbols. Possible causes: "
                    "feature_fn / target_fn errors, insufficient history, "
                    "or all symbols failing the purge+embargo window. "
                    "Check earlier warnings for feature/target errors. "
                    "(one-shot warning)",
                    self._name, current, n_syms,
                )
                self._empty_panel_warned = True
            return

        # Convert to numeric numpy arrays. A user's feature_fn / target_fn
        # could accidentally produce non-numeric dtypes. Two distinct
        # failure modes exist and each needs its own defense:
        #
        # 1. **Hard failure** (object/string dtype): np.asarray(dtype=float)
        #    raises ValueError for strings, which we catch.
        # 2. **Silent coercion** (datetime64 / timedelta64): these have
        #    kind 'M' / 'm' and np.asarray(dtype=float) SILENTLY converts
        #    them to nanosecond-epoch floats (~1.65e18 for 2022 dates).
        #    Ridge then trains on garbage, producing predictions scaled
        #    by ~1e15. Strictly worse than a crash: a silent bad fit
        #    could ship to production.
        #
        # Defense-in-depth: active dtype.kind whitelist BEFORE asarray +
        # try/except as fallback. V2.13 round 4 reviewer C1.
        X_bad_cols = {
            col: str(dt) for col, dt in X.dtypes.items()
            if dt.kind not in _NUMERIC_DTYPE_KINDS
        }
        y_bad = y.dtype.kind not in _NUMERIC_DTYPE_KINDS
        if X_bad_cols or y_bad:
            if not self._non_numeric_warned:
                _logger.warning(
                    "MLAlpha[%s]: training panel at %s has non-numeric "
                    "dtypes (feature columns: %s, target dtype: %s). "
                    "feature_fn and target_fn must produce numeric "
                    "dtypes only (float/int/bool). datetime64 and "
                    "timedelta64 look numeric to numpy but silently "
                    "coerce to nanosecond-epoch floats (~1.65e18 for "
                    "current dates) which corrupts the model. Convert "
                    "timestamps to numeric features explicitly (e.g., "
                    "days-since-epoch, day-of-week integer). Skipping "
                    "retrain, keeping prior model (%s). (one-shot warning)",
                    self._name, current,
                    X_bad_cols if X_bad_cols else "all numeric",
                    str(y.dtype),
                    "present" if self._current_model is not None else "None",
                )
                self._non_numeric_warned = True
            return

        # Dtype whitelist passed — now do the actual numpy conversion.
        # The try/except is still useful as defense-in-depth for edge
        # cases like pandas nullable Int64 with pd.NA, mixed-type object
        # columns that somehow slipped the kind check, etc.
        # V2.24 round-2 warning cleanup: keep X as DataFrame (float-coerced)
        # for fit, so feature names survive into the model. predict also
        # receives a DataFrame (single-row), which matches. This eliminates
        # sklearn's "X does not have valid feature names" warnings
        # symmetrically.
        try:
            X_float = X.astype(float)
            X_arr = X_float.to_numpy()  # for finite_mask only
            y_arr = np.asarray(y.to_numpy(), dtype=float)
        except (TypeError, ValueError) as e:
            if not self._non_numeric_warned:
                _logger.warning(
                    "MLAlpha[%s]: training panel at %s contains unconvertible "
                    "values (X dtype=%s, y dtype=%s): %s. feature_fn and "
                    "target_fn must produce numeric dtypes (float/int). "
                    "Skipping retrain, keeping prior model (%s). "
                    "(one-shot warning)",
                    self._name, current, X.dtypes.to_dict(),
                    y.dtype, e,
                    "present" if self._current_model is not None else "None",
                )
                self._non_numeric_warned = True
            return

        # Filter non-finite values. pandas isna() treats inf as present,
        # but sklearn rejects it. Do this at retrain time (not in
        # _build_training_panel) because a late training row with a
        # transient inf is better kept-and-filtered than silently
        # dropped from earlier stages.
        finite_mask = np.isfinite(X_arr).all(axis=1) & np.isfinite(y_arr)
        if not finite_mask.all():
            X_float = X_float.iloc[finite_mask]
            X_arr = X_arr[finite_mask]
            y_arr = y_arr[finite_mask]
            if len(X_arr) < 10:
                _logger.warning(
                    "MLAlpha[%s]: training panel at %s had < 10 finite rows "
                    "after inf/nan filtering; skipping retrain.",
                    self._name, current,
                )
                return

        # Wrap the factory call. At __init__ time we let factory errors
        # propagate as TypeError (hard config error). At retrain time we
        # catch and log — keep the prior model, continue the backtest.
        # This mirrors the fit() exception handling: the framework
        # promises "failure is visible but never crashes the backtest"
        # once initial construction has succeeded.
        try:
            model = self._model_factory()
        except Exception as e:
            _logger.warning(
                "MLAlpha[%s]: model_factory() raised %s at %s: %s. "
                "Keeping prior model (%s). The backtest continues.",
                self._name, type(e).__name__, current, e,
                "present" if self._current_model is not None else "None",
            )
            return

        # Re-check the factory output — user could return a different
        # estimator class on subsequent calls. V1 safety means whitelist
        # applies to every produced instance, not just the first probe.
        # UnsupportedEstimatorError propagates (hard safety boundary, not
        # a recoverable error).
        _assert_supported_estimator(model)

        try:
            # V2.24 round-2: pass DataFrame so model stores feature_names_in_
            # and subsequent predict(DataFrame) doesn't trigger the sklearn
            # name-mismatch warning.
            model.fit(X_float, y_arr)
        except Exception as e:
            _logger.warning(
                "MLAlpha[%s]: fit() raised %s at %s: %s. Keeping prior "
                "model (%s). The backtest continues.",
                self._name, type(e).__name__, current, e,
                "present" if self._current_model is not None else "None",
            )
            return

        self._current_model = model
        self._last_retrain_date = current
        self._retrain_count += 1
        # V2.13 round 5 codex-H: save the feature schema so _predict can
        # verify column order drift. sklearn uses positional features,
        # so a reorder between training and predict silently produces
        # wrong predictions. Store as list to preserve order.
        self._trained_feature_cols = list(X.columns)

        # V2.13 round 6 reviewer I1: runtime shortfall detection.
        # If feature_fn eats more rows than the user declared via
        # feature_warmup_days, the effective training panel will be
        # shorter than train_window. This is a silent data-correctness
        # bug — the model trains on fewer samples than intended. Emit a
        # one-shot warning suggesting the user bump feature_warmup_days.
        if not self._train_shortfall_warned:
            n_syms = X.index.get_level_values("symbol").nunique()
            if n_syms > 0:
                rows_per_sym = len(X) / n_syms
                threshold = self._train_window * 0.9
                if rows_per_sym < threshold:
                    _logger.warning(
                        "MLAlpha[%s]: training panel at %s has %.0f rows "
                        "per symbol (across %d symbols), which is < 90%% "
                        "of train_window=%d. Your feature_fn likely "
                        "consumes %.0f rows of warmup (e.g., rolling/pct_change "
                        "NaN head). Set feature_warmup_days=%d to request "
                        "enough history from the engine. Currently "
                        "feature_warmup_days=%d. (one-shot warning)",
                        self._name, current, rows_per_sym, n_syms,
                        self._train_window,
                        self._train_window - rows_per_sym,
                        int(self._train_window - rows_per_sym) + 5,
                        self._feature_warmup_days,
                    )
                    self._train_shortfall_warned = True

    def _build_training_panel(
        self,
        universe_data: dict[str, pd.DataFrame],
        prediction_date: _date,
    ) -> tuple[pd.DataFrame | None, pd.Series | None]:
        """Stack per-symbol ``(feature, target)`` pairs into a
        cross-sectional training panel.

        Exclusion rules (applied per-symbol):
        1. **Strict anti-lookahead on feature date**: drop all rows whose
           feature date ``>= prediction_date``. This is redundant with
           the engine's ``slice_universe_data`` upstream slice but makes
           MLAlpha correct even when called with un-sliced data.
        2. **Purge + embargo on label leakage**: drop the last
           ``purge_days + embargo_days`` **trading days** (data rows)
           from the per-symbol tail. This is POSITIONAL (``iloc[:-N]``),
           not calendar-day-based. The rationale: typical ``target_fn``
           shapes like ``df.pct_change(5).shift(-5)`` use a
           trading-day-unit forward horizon. Trimming by calendar days
           would span weekends and retain 2/5 of the tail rows with
           their labels pointing INTO the prediction window. See the
           class docstring for the bug this prevents.
        3. **NaN target filter**: ``shift(-k)`` produces a NaN tail;
           drop those rows.
        4. **train_window cap**: keep at most the last ``train_window``
           rows per symbol (after steps 1-3 have trimmed the tail).
        """
        purge_bars = self._purge_days + self._embargo_days

        rows: list[pd.DataFrame] = []
        labels: list[pd.Series] = []
        for sym, df in universe_data.items():
            if not isinstance(df.index, pd.DatetimeIndex):
                continue

            # Call feature_fn and target_fn separately so we can point
            # the user at the specific callable that raised.
            try:
                sym_features = self._feature_fn(df)
            except Exception as e:
                if not self._feature_fn_exception_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn raised %s for symbol %s: "
                        "%s. Skipping this symbol. Common causes: column "
                        "name typo (check df.columns), division by zero, "
                        "empty DataFrame. (one-shot warning per error "
                        "type — subsequent failures silenced)",
                        self._name, type(e).__name__, sym, e,
                    )
                    self._feature_fn_exception_warned = True
                continue
            try:
                sym_target = self._target_fn(df)
            except Exception as e:
                if not self._target_fn_exception_warned:
                    _logger.warning(
                        "MLAlpha[%s] target_fn raised %s for symbol %s: "
                        "%s. Skipping this symbol. Common causes: column "
                        "name typo, shift(-k) with k > len(df). (one-shot "
                        "warning per error type — subsequent failures "
                        "silenced)",
                        self._name, type(e).__name__, sym, e,
                    )
                    self._target_fn_exception_warned = True
                continue

            if sym_features is None or sym_target is None:
                continue
            if not isinstance(sym_features, pd.DataFrame):
                if not self._feature_type_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn returned %s for symbol %s, "
                        "expected pandas.DataFrame — skipping this symbol. "
                        "Wrap your Series in pd.DataFrame({'col_name': series}).",
                        self._name, type(sym_features).__name__, sym,
                    )
                    self._feature_type_warned = True
                continue
            if not isinstance(sym_target, pd.Series):
                if not self._target_type_warned:
                    _logger.warning(
                        "MLAlpha[%s] target_fn returned %s for symbol %s, "
                        "expected pandas.Series — skipping this symbol.",
                        self._name, type(sym_target).__name__, sym,
                    )
                    self._target_type_warned = True
                continue
            if sym_features.empty or sym_target.empty:
                continue

            # V2.13 round 5 codex-H: validate output index contract.
            # - Must be a DatetimeIndex (else downstream .index.date
            #   raises AttributeError or intersection silently empties)
            # - Must be monotonic increasing (else iloc[-N:] picks the
            #   wrong "latest" rows and _train_window_ cap takes the wrong
            #   window)
            if not isinstance(sym_features.index, pd.DatetimeIndex):
                if not self._feature_index_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn returned non-DatetimeIndex "
                        "(%s) for symbol %s. MLAlpha requires a DatetimeIndex "
                        "matching the input DataFrame. Common cause: calling "
                        "reset_index() inside feature_fn. Skipping this "
                        "symbol. (one-shot warning)",
                        self._name, type(sym_features.index).__name__, sym,
                    )
                    self._feature_index_warned = True
                continue
            if not isinstance(sym_target.index, pd.DatetimeIndex):
                if not self._target_index_warned:
                    _logger.warning(
                        "MLAlpha[%s] target_fn returned non-DatetimeIndex "
                        "(%s) for symbol %s. Skipping this symbol. "
                        "(one-shot warning)",
                        self._name, type(sym_target.index).__name__, sym,
                    )
                    self._target_index_warned = True
                continue
            if not sym_features.index.is_monotonic_increasing:
                if not self._unsorted_index_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn returned unsorted "
                        "DatetimeIndex for symbol %s. MLAlpha's "
                        "iloc[-train_window:] assumes chronological order. "
                        "Sorting internally, but please sort in your "
                        "feature_fn for clarity. (one-shot warning)",
                        self._name, sym,
                    )
                    self._unsorted_index_warned = True
                sym_features = sym_features.sort_index()
            if not sym_target.index.is_monotonic_increasing:
                sym_target = sym_target.sort_index()

            # Align features and target on common dates
            aligned_idx = sym_features.index.intersection(sym_target.index)
            if len(aligned_idx) == 0:
                continue
            feat = sym_features.loc[aligned_idx]
            tgt = sym_target.loc[aligned_idx]

            # Step 1: strict anti-lookahead — feature date < prediction_date
            strict_mask = feat.index.date < prediction_date
            feat = feat.loc[strict_mask]
            tgt = tgt.loc[strict_mask]
            if len(feat) == 0:
                continue

            # Step 2: positional purge — drop the last `purge_bars` rows
            # (trading days). This matches the trading-day unit of
            # typical target_fn shift(-k) patterns, so the label at the
            # retained tail cannot point INTO the [prediction_date,
            # prediction_date + k) window.
            if purge_bars > 0:
                if len(feat) <= purge_bars:
                    continue  # not enough rows to purge safely
                feat = feat.iloc[:-purge_bars]
                tgt = tgt.iloc[:-purge_bars]

            # Step 3: drop rows with NaN in EITHER target OR any feature
            # column. pct_change warmup leaves NaN at the head of features,
            # shift(-k) leaves NaN at the tail of target (though step 2
            # already trimmed most of it). Inlining both drops here (vs
            # draining NaN targets now + NaN features at the concat step)
            # keeps the per-symbol loop self-contained and readable.
            # ``inf`` filtering is deliberately deferred to ``_retrain``
            # because ``pandas.isna()`` does NOT treat ``inf`` as missing.
            row_valid = (~tgt.isna()) & (~feat.isna().any(axis=1))
            feat = feat.loc[row_valid]
            tgt = tgt.loc[row_valid]

            if len(feat) == 0:
                continue

            # Step 4: cap at train_window rows
            if len(feat) > self._train_window:
                feat = feat.iloc[-self._train_window:]
                tgt = tgt.iloc[-self._train_window:]

            # Tag with symbol as MultiIndex level
            feat = feat.copy()
            feat.index = pd.MultiIndex.from_arrays(
                [feat.index, [sym] * len(feat)],
                names=["date", "symbol"],
            )
            tgt = tgt.copy()
            tgt.index = feat.index

            rows.append(feat)
            labels.append(tgt)

        if not rows:
            return None, None

        # NaN filtering already happened per-symbol in step 3; no second
        # pass needed. inf filtering still happens in _retrain because
        # isna() doesn't catch inf.
        X = pd.concat(rows, axis=0)
        y = pd.concat(labels, axis=0)

        if len(X) == 0:
            return None, None
        return X, y

    def _predict(
        self,
        universe_data: dict[str, pd.DataFrame],
        current: _date,
    ) -> pd.Series:
        """Predict scores for each symbol at ``current``.

        For each symbol, extracts the most recent feature row strictly
        before ``current`` (anti-lookahead guard — this is redundant with
        the engine's ``slice_universe_data`` upstream slice, but doing it
        here makes MLAlpha correct even if someone passes un-sliced data).
        """
        if self._current_model is None:
            return pd.Series(dtype=float)

        predictions: dict[str, float] = {}
        n_attempted = 0
        for sym, df in universe_data.items():
            if not isinstance(df.index, pd.DatetimeIndex):
                continue
            n_attempted += 1

            # feature_fn exception at predict stage: one-shot warn.
            # Reusing _feature_fn_exception_warned would mask the
            # predict-stage error if training succeeded but prediction
            # features fail (e.g., user's feature_fn has branching on
            # data length), so we keep a separate flag.
            try:
                sym_features = self._feature_fn(df)
            except Exception as e:
                if not self._predict_feature_exception_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn raised %s for symbol %s "
                        "during predict: %s. Skipping this symbol. "
                        "(one-shot warning)",
                        self._name, type(e).__name__, sym, e,
                    )
                    self._predict_feature_exception_warned = True
                continue

            if sym_features is None:
                if not self._predict_none_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn returned None for symbol %s "
                        "at predict stage. Skipping this symbol. (one-shot)",
                        self._name, sym,
                    )
                    self._predict_none_warned = True
                continue
            if not isinstance(sym_features, pd.DataFrame):
                # V2.13 round 3 codex-MH: feature_fn returned a non-DataFrame
                # at predict stage (e.g., Series from a data-length branch
                # that degrades the return type between training and predict).
                # The training stage has its own flag (_feature_type_warned)
                # — we need a separate predict-stage flag so a user whose
                # feature_fn works at training but regresses at predict sees
                # a targeted message, not just the generic "0 predictions"
                # summary.
                if not self._predict_feature_type_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn returned %s for symbol %s "
                        "at predict stage, expected pandas.DataFrame. "
                        "The model was fitted successfully (training stage "
                        "returned DataFrame), but predict features degraded "
                        "to the wrong type. Common cause: branching on "
                        "data length (e.g., `if len(df) > N`) that produces "
                        "different return types. Wrap your result in "
                        "pd.DataFrame({'col': series}). Skipping this symbol. "
                        "(one-shot warning)",
                        self._name, type(sym_features).__name__, sym,
                    )
                    self._predict_feature_type_warned = True
                continue
            if sym_features.empty:
                continue

            # V2.13 round 5 codex-H: validate predict-stage index contract
            # too. Without this, a feature_fn that returns RangeIndex at
            # predict time (but worked at training) would raise
            # AttributeError at the .index.date line below.
            if not isinstance(sym_features.index, pd.DatetimeIndex):
                if not self._feature_index_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn returned non-DatetimeIndex "
                        "(%s) for symbol %s at predict stage. Skipping "
                        "this symbol. (one-shot warning)",
                        self._name, type(sym_features.index).__name__, sym,
                    )
                    self._feature_index_warned = True
                continue
            if not sym_features.index.is_monotonic_increasing:
                if not self._unsorted_index_warned:
                    _logger.warning(
                        "MLAlpha[%s] feature_fn returned unsorted "
                        "DatetimeIndex for symbol %s at predict stage. "
                        "Sorting internally. (one-shot warning)",
                        self._name, sym,
                    )
                    self._unsorted_index_warned = True
                sym_features = sym_features.sort_index()

            # V2.13 round 5 codex-H: feature schema drift check — verify
            # column names + order match training. sklearn uses positional
            # feature semantics (model.predict(X[:, 0] × coef_[0] + ...),
            # so a column reorder between training and predict silently
            # produces wrong predictions. Defense: if training saved a
            # feature schema, compare and reorder (or skip on set mismatch).
            if self._trained_feature_cols is not None:
                actual_cols = list(sym_features.columns)
                trained_cols = self._trained_feature_cols
                if actual_cols != trained_cols:
                    if set(actual_cols) == set(trained_cols):
                        # Same set, different order — reorder and warn once
                        if not self._feature_schema_drift_warned:
                            _logger.warning(
                                "MLAlpha[%s] feature column order drifted "
                                "between training and predict for symbol %s. "
                                "Training order: %s, predict order: %s. "
                                "Reordering predict features to match "
                                "training (sklearn uses positional features). "
                                "Please make feature_fn's column order "
                                "deterministic. (one-shot warning)",
                                self._name, sym, trained_cols, actual_cols,
                            )
                            self._feature_schema_drift_warned = True
                        sym_features = sym_features[trained_cols]
                    else:
                        # Different column set — cannot recover safely
                        if not self._feature_schema_drift_warned:
                            _logger.warning(
                                "MLAlpha[%s] feature column set differs "
                                "between training and predict for symbol %s. "
                                "Training: %s, predict: %s. Cannot reorder "
                                "because the column sets don't match. "
                                "Skipping this symbol. (one-shot warning)",
                                self._name, sym, trained_cols, actual_cols,
                            )
                            self._feature_schema_drift_warned = True
                        continue

            # Strict anti-lookahead: features date < current
            mask = sym_features.index.date < current
            if not mask.any():
                continue
            latest = sym_features.loc[mask].iloc[-1:]
            if latest.isna().any().any():
                continue

            # model.predict exception: one-shot warn. Common causes:
            # wrong feature count (model trained on N features, predict
            # given M), dtype mismatch (e.g., feature_fn emits datetime64
            # at predict but float at training), shape mismatch.
            # V2.24 round-2 warning cleanup: pass `latest` as a single-row
            # DataFrame (keeps feature names) so sklearn/LightGBM don't
            # emit `X does not have valid feature names` warnings.
            # `latest` is already a single-row DataFrame from iloc[-1:].
            try:
                pred = float(self._current_model.predict(latest)[0])
            except Exception as e:
                if not self._predict_call_exception_warned:
                    _logger.warning(
                        "MLAlpha[%s] model.predict raised %s for symbol %s: "
                        "%s. feature shape at predict time: %s, dtypes: %s. "
                        "Skipping this symbol. (one-shot warning — check "
                        "that your feature_fn produces features of the "
                        "same shape AND dtype at predict time as during "
                        "training. Common causes: column count drift, "
                        "dtype drift (e.g., datetime64 at predict vs "
                        "float at training), NaN rows.)",
                        self._name, type(e).__name__, sym, e,
                        latest.shape, latest.dtypes.to_dict(),
                    )
                    self._predict_call_exception_warned = True
                continue
            if not np.isfinite(pred):
                continue
            predictions[sym] = pred

        # M1 diagnostic: if we attempted at least one symbol but produced
        # zero predictions, warn once. Distinguishes "no signal" (empty
        # universe, no valid bars) from "all symbols failed at predict".
        if n_attempted > 0 and not predictions and not self._empty_predict_warned:
            _logger.warning(
                "MLAlpha[%s]: predict at %s attempted %d symbols but "
                "produced 0 predictions. Model is fitted but every symbol "
                "was skipped. Check earlier warnings for feature/predict "
                "errors. (one-shot warning)",
                self._name, current, n_attempted,
            )
            self._empty_predict_warned = True

        return pd.Series(predictions, dtype=float)


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


# ─── User template (consumed by Phase 4 sandbox new-file button) ─────
#
# This template is a str.format() template with three placeholders:
#   {class_name} — the Python class name (used in `class ...(MLAlpha)`)
#   {name}       — the factor instance name
#   {description} — free-form description shown in the UI
#
# We use a literal template so that calling format() only substitutes the
# three tagged fields and leaves Python-syntax braces (e.g., dict/set
# literals) alone. In Python str.format() semantics, "{{" → "{" and
# "}}" → "}", so any genuine code braces must be doubled.
ML_ALPHA_TEMPLATE = '''"""User ML Alpha: {class_name}

V2.13 ML Alpha with walk-forward framework. The framework enforces
anti-lookahead via purge/embargo. You provide:
- model_factory: returns a fresh unfit sklearn-compatible estimator
- feature_fn: extracts features from a single-symbol DataFrame
- target_fn: extracts the forward-looking label

IMPORTANT:
- Supported estimators (regressor only):
  sklearn: Ridge, Lasso, LinearRegression, ElasticNet,
           DecisionTreeRegressor, RandomForestRegressor,
           GradientBoostingRegressor
  lightgbm (optional): LGBMRegressor — pip install lightgbm>=4.0
  xgboost  (optional): XGBRegressor  — pip install xgboost>=2.0
  If you try to use an unsupported class (e.g., SVR, LGBMClassifier),
  MLAlpha construction will raise UnsupportedEstimatorError.
- Set n_jobs=1 on all estimators. The sandbox blocks multiprocessing,
  and MLAlpha enforces this at construction by inspecting the estimator
  instance — n_jobs=-1 / n_jobs=2 will raise immediately.
- Set random_state for reproducibility (required for deterministic
  regression tests).
- DO NOT use joblib/pickle to save models to disk — the sandbox blocks
  pickle. V1 keeps models in-memory only; cross-run cache is V2.13.1.
- Target horizon (e.g., 5-day forward return) dictates purge_days —
  keep them equal or larger than the horizon to prevent label leakage.
"""
from __future__ import annotations

import pandas as pd

from sklearn.linear_model import Ridge
from ez.portfolio.ml.alpha import MLAlpha


def _feature_fn(df: pd.DataFrame) -> pd.DataFrame:
    """Extract features from a single-symbol DataFrame.

    The engine slices ``df`` to anti-lookahead-safe history before
    passing it here. You only need to compute features; do not worry
    about the current-date exclusion.
    """
    return pd.DataFrame({{
        "ret1": df["adj_close"].pct_change(1),
        "ret5": df["adj_close"].pct_change(5),
        "ret20": df["adj_close"].pct_change(20),
        "vol20": df["adj_close"].pct_change(1).rolling(20).std(),
    }}).dropna()


def _target_fn(df: pd.DataFrame) -> pd.Series:
    """5-day forward return. MUST use .shift(-k) to look forward."""
    return df["adj_close"].pct_change(5).shift(-5)


class {class_name}(MLAlpha):
    """{description}"""

    def __init__(self):
        super().__init__(
            name="{name}",
            # Deterministic Ridge — no randomness, stable across runs.
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_feature_fn,
            target_fn=_target_fn,
            train_window=120,  # ~6 months of daily data
            retrain_freq=21,   # ~monthly retraining
            purge_days=5,      # matches target's 5-day forward horizon
            embargo_days=2,    # safety buffer
            # feature_warmup_days: set to the longest rolling/pct_change
            # window inside _feature_fn. The default _feature_fn above uses
            # rolling(20) → set to 20. If you change _feature_fn to use
            # rolling(100), bump this to 100 — otherwise the engine won't
            # fetch enough history, and your training panel will be 80
            # rows short of train_window with NO warning.
            feature_warmup_days=20,
        )
'''
