"""V2.8 E1: Hypothesis Generator — LLM generates strategy hypotheses from research goal."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from ez.llm.provider import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class ResearchGoal:
    """User's research objective."""
    description: str
    market: str = "cn_stock"
    symbol: str = "000001.SZ"
    period: str = "daily"
    start_date: date | None = None
    end_date: date | None = None
    n_hypotheses: int = 5

    def __post_init__(self):
        if self.end_date is None:
            self.end_date = date.today()
        if self.start_date is None:
            self.start_date = self.end_date - timedelta(days=365 * 3)


_SYSTEM_PROMPT = """你是一位资深量化研究员。你的任务是根据用户的研究目标，生成具体的、可回测的策略假设。

## 可用因子
MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR
因子列名: MA(20)→ma_20, EMA(12)→ema_12, RSI(14)→rsi_14, MACD()→macd_line/macd_signal/macd_hist, BOLL(20)→boll_mid_20/boll_upper_20/boll_lower_20, ATR(14)→atr_14

## 高表现策略模式（基于历史回测经验）
1. **多因子组合** > 单因子：如 RSI+MACD 组合信号比单独 RSI 更稳
2. **渐进仓位** > 二值信号：如 RSI(14) 映射到 0~1 连续信号 signal = (70-RSI)/40 clamp(0,1)，比简单 RSI<30=1 RSI>70=0 更平滑
3. **趋势过滤** 减少假信号：买入条件加 close > MA(60)，避免下跌趋势中抄底
4. **波动率自适应**：用 ATR(14) 动态调整阈值或仓位大小
5. **避免过度交易**：信号变化加 hold 天数或 hysteresis，减少频繁换手

## 常见失败原因（你的假设必须避免这些）
- 信号太稀疏（一年只交易 1-3 次）→ 放宽条件或用连续信号
- 信号太频繁（每天都换仓）→ 加 MA 趋势过滤或 hold 机制
- 参数过拟合（RSI 用 13 而不是 14 这类精调）→ 用常见默认参数
- 只有入场没有出场 → 必须有明确的减仓/清仓条件

## 每个假设必须包含
- 入场条件（买入信号，具体因子和阈值）
- 出场条件（卖出信号）
- 仓位逻辑（二值 0/1 还是连续 0~1）
- 预期适用的市场环境

## 输出格式
JSON array of strings，每个 string 是一个完整的策略假设。
示例: ["用 RSI(14) 做连续信号: signal = clip((70-RSI)/40, 0, 1)，close>MA(60)时才生效，趋势过滤型", "EMA(5)上穿EMA(20)时渐进加仓，ATR(14)放大时减仓，波动率自适应型"]
"""


def _parse_hypotheses(text: str) -> list[str]:
    """Parse LLM output into a list of hypothesis strings."""
    if not text.strip():
        return []
    cleaned = text.strip()
    # Remove markdown code block if present
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
    # Try JSON array
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [str(h).strip() for h in parsed if str(h).strip()]
    except json.JSONDecodeError:
        pass
    # Fallback: numbered/bullet list
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    hypotheses = []
    for line in lines:
        m = re.match(r"^(?:\d+[.)]\s*|-\s*)(.*)", line)
        if m:
            hypotheses.append(m.group(1).strip())
    return hypotheses


async def generate_hypotheses(
    provider: LLMProvider,
    goal: ResearchGoal,
    previous_analysis: str = "",
) -> list[str]:
    """Generate N strategy hypotheses from a research goal."""
    user_content = f"研究目标: {goal.description}\n市场: {goal.market}\n请生成 {goal.n_hypotheses} 个策略假设。"
    if previous_analysis:
        user_content += f"\n\n上一轮分析结果（请据此调整方向）:\n{previous_analysis}"

    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_content),
    ]
    try:
        response = await provider.achat(messages)
        return _parse_hypotheses(response.content)
    except Exception as e:
        logger.error("Hypothesis generation failed: %s", e)
        return []
