"""Tests for the AI assistant agent loop."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ez.agent.assistant import MAX_TOOL_ROUNDS, _build_system_prompt, chat_stream, chat_sync
from ez.llm.provider import LLMEvent, LLMMessage, LLMProvider, LLMResponse, ToolCall


class TestSystemPrompt:
    def test_contains_strategy_interface(self):
        prompt = _build_system_prompt()
        assert "Strategy(ABC)" in prompt
        assert "generate_signals" in prompt
        assert "required_factors" in prompt

    def test_contains_factor_interface(self):
        prompt = _build_system_prompt()
        assert "Factor(ABC)" in prompt
        assert "compute" in prompt
        assert "warmup_period" in prompt

    def test_contains_rules(self):
        prompt = _build_system_prompt()
        assert "strategies/" in prompt
        assert "contract test" in prompt

    def test_editor_code_injection(self):
        prompt = _build_system_prompt(editor_code="class MyStrat: pass")
        assert "Current Editor Code" in prompt
        assert "class MyStrat: pass" in prompt

    def test_no_editor_code(self):
        prompt = _build_system_prompt()
        assert "Current Editor Code" not in prompt


class TestChatSync:
    def test_simple_response(self):
        """LLM returns content without tool calls."""
        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.chat.return_value = LLMResponse(
            content="Hello!", tool_calls=[], finish_reason="stop"
        )

        messages = [LLMMessage(role="user", content="hi")]
        result = chat_sync(mock_provider, messages)
        assert result.content == "Hello!"
        mock_provider.chat.assert_called_once()

    def test_tool_call_then_response(self):
        """LLM calls a tool, then returns content."""
        mock_provider = MagicMock(spec=LLMProvider)
        # First call: tool call
        mock_provider.chat.side_effect = [
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="list_strategies", arguments={})],
                finish_reason="tool_calls",
            ),
            # Second call: content response
            LLMResponse(
                content="Here are the strategies!",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]

        messages = [LLMMessage(role="user", content="list strategies")]
        result = chat_sync(mock_provider, messages)
        assert result.content == "Here are the strategies!"
        assert mock_provider.chat.call_count == 2

    def test_max_rounds_limit(self):
        """Prevent infinite tool call loops."""
        mock_provider = MagicMock(spec=LLMProvider)
        # Always return tool calls (infinite loop scenario)
        mock_provider.chat.return_value = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="c1", name="list_strategies", arguments={})],
            finish_reason="tool_calls",
        )

        messages = [LLMMessage(role="user", content="loop")]
        result = chat_sync(mock_provider, messages)
        assert mock_provider.chat.call_count == MAX_TOOL_ROUNDS


class TestChatStream:
    def test_simple_content_stream(self):
        """Streaming content without tool calls yields content + done events."""
        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.stream_chat.return_value = iter([
            LLMEvent(type="content", content="Hello"),
            LLMEvent(type="content", content=" world"),
            LLMEvent(type="done"),
        ])

        messages = [LLMMessage(role="user", content="hi")]
        events = list(chat_stream(mock_provider, messages))
        content_events = [e for e in events if e["event"] == "content"]
        assert len(content_events) == 2
        assert content_events[0]["data"]["text"] == "Hello"
        assert events[-1]["event"] == "done"

    def test_tool_call_stream(self):
        """Streaming with tool calls yields tool_start + tool_result events."""
        mock_provider = MagicMock(spec=LLMProvider)
        # First stream: tool call
        mock_provider.stream_chat.side_effect = [
            iter([
                LLMEvent(type="tool_call", tool_call=ToolCall(id="c1", name="list_strategies", arguments={})),
                LLMEvent(type="done"),
            ]),
            # Second stream: content response
            iter([
                LLMEvent(type="content", content="Done!"),
                LLMEvent(type="done"),
            ]),
        ]

        messages = [LLMMessage(role="user", content="list")]
        events = list(chat_stream(mock_provider, messages))
        event_types = [e["event"] for e in events]
        assert "tool_start" in event_types
        assert "tool_result" in event_types
        assert "content" in event_types
        assert events[-1]["event"] == "done"

    def test_error_event(self):
        """Error during streaming yields error event."""
        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.stream_chat.side_effect = Exception("LLM down")

        messages = [LLMMessage(role="user", content="hi")]
        events = list(chat_stream(mock_provider, messages))
        assert any(e["event"] == "error" for e in events)
