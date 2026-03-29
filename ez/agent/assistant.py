"""V2.7: AI Assistant — agent loop with tool calling.

Flow: user message → build context → LLM → tool_calls? → execute → recurse
                                          → content → return

Max 10 tool-call rounds to prevent infinite loops.
"""
from __future__ import annotations

import logging
from typing import Iterator

from ez.llm.provider import LLMEvent, LLMMessage, LLMProvider, LLMResponse, ToolCall
from ez.agent.tools import execute_tool, get_all_tool_schemas

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10


def _build_system_prompt(editor_code: str = "") -> str:
    """Build system prompt with context about the trading platform."""
    parts = [
        "You are an AI assistant for the ez-trading quantitative trading platform.",
        "You help users create, debug, and optimize trading strategies.",
        "",
        "## Available Strategy Interface",
        "```python",
        "class Strategy(ABC):",
        "    @classmethod",
        "    def get_parameters_schema(cls) -> dict[str, dict]: ...",
        "    def required_factors(self) -> list[Factor]: ...",
        "    def generate_signals(self, data: pd.DataFrame) -> pd.Series: ...",
        "```",
        "",
        "## Available Factor Interface",
        "```python",
        "class Factor(ABC):",
        "    @property",
        "    def name(self) -> str: ...",
        "    @property",
        "    def warmup_period(self) -> int: ...",
        "    def compute(self, data: pd.DataFrame) -> pd.DataFrame: ...",
        "```",
        "",
        "## Built-in Factors",
        "MA (moving average), EMA, RSI, MACD, BOLL (Bollinger), Momentum, VWAP, OBV, ATR",
        "Import from: ez.factor.builtin.technical",
        "",
        "## Rules",
        "- Strategy files go in strategies/ directory",
        "- Signals must be pd.Series with values 0.0 (no position) to 1.0 (full position)",
        "- All strategies must pass contract test before being usable",
        "- Use create_strategy tool to save and auto-test",
        "- Use run_backtest to test a strategy's performance",
        "- After contract test failure, read the error, fix the code, retry (max 3 attempts)",
        "",
        "## Response Style",
        "- Be concise and action-oriented",
        "- Show what you're doing (reading files, creating code, running tests)",
        "- Explain results in simple Chinese when possible",
        "- If a strategy fails, explain why and offer to fix it",
    ]
    if editor_code:
        parts.extend([
            "",
            "## Current Editor Code",
            "The user is currently editing the following code:",
            "```python",
            editor_code,
            "```",
        ])
    return "\n".join(parts)


def chat_sync(
    provider: LLMProvider,
    messages: list[LLMMessage],
    editor_code: str = "",
) -> LLMResponse:
    """Synchronous chat with tool-calling loop.

    Returns the final LLMResponse after all tool calls are resolved.
    """
    system = LLMMessage(role="system", content=_build_system_prompt(editor_code))
    full_messages = [system] + messages
    tools = get_all_tool_schemas()

    for _round in range(MAX_TOOL_ROUNDS):
        response = provider.chat(full_messages, tools=tools)

        if not response.tool_calls:
            return response

        # Add assistant message with tool calls
        full_messages.append(
            LLMMessage(role="assistant", content=response.content, tool_calls=response.tool_calls)
        )

        # Execute each tool call
        for tc in response.tool_calls:
            logger.info("Tool call: %s(%s)", tc.name, tc.arguments)
            result = execute_tool(tc.name, tc.arguments)
            full_messages.append(
                LLMMessage(role="tool", content=result, tool_call_id=tc.id, name=tc.name)
            )

    # Exhausted rounds — return last response
    return response


def chat_stream(
    provider: LLMProvider,
    messages: list[LLMMessage],
    editor_code: str = "",
) -> Iterator[dict]:
    """Streaming chat with tool-calling loop.

    Yields SSE-formatted event dicts:
      {"event": "content", "data": {"text": "..."}}
      {"event": "tool_start", "data": {"name": "...", "args": {...}}}
      {"event": "tool_result", "data": {"name": "...", "result": "..."}}
      {"event": "done", "data": {}}
      {"event": "error", "data": {"message": "..."}}
    """
    system = LLMMessage(role="system", content=_build_system_prompt(editor_code))
    full_messages = [system] + messages
    tools = get_all_tool_schemas()

    for _round in range(MAX_TOOL_ROUNDS):
        # Collect streaming response
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        try:
            for event in provider.stream_chat(full_messages, tools=tools):
                if event.type == "content":
                    content_parts.append(event.content)
                    yield {"event": "content", "data": {"text": event.content}}
                elif event.type == "tool_call" and event.tool_call:
                    tool_calls.append(event.tool_call)
                elif event.type == "error":
                    yield {"event": "error", "data": {"message": event.error}}
                    return
        except Exception as e:
            yield {"event": "error", "data": {"message": str(e)}}
            return

        if not tool_calls:
            yield {"event": "done", "data": {}}
            return

        # Add assistant message
        full_content = "".join(content_parts)
        full_messages.append(
            LLMMessage(role="assistant", content=full_content, tool_calls=tool_calls)
        )

        # Execute tools
        for tc in tool_calls:
            yield {"event": "tool_start", "data": {"name": tc.name, "args": tc.arguments}}
            result = execute_tool(tc.name, tc.arguments)
            yield {"event": "tool_result", "data": {"name": tc.name, "result": result}}
            full_messages.append(
                LLMMessage(role="tool", content=result, tool_call_id=tc.id, name=tc.name)
            )

    yield {"event": "done", "data": {}}
