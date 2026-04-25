"""Tests for /api/research endpoints."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

import duckdb
import pytest
from fastapi.testclient import TestClient

from ez.agent.research.store import ResearchStore
from ez.agent.research.runner import _running_tasks
from ez.api.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _patch_store():
    conn = duckdb.connect(":memory:")
    store = ResearchStore(conn)
    with patch("ez.agent.data_access.get_research_store", return_value=store):
        yield store
    conn.close()


@pytest.fixture(autouse=True)
def _cleanup_tasks():
    _running_tasks.clear()
    yield
    _running_tasks.clear()


class TestListTasks:
    def test_empty(self, _patch_store):
        resp = client.get("/api/research/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_with_tasks(self, _patch_store):
        _patch_store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                                "status": "completed", "created_at": datetime.now().isoformat()})
        resp = client.get("/api/research/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_pagination(self, _patch_store):
        for i in range(5):
            _patch_store.save_task({"task_id": f"t{i}", "goal": f"g{i}", "config": "{}",
                                    "status": "completed", "created_at": datetime.now().isoformat()})
        resp = client.get("/api/research/tasks?limit=2&offset=0")
        assert len(resp.json()) == 2


class TestGetTask:
    def test_not_found(self):
        resp = client.get("/api/research/tasks/nonexistent")
        assert resp.status_code == 404

    def test_found_with_iterations(self, _patch_store):
        _patch_store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                                "status": "completed", "created_at": datetime.now().isoformat()})
        _patch_store.save_iteration({"task_id": "t1", "iteration": 0,
                                     "strategies_tried": 3, "strategies_passed": 1,
                                     "best_sharpe": 1.2, "spec_ids": '["s1"]'})
        resp = client.get("/api/research/tasks/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert len(data["iterations"]) == 1
        assert data["iterations"][0]["strategies_passed"] == 1


class TestCancelTask:
    def test_cancel_nonexistent(self):
        resp = client.post("/api/research/tasks/nonexistent/cancel")
        assert resp.status_code == 404

    def test_cancel_running(self):
        _running_tasks["t1"] = {"events": [], "done": False}
        resp = client.post("/api/research/tasks/t1/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"

    def test_cancel_finished_returns_404(self):
        _running_tasks["t1"] = {"events": [], "done": True}
        resp = client.post("/api/research/tasks/t1/cancel")
        assert resp.status_code == 404


class TestSerializationGuard:
    def test_409_when_task_running(self):
        """Cannot start a second task while one is running."""
        _running_tasks["existing"] = {"events": [], "done": False}
        resp = client.post("/api/research/start", json={"goal": "test"})
        assert resp.status_code == 409
        assert "运行中" in resp.json()["detail"]

    def test_allows_start_when_all_done(self):
        """Can start when previous tasks are all done."""
        _running_tasks["old"] = {"events": [], "done": True}
        with patch("ez.api.routes.research.run_research_task", new_callable=AsyncMock, return_value="t1"), \
             patch("ez.api.routes.research.register_task"):
            resp = client.post("/api/research/start", json={"goal": "test"})
        assert resp.status_code != 409


class TestStreamEndpoint:
    def test_stream_nonexistent_404(self):
        resp = client.get("/api/research/tasks/nonexistent/stream")
        assert resp.status_code == 404

    def test_stream_existing_returns_events(self):
        """SSE stream returns events for a finished task."""
        _running_tasks["t1"] = {
            "events": [
                {"event": "iteration_start", "data": {"iteration": 0, "max_iterations": 5}},
                {"event": "task_complete", "data": {"total_passed": 1}},
            ],
            "done": True,
        }
        resp = client.get("/api/research/tasks/t1/stream")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert "event: iteration_start" in body
        assert "event: task_complete" in body


class TestFactorList:
    """V2.8.1: Factor list API returns all registered factors."""

    def test_list_factors_has_all_builtin(self):
        resp = client.get("/api/factors")
        assert resp.status_code == 200
        names = [f["name"] for f in resp.json()]
        for expected in ["ma", "ema", "rsi", "macd", "boll", "momentum", "vwap", "obv", "atr"]:
            assert expected in names, f"Missing factor: {expected}"
