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
        """Return per-symbol predictions at ``date``.

        Lazy retrain: if the current model is stale (older than
        ``retrain_freq`` days from ``date``), build a fresh training
        panel from ``universe_data`` and fit a new model.
        """
        current: _date = date.date() if hasattr(date, "date") else date

        if self._needs_retrain(current):
            self._retrain(universe_data, current)

        if self._current_model is None:
            return pd.Series(dtype=float)

        return self._predict(universe_data, current)

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Raw (un-ranked) predictions. Used by ``AlphaCombiner``."""
        return self.compute(universe_data, date)

    def _needs_retrain(self, current: _date) -> bool:
        if self._current_model is None or self._last_retrain_date is None:
            return True
        elapsed = (current - self._last_retrain_date).days
        return elapsed >= self._retrain_freq

    def _retrain(self, universe_data: dict[str, pd.DataFrame], current: _date) -> None:
        """Build a fresh training panel and fit a new model on it.

        Skips retrain silently if the panel is empty or has too few
        samples (< 10) — this can happen early in the universe data when
        not enough history has accumulated to satisfy the train_window
        after purge+embargo exclusion.
        """
        X, y = self._build_training_panel(universe_data, current)
        if X is None or y is None or len(X) < 10:
            return
        model = self._model_factory()
        # Re-check the factory output — user could return a different
        # estimator class on subsequent calls. V1 safety means whitelist
        # applies to every produced instance, not just the first probe.
        _assert_supported_estimator(model)
        model.fit(X.values, y.values)
        self._current_model = model
        self._last_retrain_date = current
        self._retrain_count += 1

    def _build_training_panel(
        self,
        universe_data: dict[str, pd.DataFrame],
        prediction_date: _date,
    ) -> tuple[pd.DataFrame | None, pd.Series | None]:
        """Stack per-symbol ``(feature, target)`` pairs into a
        cross-sectional training panel.

        Exclusion rules:
        1. Exclude all dates ``>= prediction_date - purge_days - embargo_days``
           (forward-looking label leakage prevention).
        2. Include at most the last ``train_window`` rows per symbol
           (after purge exclusion).
        3. Drop rows where the target is NaN (pct_change + shift(-k)
           produces a NaN tail).
        """
        cutoff = prediction_date - timedelta(days=self._purge_days + self._embargo_days)

        rows: list[pd.DataFrame] = []
        labels: list[pd.Series] = []
        for sym, df in universe_data.items():
            if not isinstance(df.index, pd.DatetimeIndex):
                continue

            try:
                sym_features = self._feature_fn(df)
                sym_target = self._target_fn(df)
            except Exception:
                continue

            if sym_features is None or sym_target is None:
                continue
            if not isinstance(sym_features, pd.DataFrame) or not isinstance(sym_target, pd.Series):
                continue
            if sym_features.empty or sym_target.empty:
                continue

            # Align features and target on common dates
            aligned_idx = sym_features.index.intersection(sym_target.index)
            if len(aligned_idx) == 0:
                continue
            feat = sym_features.loc[aligned_idx]
            tgt = sym_target.loc[aligned_idx]

            # Apply purge + embargo: exclude dates >= cutoff
            mask = feat.index.date < cutoff
            feat = feat.loc[mask]
            tgt = tgt.loc[mask]

            # Drop rows where target is NaN (shift(-k) tail)
            valid = ~tgt.isna()
            feat = feat.loc[valid]
            tgt = tgt.loc[valid]

            if len(feat) == 0:
                continue

            # Take last train_window rows only
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

        X = pd.concat(rows, axis=0)
        y = pd.concat(labels, axis=0)

        # Drop any remaining NaN rows in X (feature NaN)
        valid_rows = ~X.isna().any(axis=1)
        X = X.loc[valid_rows]
        y = y.loc[valid_rows]

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
        for sym, df in universe_data.items():
            if not isinstance(df.index, pd.DatetimeIndex):
                continue
            try:
                sym_features = self._feature_fn(df)
            except Exception:
                continue
            if sym_features is None or not isinstance(sym_features, pd.DataFrame):
                continue
            if sym_features.empty:
                continue

            # Strict anti-lookahead: features date < current
            mask = sym_features.index.date < current
            if not mask.any():
                continue
            latest = sym_features.loc[mask].iloc[-1:]
            if latest.isna().any().any():
                continue

            try:
                pred = float(self._current_model.predict(latest.values)[0])
            except Exception:
                continue
            if not np.isfinite(pred):
                continue
            predictions[sym] = pred

        return pd.Series(predictions, dtype=float) if predictions else pd.Series(dtype=float)


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
- V1 supports only a small whitelist of sklearn estimator classes
  (Ridge, Lasso, LinearRegression, ElasticNet, DecisionTreeRegressor,
  RandomForestRegressor, GradientBoostingRegressor). Adding others
  requires explicit plan-file approval. If you try to use, e.g., SVR,
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
from ez.portfolio.ml_alpha import MLAlpha


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
        )
'''
