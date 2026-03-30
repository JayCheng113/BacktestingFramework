"""Tests for Portfolio API endpoints (V2.9 P7)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import duckdb
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ez.api.app import app
from ez.portfolio.portfolio_store import PortfolioStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def _patch_store():
    conn = duckdb.connect(":memory:")
    store = PortfolioStore(conn)
    with patch("ez.api.routes.portfolio._get_store", return_value=store):
        yield store
    conn.close()


class TestListStrategies:
    def test_returns_builtins(self):
        resp = client.get("/api/portfolio/strategies")
        assert resp.status_code == 200
        data = resp.json()
        names = [s["name"] for s in data["strategies"]]
        assert "TopNRotation" in names
        assert "MultiFactorRotation" in names
        assert len(data["available_factors"]) > 0


class TestListRuns:
    def test_empty(self, _patch_store):
        resp = client.get("/api/portfolio/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_with_runs(self, _patch_store):
        _patch_store.save_run({"run_id": "t1", "strategy_name": "X", "metrics": {}})
        resp = client.get("/api/portfolio/runs")
        assert len(resp.json()) == 1


class TestGetRun:
    def test_not_found(self):
        resp = client.get("/api/portfolio/runs/nope")
        assert resp.status_code == 404

    def test_found(self, _patch_store):
        _patch_store.save_run({
            "run_id": "r1", "strategy_name": "TopN",
            "metrics": {"sharpe_ratio": 1.2},
            "equity_curve": [1000, 1100],
        })
        resp = client.get("/api/portfolio/runs/r1")
        assert resp.status_code == 200
        assert resp.json()["strategy_name"] == "TopN"


class TestDeleteRun:
    def test_delete(self, _patch_store):
        _patch_store.save_run({"run_id": "d1", "strategy_name": "X"})
        resp = client.delete("/api/portfolio/runs/d1")
        assert resp.status_code == 200

    def test_delete_nonexistent(self):
        resp = client.delete("/api/portfolio/runs/nope")
        assert resp.status_code == 404


class TestRunEndToEnd:
    def test_full_run_with_mock_data(self, _patch_store):
        """E2E: POST /run with mocked data → engine execution → persist → response."""
        import numpy as np
        from ez.portfolio.calendar import TradingCalendar

        dates = pd.date_range("2023-01-02", periods=200, freq="B")
        mock_data = {}
        rng = np.random.default_rng(42)
        for sym in ["A", "B", "C", "D", "E"]:
            prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, 200))
            mock_data[sym] = pd.DataFrame({
                "open": prices, "high": prices * 1.01, "low": prices * 0.99,
                "close": prices, "adj_close": prices,
                "volume": rng.integers(100_000, 1_000_000, 200),
            }, index=dates)
        mock_cal = TradingCalendar.from_dates([d.date() for d in dates])

        with patch("ez.api.routes.portfolio._fetch_data", return_value=(mock_data, mock_cal)):
            resp = client.post("/api/portfolio/run", json={
                "strategy_name": "TopNRotation",
                "symbols": ["A", "B", "C", "D", "E"],
                "start_date": "2023-02-01", "end_date": "2023-12-31",
                "freq": "monthly",
                "strategy_params": {"top_n": 3, "factor": "momentum_rank_20"},
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert "metrics" in data
        assert "equity_curve" in data
        assert len(data["equity_curve"]) > 100
        assert data["metrics"]["trade_count"] > 0

        # Verify persisted in store
        runs = _patch_store.list_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == data["run_id"]


class TestRunValidation:
    def test_invalid_freq(self):
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "TopNRotation", "symbols": ["A"], "freq": "invalid",
        })
        assert resp.status_code == 422

    def test_unknown_strategy(self):
        """Unknown strategy returns 404 (would require data fetch to reach, so mock it)."""
        with patch("ez.api.routes.portfolio._fetch_data") as mock_fetch:
            import pandas as pd
            import numpy as np
            dates = pd.date_range("2024-01-01", periods=50, freq="B")
            mock_data = {"A": pd.DataFrame({
                "open": np.ones(50), "high": np.ones(50), "low": np.ones(50),
                "close": np.ones(50), "adj_close": np.ones(50), "volume": np.ones(50) * 1000,
            }, index=dates)}
            from ez.portfolio.calendar import TradingCalendar
            mock_cal = TradingCalendar.from_dates([d.date() for d in dates])
            mock_fetch.return_value = (mock_data, mock_cal)

            resp = client.post("/api/portfolio/run", json={
                "strategy_name": "NonexistentStrategy", "symbols": ["A"],
                "start_date": "2024-01-01", "end_date": "2024-03-01",
            })
            assert resp.status_code == 404
