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
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ez.api.deps import get_chain, get_store
from ez.live.deploy_gate import DeployGate
from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.monitor import (
    Monitor,
    build_persisted_broker_order_view,
    resolve_qmt_runtime_projection,
)
from ez.live.qmt_broker import (
    build_qmt_real_submit_policy,
    build_qmt_readiness_summary,
    build_qmt_submit_gate_decision,
)
from ez.live.scheduler import Scheduler

# Extracted helpers (serialization, QMT gate builders, runtime event resolution)
from ez.api._live_helpers import (
    _SESSION_RUNTIME_KINDS,
    _CALLBACK_DEGRADED_RUNTIME_KINDS,
    _ALREADY_CANCELING_STATUSES,
    _build_spec_from_run,
    _record_to_dict,
    _health_to_dict,
    _parse_gate_verdict_json,
    _extract_shadow_account_id,
    _build_qmt_submit_gate_preview,
    _build_qmt_release_gate,
    _resolve_qmt_account_id,
    _runtime_payload,
    _runtime_event_ts,
    _runtime_kind,
    _resolve_callback_health,
    _get_latest_risk_event_with_snapshot_fallback,
)

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
            _deployment_store.close()  # uses lock, not raw _conn.close()
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


class CancelOrderRequest(BaseModel):
    client_order_id: str = ""
    broker_order_id: str = ""


class TickRequest(BaseModel):
    business_date: date


# ---------------------------------------------------------------------------
# Helper: portfolio store access
# ---------------------------------------------------------------------------

def _get_portfolio_store():
    """Access the portfolio store singleton from the portfolio route module."""
    from ez.api.routes.portfolio import _get_store as get_pf_store
    return get_pf_store()


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
    except HTTPException:
        # V3.3.27 Fix-A Issue #3: re-raise structured 422 (conflicting
        # legacy / new optimizer_params config) without wrapping into 400.
        raise
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
    results: list[dict[str, Any]] = []
    for record in records:
        item = _record_to_dict(record)
        spec = store.get_spec(record.spec_id)
        if spec is not None:
            # V3.3.27 Fix-A Issue #1: preview path has no runtime projection,
            # pass hard_gate=None explicitly.
            qmt_release_gate = _build_qmt_release_gate(
                record=record,
                spec=spec,
                qmt_submit_gate=None,
                hard_gate=None,
            )
            if qmt_release_gate is not None:
                item["qmt_release_gate"] = qmt_release_gate
        results.append(item)
    return results


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
            "broker_type": spec.broker_type,
            "shadow_broker_type": spec.shadow_broker_type,
            "initial_cash": spec.initial_cash,
        }
        # V3.3.27 Fix-A Issue #1: deployment detail is a preview-type view
        # (no runtime projection). Explicit hard_gate=None avoids silent
        # drift if _build_qmt_release_gate default changes.
        result["qmt_release_gate"] = _build_qmt_release_gate(
            record=record,
            spec=spec,
            qmt_submit_gate=None,
            hard_gate=None,
        )
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

    # Run gate — V2.15.1 S1: WF metrics read from DB, not from client
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
            # V3.3.27 Fix-A Issue #1: approval preview — no runtime yet,
            # hard_gate explicitly None.
            "qmt_release_gate": _build_qmt_release_gate(
                record=store.get_record(deployment_id) or record,
                spec=spec,
                qmt_submit_gate=None,
                hard_gate=None,
            ),
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
async def stop_deployment(
    deployment_id: str,
    req: StopRequest | None = None,
    liquidate: bool = Query(False, description="Liquidate all positions before stopping"),
):
    """Stop a running deployment. If liquidate=true, sells all positions first."""
    reason = req.reason if req else "手动停止"
    scheduler = _get_scheduler()
    try:
        await scheduler.stop_deployment(deployment_id, reason=reason, liquidate=liquidate)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deployment_id": deployment_id, "status": "stopped", "liquidated": liquidate}


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


@router.post("/deployments/{deployment_id}/cancel")
async def cancel_order(
    deployment_id: str,
    req: CancelOrderRequest,
    if_not_already: bool = Query(
        True,
        description=(
            "Skip broker call when the persisted link is already in a "
            "cancel-inflight or terminal cancel state (default true)."
        ),
    ),
):
    """Request broker-side cancel for a tracked order.

    V3.3.27 Fix-A Issue #4: idempotency. Fast double-clicks on the
    frontend cancel button otherwise hit the broker twice. When
    ``if_not_already=true`` (default), inspect the persisted broker-order
    link first; if the latest status is already
    ``reported_cancel_pending`` / ``canceled`` / ``partially_canceled`` /
    ``partial_cancel``, return ``200 {"status": "already_canceling", ...}``
    without calling the broker again.
    """
    scheduler = _get_scheduler()
    store = _get_deployment_store()

    if if_not_already:
        # V3.3.27 Fix-A Issue #4: look up the persisted link for the provided
        # identifiers first. If the record / link is missing we fall through
        # to the scheduler which produces the canonical error; we only
        # short-circuit when there IS a live link in a cancel-inflight /
        # terminal cancel state.
        existing_link: dict[str, Any] | None = None
        try:
            persisted_links = store.list_broker_order_links(deployment_id)
        except Exception:
            persisted_links = []
        for link in persisted_links:
            if (
                req.client_order_id
                and str(link.get("client_order_id", "") or "") == req.client_order_id
            ):
                existing_link = link
                break
            if (
                req.broker_order_id
                and str(link.get("broker_order_id", "") or "") == req.broker_order_id
            ):
                existing_link = link
                break
        if existing_link is not None:
            latest_status = str(existing_link.get("latest_status", "") or "")
            if latest_status in _ALREADY_CANCELING_STATUSES:
                last_ts = existing_link.get("last_report_ts")
                if isinstance(last_ts, datetime):
                    last_ts_iso = last_ts.isoformat()
                else:
                    last_ts_iso = str(last_ts) if last_ts else None
                return {
                    "status": "already_canceling",
                    "deployment_id": deployment_id,
                    "client_order_id": str(
                        existing_link.get("client_order_id", "") or ""
                    ) or None,
                    "broker_order_id": str(
                        existing_link.get("broker_order_id", "") or ""
                    ) or None,
                    "link": {
                        "client_order_id": str(
                            existing_link.get("client_order_id", "") or ""
                        ),
                        "broker_order_id": str(
                            existing_link.get("broker_order_id", "") or ""
                        ),
                        "symbol": str(existing_link.get("symbol", "") or ""),
                        "account_id": str(
                            existing_link.get("account_id", "") or ""
                        ) or None,
                        "latest_status": latest_status,
                        "last_report_ts": last_ts_iso,
                    },
                }

    try:
        result = await scheduler.cancel_order(
            deployment_id,
            client_order_id=req.client_order_id,
            broker_order_id=req.broker_order_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except NotImplementedError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return result


@router.post("/deployments/{deployment_id}/broker-sync")
async def pump_broker_state(deployment_id: str):
    """Explicitly pump shadow-broker runtime/account/execution state without a daily tick."""
    scheduler = _get_scheduler()
    try:
        result = await scheduler.pump_broker_state(deployment_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return result


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


@router.get("/deployments/{deployment_id}/broker-orders")
def get_broker_orders(deployment_id: str):
    """Latest broker-order link state for a deployment.

    V3.3.27 Fix-A Issue #2: response is now a wrapped object with
    ``target_account_id`` + ``orders``; each order link carries
    ``account_id`` so the frontend no longer has to triangulate
    identity through ``qmt_submit_gate.account_id``.
    """
    store = _get_deployment_store()
    record = store.get_record(deployment_id)
    if not record:
        raise HTTPException(404, f"部署 {deployment_id} 不存在")
    spec = store.get_spec(record.spec_id)
    persisted_links = store.list_broker_order_links(deployment_id)
    account_id_by_client_order_id: dict[str, str] = {}
    for link in persisted_links:
        cid = str(link.get("client_order_id", "") or "")
        acct = str(link.get("account_id", "") or "")
        if cid:
            account_id_by_client_order_id[cid] = acct
    projected = build_persisted_broker_order_view(store, deployment_id)
    enriched: list[dict[str, Any]] = []
    for order in projected:
        cid = str(order.get("client_order_id", "") or "")
        persisted_account = account_id_by_client_order_id.get(cid, "")
        enriched.append({
            **order,
            "account_id": persisted_account or None,
        })
    resolved_account = _resolve_qmt_account_id(spec)
    return {
        "deployment_id": deployment_id,
        "target_account_id": resolved_account or None,
        "orders": enriched,
    }


@router.get("/deployments/{deployment_id}/broker-state")
def get_broker_state(deployment_id: str, runtime_limit: int = Query(5, ge=1, le=50)):
    """Latest broker account snapshot + recent runtime events + reconcile summaries."""
    store = _get_deployment_store()
    record = store.get_record(deployment_id)
    if not record:
        raise HTTPException(404, f"部署 {deployment_id} 不存在")
    spec = store.get_spec(record.spec_id)
    risk_params = spec.risk_params if spec is not None else {}
    submit_policy = build_qmt_real_submit_policy(risk_params)
    broker_account_id = _resolve_qmt_account_id(spec)
    initial_cash = float(spec.initial_cash) if spec is not None else None

    latest_account_event = store.get_latest_event(
        deployment_id,
        event_type="broker_account_recorded",
    )
    runtime_projection = resolve_qmt_runtime_projection(
        store,
        deployment_id,
        deployment_status=record.status,
    )
    runtime_fetch_limit = max(runtime_limit, 20)
    runtime_events_all = store.get_recent_events(
        deployment_id,
        event_type="broker_runtime_recorded",
        limit=runtime_fetch_limit,
    )
    recent_runtime_events = runtime_events_all[:runtime_limit]
    latest_broker_account = latest_account_event.to_dict() if latest_account_event else None
    latest_session_runtime = None
    latest_session_owner_runtime = None
    latest_session_consumer_runtime = None
    latest_session_consumer_state_runtime = None
    latest_callback_account_mode = None
    latest_callback_account_freshness = None
    latest_reconcile = None
    latest_order_reconcile = None
    latest_position_reconcile = None
    latest_trade_reconcile = None
    latest_qmt_hard_gate = None
    qmt_readiness = None
    qmt_submit_gate = None
    projection_source = None
    projection_ts = None
    target_account_id = broker_account_id or None
    if isinstance(runtime_projection, dict):
        latest_broker_account = runtime_projection.get("latest_broker_account")
        latest_session_runtime = runtime_projection.get("latest_session_runtime")
        latest_session_owner_runtime = runtime_projection.get("latest_session_owner_runtime")
        latest_session_consumer_runtime = runtime_projection.get("latest_session_consumer_runtime")
        latest_session_consumer_state_runtime = runtime_projection.get(
            "latest_session_consumer_state_runtime"
        )
        latest_callback_account_mode = runtime_projection.get("latest_callback_account_mode")
        latest_callback_account_freshness = runtime_projection.get(
            "latest_callback_account_freshness"
        )
        latest_reconcile = runtime_projection.get("latest_reconcile")
        latest_order_reconcile = runtime_projection.get("latest_order_reconcile")
        latest_position_reconcile = runtime_projection.get("latest_position_reconcile")
        latest_trade_reconcile = runtime_projection.get("latest_trade_reconcile")
        latest_qmt_hard_gate = runtime_projection.get("latest_qmt_hard_gate")
        qmt_readiness = runtime_projection.get("qmt_readiness")
        qmt_submit_gate = runtime_projection.get("qmt_submit_gate")
        projection_source = runtime_projection.get("projection_source")
        projection_ts = runtime_projection.get("projection_ts")
        target_account_id = (
            str(runtime_projection.get("target_account_id", "") or "") or target_account_id
        )
    else:
        is_real_qmt_spec = (
            spec is not None
            and str(getattr(spec, "broker_type", "") or "").lower() == "qmt"
        )
        account_reconcile_event_name = (
            "real_broker_reconcile" if is_real_qmt_spec else "broker_reconcile"
        )
        order_reconcile_event_name = (
            "real_broker_order_reconcile"
            if is_real_qmt_spec
            else "broker_order_reconcile"
        )
        position_reconcile_event_name = (
            "real_position_reconcile" if is_real_qmt_spec else "position_reconcile"
        )
        trade_reconcile_event_name = (
            "real_trade_reconcile" if is_real_qmt_spec else "trade_reconcile"
        )
        qmt_hard_gate_event_name = (
            "real_qmt_reconcile_hard_gate"
            if is_real_qmt_spec
            else "qmt_reconcile_hard_gate"
        )
        latest_session_runtime_event = store.get_latest_runtime_event(
            deployment_id,
            kinds=_SESSION_RUNTIME_KINDS,
        )
        latest_session_owner_runtime_event = store.get_latest_runtime_event(
            deployment_id,
            prefix="session_owner_",
        )
        latest_session_consumer_runtime_event = store.get_latest_runtime_event(
            deployment_id,
            prefix="session_consumer_",
        )
        latest_session_consumer_state_runtime_event = store.get_latest_runtime_event(
            deployment_id,
            kind="session_consumer_state",
        )
        latest_session_runtime = (
            latest_session_runtime_event.to_dict()
            if latest_session_runtime_event is not None
            else None
        )
        latest_session_owner_runtime = (
            latest_session_owner_runtime_event.to_dict()
            if latest_session_owner_runtime_event is not None
            else None
        )
        latest_session_consumer_runtime = (
            latest_session_consumer_runtime_event.to_dict()
            if latest_session_consumer_runtime_event is not None
            else None
        )
        latest_session_consumer_state_runtime = (
            latest_session_consumer_state_runtime_event.to_dict()
            if latest_session_consumer_state_runtime_event is not None
            else None
        )
        (
            latest_callback_account_mode,
            latest_callback_account_freshness,
        ) = _resolve_callback_health(
            latest_session_runtime=latest_session_runtime,
            latest_session_owner_runtime=latest_session_owner_runtime,
            latest_session_consumer_runtime=latest_session_consumer_runtime,
            latest_session_consumer_state_runtime=latest_session_consumer_state_runtime,
        )
        latest_reconcile = _get_latest_risk_event_with_snapshot_fallback(
            store,
            deployment_id,
            event_name=account_reconcile_event_name,
        )
        latest_order_reconcile = _get_latest_risk_event_with_snapshot_fallback(
            store,
            deployment_id,
            event_name=order_reconcile_event_name,
        )
        latest_position_reconcile = _get_latest_risk_event_with_snapshot_fallback(
            store,
            deployment_id,
            event_name=position_reconcile_event_name,
        )
        latest_trade_reconcile = _get_latest_risk_event_with_snapshot_fallback(
            store,
            deployment_id,
            event_name=trade_reconcile_event_name,
        )
        latest_qmt_hard_gate = _get_latest_risk_event_with_snapshot_fallback(
            store,
            deployment_id,
            event_name=qmt_hard_gate_event_name,
        )
    broker_order_views = build_persisted_broker_order_view(store, deployment_id)
    broker_order_cancel_summary = {
        "total": len(broker_order_views),
        "cancel_inflight": sum(
            1
            for link in broker_order_views
            if str(link.get("cancel_state", "") or "") == "cancel_inflight"
        ),
        "cancel_error": sum(
            1
            for link in broker_order_views
            if str(link.get("cancel_state", "") or "") == "cancel_error"
        ),
        "canceled": sum(
            1
            for link in broker_order_views
            if str(link.get("cancel_state", "") or "") == "canceled"
        ),
    }

    broker_type = None
    is_qmt_related = bool(
        spec is not None
        and (spec.broker_type == "qmt" or spec.shadow_broker_type == "qmt")
    )
    latest_account_payload = (
        latest_broker_account.get("payload")
        if isinstance(latest_broker_account, dict)
        else None
    )
    if isinstance(latest_account_payload, dict):
        broker_type = str(latest_account_payload.get("broker_type", "") or "") or None
    if (
        is_qmt_related
        or broker_type == "qmt"
        or latest_session_runtime is not None
        or latest_session_consumer_runtime is not None
        or latest_session_consumer_state_runtime is not None
    ) and qmt_submit_gate is None:
        qmt_readiness_summary = build_qmt_readiness_summary(
            latest_session_runtime=latest_session_runtime,
            latest_session_consumer_runtime=latest_session_consumer_runtime,
            latest_session_consumer_state_runtime=latest_session_consumer_state_runtime,
            latest_reconcile=latest_reconcile,
            latest_order_reconcile=latest_order_reconcile,
            real_submit_policy=submit_policy if spec is not None and spec.broker_type == "qmt" else None,
        )
        qmt_readiness = qmt_readiness_summary.to_dict()
        latest_total_asset = None
        if isinstance(latest_account_payload, dict):
            payload_total_asset = latest_account_payload.get("total_asset")
            if payload_total_asset is not None:
                latest_total_asset = float(payload_total_asset)
        qmt_submit_gate = build_qmt_submit_gate_decision(
            qmt_readiness_summary,
            policy=submit_policy,
            account_id=broker_account_id or None,
            total_asset=latest_total_asset,
            initial_cash=initial_cash,
            hard_gate=latest_qmt_hard_gate,
        ).to_dict()
        qmt_submit_gate["source"] = "runtime"
    # V3.3.27 Fix-A Issue #1: broker-state is the runtime path; fold the
    # latest runtime hard_gate so release.blockers reflect reconcile drift.
    runtime_hard_gate_payload: dict[str, Any] | None = None
    if isinstance(runtime_projection, dict):
        projected_hard_gate = runtime_projection.get("qmt_reconcile_hard_gate")
        if isinstance(projected_hard_gate, dict):
            runtime_hard_gate_payload = projected_hard_gate
    if runtime_hard_gate_payload is None and isinstance(latest_qmt_hard_gate, dict):
        runtime_hard_gate_payload = latest_qmt_hard_gate
    qmt_release_gate = _build_qmt_release_gate(
        record=record,
        spec=spec,
        qmt_submit_gate=qmt_submit_gate,
        hard_gate=runtime_hard_gate_payload,
    )

    return {
        "deployment_id": deployment_id,
        "latest_broker_account": latest_broker_account,
        "recent_runtime_events": [event.to_dict() for event in recent_runtime_events],
        "latest_session_runtime": latest_session_runtime,
        "latest_session_owner_runtime": latest_session_owner_runtime,
        "latest_session_consumer_runtime": latest_session_consumer_runtime,
        "latest_session_consumer_state_runtime": latest_session_consumer_state_runtime,
        "latest_callback_account_mode": latest_callback_account_mode,
        "latest_callback_account_freshness": latest_callback_account_freshness,
        "latest_reconcile": latest_reconcile,
        "latest_order_reconcile": latest_order_reconcile,
        "latest_position_reconcile": latest_position_reconcile,
        "latest_trade_reconcile": latest_trade_reconcile,
        "latest_qmt_hard_gate": latest_qmt_hard_gate,
        "qmt_readiness": qmt_readiness,
        "qmt_submit_gate": qmt_submit_gate,
        "qmt_release_gate": qmt_release_gate,
        "broker_order_cancel_summary": broker_order_cancel_summary,
        "target_account_id": target_account_id,
        "projection_source": projection_source,
        "projection_ts": projection_ts,
    }


@router.get("/deployments/{deployment_id}/broker-submit-gate")
def get_broker_submit_gate(deployment_id: str):
    """Future real-submit gate preview. Currently fail-closed for QMT shadow mode."""
    broker_state = get_broker_state(deployment_id, runtime_limit=20)
    qmt_submit_gate = broker_state.get("qmt_submit_gate")
    store = _get_deployment_store()
    record = store.get_record(deployment_id)
    spec = store.get_spec(record.spec_id) if record is not None else None
    is_qmt_related = bool(
        spec is not None
        and (spec.broker_type == "qmt" or spec.shadow_broker_type == "qmt")
    )
    broker_type = None
    latest_broker_account = broker_state.get("latest_broker_account") or {}
    payload = latest_broker_account.get("payload") if isinstance(latest_broker_account, dict) else None
    if isinstance(payload, dict):
        broker_type = str(payload.get("broker_type", "") or "") or None
    if qmt_submit_gate is not None:
        return {
            "deployment_id": deployment_id,
            "broker_type": broker_type or "qmt",
            "qmt_submit_gate": qmt_submit_gate,
            "target_account_id": broker_state.get("target_account_id"),
            "projection_source": broker_state.get("projection_source"),
            "projection_ts": broker_state.get("projection_ts"),
        }
    return {
        "deployment_id": deployment_id,
        "broker_type": broker_type or ("qmt" if is_qmt_related else "paper"),
        "qmt_submit_gate": None,
        "target_account_id": broker_state.get("target_account_id"),
        "projection_source": broker_state.get("projection_source"),
        "projection_ts": broker_state.get("projection_ts"),
    }


@router.get("/deployments/{deployment_id}/release-gate")
def get_release_gate(deployment_id: str):
    broker_state = get_broker_state(deployment_id, runtime_limit=20)
    record = _get_deployment_store().get_record(deployment_id)
    if not record:
        raise HTTPException(404, f"部署 {deployment_id} 不存在")
    return {
        "deployment_id": deployment_id,
        "deployment_status": record.status,
        "qmt_release_gate": broker_state.get("qmt_release_gate"),
        "target_account_id": broker_state.get("target_account_id"),
        "projection_source": broker_state.get("projection_source"),
        "projection_ts": broker_state.get("projection_ts"),
    }


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
