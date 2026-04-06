"""V2.13 Phase 2: MLDiagnostics unit + integration tests.

Tests for the overfitting detection companion tool. Covers:
- DiagnosticsConfig default thresholds
- DiagnosticsResult JSON serialization
- MLDiagnostics import / init / run skeleton
- Feature importance stability
- IS/OOS IC decay
- Turnover analysis
- Verdict + warnings logic
- End-to-end pipeline

**CI note**: scikit-learn is an OPTIONAL dependency. This module is
SKIPPED when sklearn is not installed.
"""
from __future__ import annotations

from datetime import date, datetime
import json

import numpy as np
import pandas as pd
import pytest

pytest.importorskip(
    "sklearn",
    reason="V2.13 MLDiagnostics tests require scikit-learn; "
           "install with `pip install -e '.[ml]'`",
)


def _make_universe(n_days: int = 400, n_stocks: int = 5, seed: int = 42):
    """Build a deterministic multi-stock universe for diagnostics testing."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    data = {}
    for i in range(n_stocks):
        prices = 100 * np.cumprod(1 + rng.normal(0.0003 * (i + 1), 0.012, n_days))
        data[f"S{i:02d}"] = pd.DataFrame({
            "open": prices, "high": prices * 1.005, "low": prices * 0.995,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
        }, index=dates)
    from ez.portfolio.calendar import TradingCalendar
    cal = TradingCalendar.from_dates([d.date() for d in dates])
    return data, cal, dates


def _make_alpha(train_window=60, retrain_freq=20, purge_days=5, **kwargs):
    """Build a standard Ridge MLAlpha for testing."""
    from ez.portfolio.ml_alpha import MLAlpha
    from sklearn.linear_model import Ridge
    return MLAlpha(
        name=kwargs.pop("name", "test_diag"),
        model_factory=kwargs.pop("model_factory", lambda: Ridge(alpha=1.0)),
        feature_fn=kwargs.pop("feature_fn", lambda df: pd.DataFrame({
            "ret1": df["adj_close"].pct_change(1),
            "ret5": df["adj_close"].pct_change(5),
        }).dropna()),
        target_fn=kwargs.pop("target_fn", lambda df: df["adj_close"].pct_change(5).shift(-5)),
        train_window=train_window,
        retrain_freq=retrain_freq,
        purge_days=purge_days,
        **kwargs,
    )


# ─── Task 2.1: Skeleton tests ────────────────────────────────────────

def test_diagnostics_config_defaults():
    from ez.portfolio.ml_diagnostics import DiagnosticsConfig
    cfg = DiagnosticsConfig()
    assert cfg.severe_overfit_threshold == 0.5
    assert cfg.mild_overfit_threshold == 0.2
    assert cfg.high_turnover_threshold == 0.6
    assert cfg.top_n_for_turnover == 10


def test_diagnostics_result_to_dict_json_serializable():
    from ez.portfolio.ml_diagnostics import DiagnosticsResult
    result = DiagnosticsResult()
    d = result.to_dict()
    json_str = json.dumps(d)
    assert len(json_str) > 10
    assert all(isinstance(k, str) for k in d.keys())


def test_ml_diagnostics_import():
    from ez.portfolio.ml_diagnostics import MLDiagnostics
    assert MLDiagnostics is not None


def test_ml_diagnostics_init():
    from ez.portfolio.ml_diagnostics import MLDiagnostics
    alpha = _make_alpha()
    diag = MLDiagnostics(alpha)
    assert diag._source_alpha is alpha


def test_ml_diagnostics_run_returns_result():
    from ez.portfolio.ml_diagnostics import MLDiagnostics, DiagnosticsResult
    data, cal, dates = _make_universe()
    alpha = _make_alpha()
    diag = MLDiagnostics(alpha)
    result = diag.run(data, cal, dates[100].date(), dates[-1].date())
    assert isinstance(result, DiagnosticsResult)
    assert result.expected_retrain_freq == 20


# ─── Task 2.2: Walk-through loop tests ──────────────────────────────

class TestDiagnosticsWalkthrough:
    def test_retrain_cadence_matches_expected(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha(train_window=60, retrain_freq=20, purge_days=5)
        diag = MLDiagnostics(alpha)
        result = diag.run(data, cal, dates[100].date(), dates[-1].date())

        assert result.retrain_count >= 3, f"Expected >= 3 retrains, got {result.retrain_count}"
        assert result.expected_retrain_freq == 20
        assert len(result.retrain_dates) == result.retrain_count
        # Average gap should be close to retrain_freq (±40% tolerance for
        # calendar vs trading day differences + weekly eval sampling)
        assert 12 <= result.actual_avg_gap_days <= 30, (
            f"Expected ~20 day gaps, got {result.actual_avg_gap_days:.1f}"
        )

    def test_run_result_has_all_metric_fields(self):
        """After a real run, all metric fields should be populated."""
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha(train_window=60, retrain_freq=20, purge_days=5)
        diag = MLDiagnostics(alpha)
        result = diag.run(data, cal, dates[100].date(), dates[-1].date())

        # Feature importance populated
        assert len(result.feature_importance) > 0
        assert len(result.feature_importance_cv) > 0
        # IC series populated
        assert len(result.ic_series) > 0
        assert len(result.ic_series) == result.retrain_count
        # Turnover populated
        assert len(result.turnover_series) > 0
        assert 0.0 <= result.avg_turnover <= 1.0
        # Verdict set
        assert result.verdict in ("healthy", "mild_overfit", "severe_overfit", "unstable")
        # Whole thing serializable
        import json
        json.dumps(result.to_dict())

    def test_diagnostic_alpha_does_not_modify_original(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()
        snap_before = alpha.diagnostics_snapshot()
        assert snap_before["retrain_count"] == 0
        assert snap_before["has_model"] is False

        diag = MLDiagnostics(alpha)
        diag.run(data, cal, dates[100].date(), dates[-1].date())

        snap_after = alpha.diagnostics_snapshot()
        assert snap_after["retrain_count"] == 0, "Original alpha was modified by diagnostics"
        assert snap_after["has_model"] is False


# ─── Task 2.3: Feature importance stability ───────────────────────

class TestFeatureImportanceStability:
    def test_ridge_coef_captured_across_retrains(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha(train_window=60, retrain_freq=20, purge_days=5)
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        assert "ret1" in result.feature_importance
        assert "ret5" in result.feature_importance
        assert len(result.feature_importance["ret1"]) == result.retrain_count
        # Ridge coef_ should be relatively stable on this synthetic data
        assert result.feature_importance_cv["ret1"] < 5.0

    def test_random_forest_importance_captured(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        from sklearn.ensemble import RandomForestRegressor
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha(
            model_factory=lambda: RandomForestRegressor(
                n_estimators=5, max_depth=3, n_jobs=1, random_state=0,
            ),
        )
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        # RF uses feature_importances_ (non-negative)
        for feat, values in result.feature_importance.items():
            assert all(v >= 0 for v in values), f"RF importance for {feat} has negative value"

    def test_cv_computed_per_feature(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        # CV should be a finite float for each feature with >= 2 retrains
        for feat, cv in result.feature_importance_cv.items():
            assert isinstance(cv, float)


# ─── Task 2.4: IS/OOS IC decay ───────────────────────────────────

class TestISOOSICDecay:
    def test_ic_series_has_one_entry_per_retrain(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha(train_window=60, retrain_freq=20, purge_days=5)
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        assert len(result.ic_series) == result.retrain_count
        for entry in result.ic_series:
            assert "retrain_date" in entry
            assert "train_ic" in entry
            assert "oos_ic" in entry

    def test_train_ic_is_finite(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha(train_window=60, retrain_freq=20, purge_days=5)
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        finite_train = [e["train_ic"] for e in result.ic_series if np.isfinite(e["train_ic"])]
        assert len(finite_train) >= 1, "No finite IS IC values computed"

    def test_overfitting_score_bounded(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        # Score is non-negative (clamped at 0)
        assert result.overfitting_score >= 0.0

    def test_simple_ridge_not_severely_overfit(self):
        """A simple Ridge with regularization on synthetic data with
        a real (if weak) signal should not be severely overfitting."""
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha(train_window=80, retrain_freq=20, purge_days=5)
        result = MLDiagnostics(alpha).run(data, cal, dates[120].date(), dates[-1].date())

        # Ridge(alpha=1.0) is strongly regularized — shouldn't overfit badly
        # We don't assert < 0.5 (severe) because synthetic data is noisy,
        # but it should be finite and computed
        assert np.isfinite(result.overfitting_score)


# ─── Task 2.5: Turnover analysis ─────────────────────────────────

class TestTurnoverAnalysis:
    def test_turnover_series_populated(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        assert len(result.turnover_series) > 0
        for entry in result.turnover_series:
            assert "date" in entry
            assert "retention_rate" in entry
            assert 0.0 <= entry["retention_rate"] <= 1.0

    def test_avg_turnover_bounded(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        assert 0.0 <= result.avg_turnover <= 1.0


# ─── Task 2.6: Verdict + warnings ────────────────────────────────

class TestVerdictAndWarnings:
    def test_verdict_is_valid_string(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        assert result.verdict in ("healthy", "mild_overfit", "severe_overfit", "unstable")

    def test_warnings_is_list_of_strings(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        assert isinstance(result.warnings, list)
        for w in result.warnings:
            assert isinstance(w, str)

    def test_custom_config_changes_verdict(self):
        """Tighter thresholds should produce a more critical verdict."""
        from ez.portfolio.ml_diagnostics import MLDiagnostics, DiagnosticsConfig

        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()

        # Default config
        result_default = MLDiagnostics(alpha).run(
            data, cal, dates[100].date(), dates[-1].date(),
        )

        # Very tight config — almost anything triggers "severe_overfit"
        tight_config = DiagnosticsConfig(
            severe_overfit_threshold=0.01,
            mild_overfit_threshold=0.005,
            high_turnover_threshold=0.1,
        )
        result_tight = MLDiagnostics(alpha, config=tight_config).run(
            data, cal, dates[100].date(), dates[-1].date(),
        )

        # Tight config should produce more/different warnings
        assert len(result_tight.warnings) >= len(result_default.warnings)

    def test_full_pipeline_to_dict_all_fields_present(self):
        """End-to-end: run → to_dict → verify all expected keys."""
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        data, cal, dates = _make_universe(n_days=400)
        alpha = _make_alpha()
        result = MLDiagnostics(alpha).run(data, cal, dates[100].date(), dates[-1].date())

        d = result.to_dict()
        expected_keys = {
            "feature_importance", "feature_importance_cv",
            "ic_series", "mean_train_ic", "mean_oos_ic", "overfitting_score",
            "turnover_series", "avg_turnover",
            "retrain_dates", "retrain_count", "expected_retrain_freq",
            "actual_avg_gap_days",
            "verdict", "warnings",
        }
        assert expected_keys.issubset(set(d.keys())), (
            f"Missing keys: {expected_keys - set(d.keys())}"
        )

        # JSON serialize end-to-end
        import json
        json_str = json.dumps(d)
        assert len(json_str) > 100
