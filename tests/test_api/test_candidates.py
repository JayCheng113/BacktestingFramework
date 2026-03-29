"""Tests for /candidates API — F1-F4 batch search."""
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ez.agent.experiment_store import ExperimentStore
from ez.api.app import app
from ez.strategy.loader import load_all_strategies

load_all_strategies()

client = TestClient(app)


def _mock_fetch_data(symbol, market, period, start, end):
    rng = np.random.default_rng(42)
    n = 500
    prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
    dates = pd.date_range("2020-01-03", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices * (1 + rng.normal(0, 0.002, n)),
        "high": prices * (1 + abs(rng.normal(0, 0.005, n))),
        "low": prices * (1 - abs(rng.normal(0, 0.005, n))),
        "close": prices, "adj_close": prices,
        "volume": rng.integers(100_000, 5_000_000, n),
    }, index=dates)


@pytest.fixture(autouse=True)
def _patch_deps():
    conn = duckdb.connect(":memory:")
    store = ExperimentStore(conn)
    with patch("ez.api.routes.experiments._get_experiment_store", return_value=store), \
         patch("ez.api.routes.experiments._fetch_data", side_effect=_mock_fetch_data):
        yield
    conn.close()


class TestCandidateSearch:
    def test_grid_search(self):
        resp = client.post("/api/candidates/search", json={
            "strategy_name": "MACrossStrategy",
            "param_ranges": [
                {"name": "short_period", "values": [3, 5]},
                {"name": "long_period", "values": [15, 20]},
            ],
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "mode": "grid",
            "skip_prefilter": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_specs"] == 4
        assert data["executed"] == 4
        assert len(data["ranked"]) == 4

    def test_random_search(self):
        resp = client.post("/api/candidates/search", json={
            "strategy_name": "MACrossStrategy",
            "param_ranges": [
                {"name": "short_period", "values": [3, 5, 10]},
                {"name": "long_period", "values": [15, 20, 30]},
            ],
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "mode": "random",
            "n_samples": 3,
            "seed": 42,
            "skip_prefilter": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_specs"] == 3
        assert data["executed"] == 3

    def test_prefilter_eliminates(self):
        resp = client.post("/api/candidates/search", json={
            "strategy_name": "MACrossStrategy",
            "param_ranges": [
                {"name": "short_period", "values": [3, 5]},
                {"name": "long_period", "values": [15, 20]},
            ],
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "prefilter_min_sharpe": 999,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["prefiltered"] == 4
        assert data["executed"] == 0

    def test_ranked_has_sharpe_and_run_id(self):
        resp = client.post("/api/candidates/search", json={
            "strategy_name": "MACrossStrategy",
            "param_ranges": [
                {"name": "short_period", "values": [3, 5]},
                {"name": "long_period", "values": [15, 20]},
            ],
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "skip_prefilter": True,
        })
        data = resp.json()
        for c in data["ranked"]:
            assert c["sharpe"] is not None, "sharpe should not be None for executed candidate"
            assert c["run_id"] is not None, "run_id should not be None for executed candidate"
            assert "params" in c

    def test_int_params_preserved(self):
        """Int strategy params (e.g., short_period) must not break from float coercion."""
        resp = client.post("/api/candidates/search", json={
            "strategy_name": "MACrossStrategy",
            "param_ranges": [
                {"name": "short_period", "values": [5]},
                {"name": "long_period", "values": [20]},
            ],
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "skip_prefilter": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["executed"] == 1
        # Verify the candidate actually completed (not silently failed)
        assert len(data["ranked"]) == 1
        assert data["ranked"][0]["sharpe"] is not None

    def test_invalid_dates_returns_422(self):
        """start_date > end_date should return 422, not 500."""
        resp = client.post("/api/candidates/search", json={
            "strategy_name": "MACrossStrategy",
            "param_ranges": [
                {"name": "short_period", "values": [5]},
                {"name": "long_period", "values": [20]},
            ],
            "symbol": "000001.SZ",
            "start_date": "2025-01-01",
            "end_date": "2020-01-01",
        })
        assert resp.status_code == 422

    def test_oversized_grid_rejected_early(self):
        """Grid exceeding 1000 combinations should be rejected before generation."""
        resp = client.post("/api/candidates/search", json={
            "strategy_name": "MACrossStrategy",
            "param_ranges": [
                {"name": "short_period", "values": list(range(1, 102))},  # 101 values
                {"name": "long_period", "values": list(range(1, 12))},    # 11 values → 1111
            ],
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
        })
        assert resp.status_code == 400
        assert "Too many" in resp.json()["detail"]
