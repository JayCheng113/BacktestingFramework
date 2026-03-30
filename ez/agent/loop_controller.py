"""V2.8: Research loop controller — budget, convergence, stop conditions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopConfig:
    """Budget and convergence settings for a research loop."""
    max_iterations: int = 10
    max_specs: int = 500
    max_llm_calls: int = 100  # approximate — chat_sync internal rounds counted as ~2 per hypothesis
    no_improve_limit: int = 3


@dataclass
class LoopState:
    """Mutable state tracked across iterations."""
    iteration: int = 0
    specs_executed: int = 0
    llm_calls: int = 0
    best_sharpe: float = float("-inf")
    gate_passed_total: int = 0
    no_improve_streak: int = 0
    cancelled: bool = False


class LoopController:
    """Decides whether the research loop should continue."""

    def __init__(self, config: LoopConfig):
        self._config = config

    def should_continue(self, state: LoopState) -> tuple[bool, str]:
        if state.cancelled:
            return False, "用户取消"
        if state.iteration >= self._config.max_iterations:
            return False, f"达到最大轮次 ({self._config.max_iterations})"
        if state.specs_executed >= self._config.max_specs:
            return False, f"达到回测预算上限 ({self._config.max_specs})"
        if state.llm_calls >= self._config.max_llm_calls:
            return False, f"达到LLM调用上限 ({self._config.max_llm_calls})"
        if (state.no_improve_streak >= self._config.no_improve_limit
                and state.iteration > 0):
            return False, f"连续{self._config.no_improve_limit}轮无新通过策略"
        return True, ""

    def update(
        self,
        state: LoopState,
        batch_result,
        llm_calls_this_round: int,
    ) -> LoopState:
        new = LoopState(
            iteration=state.iteration + 1,
            specs_executed=state.specs_executed + batch_result.executed,
            llm_calls=state.llm_calls + llm_calls_this_round,
            best_sharpe=state.best_sharpe,
            gate_passed_total=state.gate_passed_total,
            no_improve_streak=state.no_improve_streak,
            cancelled=state.cancelled,
        )
        passed = batch_result.passed
        new.gate_passed_total += len(passed)
        if passed:
            top_sharpe = max(c.sharpe for c in passed)
            if top_sharpe > new.best_sharpe:
                new.best_sharpe = top_sharpe
            new.no_improve_streak = 0
        else:
            new.no_improve_streak += 1
        return new
