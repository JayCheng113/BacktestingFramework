"""S4: End-to-end API tests for /experiments endpoints.

Uses in-memory DuckDB + mock data fetching to avoid file lock issues.
"""
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
    """Synthetic data to avoid DuckDB file lock."""
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
    """Replace store + data fetching with test doubles."""
    conn = duckdb.connect(":memory:")
    store = ExperimentStore(conn)
    with patch("ez.api.routes.experiments._get_experiment_store", return_value=store), \
         patch("ez.api.routes.experiments._fetch_data", side_effect=_mock_fetch_data):
        yield
    conn.close()


class TestSubmitExperiment:
    def test_submit_returns_report(self):
        resp = client.post("/api/experiments", json={
            "strategy_name": "MACrossStrategy",
            "strategy_params": {"short_period": 5, "long_period": 20},
            "symbol": "000001.SZ",
            "market": "cn_stock",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "run_wfo": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["run_id"]
        assert data["sharpe_ratio"] is not None
        assert "gate_passed" in data

    def test_submit_invalid_dates_returns_422(self):
        resp = client.post("/api/experiments", json={
            "strategy_name": "MACrossStrategy",
            "strategy_params": {},
            "symbol": "000001.SZ",
            "start_date": "2025-01-01",
            "end_date": "2020-01-01",  # before start
        })
        assert resp.status_code == 422

    def test_submit_invalid_strategy_returns_failed(self):
        resp = client.post("/api/experiments", json={
            "strategy_name": "NonExistentStrategy",
            "strategy_params": {},
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "run_wfo": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error"]
        assert not data["gate_passed"]

    def test_duplicate_detection(self):
        payload = {
            "strategy_name": "MACrossStrategy",
            "strategy_params": {"short_period": 5, "long_period": 20},
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "run_wfo": False,
        }
        # First submit
        r1 = client.post("/api/experiments", json=payload)
        assert r1.status_code == 200
        assert r1.json()["status"] == "completed"

        # Second submit — same spec
        r2 = client.post("/api/experiments", json=payload)
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"


class TestListExperiments:
    def test_list_empty(self):
        resp = client.get("/api/experiments")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_submit(self):
        client.post("/api/experiments", json={
            "strategy_name": "MACrossStrategy",
            "strategy_params": {"short_period": 5, "long_period": 20},
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "run_wfo": False,
        })
        resp = client.get("/api/experiments")
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) == 1
        assert runs[0]["strategy_name"] == "MACrossStrategy"
        # gate_reasons must be a list (not a JSON string)
        gr = runs[0].get("gate_reasons")
        assert gr is None or isinstance(gr, list), f"gate_reasons should be list, got {type(gr)}"

    def test_list_invalid_params(self):
        resp = client.get("/api/experiments", params={"limit": -1})
        assert resp.status_code == 422
        resp = client.get("/api/experiments", params={"offset": -5})
        assert resp.status_code == 422


class TestGetExperiment:
    def test_get_existing(self):
        r1 = client.post("/api/experiments", json={
            "strategy_name": "MACrossStrategy",
            "strategy_params": {"short_period": 5, "long_period": 20},
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "run_wfo": False,
        })
        run_id = r1.json()["run_id"]
        resp = client.get(f"/api/experiments/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == run_id
        # strategy_params must be a dict (not a JSON string)
        sp = data.get("strategy_params")
        assert sp is None or isinstance(sp, dict), f"strategy_params should be dict, got {type(sp)}"

    def test_get_nonexistent(self):
        resp = client.get("/api/experiments/nonexistent")
        assert resp.status_code == 404


class TestDeleteExperiment:
    def test_delete_existing(self):
        r1 = client.post("/api/experiments", json={
            "strategy_name": "MACrossStrategy",
            "strategy_params": {"short_period": 5, "long_period": 20},
            "symbol": "000001.SZ",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
            "run_wfo": False,
        })
        run_id = r1.json()["run_id"]
        resp = client.delete(f"/api/experiments/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        # Verify gone
        assert client.get(f"/api/experiments/{run_id}").status_code == 404

    def test_delete_nonexistent(self):
        resp = client.delete("/api/experiments/nonexistent")
        assert resp.status_code == 404


class TestCleanupExperiments:
    def test_cleanup_removes_old(self):
        # Submit 3 experiments with different params (unique spec_ids)
        for i in range(3):
            client.post("/api/experiments", json={
                "strategy_name": "MACrossStrategy",
                "strategy_params": {"short_period": 5 + i, "long_period": 20},
                "symbol": "000001.SZ",
                "start_date": "2020-01-01",
                "end_date": "2023-12-31",
                "run_wfo": False,
            })
        resp = client.post("/api/experiments/cleanup", params={"keep_last": 1})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2

        runs = client.get("/api/experiments").json()
        assert len(runs) == 1
