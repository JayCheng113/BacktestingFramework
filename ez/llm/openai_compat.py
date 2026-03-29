"""OpenAI-compatible LLM provider.

Works with DeepSeek, Qwen (DashScope), Ollama, vLLM, OpenAI, and any
provider that implements the OpenAI chat/completions API.

Uses httpx for HTTP calls (already a FastAPI dependency).
"""
from __future__ import annotations

import json
import logging
from typing import Iterator

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
                # Accumulate tool calls across chunks
                pending_tools: dict[int, dict] = {}

                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        # Emit any pending tool calls
                        for _idx in sorted(pending_tools):
                            tc_data = pending_tools[_idx]
                            try:
                                args = json.loads(tc_data.get("arguments", "{}"))
                            except (json.JSONDecodeError, TypeError):
                                args = {}
                            yield LLMEvent(
                                type="tool_call",
                                tool_call=ToolCall(
                                    id=tc_data.get("id", ""),
                                    name=tc_data.get("name", ""),
                                    arguments=args,
                                ),
                            )
                        yield LLMEvent(type="done")
                        return

                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    delta = chunk.get("choices", [{}])[0].get("delta", {})

                    # Content tokens
                    if delta.get("content"):
                        yield LLMEvent(type="content", content=delta["content"])

                    # Tool call chunks (streamed incrementally)
                    for tc_chunk in delta.get("tool_calls", []):
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

                # If stream ended without [DONE], flush pending tool calls
                for _idx in sorted(pending_tools):
                    tc_data = pending_tools[_idx]
                    try:
                        args = json.loads(tc_data.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    yield LLMEvent(
                        type="tool_call",
                        tool_call=ToolCall(
                            id=tc_data.get("id", ""),
                            name=tc_data.get("name", ""),
                            arguments=args,
                        ),
                    )
                yield LLMEvent(type="done")
