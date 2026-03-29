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


_SYSTEM_PROMPT = """你是一位资深量化研究员。你的任务是根据用户的研究目标，生成具体的策略假设。

可用因子: MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR
因子列名: MA(20)→ma_20, EMA(12)→ema_12, RSI(14)→rsi_14, MACD()→macd_line/macd_signal/macd_hist, BOLL(20)→boll_mid_20/boll_upper_20/boll_lower_20, ATR(14)→atr_14

每个假设必须包含:
- 明确的入场/出场条件
- 使用的因子和参数
- 策略的核心逻辑

输出格式: JSON array of strings, 每个 string 是一个完整的策略假设描述。
示例: ["RSI(14)<25时买入，RSI>75时卖出，适用于震荡市反转", "MA(10)上穿MA(30)时买入，下穿时卖出，趋势跟踪"]
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
