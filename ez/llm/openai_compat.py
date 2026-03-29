"""OpenAI-compatible LLM provider.

Works with DeepSeek, Qwen (DashScope), Ollama, vLLM, OpenAI, and any
provider that implements the OpenAI chat/completions API.

Uses httpx for HTTP calls (already a FastAPI dependency).

V2.7.1: Added async methods (achat/astream_chat) with persistent
httpx.AsyncClient for connection pooling.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Iterator

import httpx

from ez.llm.provider import LLMEvent, LLMMessage, LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

# Default endpoints per provider
_DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
    "local": "http://localhost:11434/v1",  # Ollama
}

_DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "qwen": "qwen-plus",
    "openai": "gpt-4o-mini",
    "local": "qwen2.5-coder:7b",
}


def _msg_to_dict(msg: LLMMessage) -> dict:
    """Convert LLMMessage to OpenAI API format."""
    content = msg.content
    # Some providers (Ollama/Qwen) require content=null when there are
    # tool_calls and no text content. Safe for DeepSeek/OpenAI too.
    if msg.tool_calls and not content:
        content = None
    d: dict = {"role": msg.role, "content": content}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in msg.tool_calls
        ]
    if msg.role == "tool":
        d["tool_call_id"] = msg.tool_call_id
        d["name"] = msg.name
    return d


def _parse_tool_calls(raw: list[dict]) -> list[ToolCall]:
    """Parse tool_calls from the API response."""
    calls = []
    for tc in raw:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            args = {}
        calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))
    return calls


def _parse_response(data: dict) -> LLMResponse:
    """Parse a chat/completions JSON response into LLMResponse."""
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    tool_calls = _parse_tool_calls(msg.get("tool_calls", []))
    finish = choice.get("finish_reason", "stop")
    if tool_calls:
        finish = "tool_calls"
    return LLMResponse(
        content=msg.get("content", "") or "",
        tool_calls=tool_calls,
        finish_reason=finish,
        usage=data.get("usage", {}),
    )


def _flush_pending_tools(pending_tools: dict[int, dict]) -> list[LLMEvent]:
    """Convert accumulated tool call chunks into LLMEvents."""
    events = []
    for _idx in sorted(pending_tools):
        tc_data = pending_tools[_idx]
        try:
            args = json.loads(tc_data.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            args = {}
        events.append(LLMEvent(
            type="tool_call",
            tool_call=ToolCall(
                id=tc_data.get("id", ""),
                name=tc_data.get("name", ""),
                arguments=args,
            ),
        ))
    return events


def _accumulate_tool_chunk(pending_tools: dict[int, dict], tc_chunk: dict) -> None:
    """Accumulate a streaming tool_call chunk into pending_tools."""
    idx = tc_chunk.get("index", 0)
    if idx not in pending_tools:
        pending_tools[idx] = {"id": "", "name": "", "arguments": ""}
    if tc_chunk.get("id"):
        pending_tools[idx]["id"] = tc_chunk["id"]
    fn = tc_chunk.get("function", {})
    if fn.get("name"):
        pending_tools[idx]["name"] = fn["name"]
    if fn.get("arguments"):
        pending_tools[idx]["arguments"] += fn["arguments"]


class OpenAICompatProvider(LLMProvider):
    """OpenAI-compatible provider for DeepSeek, Qwen, local models, etc."""

    def __init__(
        self,
        provider: str = "deepseek",
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        timeout: float = 60.0,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ):
        self._provider = provider
        self._api_key = api_key
        self._model = model or _DEFAULT_MODELS.get(provider, "deepseek-chat")
        self._base_url = (base_url or _DEFAULT_BASE_URLS.get(provider, "")).rstrip("/")
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Persistent async client (lazy-init on first async call)
        self._async_client: httpx.AsyncClient | None = None

    # -- Public properties (V2.7.1) --

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    # -- Internal helpers --

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _build_body(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None,
        stream: bool = False,
    ) -> dict:
        body: dict = {
            "model": self._model,
            "messages": [_msg_to_dict(m) for m in messages],
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return body

    def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create the persistent async client."""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers(),
            )
        return self._async_client

    # -- Sync methods (kept for backward compat / tests) --

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        url = f"{self._base_url}/chat/completions"
        body = self._build_body(messages, tools, stream=False)

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(url, json=body, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        return _parse_response(data)

    def stream_chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
    ) -> Iterator[LLMEvent]:
        url = f"{self._base_url}/chat/completions"
        body = self._build_body(messages, tools, stream=True)

        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("POST", url, json=body, headers=self._headers()) as resp:
                resp.raise_for_status()
                pending_tools: dict[int, dict] = {}

                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        yield from _flush_pending_tools(pending_tools)
                        yield LLMEvent(type="done")
                        return

                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if delta.get("content"):
                        yield LLMEvent(type="content", content=delta["content"])
                    for tc_chunk in delta.get("tool_calls", []):
                        _accumulate_tool_chunk(pending_tools, tc_chunk)

                # Stream ended without [DONE]
                yield from _flush_pending_tools(pending_tools)
                yield LLMEvent(type="done")

    # -- Async methods (V2.7.1) --

    async def achat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        url = f"{self._base_url}/chat/completions"
        body = self._build_body(messages, tools, stream=False)
        client = self._get_async_client()
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        return _parse_response(data)

    async def astream_chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        url = f"{self._base_url}/chat/completions"
        body = self._build_body(messages, tools, stream=True)
        client = self._get_async_client()
        async with client.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            pending_tools: dict[int, dict] = {}

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    for evt in _flush_pending_tools(pending_tools):
                        yield evt
                    yield LLMEvent(type="done")
                    return

                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    yield LLMEvent(type="content", content=delta["content"])
                for tc_chunk in delta.get("tool_calls", []):
                    _accumulate_tool_chunk(pending_tools, tc_chunk)

            # Stream ended without [DONE]
            for evt in _flush_pending_tools(pending_tools):
                yield evt
            yield LLMEvent(type="done")

    def close(self) -> None:
        """Sync close for httpx.AsyncClient (which only has async aclose()).

        Strategy: asyncio.run() works from threadpool threads (FastAPI sync endpoints).
        If already inside an event loop, schedule via create_task.
        """
        if self._async_client and not self._async_client.is_closed:
            import asyncio
            client = self._async_client
            self._async_client = None
            try:
                loop = asyncio.get_running_loop()
                # Inside event loop thread — schedule as background task
                loop.create_task(client.aclose())
            except RuntimeError:
                # No running loop (threadpool thread or plain sync) — safe to run
                asyncio.run(client.aclose())

    async def aclose(self) -> None:
        """Close the persistent async client."""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
            self._async_client = None
