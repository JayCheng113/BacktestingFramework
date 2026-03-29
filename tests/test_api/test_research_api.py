"""Tests for /api/research endpoints."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import duckdb
import pytest
from fastapi.testclient import TestClient

from ez.agent.research_store import ResearchStore
from ez.agent.research_runner import _running_tasks
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


class TestGetTask:
    def test_not_found(self):
        resp = client.get("/api/research/tasks/nonexistent")
        assert resp.status_code == 404

    def test_found(self, _patch_store):
        _patch_store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                                "status": "completed", "created_at": datetime.now().isoformat()})
        resp = client.get("/api/research/tasks/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert "iterations" in data


class TestCancelTask:
    def test_cancel_nonexistent(self):
        resp = client.post("/api/research/tasks/nonexistent/cancel")
        assert resp.status_code == 404

    def test_cancel_running(self):
        _running_tasks["t1"] = {"events": [], "done": False}
        resp = client.post("/api/research/tasks/t1/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"
