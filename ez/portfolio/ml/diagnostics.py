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
attributes on MLAlpha except:
1. ``_build_training_panel()`` (read-only, for IS IC computation,
   same-package access)
2. ``_current_model`` (read-only, inside the walk-through loop ONLY
   at the moment of a retrain event, for IS IC prediction — the model
   reference is valid because it was just trained in this iteration)
All other state observation goes through the public
``diagnostics_snapshot()`` method.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.ml.alpha import MLAlpha

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
    forward_horizon: int = 5
    """Forward return horizon in trading days for OOS IC computation.
    Must match the ``target_fn``'s forward horizon (e.g., if target_fn
    is ``pct_change(10).shift(-10)``, set ``forward_horizon=10``).
    Default 5 matches the common ``pct_change(5).shift(-5)`` pattern."""


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
    """One of: 'healthy', 'mild_overfit', 'severe_overfit', 'unstable',
    'insufficient_data'. The last is set when IC samples are empty
    (too few retrains, too short date range, or forward_horizon config
    mismatch) so overfitting_score is NaN."""
    warnings: list[str] = field(default_factory=list)
    """Human-readable diagnostic messages."""

    def to_dict(self) -> dict:
        """JSON-serializable dict. No numpy, no pandas, no sklearn.
        NaN and inf are converted to None (RFC 8259 compliance)."""
        def _clean(v: Any) -> Any:
            if isinstance(v, (np.integer,)):
                return int(v)
            if isinstance(v, (np.floating,)):
                v = float(v)
                # Fall through to float check below
            if isinstance(v, float):
                # NaN/inf are not valid JSON per RFC 8259
                if v != v or v == float("inf") or v == float("-inf"):
                    return None
                return v
            if isinstance(v, np.ndarray):
                return [_clean(x) for x in v.tolist()]
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

        from ez.portfolio.ml.diagnostics import MLDiagnostics
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
        calendar: TradingCalendar,
        start: _date,
        end: _date,
        eval_freq: str = "weekly",
    ) -> DiagnosticsResult:
        """Walk through [start, end] at ``eval_freq``, drive a fresh
        MLAlpha through the date range, capture retrain snapshots, and
        compute all diagnostic metrics.
        """
        config = self._source_alpha.config_dict()
        result = DiagnosticsResult(
            expected_retrain_freq=config["retrain_freq"],
        )

        # ── 1. Create a fresh diagnostic alpha (source is untouched) ──
        diag_alpha = MLAlpha(**config)

        # ── 2. Get eval dates ──
        eval_dates = calendar.rebalance_dates(start, end, eval_freq)
        if not eval_dates:
            _logger.warning("MLDiagnostics: no eval dates in [%s, %s]", start, end)
            return result

        # ── 3. Walk-through loop ──
        #
        # IMPORTANT (review round fix): IS IC MUST be computed INSIDE
        # the loop, immediately after detecting a retrain event, because
        # `diag_alpha._current_model` holds the freshly-trained model
        # only at that moment. If we defer IS IC to a post-loop pass,
        # `_current_model` would be the LAST retrain's model, and IS IC
        # for all earlier retrains would use the wrong model.
        prev_retrain_count = 0
        retrain_snapshots: list[dict] = []
        # Tag each score with the retrain_count at that moment. This is
        # critical for OOS IC: if a retrain happens DURING the OOS window,
        # scores after that retrain come from a DIFFERENT model and must
        # be excluded. (Codex review #1 — the hardest logic bug in Phase 2.)
        all_scores: list[tuple[_date, pd.Series, int]] = []
        is_ic_at_retrain: list[float] = []

        for eval_date in eval_dates:
            dt = datetime.combine(eval_date, datetime.min.time())
            scores = diag_alpha.compute(universe_data, dt)

            snapshot = diag_alpha.diagnostics_snapshot()
            current_retrain_count = snapshot["retrain_count"]

            # Record scores AFTER checking retrain, so the retrain_count
            # tag reflects whether this eval_date's scores came from the
            # OLD model (before retrain) or the NEW model (after retrain
            # that may have been triggered by this compute() call).
            all_scores.append((eval_date, scores, current_retrain_count))

            if current_retrain_count > prev_retrain_count:
                retrain_date_str = snapshot["last_retrain_date"]
                result.retrain_dates.append(retrain_date_str)
                retrain_snapshots.append(snapshot)

                # IS IC NOW while _current_model is the correct model
                retrain_date = _date.fromisoformat(retrain_date_str)
                is_ic = self._compute_is_ic(
                    diag_alpha, universe_data, retrain_date,
                )
                is_ic_at_retrain.append(is_ic)

                prev_retrain_count = current_retrain_count

        # ── 4. Retrain cadence metrics ──
        result.retrain_count = len(result.retrain_dates)

        if result.retrain_count >= 2:
            retrain_date_objs = [
                _date.fromisoformat(d) for d in result.retrain_dates
            ]
            gaps = [
                (retrain_date_objs[i + 1] - retrain_date_objs[i]).days
                for i in range(len(retrain_date_objs) - 1)
            ]
            result.actual_avg_gap_days = float(np.mean(gaps))
        elif result.retrain_count == 1:
            result.actual_avg_gap_days = 0.0

        # ── 5. Feature importance stability ──
        self._compute_feature_importance(retrain_snapshots, result)

        # ── 6. IS/OOS IC decay ──
        self._compute_ic_decay(
            is_ic_at_retrain, universe_data, all_scores, eval_dates, result,
        )

        # ── 7. Turnover analysis ──
        self._compute_turnover(all_scores, result)

        # ── 8. Verdict + warnings ──
        self._compute_verdict(result)

        return result

    # ── Task 2.3: Feature importance stability ──────────────────────

    def _compute_feature_importance(
        self,
        retrain_snapshots: list[dict],
        result: DiagnosticsResult,
    ) -> None:
        """Collect feature importance across retrains, compute per-feature CV."""
        if not retrain_snapshots:
            return

        for snap in retrain_snapshots:
            imp = snap.get("feature_importance", {})
            for feat_name, value in imp.items():
                result.feature_importance.setdefault(feat_name, []).append(value)

        for feat_name, values in result.feature_importance.items():
            if len(values) < 2:
                result.feature_importance_cv[feat_name] = float("inf")
                continue
            arr = np.array(values, dtype=float)
            mean = float(np.mean(arr))
            std = float(np.std(arr, ddof=1))
            cv = std / abs(mean) if abs(mean) > 1e-10 else float("inf")
            result.feature_importance_cv[feat_name] = cv

    # ── Task 2.4: IS/OOS IC decay ──────────────────────────────────

    def _compute_ic_decay(
        self,
        is_ic_at_retrain: list[float],
        universe_data: dict[str, pd.DataFrame],
        all_scores: list[tuple[_date, pd.Series, int]],
        eval_dates: list[_date],
        result: DiagnosticsResult,
    ) -> None:
        """Assemble per-retrain IS/OOS IC pairs and compute
        ``overfitting_score``.

        IS IC was pre-computed inside the walk-through loop (at retrain
        time). OOS IC uses factor scores vs actual forward returns, but
        ONLY scores produced by the SAME model (same ``retrain_count``
        tag). If a subsequent retrain occurs within the OOS window, the
        scores after that retrain are excluded — they came from a
        different model and would pollute the OOS IC. (Codex review #1.)
        """
        if not result.retrain_dates:
            return

        retrain_freq = result.expected_retrain_freq
        oos_window = min(max(retrain_freq, 21), 42)
        fwd_horizon = self._config.forward_horizon

        # Fix #3 (codex review): forward_horizon is a hidden contract —
        # it must match target_fn's forward horizon. We can't infer it
        # from target_fn (it's a lambda), but we can sanity-check it.
        if fwd_horizon > retrain_freq:
            result.warnings.append(
                f"forward_horizon ({fwd_horizon}) > retrain_freq "
                f"({retrain_freq}). OOS IC uses a forward return "
                f"horizon that exceeds the retrain cycle — check that "
                f"DiagnosticsConfig.forward_horizon matches your "
                f"target_fn's shift(-k) horizon."
            )
        if fwd_horizon <= 0:
            result.warnings.append(
                f"forward_horizon ({fwd_horizon}) must be >= 1. "
                f"OOS IC will be NaN."
            )

        # Build date→(scores, retrain_count) lookup
        scores_and_rc: dict[_date, tuple[pd.Series, int]] = {
            d: (s, rc) for d, s, rc in all_scores
        }

        # Pre-compute forward returns at each eval date
        fwd_returns_by_date: dict[_date, dict[str, float]] = {}
        for eval_date, _, _ in all_scores:
            if eval_date in fwd_returns_by_date:
                continue
            fwd: dict[str, float] = {}
            for sym, df in universe_data.items():
                if not isinstance(df.index, pd.DatetimeIndex):
                    continue
                mask_at = df.index.date <= eval_date
                mask_fwd = df.index.date > eval_date
                if not mask_at.any() or not mask_fwd.any():
                    continue
                close_at = float(df.loc[mask_at, "adj_close"].iloc[-1])
                fwd_bars = df.loc[mask_fwd]
                if len(fwd_bars) < fwd_horizon:
                    continue
                close_fwd = float(fwd_bars["adj_close"].iloc[fwd_horizon - 1])
                if close_at > 0:
                    fwd[sym] = close_fwd / close_at - 1.0
            fwd_returns_by_date[eval_date] = fwd

        train_ics: list[float] = []
        oos_ics: list[float] = []

        for i, retrain_date_str in enumerate(result.retrain_dates):
            train_ic = is_ic_at_retrain[i] if i < len(is_ic_at_retrain) else float("nan")
            retrain_date = _date.fromisoformat(retrain_date_str)
            # The retrain_count at this retrain event = i + 1 (1-indexed:
            # first retrain → count=1, second → count=2, etc.)
            retrain_rc = i + 1

            oos_ic = self._compute_oos_ic(
                retrain_date, retrain_rc, oos_window, eval_dates,
                scores_and_rc, fwd_returns_by_date,
            )

            result.ic_series.append({
                "retrain_date": retrain_date_str,
                "train_ic": train_ic,
                "oos_ic": oos_ic,
            })
            if np.isfinite(train_ic):
                train_ics.append(train_ic)
            if np.isfinite(oos_ic):
                oos_ics.append(oos_ic)

        # Fix #2 (codex review): empty → NaN, not 0.0. A 0.0 default
        # masquerades as "no overfit" when the real situation is
        # "insufficient data to assess".
        result.mean_train_ic = float(np.mean(train_ics)) if train_ics else float("nan")
        result.mean_oos_ic = float(np.mean(oos_ics)) if oos_ics else float("nan")
        if np.isfinite(result.mean_train_ic) and np.isfinite(result.mean_oos_ic):
            denom = max(abs(result.mean_train_ic), 1e-6)
            result.overfitting_score = max(
                0.0, (result.mean_train_ic - result.mean_oos_ic) / denom,
            )
        else:
            result.overfitting_score = float("nan")

    def _compute_is_ic(
        self,
        diag_alpha: MLAlpha,
        universe_data: dict[str, pd.DataFrame],
        retrain_date: _date,
    ) -> float:
        """Compute IS IC: spearman(model_predictions, actual_labels) on
        the training panel.

        This is the ONE case where we call MLAlpha._build_training_panel
        (a private method). Justified: read-only, same package, no public
        equivalent without code duplication.
        """
        try:
            X, y = diag_alpha._build_training_panel(universe_data, retrain_date)
        except Exception:
            return float("nan")
        if X is None or y is None or len(X) < 10:
            return float("nan")

        model = diag_alpha._current_model
        if model is None:
            return float("nan")

        try:
            # V2.24 round-2: pass DataFrame (feature names) to match training.
            X_float = X.astype(float)
            y_arr = np.asarray(y.to_numpy(), dtype=float)
            preds = model.predict(X_float)
        except Exception:
            return float("nan")

        finite = np.isfinite(preds) & np.isfinite(y_arr)
        if finite.sum() < 5:
            return float("nan")

        try:
            ic = float(stats.spearmanr(preds[finite], y_arr[finite]).statistic)
        except Exception:
            ic = float("nan")
        return ic

    def _compute_oos_ic(
        self,
        retrain_date: _date,
        retrain_rc: int,
        oos_window: int,
        eval_dates: list[_date],
        scores_and_rc: dict[_date, tuple[pd.Series, int]],
        fwd_returns_by_date: dict[_date, dict[str, float]],
    ) -> float:
        """Compute OOS IC: average spearman(factor_scores, forward_returns)
        over the next ``oos_window`` calendar days after ``retrain_date``.

        **Codex review #1 fix**: only use scores whose ``retrain_count``
        tag matches ``retrain_rc``. If a subsequent retrain happens
        within the OOS window, scores after that retrain came from a
        DIFFERENT model and are excluded. Without this filter, the OOS
        IC for early retrains would be polluted by later models' predictions.
        """
        from datetime import timedelta

        oos_end = retrain_date + timedelta(days=oos_window)
        oos_ics: list[float] = []

        for ed in eval_dates:
            if ed <= retrain_date or ed > oos_end:
                continue
            entry = scores_and_rc.get(ed)
            if entry is None:
                continue
            scores, rc = entry
            # Key filter: only scores from the SAME model (same retrain_count)
            if rc != retrain_rc:
                continue
            fwd = fwd_returns_by_date.get(ed, {})
            if scores is None or scores.empty or not fwd:
                continue

            common = sorted(set(scores.index) & set(fwd.keys()))
            if len(common) < 5:
                continue

            s = np.array([float(scores[sym]) for sym in common])
            r = np.array([fwd[sym] for sym in common])
            finite = np.isfinite(s) & np.isfinite(r)
            if finite.sum() < 5:
                continue

            try:
                ic = float(stats.spearmanr(s[finite], r[finite]).statistic)
                oos_ics.append(ic)
            except Exception:
                continue

        return float(np.mean(oos_ics)) if oos_ics else float("nan")

    # ── Task 2.5: Turnover analysis ────────────────────────────────

    def _compute_turnover(
        self,
        all_scores: list[tuple[_date, pd.Series, int]],
        result: DiagnosticsResult,
    ) -> None:
        """Compute top-N retention rate across consecutive eval dates.

        Uses **Jaccard similarity** (|intersection| / |union|) instead of
        the original asymmetric |intersection| / |prev| — this is
        symmetric and doesn't over-penalize when the universe shrinks
        between consecutive dates. (Codex review #4.)

        ``avg_turnover = 1 - mean(jaccard_similarity)``
        """
        top_n = self._config.top_n_for_turnover
        prev_top: set[str] | None = None

        for eval_date, scores, _rc in all_scores:
            if scores.empty:
                continue
            current_top = set(scores.nlargest(min(top_n, len(scores))).index)

            if prev_top is not None and current_top:
                union = prev_top | current_top
                intersection = prev_top & current_top
                jaccard = len(intersection) / max(len(union), 1)
                result.turnover_series.append({
                    "date": eval_date.isoformat(),
                    "retention_rate": round(jaccard, 4),
                })

            prev_top = current_top

        if result.turnover_series:
            rates = [e["retention_rate"] for e in result.turnover_series]
            result.avg_turnover = round(1.0 - float(np.mean(rates)), 4)

    # ── Task 2.6: Verdict + warnings ───────────────────────────────

    def _compute_verdict(self, result: DiagnosticsResult) -> None:
        """Compute summary verdict and human-readable warnings."""
        cfg = self._config

        # Fix #2 (codex review): if IC data is insufficient, the verdict
        # must be "insufficient_data", not a false "healthy" from 0.0 defaults.
        if not np.isfinite(result.overfitting_score):
            result.verdict = "insufficient_data"
            result.warnings.append(
                "Insufficient IC data to compute overfitting score. "
                "Possible causes: too few retrains, too short date range, "
                "or forward_horizon exceeding available data. "
                "Cannot assess overfitting risk."
            )
            return

        # Overfitting verdict
        if result.overfitting_score > cfg.severe_overfit_threshold:
            result.verdict = "severe_overfit"
            result.warnings.append(
                f"IS IC ({result.mean_train_ic:.3f}) >> OOS IC "
                f"({result.mean_oos_ic:.3f}) — overfitting_score="
                f"{result.overfitting_score:.2f} > {cfg.severe_overfit_threshold}"
            )
        elif result.overfitting_score > cfg.mild_overfit_threshold:
            result.verdict = "mild_overfit"
            result.warnings.append(
                f"Mild IS→OOS IC decay: overfitting_score="
                f"{result.overfitting_score:.2f}"
            )
        elif result.avg_turnover > cfg.high_turnover_threshold:
            result.verdict = "unstable"
            result.warnings.append(
                f"High turnover: {result.avg_turnover:.2f} — signal "
                f"may be noise-driven"
            )
        else:
            result.verdict = "healthy"

        # Feature stability warnings
        for feat, cv in result.feature_importance_cv.items():
            if cv > 2.0:
                result.warnings.append(
                    f"Feature '{feat}' has very high CV={cv:.2f} — "
                    f"unstable importance across retrains"
                )

        # Retrain cadence warnings
        if result.retrain_count >= 2:
            expected = result.expected_retrain_freq
            actual = result.actual_avg_gap_days
            if actual > expected * 1.5:
                result.warnings.append(
                    f"Retrain gap ({actual:.0f} days) is much larger "
                    f"than expected ({expected} days) — possible data "
                    f"scarcity or warmup issues"
                )
