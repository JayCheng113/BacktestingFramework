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
        "你是 ez-trading 量化交易平台的 AI 代码助手。",
        "你的主要职责是帮助用户编写和修改策略代码。",
        "",
        "## 策略接口",
        "```python",
        "from ez.strategy import Strategy",
        "from ez.factor import Factor",
        "from ez.factor.builtin.technical import MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR",
        "",
        "class MyStrategy(Strategy):",
        "    @classmethod",
        "    def get_parameters_schema(cls) -> dict[str, dict]: ...",
        "    def required_factors(self) -> list[Factor]: ...",
        "    def generate_signals(self, data: pd.DataFrame) -> pd.Series:",
        "        # 返回 0.0(空仓) 到 1.0(满仓) 的信号序列",
        "```",
        "",
        "## 因子列名",
        "MA(20)→ma_20, EMA(12)→ema_12, RSI(14)→rsi_14, MACD()→macd/macd_signal,",
        "BOLL(20)→boll_upper/boll_lower, Momentum(20)→momentum_20,",
        "VWAP(20)→vwap_20, OBV()→obv, ATR(14)→atr_14",
        "",
        "## 工作方式（重要）",
        "- 你的核心任务是**写代码**，代码会直接显示到用户的编辑器中",
        "- 用 create_strategy 或 update_strategy 工具保存代码，系统会自动跑 contract test",
        "- **不要主动跑回测或实验**，除非用户明确要求",
        "- 如果 contract test 失败，读取错误信息，修改代码后重试（最多3次）",
        "- 先用 read_source 读参考策略（如 ez/strategy/builtin/ma_cross.py）学习正确格式",
        "",
        "## 回复风格",
        "- 用中文简洁回复",
        "- 写代码前简要说明思路（1-2句）",
        "- 代码写完后说明关键逻辑",
        "- 不要啰嗦，不要重复代码内容",
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
