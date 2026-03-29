"""LLM provider abstract base class and data types.

All providers implement chat() and stream_chat() with a unified
message/tool format. The wire protocol (OpenAI-compatible, Anthropic, etc.)
is handled inside each provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class ToolCall:
    """A tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict


@dataclass
class LLMMessage:
    """A message in the conversation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""  # for role="tool" responses
    name: str = ""  # tool name for role="tool"


@dataclass
class LLMResponse:
    """Complete (non-streaming) response from the LLM."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""  # "stop" | "tool_calls"
    usage: dict = field(default_factory=dict)


@dataclass
class LLMEvent:
    """A single event in a streaming response."""

    type: str  # "content" | "tool_call" | "done" | "error"
    content: str = ""
    tool_call: ToolCall | None = None
    error: str = ""


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Send messages and return a complete response."""
        ...

    @abstractmethod
    def stream_chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
    ) -> Iterator[LLMEvent]:
        """Send messages and yield streaming events."""
        ...
