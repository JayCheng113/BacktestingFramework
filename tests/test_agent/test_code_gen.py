"""Tests for code generation (E2)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ez.agent.code_gen import generate_strategy_code, _extract_strategy_class_name
from ez.llm.provider import LLMResponse


class TestExtractClassName:
    def test_simple_strategy(self):
        code = 'from ez.strategy.base import Strategy\nclass RSIReversal(Strategy):\n    pass'
        assert _extract_strategy_class_name(code) == "RSIReversal"

    def test_multiple_classes(self):
        code = 'from ez.strategy.base import Strategy\nclass Helper:\n    pass\nclass MyStrat(Strategy):\n    pass'
        assert _extract_strategy_class_name(code) == "MyStrat"

    def test_no_strategy(self):
        assert _extract_strategy_class_name('class Foo: pass') is None

    def test_syntax_error(self):
        assert _extract_strategy_class_name('def bad{') is None


class TestGenerateStrategyCode:
    @pytest.mark.asyncio
    async def test_success(self):
        p = MagicMock()
        with patch("ez.agent.code_gen.chat_sync", return_value=LLMResponse(content="done")), \
             patch("ez.agent.code_gen._find_latest_strategy", return_value=("rsi.py", "RSI")):
            f, c, e = await generate_strategy_code(p, "RSI<30买入")
            assert f == "rsi.py"
            assert c == "RSI"
            assert e is None

    @pytest.mark.asyncio
    async def test_retries_exhaust(self):
        p = MagicMock()
        with patch("ez.agent.code_gen.chat_sync", return_value=LLMResponse(content="fail")), \
             patch("ez.agent.code_gen._find_latest_strategy", return_value=(None, None)):
            f, c, e = await generate_strategy_code(p, "bad", max_retries=2)
            assert f is None
            assert "2次重试" in e

    @pytest.mark.asyncio
    async def test_exception(self):
        p = MagicMock()
        with patch("ez.agent.code_gen.chat_sync", side_effect=Exception("LLM down")):
            f, c, e = await generate_strategy_code(p, "test")
            assert f is None
            assert "LLM down" in e
