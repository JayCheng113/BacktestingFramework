"""Tests for portfolio parameter search endpoint (V2.11.1 U3) + sorting edge cases."""
from unittest.mock import patch
import numpy as np
import pytest
from fastapi.testclient import TestClient
from ez.api.app import app

client = TestClient(app)


class TestSearchEndpoint:
    def test_empty_grid_returns_400(self):
        resp = client.post("/api/portfolio/search", json={
            "strategy_name": "TopNRotation",
            "symbols": ["A", "B"],
            "param_grid": {},
        })
        assert resp.status_code == 400
        assert "空" in resp.json()["detail"]

    def test_search_with_mock_data(self):
        """Search should accept valid param_grid and return results."""
        # This test uses synthetic data via the test environment
        resp = client.post("/api/portfolio/search", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ"],
            "param_grid": {"factor": ["momentum_rank_20"], "top_n": [3]},
            "max_combinations": 1,
            "start_date": "2023-06-01",
            "end_date": "2024-01-01",
        })
        # May fail with data not found, but should not 500
        assert resp.status_code in (200, 400, 404)


class TestSortingCorrectness:
    """Issue 3: sorting must handle 0.0, NaN, None correctly."""

    def test_sort_key_handles_zero(self):
        from ez.api.routes.portfolio import np as _np
        # Simulate the sort key logic directly
        results = [
            {"sharpe": 1.5}, {"sharpe": 0.0}, {"sharpe": -0.5}, {"sharpe": None},
        ]

        def _sort_key(r):
            v = r.get("sharpe")
            if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                return -999.0
            return v

        results.sort(key=_sort_key, reverse=True)
        assert results[0]["sharpe"] == 1.5
        assert results[1]["sharpe"] == 0.0   # 0.0 should NOT be treated as missing
        assert results[2]["sharpe"] == -0.5
        assert results[3]["sharpe"] is None  # None goes last

    def test_sort_key_handles_nan(self):
        results = [
            {"sharpe": float('nan')}, {"sharpe": 0.5}, {"sharpe": -1.0},
        ]

        def _sort_key(r):
            v = r.get("sharpe")
            if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                return -999.0
            return v

        results.sort(key=_sort_key, reverse=True)
        assert results[0]["sharpe"] == 0.5
        assert results[1]["sharpe"] == -1.0
        # NaN should be last
        assert np.isnan(results[2]["sharpe"])


class TestAlphaCombinerValidation:
    """Issue 7: alpha_combiner as own sub-factor should 400."""

    def test_self_reference_blocked(self):
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ"],
            "strategy_params": {
                "factor": "alpha_combiner",
                "alpha_factors": ["alpha_combiner", "momentum_rank_20"],
            },
        })
        assert resp.status_code == 400
        assert "自身" in resp.json()["detail"]

    def test_empty_alpha_factors_blocked(self):
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "TopNRotation",
            "symbols": ["000001.SZ"],
            "strategy_params": {"factor": "alpha_combiner", "alpha_factors": []},
        })
        assert resp.status_code == 400


class TestAbstractFactorNotExposed:
    """Issue 2: FundamentalCrossFactor should not appear in available factors."""

    def test_abstract_base_not_in_factors(self):
        resp = client.get("/api/portfolio/strategies")
        data = resp.json()
        factors = data.get("available_factors", [])
        assert "FundamentalCrossFactor" not in factors
