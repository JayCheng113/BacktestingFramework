"""V2.8: Research Runner — main orchestrator for autonomous research tasks."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

import pandas as pd

from ez.agent.analyzer import analyze_results
from ez.agent.code_gen import generate_strategy_code
from ez.agent.data_access import get_chain, get_experiment_store, get_research_store
from ez.agent.hypothesis import ResearchGoal, generate_hypotheses
from ez.agent.loop_controller import LoopConfig, LoopController, LoopState
from ez.agent.research_report import build_report
from ez.agent.run_spec import RunSpec
from ez.agent.batch_runner import run_batch, BatchConfig
from ez.agent.gates import GateConfig
from ez.llm.factory import create_provider

logger = logging.getLogger(__name__)

# In-memory event queues for SSE streaming
_running_tasks: dict[str, dict] = {}
# Serialization lock — ensures check-and-start is atomic
_start_lock = asyncio.Lock()


def _emit(task_id: str, event: str, data: dict) -> None:
    """Append an SSE event to the task's event queue."""
    if task_id in _running_tasks:
        _running_tasks[task_id]["events"].append({"event": event, "data": data})


def _fetch_data(goal: ResearchGoal) -> pd.DataFrame:
    """Fetch market data for the research task."""
    chain = get_chain()
    bars = chain.get_kline(goal.symbol, goal.market, goal.period, goal.start_date, goal.end_date)
    if not bars:
        raise ValueError(f"No data for {goal.symbol} ({goal.start_date} to {goal.end_date})")
    return pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")


def _run_batch_for_strategies(
    strategy_names: list[str],
    goal: ResearchGoal,
    data: pd.DataFrame,
    gate_config: GateConfig,
) -> tuple:
    """Create RunSpecs and run batch. Returns (batch_result, spec_ids)."""
    specs = [
        RunSpec(
            strategy_name=name, strategy_params={},
            symbol=goal.symbol, market=goal.market, period=goal.period,
            start_date=goal.start_date, end_date=goal.end_date,
        )
        for name in strategy_names
    ]
    if not specs:
        from types import SimpleNamespace
        return SimpleNamespace(passed=[], executed=0, candidates=[], ranked=[]), []
    spec_ids = [s.spec_id for s in specs]
    config = BatchConfig(gate_config=gate_config, skip_prefilter=True)
    store = get_experiment_store()
    return run_batch(specs, data, config=config, store=store), spec_ids


def register_task(task_id: str) -> None:
    """Pre-register task in memory BEFORE background work starts (prevents SSE 404)."""
    _running_tasks[task_id] = {"events": [], "done": False, "state": LoopState()}


async def run_research_task(
    goal: ResearchGoal,
    loop_config: LoopConfig = LoopConfig(),
    gate_config: GateConfig = GateConfig(),
    task_id: str = "",
) -> str:
    """Main orchestrator. task_id must be pre-registered via register_task()."""
    if not task_id:
        task_id = uuid.uuid4().hex[:12]
        register_task(task_id)

    # Everything inside try/finally so done=True is always set
    stop_reason = ""
    try:
        provider = create_provider()
        research_store = get_research_store()
        controller = LoopController(loop_config)
        state = LoopState()
        start_time = datetime.now()

        research_store.save_task({
            "task_id": task_id,
            "goal": goal.description,
            "config": json.dumps({
                "max_iterations": loop_config.max_iterations,
                "max_specs": loop_config.max_specs,
                "max_llm_calls": loop_config.max_llm_calls,
                "symbol": goal.symbol, "market": goal.market,
                "start_date": str(goal.start_date), "end_date": str(goal.end_date),
            }),
            "status": "running",
        })

        data = await asyncio.to_thread(_fetch_data, goal)
        previous_analysis = ""

        while True:
            # Check cancel
            if task_id in _running_tasks and _running_tasks[task_id].get("cancel"):
                state.cancelled = True
            ok, reason = controller.should_continue(state)
            if not ok:
                stop_reason = reason
                break

            _emit(task_id, "iteration_start", {
                "iteration": state.iteration, "max_iterations": loop_config.max_iterations})
            llm_calls = 0

            # E1: Hypotheses
            hypotheses = await generate_hypotheses(provider, goal, previous_analysis)
            llm_calls += 1
            for i, h in enumerate(hypotheses):
                _emit(task_id, "hypothesis", {"index": i, "total": len(hypotheses), "text": h})

            # E2: Code generation — count each hypothesis as 2 LLM calls (conservative)
            strategy_names: list[str] = []
            strategy_files: list[str] = []
            for i, hypothesis in enumerate(hypotheses):
                filename, class_name, error = await generate_strategy_code(provider, hypothesis)
                llm_calls += 2  # chat_sync does >=1 round + tool execution
                if class_name:
                    strategy_names.append(class_name)
                    if filename:
                        strategy_files.append(filename)
                    _emit(task_id, "code_success", {
                        "index": i, "filename": filename, "class_name": class_name})
                else:
                    _emit(task_id, "code_failed", {
                        "index": i, "hypothesis": hypothesis[:100], "error": error or "unknown"})

            # Budget check before batch (P1-5: prevent overshooting)
            state_preview = LoopState(
                iteration=state.iteration,
                specs_executed=state.specs_executed + len(strategy_names),
                llm_calls=state.llm_calls + llm_calls,
                cancelled=state.cancelled,
            )
            ok2, reason2 = controller.should_continue(state_preview)
            if not ok2 and not state.cancelled:
                # Budget would be exceeded — skip batch, exit
                stop_reason = reason2
                _emit(task_id, "iteration_end", {
                    "iteration": state.iteration, "cumulative_passed": state.gate_passed_total,
                    "cumulative_specs": state.specs_executed, "skipped": "budget"})
                break

            # E3: Batch execution
            _emit(task_id, "batch_start", {"total_specs": len(strategy_names)})
            batch_result, spec_ids = await asyncio.to_thread(
                _run_batch_for_strategies, strategy_names, goal, data, gate_config)
            best_sharpe = max((c.sharpe for c in batch_result.passed), default=0.0)
            _emit(task_id, "batch_complete", {
                "executed": batch_result.executed, "passed": len(batch_result.passed),
                "best_sharpe": round(best_sharpe, 4)})

            # E4: Analyze
            analysis = await analyze_results(provider, batch_result, goal, hypotheses)
            llm_calls += 1
            previous_analysis = analysis.direction
            _emit(task_id, "analysis", {
                "direction": analysis.direction, "passed": analysis.passed_count,
                "failed": analysis.failed_count})

            # E5: Update state
            state = controller.update(state, batch_result, llm_calls)
            _running_tasks[task_id]["state"] = state

            # Persist iteration
            research_store.save_iteration({
                "task_id": task_id,
                "iteration": state.iteration - 1,
                "hypotheses": json.dumps(hypotheses),
                "strategies_tried": len(strategy_names),
                "strategies_passed": len(batch_result.passed),
                "best_sharpe": best_sharpe,
                "analysis": json.dumps({"direction": analysis.direction, "suggestions": analysis.suggestions, "strategy_files": strategy_files}),
                "spec_ids": json.dumps(spec_ids),
            })
            _emit(task_id, "iteration_end", {
                "iteration": state.iteration,
                "cumulative_passed": state.gate_passed_total,
                "cumulative_specs": state.specs_executed})

        # Determine final status (P0-3: cancelled → "cancelled", not "completed")
        if state.cancelled:
            final_status = "cancelled"
        else:
            final_status = "completed"

        # E6: Report
        exp_store = get_experiment_store()
        report = await build_report(provider, research_store, task_id, stop_reason, exp_store=exp_store)
        report.duration_sec = (datetime.now() - start_time).total_seconds()
        research_store.update_task_status(
            task_id, final_status, stop_reason=stop_reason, summary=report.summary)
        _emit(task_id, "task_complete" if final_status == "completed" else "task_cancelled", {
            "total_passed": state.gate_passed_total,
            "best_sharpe": round(state.best_sharpe, 4) if state.best_sharpe > float("-inf") else 0,
            "stop_reason": stop_reason})

    except Exception as e:
        logger.error("Research task %s failed: %s", task_id, e)
        try:
            research_store = get_research_store()
            research_store.update_task_status(task_id, "failed", error=str(e))
        except Exception:
            pass  # store might not be initialized
        _emit(task_id, "task_failed", {"error": str(e)})

    finally:
        # P0-1: ALWAYS mark done, even if init failed
        if task_id in _running_tasks:
            _running_tasks[task_id]["done"] = True
        cleanup_finished_tasks()

    return task_id


def is_any_task_running() -> bool:
    """Check if any research task is currently running."""
    return any(not t["done"] for t in _running_tasks.values())


def cancel_task(task_id: str) -> bool:
    """Cancel a running task."""
    if task_id in _running_tasks and not _running_tasks[task_id]["done"]:
        _running_tasks[task_id]["cancel"] = True
        return True
    return False


def get_task_events(task_id: str) -> dict | None:
    """Get in-memory event data for SSE streaming."""
    return _running_tasks.get(task_id)


def cleanup_finished_tasks(keep: int = 5) -> None:
    """Remove old finished task events from memory (prevent leak)."""
    finished = [(tid, t) for tid, t in _running_tasks.items() if t["done"]]
    if len(finished) > keep:
        for tid, _ in finished[:-keep]:
            del _running_tasks[tid]
