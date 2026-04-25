"""V2.8: Research API — start, list, detail, cancel, SSE stream."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ez.agent.research.hypothesis import ResearchGoal
from ez.agent.research.loop_controller import LoopConfig
from ez.agent.gates import GateConfig
from ez.agent.research.runner import (
    run_research_task, cancel_task, get_task_events,
    is_any_task_running, register_task, get_start_lock,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ResearchRequest(BaseModel):
    goal: str = Field(min_length=1, description="研究目标描述，不能为空")
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
    if start >= end:
        raise HTTPException(422, f"start_date ({start}) must be before end_date ({end})")

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

    # P0-2: Atomic check-and-start via lock (prevents concurrent starts)
    async with get_start_lock():
        if is_any_task_running():
            raise HTTPException(status_code=409, detail="已有研究任务运行中，请等待完成或取消后重试")
        task_id = uuid.uuid4().hex[:12]
        # P1-9: Pre-register so SSE stream is immediately available
        register_task(task_id)

    asyncio.create_task(run_research_task(goal, loop_config, gate_config, task_id=task_id))
    return {"task_id": task_id, "status": "started"}


@router.get("/tasks")
def list_research_tasks(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    from ez.agent.data_access import get_research_store
    return get_research_store().list_tasks(limit=limit, offset=offset)


@router.get("/tasks/{task_id}")
def get_research_task(task_id: str):
    from ez.agent.data_access import get_research_store
    store = get_research_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    task["iterations"] = store.get_iterations(task_id)
    return task


@router.post("/tasks/{task_id}/cancel")
def cancel_research_task(task_id: str):
    if cancel_task(task_id):
        return {"status": "cancelling", "task_id": task_id}
    raise HTTPException(status_code=404, detail="Task not found or already finished")


@router.get("/tasks/{task_id}/stream")
async def stream_research_task(task_id: str):
    events_data = get_task_events(task_id)
    if events_data is None:
        raise HTTPException(status_code=404, detail="Task not found or not running")

    async def generate():
        idx = 0
        heartbeat_counter = 0
        while True:
            while idx < len(events_data["events"]):
                evt = events_data["events"][idx]
                line = f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"
                yield line
                idx += 1
                heartbeat_counter = 0  # reset after real event
            if events_data["done"]:
                break
            await asyncio.sleep(0.5)
            heartbeat_counter += 1
            # Send keepalive every 15s (30 × 0.5s) to prevent proxy/browser timeout
            if heartbeat_counter >= 30:
                yield ": keepalive\n\n"
                heartbeat_counter = 0

    return StreamingResponse(generate(), media_type="text/event-stream")
