"""Tests for result analyzer (E4)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ez.agent.research.analyzer import AnalysisResult, analyze_results, _build_summary
from ez.agent.research.hypothesis import ResearchGoal
from ez.llm.provider import LLMResponse


class TestBuildSummary:
    def test_with_results(self):
        batch = MagicMock()
        batch.passed = [MagicMock(sharpe=1.2), MagicMock(sharpe=0.8)]
        batch.executed = 5
        batch.candidates = []
        summary = _build_summary(batch, ["h1", "h2"])
        assert "5" in summary
        assert "2" in summary

    def test_empty(self):
        batch = MagicMock()
        batch.passed = []
        batch.executed = 0
        batch.candidates = []
        summary = _build_summary(batch, [])
        assert "0" in summary

    def test_with_failure_reasons(self):
        """Extracts gate_reasons from failed candidates."""
        c = MagicMock()
        c.gate_passed = False
        c.report = MagicMock()
        c.report.gate_reasons = [
            {"rule": "min_sharpe", "passed": False, "message": "Sharpe=0.1 < 0.5"},
        ]
        batch = MagicMock(passed=[], executed=1, candidates=[c])
        summary = _build_summary(batch, ["h1"])
        assert "Sharpe=0.1" in summary

    def test_candidate_no_report(self):
        """Candidate with report=None should not crash."""
        c = MagicMock()
        c.gate_passed = False
        c.report = None
        batch = MagicMock(passed=[], executed=1, candidates=[c])
        summary = _build_summary(batch, [])
        assert "0" in summary  # no crash

    def test_truncates_hypotheses(self):
        """Long hypothesis texts are truncated to 60 chars."""
        batch = MagicMock(passed=[], executed=0, candidates=[])
        long_h = "a" * 100
        summary = _build_summary(batch, [long_h])
        assert "a" * 60 in summary
        assert "a" * 100 not in summary


class TestAnalyzeResults:
    @pytest.mark.asyncio
    async def test_basic(self):
        p = MagicMock()
        p.achat = AsyncMock(return_value=LLMResponse(
            content='{"direction": "收紧RSI阈值", "suggestions": ["RSI<20"]}'))
        batch = MagicMock(passed=[MagicMock(sharpe=1.1)], executed=3, candidates=[])
        result = await analyze_results(p, batch, ResearchGoal(description="test"), ["h1"])
        assert isinstance(result, AnalysisResult)
        assert "RSI" in result.direction
        assert result.passed_count == 1

    @pytest.mark.asyncio
    async def test_error_fallback(self):
        p = MagicMock()
        p.achat = AsyncMock(side_effect=Exception("timeout"))
        batch = MagicMock(passed=[], executed=2, candidates=[])
        result = await analyze_results(p, batch, ResearchGoal(description="test"), ["h1"])
        assert result.direction != ""
        assert result.passed_count == 0

    @pytest.mark.asyncio
    async def test_malformed_json(self):
        p = MagicMock()
        p.achat = AsyncMock(return_value=LLMResponse(content="just text, no json"))
        batch = MagicMock(passed=[], executed=1, candidates=[])
        result = await analyze_results(p, batch, ResearchGoal(description="test"), [])
        assert result.direction == "继续探索不同策略类型"
