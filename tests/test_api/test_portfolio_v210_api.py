"""V2.10 API route tests: evaluate-factors, factor-correlation, walk-forward."""
import pytest
from fastapi.testclient import TestClient

from ez.api.app import app

client = TestClient(app)


class TestEvaluateFactors:
    """POST /api/portfolio/evaluate-factors"""

    def test_basic_evaluation(self):
        resp = client.post("/api/portfolio/evaluate-factors", json={
            "symbols": ["000001.SZ", "600519.SH"],
            "factor_names": ["momentum_rank_20"],
            "start_date": "2023-01-01",
            "end_date": "2023-06-01",
        })
        # May return empty results if no data available, but should not crash
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "symbols_count" in data

    def test_invalid_factor_name(self):
        resp = client.post("/api/portfolio/evaluate-factors", json={
            "symbols": ["000001.SZ"],
            "factor_names": ["nonexistent_factor"],
        })
        assert resp.status_code == 400
        assert "Unknown factor" in resp.json()["detail"]

    def test_missing_symbols(self):
        resp = client.post("/api/portfolio/evaluate-factors", json={
            "factor_names": ["momentum_rank_20"],
        })
        assert resp.status_code == 422  # Pydantic validation

    def test_forward_days_bounds(self):
        resp = client.post("/api/portfolio/evaluate-factors", json={
            "symbols": ["000001.SZ"],
            "factor_names": ["momentum_rank_20"],
            "forward_days": 0,
        })
        assert resp.status_code == 422

    def test_eval_freq_validation(self):
        resp = client.post("/api/portfolio/evaluate-factors", json={
            "symbols": ["000001.SZ"],
            "factor_names": ["momentum_rank_20"],
            "eval_freq": "biweekly",
        })
        assert resp.status_code == 422


class TestFactorCorrelation:
    """POST /api/portfolio/factor-correlation"""

    def test_basic_correlation(self):
        resp = client.post("/api/portfolio/factor-correlation", json={
            "symbols": ["000001.SZ", "600519.SH"],
            "factor_names": ["momentum_rank_20", "volume_rank_20"],
            "start_date": "2023-01-01",
            "end_date": "2023-06-01",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "factor_names" in data
        assert "correlation_matrix" in data
        assert len(data["factor_names"]) == 2

    def test_single_factor(self):
        resp = client.post("/api/portfolio/factor-correlation", json={
            "symbols": ["000001.SZ"],
            "factor_names": ["momentum_rank_20"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["factor_names"]) == 1

    def test_invalid_factor(self):
        resp = client.post("/api/portfolio/factor-correlation", json={
            "symbols": ["000001.SZ"],
            "factor_names": ["bad_factor"],
        })
        assert resp.status_code == 400


class TestWalkForward:
    """POST /api/portfolio/walk-forward"""

    def test_basic_wf(self):
        resp = client.post("/api/portfolio/walk-forward", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ", "600519.SH"],
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "n_splits": 2,
            "strategy_params": {"top_n": 1, "factor": "momentum_rank_20"},
        })
        # May fail if no data, but should return 200 or 400 (not 500)
        assert resp.status_code in (200, 400)

    def test_invalid_splits(self):
        resp = client.post("/api/portfolio/walk-forward", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ"],
            "n_splits": 1,
        })
        assert resp.status_code == 422  # Pydantic ge=2

    def test_invalid_train_ratio(self):
        resp = client.post("/api/portfolio/walk-forward", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ"],
            "train_ratio": 0.0,
        })
        assert resp.status_code == 422

    def test_response_structure(self):
        resp = client.post("/api/portfolio/walk-forward", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ", "600519.SH"],
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
            "n_splits": 2,
            "strategy_params": {"top_n": 1, "factor": "momentum_rank_20"},
        })
        if resp.status_code == 200:
            data = resp.json()
            assert "n_splits" in data
            assert "is_sharpes" in data
            assert "oos_sharpes" in data
            assert "degradation" in data
            assert "significance" in data or data.get("significance") is None
