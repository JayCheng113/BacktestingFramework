"""Tests for V2.22 /api/research/validate endpoint.

Uses TestClient + in-memory portfolio_runs mocking.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from ez.api.app import app
    return TestClient(app)


@pytest.fixture
def sample_run_data():
    """Synthetic equity curve + dates for a profitable strategy."""
    rng = np.random.default_rng(42)
    n_days = 1000  # ~4 years
    returns = rng.normal(0.0005, 0.01, n_days)
    equity = [1_000_000.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    dates = pd.bdate_range("2020-01-01", periods=n_days + 1)
    return {
        "equity_curve": list(equity),
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
    }


@pytest.fixture
def mock_store(monkeypatch, sample_run_data):
    """Patch PortfolioStore.get_run to return synthetic data."""
    def make_run(run_id: str, multiplier: float = 1.0):
        """Multiplier lets us create a second baseline run."""
        rng = np.random.default_rng(hash(run_id) % 10**6)
        n_days = 1000
        returns = rng.normal(0.0005 * multiplier, 0.01, n_days)
        equity = [1_000_000.0]
        for r in returns:
            equity.append(equity[-1] * (1 + r))
        dates = pd.bdate_range("2020-01-01", periods=n_days + 1)
        return {
            "run_id": run_id,
            "equity_curve": json.dumps(list(equity)),
            "dates": json.dumps([d.strftime("%Y-%m-%d") for d in dates]),
            "wf_metrics": None,
        }

    class MockStore:
        def __init__(self):
            pass
        def get_run(self, run_id: str):
            if run_id == "nonexistent":
                return None
            if run_id == "baseline_run":
                return make_run(run_id, multiplier=0.5)  # weaker baseline
            return make_run(run_id)

    monkeypatch.setattr(
        "ez.portfolio.portfolio_store.PortfolioStore", MockStore
    )
    return MockStore


class TestValidateEndpoint:
    def test_happy_path(self, client, mock_store):
        resp = client.post("/api/research/validate", json={
            "run_id": "test_run_001",
            "n_bootstrap": 200,
            "block_size": 21,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "test_run_001"
        assert "significance" in data
        assert "deflated" in data
        assert "min_btl" in data
        assert "annual" in data
        assert "verdict" in data

    def test_404_for_missing_run(self, client, mock_store):
        resp = client.post("/api/research/validate", json={
            "run_id": "nonexistent",
            "n_bootstrap": 200,
            "block_size": 21,
        })
        assert resp.status_code == 404

    def test_significance_structure(self, client, mock_store):
        resp = client.post("/api/research/validate", json={
            "run_id": "test_run_002",
            "n_bootstrap": 200,
        })
        assert resp.status_code == 200
        sig = resp.json()["significance"]
        assert "observed_sharpe" in sig
        assert "ci_lower" in sig
        assert "ci_upper" in sig
        assert "p_value" in sig
        assert 0 <= sig["p_value"] <= 1

    def test_verdict_structure(self, client, mock_store):
        resp = client.post("/api/research/validate", json={
            "run_id": "test_run_003",
            "n_bootstrap": 200,
        })
        assert resp.status_code == 200
        v = resp.json()["verdict"]
        assert v["result"] in ("pass", "warn", "fail")
        assert "checks" in v
        assert "summary" in v
        assert isinstance(v["checks"], list)

    def test_comparison_when_baseline_provided(self, client, mock_store):
        resp = client.post("/api/research/validate", json={
            "run_id": "test_run_004",
            "baseline_run_id": "baseline_run",
            "n_bootstrap": 200,
            "block_size": 21,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["comparison"] is not None
        cmp = data["comparison"]
        assert "sharpe_diff" in cmp
        assert "ci_lower" in cmp
        assert "p_value" in cmp
        assert "treatment_metrics" in cmp
        assert "control_metrics" in cmp

    def test_comparison_none_when_no_baseline(self, client, mock_store):
        resp = client.post("/api/research/validate", json={
            "run_id": "test_run_005",
            "n_bootstrap": 200,
        })
        assert resp.status_code == 200
        assert resp.json()["comparison"] is None

    def test_annual_breakdown_populated(self, client, mock_store):
        resp = client.post("/api/research/validate", json={
            "run_id": "test_run_006",
            "n_bootstrap": 200,
        })
        data = resp.json()
        assert "per_year" in data["annual"]
        # 1000 business days ≈ 4 years
        assert len(data["annual"]["per_year"]) >= 3

    def test_invalid_n_bootstrap(self, client, mock_store):
        resp = client.post("/api/research/validate", json={
            "run_id": "x",
            "n_bootstrap": 50,  # below ge=100
        })
        assert resp.status_code == 422

    def test_n_trials_affects_deflated_sharpe(self, client, mock_store):
        # Single trial
        r1 = client.post("/api/research/validate", json={
            "run_id": "test_run_007",
            "n_bootstrap": 200,
            "n_trials": 1,
        })
        # Many trials
        r100 = client.post("/api/research/validate", json={
            "run_id": "test_run_007",  # same run
            "n_bootstrap": 200,
            "n_trials": 100,
        })
        assert r1.status_code == 200 and r100.status_code == 200
        d1 = r1.json()["deflated"]
        d100 = r100.json()["deflated"]
        # More trials → higher SR_0 threshold → lower DSR
        assert d1["deflated_sharpe"] >= d100["deflated_sharpe"]
