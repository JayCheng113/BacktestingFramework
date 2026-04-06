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
