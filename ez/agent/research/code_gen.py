"""V2.8 E2: Code Generator — LLM writes strategy code with sandbox validation."""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path

from ez.agent.assistant import chat_sync
from ez.agent.sandbox import list_user_strategies
from ez.llm.provider import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)

_CODE_GEN_SYSTEM = """你是 ez-trading 量化交易平台的策略代码生成器。
你的唯一任务是：根据给定的策略假设，使用 create_strategy 工具创建一个 Python 策略文件。

## 策略接口
```python
from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR
import pandas as pd
import numpy as np

class MyStrategy(Strategy):
    def __init__(self, period: int = 20):
        self.period = period

    @classmethod
    def get_description(cls) -> str:
        return "策略描述"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {"period": {"type": "int", "default": 20, "min": 5, "max": 120, "label": "周期"}}

    def required_factors(self) -> list[Factor]:
        return [MA(period=self.period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        # 返回 0.0~1.0 的连续信号，不要只返回 0/1 二值
        return (data["adj_close"] > data["ma_20"]).astype(float)
```

## 高表现策略代码示例（参考这些模式）
```python
# 模式1: RSI连续信号 + 趋势过滤（比二值RSI<30/RSI>70好很多）
def generate_signals(self, data):
    rsi = data["rsi_14"]
    trend = data["adj_close"] > data["ma_60"]  # 趋势过滤
    raw_signal = ((70 - rsi) / 40).clip(0, 1)   # 连续信号
    return raw_signal * trend.astype(float)       # 下跌趋势时清仓

# 模式2: 多因子组合（比单因子更稳定）
def generate_signals(self, data):
    mom = (data["adj_close"].pct_change(20) > 0).astype(float)
    rsi_ok = (data["rsi_14"] < 65).astype(float)
    trend = (data["adj_close"] > data["ma_60"]).astype(float)
    return (mom * 0.4 + rsi_ok * 0.3 + trend * 0.3)  # 加权组合

# 模式3: 布林带均值回归 + ATR 仓位管理
def generate_signals(self, data):
    mid = data["boll_mid_20"]
    lower = data["boll_lower_20"]
    upper = data["boll_upper_20"]
    band_width = upper - lower
    position = (mid - data["adj_close"]) / band_width.clip(lower=0.001)
    return position.clip(0, 1)  # 越接近下轨仓位越重
```

## 因子列名
MA(20)→ma_20, EMA(12)→ema_12, RSI(14)→rsi_14
MACD()→macd_line/macd_signal/macd_hist
BOLL(20)→boll_mid_20/boll_upper_20/boll_lower_20
Momentum(20)→momentum_20, VWAP(20)→vwap_20, OBV()→obv, ATR(14)→atr_14

## 常见错误（你必须避免）
- ❌ 只返回 0 和 1 的二值信号 → ✅ 返回 0.0~1.0 的连续信号
- ❌ 信号全 0（没有任何买入机会）→ ✅ 确保在合理市场条件下能触发买入
- ❌ 信号全 1（永远满仓）→ ✅ 必须有减仓/清仓条件
- ❌ 用 data["close"] → ✅ 用 data["adj_close"]（复权价）
- ❌ 忘记 import numpy as np → ✅ 需要 clip/where 等函数时必须导入

## 规则
- 文件名必须以 research_ 开头，蛇形命名且唯一 (如 research_rsi_reversal.py)
- 类名以 Research 开头，驼峰命名 (如 ResearchRsiReversal)
- 信号返回 0.0 (空仓) 到 1.0 (满仓) 的 pd.Series
- 必须使用 create_strategy 工具保存代码
- 不要跑回测或实验，只创建策略文件
"""


def _extract_strategy_class_name(code: str) -> str | None:
    """Extract the Strategy subclass name from Python code via AST."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "Strategy":
                    return node.name
    return None


def _find_latest_strategy(before_set: set[str]) -> tuple[str | None, str | None]:
    """Find a newly created strategy by comparing with a snapshot of filenames."""
    current = {s["filename"] for s in list_user_strategies()}
    new_files = current - before_set
    if not new_files:
        return None, None
    filename = sorted(new_files)[0]
    from ez.config import get_project_root
    strategies_dir = get_project_root() / "strategies"
    code = (strategies_dir / filename).read_text(encoding="utf-8")
    class_name = _extract_strategy_class_name(code)
    return filename, class_name


async def generate_strategy_code(
    provider: LLMProvider,
    hypothesis: str,
    max_retries: int = 3,
) -> tuple[str | None, str | None, str | None]:
    """Generate a strategy from a hypothesis.

    Returns: (filename, class_name, error)
    """
    before = {s["filename"] for s in list_user_strategies()}
    messages = [
        LLMMessage(role="system", content=_CODE_GEN_SYSTEM),
        LLMMessage(role="user", content=f"请根据以下假设创建策略:\n{hypothesis}"),
    ]

    last_error = ""
    # Allowed tools are locked to what _find_latest_strategy() can observe (strategies/ dir).
    # Including create_portfolio_strategy / create_cross_factor here causes retry waste:
    # LLM may legitimately call them, file IS created, but _find_latest_strategy() misses it
    # because it only scans list_user_strategies() → "策略文件未创建" → retry → budget burn.
    _STRATEGY_ONLY_TOOLS = ["create_strategy", "read_source", "list_factors"]
    for attempt in range(max_retries):
        try:
            await asyncio.to_thread(
                chat_sync, provider, messages,
                allowed_tools=_STRATEGY_ONLY_TOOLS)
            filename, class_name = _find_latest_strategy(before)
            if filename and class_name:
                logger.info("Code gen success: %s (%s)", filename, class_name)
                return filename, class_name, None
            last_error = "策略文件未创建"
            messages.append(LLMMessage(role="user",
                content="策略文件未创建成功。请使用 create_strategy 工具重新尝试。"))
        except Exception as e:
            logger.warning("Code gen attempt %d failed: %s", attempt + 1, e)
            last_error = str(e)
            # Continue retrying on exception (P1-7)
            if attempt < max_retries - 1:
                messages.append(LLMMessage(role="user",
                    content=f"出错了: {e}。请重试。"))

    return None, None, f"经过{max_retries}次重试仍未成功: {last_error}"
