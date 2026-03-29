"""Tests for LLM provider types and factory."""
from __future__ import annotations

import pytest

from ez.llm.provider import LLMEvent, LLMMessage, LLMProvider, LLMResponse, ToolCall
from ez.llm.openai_compat import OpenAICompatProvider, _msg_to_dict, _parse_tool_calls
from ez.llm.factory import create_provider
from ez.config import LLMConfig


class TestLLMTypes:
    def test_tool_call_creation(self):
        tc = ToolCall(id="call_1", name="test", arguments={"a": 1})
        assert tc.id == "call_1"
        assert tc.name == "test"
        assert tc.arguments == {"a": 1}

    def test_llm_message_defaults(self):
        msg = LLMMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.tool_calls == []
        assert msg.tool_call_id == ""

    def test_llm_response_defaults(self):
        resp = LLMResponse()
        assert resp.content == ""
        assert resp.tool_calls == []
        assert resp.finish_reason == ""

    def test_llm_event_types(self):
        e1 = LLMEvent(type="content", content="hello")
        assert e1.type == "content"
        e2 = LLMEvent(type="done")
        assert e2.type == "done"
        e3 = LLMEvent(type="error", error="fail")
        assert e3.error == "fail"


class TestMsgConversion:
    def test_user_message(self):
        msg = LLMMessage(role="user", content="hi")
        d = _msg_to_dict(msg)
        assert d == {"role": "user", "content": "hi"}

    def test_assistant_with_tool_calls(self):
        tc = ToolCall(id="c1", name="fn", arguments={"x": 1})
        msg = LLMMessage(role="assistant", content="", tool_calls=[tc])
        d = _msg_to_dict(msg)
        assert d["role"] == "assistant"
        assert d["content"] is None  # empty content + tool_calls → null
        assert len(d["tool_calls"]) == 1
        assert d["tool_calls"][0]["function"]["name"] == "fn"

    def test_assistant_with_content_and_tool_calls(self):
        tc = ToolCall(id="c1", name="fn", arguments={})
        msg = LLMMessage(role="assistant", content="Let me check", tool_calls=[tc])
        d = _msg_to_dict(msg)
        assert d["content"] == "Let me check"  # non-empty content preserved

    def test_tool_response(self):
        msg = LLMMessage(role="tool", content="result", tool_call_id="c1", name="fn")
        d = _msg_to_dict(msg)
        assert d["role"] == "tool"
        assert d["tool_call_id"] == "c1"
        assert d["name"] == "fn"


class TestParseToolCalls:
    def test_empty(self):
        assert _parse_tool_calls([]) == []

    def test_single_call(self):
        raw = [{"id": "c1", "function": {"name": "test", "arguments": '{"a": 1}'}}]
        calls = _parse_tool_calls(raw)
        assert len(calls) == 1
        assert calls[0].name == "test"
        assert calls[0].arguments == {"a": 1}

    def test_malformed_args(self):
        raw = [{"id": "c1", "function": {"name": "test", "arguments": "not json"}}]
        calls = _parse_tool_calls(raw)
        assert calls[0].arguments == {}


class TestFactory:
    def test_create_default(self):
        config = LLMConfig(provider="deepseek", api_key="test-key")
        provider = create_provider(config)
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._provider == "deepseek"
        assert provider._api_key == "test-key"

    def test_create_local(self):
        config = LLMConfig(provider="local")
        provider = create_provider(config)
        assert provider._provider == "local"
        assert "localhost" in provider._base_url

    def test_create_with_custom_url(self):
        config = LLMConfig(provider="openai", base_url="http://myproxy.com/v1", model="gpt-4")
        provider = create_provider(config)
        assert provider._base_url == "http://myproxy.com/v1"
        assert provider._model == "gpt-4"


class TestOpenAICompatProvider:
    def test_build_body_basic(self):
        p = OpenAICompatProvider(provider="deepseek", api_key="k")
        msgs = [LLMMessage(role="user", content="hi")]
        body = p._build_body(msgs, tools=None, stream=False)
        assert body["model"] == "deepseek-chat"
        assert len(body["messages"]) == 1
        assert body["stream"] is False
        assert "tools" not in body

    def test_build_body_with_tools(self):
        p = OpenAICompatProvider(provider="deepseek", api_key="k")
        msgs = [LLMMessage(role="user", content="hi")]
        tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
        body = p._build_body(msgs, tools=tools, stream=True)
        assert body["stream"] is True
        assert body["tools"] == tools
        assert body["tool_choice"] == "auto"

    def test_headers(self):
        p = OpenAICompatProvider(provider="deepseek", api_key="my-key")
        h = p._headers()
        assert h["Authorization"] == "Bearer my-key"
        assert h["Content-Type"] == "application/json"

    def test_headers_no_key(self):
        p = OpenAICompatProvider(provider="local")
        h = p._headers()
        assert "Authorization" not in h
