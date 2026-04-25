"""自主研究 agent：假设生成 → 代码编写 → 回测 → 分析 → 迭代优化。

本包使用 lazy re-export，避免导入 `ez.agent.data_access` 时被 `runner`
反向导入造成循环初始化。外部仍可通过 `from ez.agent.research import ...`
访问常用公开对象。
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ResearchGoal": "ez.agent.research.hypothesis",
    "generate_hypotheses": "ez.agent.research.hypothesis",
    "generate_strategy_code": "ez.agent.research.code_gen",
    "AnalysisResult": "ez.agent.research.analyzer",
    "analyze_results": "ez.agent.research.analyzer",
    "LoopConfig": "ez.agent.research.loop_controller",
    "LoopController": "ez.agent.research.loop_controller",
    "LoopState": "ez.agent.research.loop_controller",
    "ResearchReport": "ez.agent.research.report",
    "build_report": "ez.agent.research.report",
    "run_research_task": "ez.agent.research.runner",
    "ResearchStore": "ez.agent.research.store",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """按需加载公开 re-export。

    Args:
        name: 要访问的公开对象名。

    Returns:
        从真实子模块加载出来的对象；加载后会缓存到包模块全局变量中。

    Raises:
        AttributeError: 当 `name` 不是本包声明的公开对象时抛出。
    """
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
