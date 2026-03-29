"""Integration tests for the research runner orchestrator."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import numpy as np
import pandas as pd
import pytest

from ez.agent.hypothesis import ResearchGoal
from ez.agent.loop_controller import LoopConfig
from ez.agent.research_runner import run_research_task, cancel_task, get_task_events, _running_tasks
from ez.agent.research_store import ResearchStore
from ez.llm.provider import LLMResponse


def _make_test_data():
    rng = np.random.default_rng(42)
    n = 300
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
def _cleanup():
    _running_tasks.clear()
    yield
    _running_tasks.clear()


class TestRunResearchTask:
    @pytest.mark.asyncio
    async def test_full_pipeline_mock(self):
        """Full pipeline with mocked LLM — verifies orchestration."""
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["MA交叉策略"]'),  # E1
            LLMResponse(content='{"direction": "ok", "suggestions": []}'),  # E4
            LLMResponse(content="研究完成"),  # E6 summary
        ])
        goal = ResearchGoal(description="test", n_hypotheses=1)
        loop_config = LoopConfig(max_iterations=1)

        conn = duckdb.connect(":memory:")
        research_store = ResearchStore(conn)
        mock_batch = MagicMock(passed=[], executed=1, candidates=[])

        with patch("ez.agent.research_runner.create_provider", return_value=mock_provider), \
             patch("ez.agent.research_runner.get_research_store", return_value=research_store), \
             patch("ez.agent.research_runner.get_experiment_store"), \
             patch("ez.agent.research_runner._fetch_data", return_value=_make_test_data()), \
             patch("ez.agent.research_runner.generate_strategy_code",
                   new_callable=AsyncMock, return_value=("test.py", "TestStrat", None)), \
             patch("ez.agent.research_runner._run_batch_for_strategies", return_value=mock_batch):
            task_id = await run_research_task(goal, loop_config)

        assert task_id is not None
        task = research_store.get_task(task_id)
        assert task["status"] in ("completed", "failed")
        events = get_task_events(task_id)
        assert events["done"] is True
        event_types = [e["event"] for e in events["events"]]
        assert "iteration_start" in event_types
        assert "task_complete" in event_types or "task_failed" in event_types
        conn.close()

    @pytest.mark.asyncio
    async def test_data_fetch_failure(self):
        """Data fetch failure marks task as failed."""
        mock_provider = MagicMock()
        goal = ResearchGoal(description="test")
        loop_config = LoopConfig(max_iterations=1)

        conn = duckdb.connect(":memory:")
        research_store = ResearchStore(conn)

        with patch("ez.agent.research_runner.create_provider", return_value=mock_provider), \
             patch("ez.agent.research_runner.get_research_store", return_value=research_store), \
             patch("ez.agent.research_runner._fetch_data", side_effect=ValueError("No data")):
            task_id = await run_research_task(goal, loop_config)

        task = research_store.get_task(task_id)
        assert task["status"] == "failed"
        assert "No data" in task["error"]
        conn.close()


class TestCancel:
    def test_cancel_nonexistent(self):
        assert cancel_task("nope") is False

    def test_cancel_running(self):
        _running_tasks["t1"] = {"events": [], "done": False}
        assert cancel_task("t1") is True
        assert _running_tasks["t1"]["cancel"] is True

    def test_cancel_done(self):
        _running_tasks["t1"] = {"events": [], "done": True}
        assert cancel_task("t1") is False
