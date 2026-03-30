"""Tests for research task persistence."""
from __future__ import annotations

import json
from datetime import datetime

import duckdb
import pytest

from ez.agent.research_store import ResearchStore


@pytest.fixture
def store():
    conn = duckdb.connect(":memory:")
    s = ResearchStore(conn)
    yield s
    conn.close()


class TestSaveAndGetTask:
    def test_save_and_get(self, store):
        store.save_task({"task_id": "t1", "goal": "探索动量策略",
                         "config": json.dumps({"max_iterations": 5}), "status": "running",
                         "created_at": datetime.now().isoformat()})
        task = store.get_task("t1")
        assert task is not None
        assert task["goal"] == "探索动量策略"
        assert task["status"] == "running"

    def test_get_nonexistent(self, store):
        assert store.get_task("nope") is None

    def test_update_status(self, store):
        store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                         "status": "running", "created_at": datetime.now().isoformat()})
        store.update_task_status("t1", "completed", stop_reason="收敛", summary="找到3个策略")
        task = store.get_task("t1")
        assert task["status"] == "completed"
        assert task["stop_reason"] == "收敛"
        assert task["completed_at"] is not None


class TestListTasks:
    def test_list_empty(self, store):
        assert store.list_tasks() == []

    def test_list_with_tasks(self, store):
        for i in range(3):
            store.save_task({"task_id": f"t{i}", "goal": f"goal {i}", "config": "{}",
                             "status": "completed", "created_at": datetime.now().isoformat()})
        assert len(store.list_tasks(limit=2)) == 2

    def test_list_ordered_desc(self, store):
        store.save_task({"task_id": "old", "goal": "old", "config": "{}",
                         "status": "completed", "created_at": "2024-01-01T00:00:00"})
        store.save_task({"task_id": "new", "goal": "new", "config": "{}",
                         "status": "completed", "created_at": "2025-01-01T00:00:00"})
        tasks = store.list_tasks()
        assert tasks[0]["task_id"] == "new"


class TestIterations:
    def test_save_and_get(self, store):
        store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                         "status": "running", "created_at": datetime.now().isoformat()})
        store.save_iteration({"task_id": "t1", "iteration": 0,
                              "hypotheses": json.dumps(["h1", "h2"]),
                              "strategies_tried": 2, "strategies_passed": 1,
                              "best_sharpe": 1.2,
                              "analysis": json.dumps({"direction": "继续"}),
                              "spec_ids": json.dumps(["spec1"])})
        iters = store.get_iterations("t1")
        assert len(iters) == 1
        assert iters[0]["strategies_passed"] == 1
        assert iters[0]["best_sharpe"] == 1.2

    def test_multiple_iterations(self, store):
        store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                         "status": "running", "created_at": datetime.now().isoformat()})
        for i in range(3):
            store.save_iteration({"task_id": "t1", "iteration": i,
                                  "strategies_tried": i + 1, "strategies_passed": 0,
                                  "best_sharpe": 0.0, "spec_ids": "[]"})
        iters = store.get_iterations("t1")
        assert len(iters) == 3
        assert iters[0]["iteration"] == 0
        assert iters[2]["iteration"] == 2

    def test_get_iterations_nonexistent_task(self, store):
        assert store.get_iterations("nope") == []


class TestStoreEdgeCases:
    def test_update_nonexistent_task(self, store):
        """Updating non-existent task is a silent no-op (no error)."""
        store.update_task_status("nope", "completed")
        assert store.get_task("nope") is None

    def test_duplicate_task_id_raises(self, store):
        """Duplicate task_id should raise."""
        store.save_task({"task_id": "t1", "goal": "a", "config": "{}", "status": "running",
                         "created_at": datetime.now().isoformat()})
        with pytest.raises(Exception):
            store.save_task({"task_id": "t1", "goal": "b", "config": "{}", "status": "running",
                             "created_at": datetime.now().isoformat()})

    def test_update_to_running_no_completed_at(self, store):
        store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                         "status": "pending", "created_at": datetime.now().isoformat()})
        store.update_task_status("t1", "running")
        task = store.get_task("t1")
        assert task["status"] == "running"
        assert task["completed_at"] is None
