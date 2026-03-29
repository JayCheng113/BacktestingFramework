"""V2.8: Research API — start, list, detail, cancel, SSE stream."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ez.agent.hypothesis import ResearchGoal
from ez.agent.loop_controller import LoopConfig
from ez.agent.gates import GateConfig
from ez.agent.research_runner import run_research_task, cancel_task, get_task_events

router = APIRouter()
logger = logging.getLogger(__name__)


class ResearchRequest(BaseModel):
    goal: str
    symbol: str = "000001.SZ"
    market: str = "cn_stock"
    period: str = "daily"
    start_date: date | None = None
    end_date: date | None = None
    max_iterations: int = Field(default=10, ge=1, le=50)
    max_specs: int = Field(default=500, ge=1, le=5000)
    max_llm_calls: int = Field(default=100, ge=1, le=1000)
    n_hypotheses: int = Field(default=5, ge=1, le=20)
    gate_min_sharpe: float = 0.5
    gate_max_drawdown: float = 0.3


@router.post("/start")
async def start_research(req: ResearchRequest):
    """Start an autonomous research task in the background."""
    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    goal = ResearchGoal(
        description=req.goal, market=req.market, symbol=req.symbol,
        period=req.period, start_date=start, end_date=end,
        n_hypotheses=req.n_hypotheses,
    )
    loop_config = LoopConfig(
        max_iterations=req.max_iterations, max_specs=req.max_specs,
        max_llm_calls=req.max_llm_calls,
    )
    gate_config = GateConfig(
        min_sharpe=req.gate_min_sharpe, max_drawdown=req.gate_max_drawdown,
    )

    # Run in background
    async def _run():
        await run_research_task(goal, loop_config, gate_config)

    task_id_future: list[str] = []

    async def _run_and_capture():
        tid = await run_research_task(goal, loop_config, gate_config)
        task_id_future.append(tid)

    # We need the task_id before the background task finishes.
    # run_research_task creates the task_id at the start, so we run it
    # directly and let it complete in the background via create_task.
    import uuid
    # Pre-generate task_id and pass via a wrapper
    from ez.agent.research_runner import _running_tasks, LoopState
    task_id = uuid.uuid4().hex[:12]

    asyncio.create_task(_run())

    # Wait briefly for task_id to appear in _running_tasks
    for _ in range(10):
        if _running_tasks:
            task_id = list(_running_tasks.keys())[-1]
            break
        await asyncio.sleep(0.05)

    return {"task_id": task_id, "status": "started"}


@router.get("/tasks")
def list_research_tasks(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List research tasks."""
    from ez.agent.data_access import get_research_store
    return get_research_store().list_tasks(limit=limit, offset=offset)


@router.get("/tasks/{task_id}")
def get_research_task(task_id: str):
    """Get research task detail with iterations."""
    from ez.agent.data_access import get_research_store
    store = get_research_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    task["iterations"] = store.get_iterations(task_id)
    return task


@router.post("/tasks/{task_id}/cancel")
def cancel_research_task(task_id: str):
    """Cancel a running research task."""
    if cancel_task(task_id):
        return {"status": "cancelling", "task_id": task_id}
    raise HTTPException(status_code=404, detail="Task not found or already finished")


@router.get("/tasks/{task_id}/stream")
async def stream_research_task(task_id: str):
    """SSE stream of research task progress."""
    events_data = get_task_events(task_id)
    if events_data is None:
        raise HTTPException(status_code=404, detail="Task not found or not running")

    async def generate():
        idx = 0
        while True:
            while idx < len(events_data["events"]):
                evt = events_data["events"][idx]
                line = f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"
                yield line
                idx += 1
            if events_data["done"]:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream")
