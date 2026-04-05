"""V2.13 F9: MLDiagnostics — overfitting detection for MLAlpha.

Diagnostics computed:
1. Feature importance stability (``coef_`` / ``feature_importances_`` CV)
2. IS/OOS IC decay (``train_ic`` vs ``oos_ic`` per retrain,
   ``overfitting_score``)
3. Turnover analysis (top-N retention rate across predictions)
4. Retrain cadence (gap consistency vs expected ``retrain_freq``)

MLDiagnostics uses **Option C** (fresh instance + polling): creates a
fresh ``MLAlpha`` via ``config_dict()``, drives it through the date
range, observes retrain events via ``diagnostics_snapshot()``.

**Interface contract**: MLDiagnostics does NOT access any ``_private``
attributes on MLAlpha except ``_build_training_panel()`` (read-only,
for IS IC computation, same-package access). All other state
observation goes through the public ``diagnostics_snapshot()`` method.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ez.portfolio.ml_alpha import MLAlpha

_logger = logging.getLogger(__name__)


@dataclass
class DiagnosticsConfig:
    """Parameterized verdict thresholds.

    Defaults are sensible starting points but should be tuned per
    strategy type (e.g., high-frequency rotations naturally have higher
    baseline turnover).
    """

    severe_overfit_threshold: float = 0.5
    mild_overfit_threshold: float = 0.2
    high_turnover_threshold: float = 0.6
    top_n_for_turnover: int = 10


@dataclass
class DiagnosticsResult:
    """All-in-one diagnostics report. Every field is JSON-serializable
    after calling ``to_dict()``."""

    # ── Feature importance stability ──
    feature_importance: dict[str, list[float]] = field(default_factory=dict)
    """Per-feature importance values across retrains. key=feature_name."""
    feature_importance_cv: dict[str, float] = field(default_factory=dict)
    """Coefficient of variation per feature. Lower = more stable.
    CV > 2.0 → very unstable, consider dropping the feature."""

    # ── IS / OOS IC series ──
    ic_series: list[dict] = field(default_factory=list)
    """Per-retrain IC pairs: [{"retrain_date": str, "train_ic": float,
    "oos_ic": float}, ...]."""
    mean_train_ic: float = 0.0
    mean_oos_ic: float = 0.0
    overfitting_score: float = 0.0
    """max(0, (mean_train_ic - mean_oos_ic) / max(|mean_train_ic|, 1e-6)).
    >0.5 = severe overfitting."""

    # ── Turnover ──
    turnover_series: list[dict] = field(default_factory=list)
    """Per-eval-date turnover: [{"date": str, "retention_rate": float}, ...]."""
    avg_turnover: float = 0.0
    """1 - mean(retention_rates). >0.6 = very unstable signal."""

    # ── Retrain cadence ──
    retrain_dates: list[str] = field(default_factory=list)
    """ISO date strings of actual retrain events."""
    retrain_count: int = 0
    expected_retrain_freq: int = 0
    actual_avg_gap_days: float = 0.0

    # ── Summary ──
    verdict: str = "unknown"
    """One of: 'healthy', 'mild_overfit', 'severe_overfit', 'unstable'."""
    warnings: list[str] = field(default_factory=list)
    """Human-readable diagnostic messages."""

    def to_dict(self) -> dict:
        """JSON-serializable dict. No numpy, no pandas, no sklearn."""
        def _clean(v: Any) -> Any:
            if isinstance(v, (np.integer,)):
                return int(v)
            if isinstance(v, (np.floating,)):
                return float(v)
            if isinstance(v, np.ndarray):
                return v.tolist()
            if isinstance(v, dict):
                return {str(k): _clean(vv) for k, vv in v.items()}
            if isinstance(v, (list, tuple)):
                return [_clean(vv) for vv in v]
            if isinstance(v, _date):
                return v.isoformat()
            return v
        return {k: _clean(v) for k, v in self.__dict__.items()}


class MLDiagnostics:
    """Diagnose overfitting risk of a configured MLAlpha.

    Usage::

        from ez.portfolio.ml_diagnostics import MLDiagnostics
        diag = MLDiagnostics(alpha)
        result = diag.run(universe_data, calendar, start, end)
        print(result.verdict)   # "healthy" / "mild_overfit" / ...
        print(result.to_dict()) # JSON-serializable
    """

    def __init__(
        self,
        alpha: MLAlpha,
        config: DiagnosticsConfig | None = None,
    ):
        self._source_alpha = alpha
        self._config = config or DiagnosticsConfig()

    def run(
        self,
        universe_data: dict[str, pd.DataFrame],
        calendar: "TradingCalendar",
        start: _date,
        end: _date,
        eval_freq: str = "weekly",
    ) -> DiagnosticsResult:
        """Walk through [start, end] at ``eval_freq``, drive a fresh
        MLAlpha through the date range, capture retrain snapshots, and
        compute all diagnostic metrics.
        """
        result = DiagnosticsResult(
            expected_retrain_freq=self._source_alpha._retrain_freq,
        )
        # TODO: Tasks 2.2-2.6 implement the walk-through + metrics
        return result
