"""V2.7: AI Assistant — agent loop with tool calling.

Flow: user message → build context → LLM → tool_calls? → execute → recurse
                                          → content → return

Max 10 tool-call rounds to prevent infinite loops.

V2.7.1: Added achat_stream() async generator — does not block the event loop.
Tool execution runs in threadpool via asyncio.to_thread().
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Iterator

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
        "MA(20)→ma_20, EMA(12)→ema_12, RSI(14)→rsi_14,",
        "MACD()→macd_line/macd_signal/macd_hist（列名固定，无参数后缀）,",
        "BOLL(20)→boll_mid_20/boll_upper_20/boll_lower_20（列名含period后缀）,",
        "Momentum(20)→momentum_20, VWAP(20)→vwap_20, OBV()→obv, ATR(14)→atr_14",
        "",
        "## 工作方式（重要 — 严格遵守）",
        "- 你的核心任务是**写代码**，代码会直接显示到用户的编辑器中",
        "- 用 create_strategy / update_strategy / create_portfolio_strategy / create_cross_factor / create_ml_alpha 工具保存代码",
        "- 系统会自动跑 contract test，失败时读取错误信息修改后重试（最多3次）",
        "- 先用 read_source 读参考策略学习正确格式",
        "",
        "## 操作边界（必须严格遵守）",
        "- **只执行用户明确要求的操作**。不要额外猜测用户意图。",
        "- 用户说'创建策略' → 只创建，不要自动回测",
        "- 用户说'回测' → 只回测，不要自动创建文件",
        "- 用户问'参数是什么' → 只查询/回答，不要创建或运行任何东西",
        "- **绝对不要在用户没有明确要求时调用 run_backtest / run_experiment / run_portfolio_backtest**",
        "- **绝对不要在用户没有要求时调用 create_* / update_* 工具**",
        "- 如果不确定用户是否需要某个操作，先问用户再做",
        "",
        "## ML Alpha 创建指南",
        "使用 create_ml_alpha 工具。必须遵循以下模板结构：",
        "```python",
        "import pandas as pd",
        "from ez.portfolio.ml_alpha import MLAlpha",
        "from sklearn.linear_model import Ridge",
        "",
        "def _feature_fn(df: pd.DataFrame) -> pd.DataFrame:",
        "    return pd.DataFrame({'ret20': df['adj_close'].pct_change(20), 'vol20': df['adj_close'].pct_change(1).rolling(20).std()}).dropna()",
        "",
        "def _target_fn(df: pd.DataFrame) -> pd.Series:",
        "    return df['adj_close'].pct_change(5).shift(-5)",
        "",
        "class MyAlpha(MLAlpha):",
        "    def __init__(self):",
        "        super().__init__(name='my_alpha', model_factory=lambda: Ridge(alpha=1.0),",
        "            feature_fn=_feature_fn, target_fn=_target_fn,",
        "            train_window=120, retrain_freq=21, purge_days=5, embargo_days=2, feature_warmup_days=20)",
        "```",
        "- model_factory 必须返回白名单内的 estimator: Ridge, Lasso, LinearRegression, ElasticNet, DecisionTreeRegressor, RandomForestRegressor, GradientBoostingRegressor, LGBMRegressor, XGBRegressor",
        "- 必须设 random_state（如有）和 n_jobs=1",
        "- feature_warmup_days 必须 >= feature_fn 中最大的 rolling/pct_change 窗口",
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
    allowed_tools: list[str] | None = None,
) -> LLMResponse:
    """Synchronous chat with tool-calling loop.

    Args:
        allowed_tools: If set, only these tool names are exposed to the LLM.
            None = all tools (default, backward compatible).

    Returns the final LLMResponse after all tool calls are resolved.
    """
    system = LLMMessage(role="system", content=_build_system_prompt(editor_code))
    full_messages = [system] + messages
    all_tools = get_all_tool_schemas()
    if allowed_tools is not None:
        tools = [t for t in all_tools if t.get("function", {}).get("name") in allowed_tools]
    else:
        tools = all_tools

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
    """Streaming chat with tool-calling loop (sync version).

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


async def achat_stream(
    provider: LLMProvider,
    messages: list[LLMMessage],
    editor_code: str = "",
) -> AsyncIterator[dict]:
    """Async streaming chat with tool-calling loop (V2.7.1).

    Does NOT block the event loop. Uses provider.astream_chat() for async HTTP.
    Tool execution runs in threadpool via asyncio.to_thread (does not block event loop).
    Yields same SSE event dicts as chat_stream().
    """
    system = LLMMessage(role="system", content=_build_system_prompt(editor_code))
    full_messages = [system] + messages
    tools = get_all_tool_schemas()

    for _round in range(MAX_TOOL_ROUNDS):
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        try:
            async for event in provider.astream_chat(full_messages, tools=tools):
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

        full_content = "".join(content_parts)
        full_messages.append(
            LLMMessage(role="assistant", content=full_content, tool_calls=tool_calls)
        )

        for tc in tool_calls:
            yield {"event": "tool_start", "data": {"name": tc.name, "args": tc.arguments}}
            # Run tool in threadpool — tools can be heavy (backtest, subprocess, IO)
            result = await asyncio.to_thread(execute_tool, tc.name, tc.arguments)
            yield {"event": "tool_result", "data": {"name": tc.name, "result": result}}
            full_messages.append(
                LLMMessage(role="tool", content=result, tool_call_id=tc.id, name=tc.name)
            )

    yield {"event": "done", "data": {}}
