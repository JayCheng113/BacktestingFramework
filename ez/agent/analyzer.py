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
    # Sample failure reasons
    failure_reasons: list[str] = []
    for c in getattr(batch_result, "candidates", []):
        if not getattr(c, "gate_passed", True) and getattr(c, "report", None):
            for reason in (getattr(c.report, "gate_reasons", None) or []):
                if isinstance(reason, dict) and not reason.get("passed", True):
                    failure_reasons.append(reason.get("message", ""))
            if len(failure_reasons) >= 5:
                break
    if failure_reasons:
        lines.append("主要失败原因: " + "; ".join(failure_reasons[:5]))
    if hypothesis_texts:
        lines.append("本轮假设: " + "; ".join(h[:60] for h in hypothesis_texts[:5]))
    return "\n".join(lines)


_ANALYZER_SYSTEM = """你是量化研究分析师。根据本轮回测结果，分析策略表现并提出下一轮研究方向。

输出 JSON 格式:
{"direction": "下轮研究方向建议（一句话）", "suggestions": ["具体建议1", "具体建议2"]}
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
