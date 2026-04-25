"""Tests for research report builder (E6)."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import duckdb
import pytest

from ez.agent.research.report import ResearchReport, build_report
from ez.agent.research.store import ResearchStore
from ez.llm.provider import LLMResponse


@pytest.fixture
def store():
    conn = duckdb.connect(":memory:")
    rs = ResearchStore(conn)
    rs.save_task({"task_id": "t1", "goal": "test goal", "config": '{"max_iterations": 3}',
                  "status": "completed", "created_at": datetime.now().isoformat()})
    rs.save_iteration({"task_id": "t1", "iteration": 0, "hypotheses": '["h1", "h2"]',
                       "strategies_tried": 2, "strategies_passed": 1, "best_sharpe": 1.2,
                       "analysis": '{"direction": "继续"}', "spec_ids": '["spec1"]'})
    rs.save_iteration({"task_id": "t1", "iteration": 1, "hypotheses": '["h3"]',
                       "strategies_tried": 1, "strategies_passed": 0, "best_sharpe": 0.0,
                       "analysis": '{"direction": "换方向"}', "spec_ids": '[]'})
    yield rs
    conn.close()


class TestBuildReport:
    @pytest.mark.asyncio
    async def test_without_llm(self, store):
        report = await build_report(None, store, "t1", "收敛")
        assert report.task_id == "t1"
        assert report.goal == "test goal"
        assert len(report.iterations) == 2
        assert report.total_specs == 3
        assert report.total_passed == 1
        assert report.stop_reason == "收敛"
        assert report.summary == ""

    @pytest.mark.asyncio
    async def test_with_llm_summary(self, store):
        p = MagicMock()
        p.achat = AsyncMock(return_value=LLMResponse(content="找到1个有效策略"))
        report = await build_report(p, store, "t1", "收敛")
        assert "有效策略" in report.summary

    @pytest.mark.asyncio
    async def test_llm_failure_graceful(self, store):
        p = MagicMock()
        p.achat = AsyncMock(side_effect=Exception("down"))
        report = await build_report(p, store, "t1", "收敛")
        assert report.summary == ""
        assert report.total_specs == 3

    def test_to_dict(self):
        r = ResearchReport(task_id="t1", goal="test", total_specs=5, total_passed=2)
        d = r.to_dict()
        assert d["task_id"] == "t1"
        assert d["total_specs"] == 5
