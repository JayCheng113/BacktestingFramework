"""Tests for LLM provider types, factory, and async methods."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ez.llm.provider import LLMEvent, LLMMessage, LLMProvider, LLMResponse, ToolCall
from ez.llm.openai_compat import (
    OpenAICompatProvider, _msg_to_dict, _parse_tool_calls,
    _parse_response, _flush_pending_tools, _accumulate_tool_chunk,
)
from ez.llm.factory import create_provider, reset_provider_cache
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


class TestHelperFunctions:
    """V2.7.1: Test extracted helper functions."""

    def test_parse_response(self):
        data = {
            "choices": [{"message": {"content": "hi", "tool_calls": []}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10},
        }
        resp = _parse_response(data)
        assert resp.content == "hi"
        assert resp.finish_reason == "stop"
        assert resp.usage == {"total_tokens": 10}

    def test_parse_response_with_tool_calls(self):
        data = {
            "choices": [{"message": {
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "fn", "arguments": '{"a":1}'}}],
            }, "finish_reason": "tool_calls"}],
        }
        resp = _parse_response(data)
        assert resp.finish_reason == "tool_calls"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "fn"

    def test_flush_pending_tools_empty(self):
        assert _flush_pending_tools({}) == []

    def test_flush_pending_tools_ordered(self):
        pending = {
            1: {"id": "c2", "name": "fn2", "arguments": '{"b":2}'},
            0: {"id": "c1", "name": "fn1", "arguments": '{"a":1}'},
        }
        events = _flush_pending_tools(pending)
        assert len(events) == 2
        assert events[0].tool_call.name == "fn1"
        assert events[1].tool_call.name == "fn2"

    def test_accumulate_tool_chunk(self):
        pending: dict = {}
        _accumulate_tool_chunk(pending, {"index": 0, "id": "c1", "function": {"name": "fn"}})
        _accumulate_tool_chunk(pending, {"index": 0, "function": {"arguments": '{"a":'}})
        _accumulate_tool_chunk(pending, {"index": 0, "function": {"arguments": '1}'}})
        assert pending[0]["id"] == "c1"
        assert pending[0]["name"] == "fn"
        assert pending[0]["arguments"] == '{"a":1}'


class TestFactory:
    def setup_method(self):
        reset_provider_cache()

    def test_create_default(self):
        config = LLMConfig(provider="deepseek", api_key="test-key")
        provider = create_provider(config)
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.provider_name == "deepseek"
        assert provider.has_api_key is True

    def test_create_local(self):
        config = LLMConfig(provider="local")
        provider = create_provider(config)
        assert provider.provider_name == "local"
        assert provider.has_api_key is False
        assert "localhost" in provider._base_url

    def test_create_with_custom_url(self):
        config = LLMConfig(provider="openai", base_url="http://myproxy.com/v1", model="gpt-4")
        provider = create_provider(config)
        assert provider._base_url == "http://myproxy.com/v1"
        assert provider.model_name == "gpt-4"

    def test_singleton_caching(self):
        """Same config returns same instance."""
        config = LLMConfig(provider="deepseek", api_key="key1")
        p1 = create_provider(config)
        p2 = create_provider(config)
        assert p1 is p2

    def test_cache_invalidation_on_config_change(self):
        """Different config returns new instance."""
        c1 = LLMConfig(provider="deepseek", api_key="key1")
        c2 = LLMConfig(provider="deepseek", api_key="key2")
        p1 = create_provider(c1)
        p2 = create_provider(c2)
        assert p1 is not p2

    def test_reset_provider_cache(self):
        config = LLMConfig(provider="deepseek", api_key="key1")
        p1 = create_provider(config)
        reset_provider_cache()
        p2 = create_provider(config)
        assert p1 is not p2


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

    def test_public_properties(self):
        """V2.7.1: Public properties for provider info."""
        p = OpenAICompatProvider(provider="deepseek", api_key="sk-123", model="deepseek-chat")
        assert p.provider_name == "deepseek"
        assert p.model_name == "deepseek-chat"
        assert p.has_api_key is True

    def test_public_properties_no_key(self):
        p = OpenAICompatProvider(provider="local")
        assert p.has_api_key is False

    def test_async_client_lazy_init(self):
        """Async client is created lazily on first use."""
        p = OpenAICompatProvider(provider="local")
        assert p._async_client is None
        client = p._get_async_client()
        assert client is not None
        assert not client.is_closed

    @pytest.mark.asyncio
    async def test_aclose(self):
        """aclose() shuts down the async client."""
        p = OpenAICompatProvider(provider="local")
        p._get_async_client()
        assert p._async_client is not None
        await p.aclose()
        assert p._async_client is None
