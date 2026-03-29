"""V2.8 E6: Research Report — aggregate iterations into final report."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ez.agent.research_store import ResearchStore
from ez.llm.provider import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class ResearchReport:
    """Final output of a research task."""
    task_id: str = ""
    goal: str = ""
    config: dict = field(default_factory=dict)
    status: str = ""
    iterations: list[dict] = field(default_factory=list)
    best_strategies: list[dict] = field(default_factory=list)
    total_specs: int = 0
    total_passed: int = 0
    summary: str = ""
    duration_sec: float = 0.0
    stop_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "goal": self.goal, "config": self.config,
            "status": self.status, "iterations": self.iterations,
            "best_strategies": self.best_strategies,
            "total_specs": self.total_specs, "total_passed": self.total_passed,
            "summary": self.summary, "duration_sec": self.duration_sec,
            "stop_reason": self.stop_reason,
        }


async def build_report(
    provider: LLMProvider | None,
    store: ResearchStore,
    task_id: str,
    stop_reason: str,
) -> ResearchReport:
    """Build report from stored iterations."""
    task = store.get_task(task_id) or {}
    iterations = store.get_iterations(task_id)

    total_specs = sum(it.get("strategies_tried", 0) for it in iterations)
    total_passed = sum(it.get("strategies_passed", 0) for it in iterations)

    config = {}
    try:
        config = json.loads(task.get("config", "{}"))
    except (json.JSONDecodeError, TypeError):
        pass

    # Collect best strategies from iterations' spec_ids
    best_strategies: list[dict] = []
    all_spec_ids: list[str] = []
    for it in iterations:
        try:
            sids = json.loads(it.get("spec_ids", "[]"))
            all_spec_ids.extend(sids)
        except (json.JSONDecodeError, TypeError):
            pass

    report = ResearchReport(
        task_id=task_id,
        goal=task.get("goal", ""),
        config=config,
        status=task.get("status", "completed"),
        iterations=iterations,
        best_strategies=best_strategies,
        total_specs=total_specs,
        total_passed=total_passed,
        stop_reason=stop_reason,
    )

    if provider is not None:
        try:
            summary_prompt = (
                f"研究目标: {report.goal}\n"
                f"共执行 {total_specs} 个回测，{total_passed} 个通过。\n"
                f"停止原因: {stop_reason}\n请用2-3句话总结本次研究结果。"
            )
            response = await provider.achat([
                LLMMessage(role="system", content="你是量化研究报告撰写者，请简洁总结研究发现。"),
                LLMMessage(role="user", content=summary_prompt),
            ])
            report.summary = response.content.strip()
        except Exception as e:
            logger.warning("Report summary LLM failed: %s", e)

    return report
