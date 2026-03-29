"""Tests for hypothesis generation (E1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from ez.agent.hypothesis import ResearchGoal, generate_hypotheses, _parse_hypotheses
from ez.llm.provider import LLMResponse


class TestResearchGoal:
    def test_defaults(self):
        g = ResearchGoal(description="test")
        assert g.market == "cn_stock"
        assert g.symbol == "000001.SZ"
        assert g.n_hypotheses == 5
        assert g.start_date is not None
        assert g.end_date is not None

    def test_custom_dates(self):
        g = ResearchGoal(description="test", start_date=date(2020, 1, 1), end_date=date(2023, 12, 31))
        assert g.start_date == date(2020, 1, 1)


class TestParseHypotheses:
    def test_json_array(self):
        assert _parse_hypotheses('["RSI超卖反转", "双均线交叉"]') == ["RSI超卖反转", "双均线交叉"]

    def test_json_in_markdown(self):
        text = '```json\n["hypothesis 1", "hypothesis 2"]\n```'
        assert _parse_hypotheses(text) == ["hypothesis 1", "hypothesis 2"]

    def test_numbered_list(self):
        text = "1. RSI反转策略\n2. 均线交叉\n3. MACD动量"
        result = _parse_hypotheses(text)
        assert len(result) == 3
        assert "RSI反转策略" in result[0]

    def test_bullet_list(self):
        text = "- RSI策略\n- MA策略"
        assert len(_parse_hypotheses(text)) == 2

    def test_empty(self):
        assert _parse_hypotheses("") == []

    def test_no_match(self):
        assert _parse_hypotheses("just some text") == []


class TestGenerateHypotheses:
    @pytest.mark.asyncio
    async def test_basic(self):
        p = MagicMock()
        p.achat = AsyncMock(return_value=LLMResponse(content='["RSI<30买入", "双均线金叉"]'))
        result = await generate_hypotheses(p, ResearchGoal(description="test", n_hypotheses=2))
        assert len(result) == 2
        assert "RSI" in result[0]

    @pytest.mark.asyncio
    async def test_with_previous_analysis(self):
        p = MagicMock()
        p.achat = AsyncMock(return_value=LLMResponse(content='["改进版RSI"]'))
        await generate_hypotheses(p, ResearchGoal(description="test"), previous_analysis="RSI效果好")
        msgs = p.achat.call_args[0][0]
        user_msg = [m for m in msgs if m.role == "user"][0]
        assert "RSI效果好" in user_msg.content

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        p = MagicMock()
        p.achat = AsyncMock(side_effect=Exception("timeout"))
        assert await generate_hypotheses(p, ResearchGoal(description="test")) == []
