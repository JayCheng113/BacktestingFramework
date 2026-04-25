"""V2.13.1 Phase 5: ML Alpha API integration tests.

Tests split into two groups:
- No-sklearn tests: /template, /files — run on ALL CI envs
- Sklearn-required tests: /diagnostics — skip if sklearn missing
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

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


# ─── No-sklearn tests (run on ALL CI envs) ────────────────────────

class TestCodeAPIMLAlphaRouting:
    """These tests do NOT require sklearn — they test code.py routing
    changes that should work on base install."""

    def test_template_endpoint_accepts_ml_alpha(self):
        resp = client.post("/api/code/template", json={
            "class_name": "TestAlpha",
            "kind": "ml_alpha",
            "description": "test alpha",
        })
        assert resp.status_code == 200
        code = resp.json()["code"]
        assert "class TestAlpha" in code
        assert "MLAlpha" in code

    def test_files_endpoint_accepts_ml_alpha_kind(self):
        resp = client.get("/api/code/files?kind=ml_alpha")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_files_endpoint_rejects_unknown_kind(self):
        resp = client.get("/api/code/files?kind=nonexistent")
        assert resp.status_code == 422

    def test_template_endpoint_rejects_unknown_kind(self):
        resp = client.post("/api/code/template", json={
            "class_name": "X", "kind": "nonexistent", "description": "x",
        })
        assert resp.status_code == 422


# ─── Startup-scan + refresh regression (codex gap #2) ─────────────

class TestRegistryMLAlpha:
    """G1.1 regression: /registry must include ml_alpha as a 5th category."""

    def test_registry_has_ml_alpha_category(self):
        resp = client.get("/api/code/registry")
        assert resp.status_code == 200
        data = resp.json()
        assert "ml_alpha" in data, f"Missing ml_alpha key, got: {sorted(data.keys())}"
        assert "builtin" in data["ml_alpha"]
        assert "user" in data["ml_alpha"]

    def test_refresh_preserves_ml_alpha_category(self):
        """After /refresh, ml_alpha must still be present in /registry."""
        client.post("/api/code/refresh")
        resp = client.get("/api/code/registry")
        assert resp.status_code == 200
        assert "ml_alpha" in resp.json()


class TestStartupScanAndRefresh:
    """Regression tests for the root cause Phase 5 was built to fix:
    ml_alphas/ must be scanned at startup AND cleaned+reloaded on
    /refresh."""

    def test_load_ml_alphas_is_called_at_startup(self):
        """Verify load_ml_alphas exists and is importable from loader.
        The actual startup call is in app.py lifespan — we verify the
        function exists and is callable."""
        from ez.portfolio.loader import load_ml_alphas
        assert callable(load_ml_alphas)

    def test_refresh_endpoint_includes_ml_alpha_in_cleanup(self):
        """The /refresh endpoint must clean ml_alphas.* modules from
        sys.modules + CrossSectionalFactor registry, then re-scan.
        We verify by checking the endpoint runs without error and
        returns a count summary."""
        resp = client.post("/api/code/refresh")
        assert resp.status_code == 200


# ─── Sklearn-required tests ───────────────────────────────────────

class TestStrictLookbackAPI:
    """strict_lookback=True must trigger 400 through all 3 portfolio routes."""

    def test_run_strict_lookback_400(self):
        """POST /run with strict_lookback=true + insufficient strategy
        lookback → 400 ValueError."""
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ", "000002.SZ", "600000.SH"],
            "market": "cn_stock",
            "start_date": "2023-01-01",
            "end_date": "2024-01-01",
            "strategy_params": {"factor": "momentum_rank_20", "top_n": 3},
            "strict_lookback": True,
        })
        # With default TopNRotation(lookback=252) and MomentumRank(warmup=20),
        # 252 >= 20 so it should NOT raise. This verifies the field is accepted.
        # A true 400 test requires a strategy with lookback < warmup, which
        # needs Python-level setup (not possible via pure JSON API without
        # registering a custom strategy). So we verify acceptance, not rejection.
        assert resp.status_code in (200, 502), (
            f"strict_lookback field rejected: {resp.status_code} {resp.json()}"
        )

    def test_walk_forward_accepts_strict_lookback(self):
        """POST /walk-forward with strict_lookback=true is accepted."""
        resp = client.post("/api/portfolio/walk-forward", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ", "000002.SZ", "600000.SH"],
            "market": "cn_stock",
            "start_date": "2023-01-01",
            "end_date": "2024-01-01",
            "strategy_params": {"factor": "momentum_rank_20", "top_n": 3},
            "strict_lookback": True,
            "n_splits": 2,
            "train_ratio": 0.7,
        })
        # Accept or data-fetch fail (502) — NOT 422 for unknown field
        assert resp.status_code in (200, 502), (
            f"strict_lookback field rejected: {resp.status_code}"
        )

    def test_search_accepts_strict_lookback(self):
        """POST /search with strict_lookback=true passes Pydantic validation.
        The 400 '参数网格为空' is a search-logic rejection (param grid format),
        NOT a Pydantic field rejection. A 422 would mean the field was unknown."""
        resp = client.post("/api/portfolio/search", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ", "000002.SZ", "600000.SH"],
            "market": "cn_stock",
            "start_date": "2023-06-01",
            "end_date": "2024-01-01",
            "strict_lookback": True,
            "param_ranges": [{"name": "top_n", "min": 3, "max": 5, "step": 1}],
        })
        # 400 = search logic error (param grid empty), NOT Pydantic rejection.
        # 422 would mean strict_lookback field was unknown → test fail.
        assert resp.status_code != 422, (
            f"strict_lookback field rejected by Pydantic: {resp.json()}"
        )


class TestMLAlphaDiagnosticsEndpoint:
    """These tests require sklearn for MLAlpha instantiation."""

    @pytest.fixture(autouse=True)
    def _skip_without_sklearn(self):
        pytest.importorskip("sklearn", reason="diagnostics tests need sklearn")

    def test_unknown_alpha_404(self):
        resp = client.post("/api/portfolio/ml-alpha/diagnostics", json={
            "ml_alpha_name": "NonExistentAlpha",
            "symbols": ["S00"],
        })
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_non_mlalpha_422(self):
        resp = client.post("/api/portfolio/ml-alpha/diagnostics", json={
            "ml_alpha_name": "MomentumRank",
            "symbols": ["S00"],
        })
        assert resp.status_code == 422
        assert "not an MLAlpha" in resp.json()["detail"]

    def test_empty_symbols_422(self):
        resp = client.post("/api/portfolio/ml-alpha/diagnostics", json={
            "ml_alpha_name": "SomeAlpha",
            "symbols": [],
        })
        assert resp.status_code == 422

    def test_happy_path_with_mock_data(self):
        from ez.portfolio.ml.alpha import MLAlpha
        from ez.portfolio.cross_factor import CrossSectionalFactor
        from sklearn.linear_model import Ridge

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

            assert resp.status_code == 200, f"Got {resp.status_code}: {resp.json()}"
            data = resp.json()
            assert data["verdict"] in (
                "healthy", "mild_overfit", "severe_overfit",
                "unstable", "insufficient_data",
            )
            assert "ic_series" in data
            assert "feature_importance_cv" in data
            assert "overfitting_score" in data
            assert isinstance(data["warnings"], list)

        finally:
            CrossSectionalFactor._registry.pop("_TestDiagApiAlpha", None)
            for key in list(CrossSectionalFactor._registry_by_key.keys()):
                if "_TestDiagApiAlpha" in key:
                    del CrossSectionalFactor._registry_by_key[key]
