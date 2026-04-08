"""V2.15 C1: Live API integration tests.

Tests:
1. test_deploy_creates_deployment — POST /deploy with valid run_id
2. test_approve_runs_gate — POST /approve, check gate verdict
3. test_lifecycle_flow — deploy -> approve -> start -> tick -> stop
4. test_approve_rejects_bad_metrics — gate fails -> 400
5. test_list_deployments — GET /deployments returns list
6. test_dashboard — GET /dashboard returns health
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ez.api.app import app


client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures: mock portfolio store and deployment store
# ---------------------------------------------------------------------------

def _make_mock_run(run_id: str = "test-run-001") -> dict:
    """Build a synthetic portfolio run dict matching PortfolioStore.get_run() output."""
    return {
        "run_id": run_id,
        "strategy_name": "TopNRotation",
        "strategy_params": {"factor": "momentum_rank_20", "top_n": 5},
        "symbols": ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "000858.SZ",
                     "601318.SH", "600519.SH", "000333.SZ", "002415.SZ", "600276.SH"],
        "start_date": "2021-01-01",
        "end_date": "2024-01-01",
        "freq": "daily",
        "initial_cash": 1000000.0,
        "metrics": {
            "sharpe_ratio": 1.2,
            "max_drawdown": -0.15,
            "trade_count": 120,
            "total_return": 0.45,
        },
        "equity_curve": [1000000.0] * 756,
        "trade_count": 120,
        "rebalance_count": 30,
        "created_at": "2024-01-01T00:00:00",
        "rebalance_weights": [
            {"date": "2021-01-04", "weights": {"000001.SZ": 0.1, "000002.SZ": 0.1,
                                                "600000.SH": 0.1, "600036.SH": 0.1,
                                                "000858.SZ": 0.1}},
        ],
        "trades": [],
        "config": {
            "market": "cn_stock",
            "freq": "daily",
            "t_plus_1": True,
            "lot_size": 100,
            "buy_commission_rate": 0.00008,
            "sell_commission_rate": 0.00008,
            "stamp_tax_rate": 0.0005,
            "slippage_rate": 0.001,
            "min_commission": 0.0,
            "price_limit_pct": 0.1,
        },
        "warnings": [],
        "dates": [f"2021-01-{i:02d}" for i in range(4, 30)] * 28,  # >504 dates
        "weights_history": [],
    }


_WF_METRICS_GOOD = {
    "p_value": 0.01,
    "overfitting_score": 0.1,
}

_WF_METRICS_BAD = {
    "p_value": 0.5,
    "overfitting_score": 0.8,
}


def _make_mock_run_with_wf(run_id: str = "test-run-001", wf_metrics: dict | None = None) -> dict:
    """Build mock run with wf_metrics in the run dict (V2.15.1 S1: server-side WF)."""
    run = _make_mock_run(run_id)
    run["wf_metrics"] = wf_metrics
    return run


@pytest.fixture(autouse=True)
def _use_memory_db_for_live(monkeypatch):
    """Use in-memory DuckDB for live tests — never touch data/ez_trading.db."""
    import duckdb
    from ez.api.routes import live as live_module
    from ez.live.deployment_store import DeploymentStore

    # Reset singletons
    live_module.reset_live_singletons()

    # Patch _deployment_store to use in-memory DB
    _mem_store = DeploymentStore(duckdb.connect(":memory:"))
    monkeypatch.setattr(live_module, "_deployment_store", _mem_store)

    # Also patch scheduler to avoid _get_scheduler() calling get_chain()
    # which opens the real data/ez_trading.db
    from ez.live.scheduler import Scheduler
    _mock_scheduler = MagicMock(spec=Scheduler)
    _mock_scheduler.store = _mem_store
    _mock_scheduler.start_deployment = AsyncMock()
    _mock_scheduler.stop_deployment = AsyncMock()
    _mock_scheduler.pause_deployment = AsyncMock()
    _mock_scheduler.resume_deployment = AsyncMock()
    _mock_scheduler.tick = AsyncMock(return_value=[])
    monkeypatch.setattr(live_module, "_scheduler", _mock_scheduler)

    yield

    # Cleanup
    try:
        _mem_store._conn.close()
    except Exception:
        pass
    live_module.reset_live_singletons()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeployEndpoint:
    """POST /api/live/deploy"""

    def test_deploy_creates_deployment(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Test Deployment",
            })
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.json()}"
        data = resp.json()
        assert "deployment_id" in data
        assert "spec_id" in data
        assert len(data["deployment_id"]) > 0
        assert len(data["spec_id"]) > 0

    def test_deploy_missing_run_404(self):
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = None
            resp = client.post("/api/live/deploy", json={
                "source_run_id": "nonexistent",
                "name": "Test",
            })
        assert resp.status_code == 404


class TestApproveEndpoint:
    """POST /api/live/deployments/{id}/approve"""

    def test_approve_runs_gate(self):
        """Deploy then approve — should pass with good metrics.
        V2.15.1 S1: wf_metrics are now in the run dict (server-side), not deploy request.
        """
        mock_run = _make_mock_run_with_wf(wf_metrics=_WF_METRICS_GOOD)
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            # 1. Deploy (no wf_metrics in request)
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Test Gate",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            # 2. Approve — gate reads wf_metrics from DB
            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")

        assert approve_resp.status_code == 200, f"Got {approve_resp.status_code}: {approve_resp.json()}"
        data = approve_resp.json()
        assert data["status"] == "approved"
        assert data["verdict"]["passed"] is True

    def test_approve_rejects_bad_metrics(self):
        """Deploy then approve with bad WF + run metrics — should fail.
        V2.15.1 S1: wf_metrics are now in the run dict (server-side).
        """
        mock_run = _make_mock_run_with_wf(wf_metrics=_WF_METRICS_BAD)
        # Make run metrics bad too
        mock_run["metrics"]["sharpe_ratio"] = 0.1  # below 0.5 threshold
        mock_run["metrics"]["max_drawdown"] = -0.40  # exceeds 0.25

        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            # Deploy (no wf_metrics in request)
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Bad Deploy",
            })
            dep_id = deploy_resp.json()["deployment_id"]

            # Approve — should fail (gate reads bad wf_metrics from DB)
            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")

        assert approve_resp.status_code == 400
        detail = approve_resp.json()["detail"]
        assert detail["verdict"]["passed"] is False


class TestLifecycleFlow:
    """Full lifecycle: deploy -> approve -> start -> tick -> stop"""

    def test_lifecycle_flow(self):
        mock_run = _make_mock_run_with_wf(wf_metrics=_WF_METRICS_GOOD)
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            # 1. Deploy (no wf_metrics in request)
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Lifecycle Test",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            # 2. Approve
            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

            # 3. Start — mock _start_engine to avoid real strategy instantiation
            with patch("ez.live.scheduler.Scheduler._start_engine", new_callable=AsyncMock) as mock_engine:
                start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
                assert start_resp.status_code == 200
                assert start_resp.json()["status"] == "running"

            # 4. Stop
            stop_resp = client.post(f"/api/live/deployments/{dep_id}/stop", json={
                "reason": "test complete",
            })
            assert stop_resp.status_code == 200
            assert stop_resp.json()["status"] == "stopped"


class TestListDeployments:
    """GET /api/live/deployments"""

    def test_list_deployments(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            # Create a deployment first
            client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "List Test",
            })

            # List
            resp = client.get("/api/live/deployments")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "deployment_id" in data[0]
        assert "name" in data[0]
        assert "status" in data[0]

    def test_list_deployments_with_status_filter(self):
        """Filter by status=pending."""
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Filter Test",
            })

            resp = client.get("/api/live/deployments?status=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert all(d["status"] == "pending" for d in data)


class TestDashboard:
    """GET /api/live/dashboard"""

    def test_dashboard(self):
        resp = client.get("/api/live/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "deployments" in data
        assert "alerts" in data
        assert isinstance(data["deployments"], list)
        assert isinstance(data["alerts"], list)


class TestDeploymentDetail:
    """GET /api/live/deployments/{id}"""

    def test_get_deployment_detail(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Detail Test",
            })
            dep_id = deploy_resp.json()["deployment_id"]

        resp = client.get(f"/api/live/deployments/{dep_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployment_id"] == dep_id
        assert data["name"] == "Detail Test"
        assert "spec" in data
        assert data["spec"]["strategy_name"] == "TopNRotation"

    def test_get_nonexistent_404(self):
        resp = client.get("/api/live/deployments/nonexistent-id")
        assert resp.status_code == 404


class TestSnapshotsAndTrades:
    """GET /api/live/deployments/{id}/snapshots and /trades"""

    def test_snapshots_empty(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Snap Test",
            })
            dep_id = deploy_resp.json()["deployment_id"]

        resp = client.get(f"/api/live/deployments/{dep_id}/snapshots")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_trades_empty(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Trade Test",
            })
            dep_id = deploy_resp.json()["deployment_id"]

        resp = client.get(f"/api/live/deployments/{dep_id}/trades")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_snapshots_nonexistent_404(self):
        resp = client.get("/api/live/deployments/nonexistent/snapshots")
        assert resp.status_code == 404

    def test_trades_nonexistent_404(self):
        resp = client.get("/api/live/deployments/nonexistent/trades")
        assert resp.status_code == 404
