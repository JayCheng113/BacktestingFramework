"""V2.8 E4: Analyzer — LLM interprets batch results and suggests next direction."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ez.agent.hypothesis import ResearchGoal
from ez.llm.provider import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Output from the analyzer."""
    direction: str = ""
    suggestions: list[str] = field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0
    best_sharpe: float = 0.0
    key_failure_reasons: list[str] = field(default_factory=list)


def _build_summary(batch_result, hypothesis_texts: list[str]) -> str:
    """Build a concise summary of batch results for LLM consumption."""
    passed = batch_result.passed
    executed = batch_result.executed
    lines = [
        f"本轮测试了 {len(hypothesis_texts)} 个假设，执行了 {executed} 个回测。",
        f"通过 Gate 的策略: {len(passed)} 个",
    ]
    if passed:
        top3 = sorted(passed, key=lambda c: c.sharpe, reverse=True)[:3]
        lines.append("Top Sharpe: " + ", ".join(f"{c.sharpe:.2f}" for c in top3))

    # Detailed per-strategy metrics (helps LLM understand WHY strategies failed)
    all_candidates = getattr(batch_result, "candidates", [])
    if all_candidates:
        lines.append("")
        lines.append("## 每个策略的回测详情（供下轮改进参考）:")
        for c in all_candidates[:5]:
            report = getattr(c, "report", None)
            if report:
                try:
                    sharpe = float(getattr(report, "sharpe", 0) or 0)
                    ret = float(getattr(report, "total_return", 0) or 0)
                    dd = float(getattr(report, "max_drawdown", 0) or 0)
                    trades = int(getattr(report, "trade_count", 0) or 0)
                    name = getattr(c, "strategy_name", getattr(c, "name", "?"))
                    lines.append(f"- {name}: 夏普={sharpe:.2f} 收益={ret*100:.1f}% 回撤={dd*100:.1f}% 交易={trades}次")
                    # Diagnostic hints
                    if trades <= 2:
                        lines.append(f"  → 问题: 交易次数太少({trades}次), 信号条件太严格")
                    elif trades > 200:
                        lines.append(f"  → 问题: 交易太频繁({trades}次), 需要加趋势过滤或hold机制")
                    if sharpe < 0:
                        lines.append(f"  → 问题: 夏普为负, 信号方向可能反了或逻辑有误")
                    elif 0 < sharpe < 0.3:
                        lines.append(f"  → 建议: 夏普偏低, 尝试组合多因子或加趋势过滤")
                except (TypeError, ValueError):
                    continue  # skip if report attributes are mock/invalid

    # Sample failure reasons
    failure_reasons: list[str] = []
    for c in all_candidates:
        if not getattr(c, "gate_passed", True) and getattr(c, "report", None):
            for reason in (getattr(c.report, "gate_reasons", None) or []):
                if isinstance(reason, dict) and not reason.get("passed", True):
                    failure_reasons.append(reason.get("message", ""))
            if len(failure_reasons) >= 5:
                break
    if failure_reasons:
        lines.append("Gate 失败原因: " + "; ".join(failure_reasons[:5]))
    if hypothesis_texts:
        lines.append("本轮假设: " + "; ".join(h[:60] for h in hypothesis_texts[:5]))
    return "\n".join(lines)


_ANALYZER_SYSTEM = """你是量化研究分析师。根据本轮回测结果，分析策略表现并提出具体的改进方向。

## 分析重点
- 如果交易次数太少（<5次），说明信号条件太严格，建议放宽阈值或用连续信号
- 如果夏普为负，说明信号方向可能反了，或者策略逻辑有根本问题
- 如果夏普在0~0.5之间，策略方向对但不够强，建议加趋势过滤或多因子组合
- 如果交易太频繁（>100次），建议加hold机制或均线过滤减少换手

## 高效改进策略
1. 二值信号(0/1) → 改为连续信号(0~1): 如 RSI 映射 signal = clip((70-RSI)/40, 0, 1)
2. 单因子 → 多因子组合: 动量+RSI+趋势 加权
3. 无过滤 → 加趋势过滤: close > MA(60) 时才允许买入
4. 固定仓位 → 波动率自适应: ATR 大时降低仓位

输出 JSON 格式:
{"direction": "下轮研究方向建议（一句话，要具体）", "suggestions": ["具体代码级别的改进建议1", "具体代码级别的改进建议2"]}
"""


async def analyze_results(
    provider: LLMProvider,
    batch_result,
    goal: ResearchGoal,
    hypothesis_texts: list[str],
) -> AnalysisResult:
    """Analyze batch results and suggest next iteration direction."""
    passed_count = len(batch_result.passed)
    failed_count = batch_result.executed - passed_count
    best_sharpe = max((c.sharpe for c in batch_result.passed), default=0.0)

    summary = _build_summary(batch_result, hypothesis_texts)
    messages = [
        LLMMessage(role="system", content=_ANALYZER_SYSTEM),
        LLMMessage(role="user", content=f"研究目标: {goal.description}\n\n{summary}\n\n请分析并给出下轮方向。"),
    ]

    direction = "继续探索不同策略类型"
    suggestions: list[str] = []
    try:
        response = await provider.achat(messages)
        text = response.content.strip()
        if "{" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            data = json.loads(text[start:end])
            direction = data.get("direction", direction)
            suggestions = data.get("suggestions", [])
    except Exception as e:
        logger.warning("Analysis LLM call failed: %s", e)
        direction = f"上轮{passed_count}个通过，{failed_count}个失败。建议调整参数范围。"

    return AnalysisResult(
        direction=direction,
        suggestions=suggestions,
        passed_count=passed_count,
        failed_count=failed_count,
        best_sharpe=best_sharpe,
    )
