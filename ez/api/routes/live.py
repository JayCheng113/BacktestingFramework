"""V2.15 C1: Live API — 13 REST endpoints for paper-trading deployment lifecycle.

Endpoints:
- POST /deploy              — Create deployment from portfolio run
- GET  /deployments         — List all deployments
- GET  /deployments/{id}    — Deployment detail + latest snapshot
- POST /deployments/{id}/approve — Run DeployGate
- POST /deployments/{id}/start   — Start paper trading
- POST /deployments/{id}/stop    — Stop
- POST /deployments/{id}/pause   — Pause
- POST /deployments/{id}/resume  — Resume
- POST /tick                — Trigger daily tick
- GET  /dashboard           — Monitor dashboard
- GET  /deployments/{id}/snapshots — Historical snapshots
- GET  /deployments/{id}/trades    — Trade records
- GET  /deployments/{id}/stream    — SSE live stream
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ez.api.deps import get_chain, get_store
from ez.live.deploy_gate import DeployGate
from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.monitor import Monitor
from ez.live.scheduler import Scheduler

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Singletons (lazy init, same pattern as portfolio_store in portfolio.py)
# ---------------------------------------------------------------------------

_deployment_store: DeploymentStore | None = None
_scheduler: Scheduler | None = None
_monitor: Monitor | None = None


def _get_deployment_store() -> DeploymentStore:
    global _deployment_store
    if _deployment_store is None:
        import duckdb
        from ez.config import load_config
        db_path = load_config().database.path
        _deployment_store = DeploymentStore(duckdb.connect(db_path))
    return _deployment_store


def _get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler(store=_get_deployment_store(), data_chain=get_chain())
    return _scheduler


def _get_monitor() -> Monitor:
    global _monitor
    if _monitor is None:
        _monitor = Monitor(store=_get_deployment_store())
    return _monitor


def reset_live_singletons() -> None:
    """Reset singletons (called by deps.close_resources or tests).
    Closes the independent DeploymentStore connection if it exists."""
    global _deployment_store, _scheduler, _monitor
    if _deployment_store is not None:
        try:
            _deployment_store._conn.close()
        except Exception:
            pass
    _deployment_store = None
    _scheduler = None
    _monitor = None


def get_scheduler() -> Scheduler:
    """Public accessor for app.py lifespan to call resume_all()."""
    return _get_scheduler()


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class DeployRequest(BaseModel):
    source_run_id: str
    name: str


class DeployResponse(BaseModel):
    deployment_id: str
    spec_id: str


class StopRequest(BaseModel):
    reason: str = "手动停止"


class TickRequest(BaseModel):
    business_date: date


# ---------------------------------------------------------------------------
# Helper: portfolio store access
# ---------------------------------------------------------------------------

def _get_portfolio_store():
    """Access the portfolio store singleton from the portfolio route module."""
    from ez.api.routes.portfolio import _get_store as get_pf_store
    return get_pf_store()


def _build_spec_from_run(run: dict) -> DeploymentSpec:
    """Build a DeploymentSpec from a portfolio run dict."""
    # Parse JSON string fields if needed
    config = run.get("config") or {}
    if isinstance(config, str):
        config = json.loads(config)

    params = run.get("strategy_params") or {}
    if isinstance(params, str):
        params = json.loads(params)

    symbols = run.get("symbols") or []
    if isinstance(symbols, str):
        symbols = json.loads(symbols)

    market = config.get("market", "cn_stock")
    freq = config.get("freq", "daily")

    return DeploymentSpec(
        strategy_name=run.get("strategy_name", ""),
        strategy_params=params,
        symbols=tuple(symbols),
        market=market,
        freq=freq,
        t_plus_1=config.get("t_plus_1", True),
        price_limit_pct=config.get("price_limit_pct", 0.1),
        lot_size=config.get("lot_size", 100),
        buy_commission_rate=config.get("buy_commission_rate", 0.0003),
        sell_commission_rate=config.get("sell_commission_rate", 0.0003),
        stamp_tax_rate=config.get("stamp_tax_rate", 0.0005),
        slippage_rate=config.get("slippage_rate", 0.001),
        min_commission=config.get("min_commission", 5.0),
        optimizer=config.get("optimizer", ""),
        optimizer_params=config.get("optimizer_params"),
        risk_control=config.get("risk_control", False),
        risk_params=config.get("risk_params"),
        initial_cash=float(run.get("initial_cash", 1_000_000.0)),
    )


def _record_to_dict(record: DeploymentRecord) -> dict:
    """Serialize a DeploymentRecord to a JSON-safe dict."""
    return {
        "deployment_id": record.deployment_id,
        "spec_id": record.spec_id,
        "name": record.name,
        "status": record.status,
        "stop_reason": record.stop_reason,
        "source_run_id": record.source_run_id,
        "code_commit": record.code_commit,
        "gate_verdict": record.gate_verdict,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "approved_at": record.approved_at.isoformat() if record.approved_at else None,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "stopped_at": record.stopped_at.isoformat() if record.stopped_at else None,
    }


def _health_to_dict(h) -> dict:
    """Serialize a DeploymentHealth dataclass to a JSON-safe dict."""
    d = asdict(h)
    # date objects -> str
    if d.get("last_execution_date"):
        d["last_execution_date"] = str(d["last_execution_date"])
    return d


# ---------------------------------------------------------------------------
# 1. POST /deploy
# ---------------------------------------------------------------------------

@router.post("/deploy")
def create_deployment(req: DeployRequest):
    """Create DeploymentSpec + Record from a portfolio run."""
    store = _get_deployment_store()
    pf_store = _get_portfolio_store()

    # 1. Read source run
    run = pf_store.get_run(req.source_run_id)
    if not run:
        raise HTTPException(404, f"来源回测 {req.source_run_id} 不存在")

    # 2. Build spec from run config
    try:
        spec = _build_spec_from_run(run)
    except Exception as e:
        raise HTTPException(400, f"无法从回测记录构建部署配置: {e}")

    # 3. Create record (with code_commit for audit trail)
    # NOTE: code_commit is the git HEAD at deployment creation time, not the
    # commit that produced the source backtest run (portfolio_runs has no commit
    # field). This is a best-effort audit trail — for strict reproducibility,
    # portfolio_runs would need a code_commit column (V3.0 scope).
    def _get_git_sha() -> str:
        try:
            import subprocess
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True, timeout=5
            ).strip()[:12]
        except Exception:
            return ""

    record = DeploymentRecord(
        spec_id=spec.spec_id,
        name=req.name,
        source_run_id=req.source_run_id,
        code_commit=_get_git_sha(),
    )
    # 4. Save both
    store.save_spec(spec)
    store.save_record(record)

    return DeployResponse(deployment_id=record.deployment_id, spec_id=spec.spec_id)


# ---------------------------------------------------------------------------
# 2. GET /deployments
# ---------------------------------------------------------------------------

@router.get("/deployments")
def list_deployments(status: str | None = Query(None)):
    """List all deployments, optionally filtered by status."""
    store = _get_deployment_store()
    records = store.list_deployments(status=status)
    return [_record_to_dict(r) for r in records]


# ---------------------------------------------------------------------------
# 3. GET /deployments/{id}
# ---------------------------------------------------------------------------

@router.get("/deployments/{deployment_id}")
def get_deployment(deployment_id: str):
    """Deployment detail + latest snapshot."""
    store = _get_deployment_store()
    record = store.get_record(deployment_id)
    if not record:
        raise HTTPException(404, f"部署 {deployment_id} 不存在")

    result = _record_to_dict(record)
    result["latest_snapshot"] = store.get_latest_snapshot(deployment_id)

    # Load spec for additional context
    spec = store.get_spec(record.spec_id)
    if spec:
        result["spec"] = {
            "strategy_name": spec.strategy_name,
            "symbols": list(spec.symbols),
            "market": spec.market,
            "freq": spec.freq,
            "initial_cash": spec.initial_cash,
        }
    return result


# ---------------------------------------------------------------------------
# 4. POST /deployments/{id}/approve
# ---------------------------------------------------------------------------

@router.post("/deployments/{deployment_id}/approve")
def approve_deployment(deployment_id: str):
    """Run DeployGate on a pending deployment."""
    store = _get_deployment_store()
    record = store.get_record(deployment_id)
    if not record:
        raise HTTPException(404, f"部署 {deployment_id} 不存在")
    if record.status != "pending":
        raise HTTPException(400, f"部署状态为 {record.status!r}，只有 pending 状态可以审批")

    spec = store.get_spec(record.spec_id)
    if not spec:
        raise HTTPException(500, f"部署配置 {record.spec_id} 未找到")

    # Run gate — V2.16 S1: WF metrics read from DB, not from client
    gate = DeployGate()
    pf_store = _get_portfolio_store()
    verdict = gate.evaluate(
        spec=spec,
        source_run_id=record.source_run_id or "",
        portfolio_store=pf_store,
    )

    # Serialize verdict
    verdict_data = {
        "passed": verdict.passed,
        "summary": verdict.summary,
        "reasons": [
            {
                "rule": r.rule,
                "passed": r.passed,
                "value": r.value,
                "threshold": r.threshold,
                "message": r.message,
            }
            for r in verdict.reasons
        ],
    }
    verdict_json = json.dumps(verdict_data, ensure_ascii=False)

    if verdict.passed:
        store.update_status(deployment_id, "approved")
        store.update_gate_verdict(deployment_id, verdict_json)
        return {
            "deployment_id": deployment_id,
            "status": "approved",
            "verdict": verdict_data,
        }
    else:
        # Save verdict but keep status as pending — retryable
        store.update_gate_verdict(deployment_id, verdict_json)
        raise HTTPException(400, detail={
            "message": "部署门禁未通过",
            "verdict": json.loads(verdict_json),
        })


# ---------------------------------------------------------------------------
# 5-8. POST /start, /stop, /pause, /resume
# ---------------------------------------------------------------------------

@router.post("/deployments/{deployment_id}/start")
async def start_deployment(deployment_id: str):
    """Start paper trading for an approved deployment."""
    scheduler = _get_scheduler()
    try:
        await scheduler.start_deployment(deployment_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deployment_id": deployment_id, "status": "running"}


@router.post("/deployments/{deployment_id}/stop")
async def stop_deployment(deployment_id: str, req: StopRequest | None = None):
    """Stop a running deployment."""
    reason = req.reason if req else "手动停止"
    scheduler = _get_scheduler()
    try:
        await scheduler.stop_deployment(deployment_id, reason=reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deployment_id": deployment_id, "status": "stopped"}


@router.post("/deployments/{deployment_id}/pause")
async def pause_deployment(deployment_id: str):
    """Pause a running deployment (engine stays in memory, tick skips)."""
    scheduler = _get_scheduler()
    try:
        await scheduler.pause_deployment(deployment_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deployment_id": deployment_id, "status": "paused"}


@router.post("/deployments/{deployment_id}/resume")
async def resume_deployment(deployment_id: str):
    """Resume a paused deployment."""
    scheduler = _get_scheduler()
    try:
        await scheduler.resume_deployment(deployment_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deployment_id": deployment_id, "status": "running"}


# ---------------------------------------------------------------------------
# 9. POST /tick
# ---------------------------------------------------------------------------

@router.post("/tick")
async def trigger_tick(req: TickRequest):
    """Trigger daily tick execution for all active deployments."""
    scheduler = _get_scheduler()
    results = await scheduler.tick(req.business_date)
    return {"business_date": str(req.business_date), "results": results}


# ---------------------------------------------------------------------------
# 10. GET /dashboard
# ---------------------------------------------------------------------------

@router.get("/dashboard")
def get_dashboard():
    """Monitor dashboard — health summary for all active deployments."""
    monitor = _get_monitor()
    dashboard = monitor.get_dashboard()
    return {
        "deployments": [_health_to_dict(h) for h in dashboard],
        "alerts": monitor.check_alerts(),
    }


# ---------------------------------------------------------------------------
# 11. GET /deployments/{id}/snapshots
# ---------------------------------------------------------------------------

@router.get("/deployments/{deployment_id}/snapshots")
def get_snapshots(deployment_id: str):
    """Historical snapshots for a deployment."""
    store = _get_deployment_store()
    record = store.get_record(deployment_id)
    if not record:
        raise HTTPException(404, f"部署 {deployment_id} 不存在")
    snapshots = store.get_all_snapshots(deployment_id)
    # Ensure snapshot_date is serializable
    for snap in snapshots:
        if isinstance(snap.get("snapshot_date"), date):
            snap["snapshot_date"] = str(snap["snapshot_date"])
    return snapshots


# ---------------------------------------------------------------------------
# 12. GET /deployments/{id}/trades
# ---------------------------------------------------------------------------

@router.get("/deployments/{deployment_id}/trades")
def get_trades(deployment_id: str):
    """All trade records across snapshots for a deployment."""
    store = _get_deployment_store()
    record = store.get_record(deployment_id)
    if not record:
        raise HTTPException(404, f"部署 {deployment_id} 不存在")
    snapshots = store.get_all_snapshots(deployment_id)
    trades: list[dict] = []
    for snap in snapshots:
        snap_trades = snap.get("trades") or []
        snap_date = snap.get("snapshot_date")
        if isinstance(snap_date, date):
            snap_date = str(snap_date)
        for t in snap_trades:
            t["snapshot_date"] = snap_date
            trades.append(t)
    return trades


# ---------------------------------------------------------------------------
# 13. GET /deployments/{id}/stream — SSE live stream
# ---------------------------------------------------------------------------

@router.get("/deployments/{deployment_id}/stream")
async def stream_deployment(deployment_id: str):
    """SSE stream of snapshots for a deployment. Sends new snapshots as they appear."""
    store = _get_deployment_store()
    record = store.get_record(deployment_id)
    if not record:
        raise HTTPException(404, f"部署 {deployment_id} 不存在")

    async def generate():
        seen_count = 0
        heartbeat_counter = 0
        while True:
            snapshots = store.get_all_snapshots(deployment_id)
            while seen_count < len(snapshots):
                snap = snapshots[seen_count]
                # Serialize date
                if isinstance(snap.get("snapshot_date"), date):
                    snap["snapshot_date"] = str(snap["snapshot_date"])
                line = f"event: snapshot\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
                yield line
                seen_count += 1
                heartbeat_counter = 0

            # Check if deployment is terminal
            rec = store.get_record(deployment_id)
            if rec and rec.status in ("stopped", "error"):
                yield f"event: done\ndata: {{\"status\": \"{rec.status}\"}}\n\n"
                break

            await asyncio.sleep(1.0)
            heartbeat_counter += 1
            # Keepalive every 15s
            if heartbeat_counter >= 15:
                yield ": keepalive\n\n"
                heartbeat_counter = 0

    return StreamingResponse(generate(), media_type="text/event-stream")
