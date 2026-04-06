"""V2.13.1 Phase 5: ML Alpha API integration tests.

All tests use mocked data (no real Tushare/AKShare dependency).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn", reason="ML Alpha API tests require scikit-learn")

from fastapi.testclient import TestClient
from ez.api.app import app

client = TestClient(app)


def _make_synthetic_universe(n_days=300, symbols=None):
    """Build synthetic universe data for mocked _fetch_data."""
    if symbols is None:
        symbols = ["S00", "S01", "S02"]
    rng = np.random.default_rng(42)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    data = {}
    for i, sym in enumerate(symbols):
        prices = 100 * np.cumprod(1 + rng.normal(0.0003 * (i + 1), 0.012, n_days))
        data[sym] = pd.DataFrame({
            "open": prices, "high": prices * 1.005, "low": prices * 0.995,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
        }, index=dates)
    from ez.portfolio.calendar import TradingCalendar
    cal = TradingCalendar.from_dates([d.date() for d in dates])
    return data, cal


# ─── Task 5.1: Loader + template + files ─────────────────────────

def test_template_endpoint_accepts_ml_alpha():
    """POST /api/code/template with kind=ml_alpha returns valid code."""
    resp = client.post("/api/code/template", json={
        "class_name": "TestAlpha",
        "kind": "ml_alpha",
        "description": "test alpha",
    })
    assert resp.status_code == 200
    code = resp.json()["code"]
    assert "class TestAlpha(MLAlpha)" in code
    assert "from sklearn.linear_model import Ridge" in code
    compile(code, "<template>", "exec")


def test_files_endpoint_accepts_ml_alpha_kind():
    """GET /api/code/files?kind=ml_alpha should not error."""
    resp = client.get("/api/code/files?kind=ml_alpha")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Task 5.2: Diagnostics endpoint ──────────────────────────────

def test_diagnostics_unknown_alpha_404():
    resp = client.post("/api/portfolio/ml-alpha/diagnostics", json={
        "ml_alpha_name": "NonExistentAlpha",
        "symbols": ["S00"],
    })
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_diagnostics_non_mlalpha_422():
    """MomentumRank is a CrossSectionalFactor but NOT an MLAlpha."""
    resp = client.post("/api/portfolio/ml-alpha/diagnostics", json={
        "ml_alpha_name": "MomentumRank",
        "symbols": ["S00"],
    })
    assert resp.status_code == 422
    assert "not an MLAlpha" in resp.json()["detail"]


def test_diagnostics_empty_symbols_422():
    resp = client.post("/api/portfolio/ml-alpha/diagnostics", json={
        "ml_alpha_name": "SomeAlpha",
        "symbols": [],
    })
    assert resp.status_code == 422


def test_diagnostics_happy_path_with_mock_data():
    """Register a test MLAlpha, mock _fetch_data, call the endpoint,
    verify 200 + valid result structure. No real data provider needed."""
    from ez.portfolio.ml_alpha import MLAlpha
    from ez.portfolio.cross_factor import CrossSectionalFactor
    from sklearn.linear_model import Ridge
    import sys

    # 1. Define + register a test MLAlpha
    class _TestDiagApiAlpha(MLAlpha):
        def __init__(self):
            super().__init__(
                name="_test_diag_api",
                model_factory=lambda: Ridge(alpha=1.0),
                feature_fn=lambda df: pd.DataFrame({
                    "ret": df["adj_close"].pct_change(1),
                }).dropna(),
                target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
                train_window=60, retrain_freq=20, purge_days=5,
            )

    try:
        # 2. Mock _fetch_data to return synthetic data
        synthetic_data, synthetic_cal = _make_synthetic_universe(n_days=300)

        def mock_fetch(symbols, market, start, end, lookback_days=252):
            return synthetic_data, synthetic_cal

        with patch("ez.api.routes.portfolio._fetch_data", side_effect=mock_fetch):
            resp = client.post("/api/portfolio/ml-alpha/diagnostics", json={
                "ml_alpha_name": "_TestDiagApiAlpha",
                "symbols": ["S00", "S01", "S02"],
                "start_date": "2022-04-01",
                "end_date": "2023-01-01",
            })

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        data = resp.json()

        # Verify response structure
        assert "verdict" in data
        assert data["verdict"] in (
            "healthy", "mild_overfit", "severe_overfit",
            "unstable", "insufficient_data",
        )
        assert "ic_series" in data
        assert "feature_importance_cv" in data
        assert "overfitting_score" in data
        assert "retrain_dates" in data
        assert "warnings" in data
        assert isinstance(data["warnings"], list)

    finally:
        # Cleanup registry
        CrossSectionalFactor._registry.pop("_TestDiagApiAlpha", None)
        for key in list(CrossSectionalFactor._registry_by_key.keys()):
            if "_TestDiagApiAlpha" in key:
                del CrossSectionalFactor._registry_by_key[key]
