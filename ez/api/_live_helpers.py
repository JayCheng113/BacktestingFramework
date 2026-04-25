"""Extracted helper functions for live route handlers.

QMT gate builders, serialization helpers, and runtime event
resolution. Moved from routes/live.py to reduce route file size.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException

from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.qmt.broker import (
    build_qmt_release_gate_decision,
    build_qmt_real_submit_policy,
    build_qmt_readiness_summary,
    build_qmt_submit_gate_decision,
)

logger = logging.getLogger(__name__)

_SESSION_RUNTIME_KINDS = (
    "session_bootstrap_started",
    "session_started",
    "session_connected",
    "session_subscribed",
    "session_reconnect_started",
    "session_reconnected",
    "session_resubscribed",
    "session_connect_failed",
    "session_subscribe_failed",
    "session_reconnect_failed",
    "session_resubscribe_failed",
    "session_reconnect_deferred",
    "disconnected",
)

_CALLBACK_DEGRADED_RUNTIME_KINDS = frozenset(
    {
        "disconnected",
        "session_connect_failed",
        "session_subscribe_failed",
        "session_reconnect_started",
        "session_reconnect_failed",
        "session_resubscribe_failed",
        "session_reconnect_deferred",
        "session_consumer_started",
        "session_consumer_restarted",
        "session_consumer_stopped",
        "session_consumer_failed",
        "session_consumer_restart_failed",
        "session_owner_detached",
        "session_owner_closed",
        "session_owner_close_failed",
    }
)

_ALREADY_CANCELING_STATUSES = frozenset(
    {
        "reported_cancel_pending",
        "canceled",
        "partially_canceled",
        "partial_cancel",
    }
)


def _build_spec_from_run(run: dict) -> DeploymentSpec:
    """Build a DeploymentSpec from a portfolio run dict.

    V2.16.2 CRITICAL fix: /run persists cost/limit fields under nested
    `config._cost` and optimizer/risk under `config._optimizer` / `_risk`
    (see `routes/portfolio.py::run_config` construction). Prior version
    of this function read them from top-level `config` keys, which
    always missed -> every deployment silently fell back to hardcoded
    CN-stock defaults (T+1 True, stamp_tax 0.05%, lot_size 100, limit
    10%). US / HK deployments would enforce CN market rules on
    execution, diverging from the backtest that produced the run.

    Precedence per field:
      1. Nested bucket (config._cost.lot_size, etc.)
      2. Top-level (legacy/future path, or explicit override)
      3. Market-gated default
    """
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

    cost_cfg = config.get("_cost") or {}
    opt_cfg = config.get("_optimizer") or {}
    risk_cfg = config.get("_risk") or {}
    legacy_optimizer_params = config.get("optimizer_params")
    legacy_risk_params = config.get("risk_params")
    if not isinstance(cost_cfg, dict):
        cost_cfg = {}
    if not isinstance(opt_cfg, dict):
        opt_cfg = {}
    if not isinstance(risk_cfg, dict):
        risk_cfg = {}
    # V3.3.27 Fix-A Issue #3: Detect silent override of legacy bucket by new
    # bucket. If the run dict carries BOTH legacy (`optimizer_params` /
    # `risk_params`) AND the new nested (`_optimizer` / `_risk`) and their
    # values differ, prior logic would silently prefer the new bucket and
    # drop the legacy fields — which is a real production bug when a run
    # is hand-edited or replayed from an older store. Fail closed with
    # 422 so the caller resolves the conflict manually.
    conflict_fields: list[str] = []
    if (
        isinstance(legacy_optimizer_params, dict)
        and opt_cfg
        and dict(legacy_optimizer_params) != dict(opt_cfg)
    ):
        conflict_fields.extend(["optimizer_params", "_optimizer"])
    if (
        isinstance(legacy_risk_params, dict)
        and risk_cfg
        and dict(legacy_risk_params) != dict(risk_cfg)
    ):
        conflict_fields.extend(["risk_params", "_risk"])
    if conflict_fields:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "conflicting_spec_config",
                "message": (
                    "Both legacy (optimizer_params/risk_params) and new "
                    "(_optimizer/_risk) config present; resolve manually "
                    "before redeploy"
                ),
                "fields": conflict_fields,
            },
        )
    if not opt_cfg and isinstance(legacy_optimizer_params, dict):
        opt_cfg = dict(legacy_optimizer_params)
    if not risk_cfg and isinstance(legacy_risk_params, dict):
        risk_cfg = dict(legacy_risk_params)

    def _field(bucket: dict, key: str, top_level_fallback, market_default):
        """bucket[key] if present, else top-level config[key], else market-gated default."""
        if key in bucket and bucket[key] is not None:
            return bucket[key]
        if key in config and config[key] is not None:
            return config[key]
        return top_level_fallback if top_level_fallback is not None else market_default

    # Market-gated defaults — only A-shares get CN market rules.
    is_cn = market == "cn_stock"
    default_t_plus_1 = is_cn
    default_stamp_tax = 0.0005 if is_cn else 0.0
    default_limit_pct = 0.10 if is_cn else 0.0  # 0 = disabled (no limit check)
    default_lot_size = 100 if is_cn else 1

    return DeploymentSpec(
        strategy_name=run.get("strategy_name", ""),
        strategy_params=params,
        symbols=tuple(symbols),
        market=market,
        freq=freq,
        broker_type=str(config.get("broker_type", "paper") or "paper"),
        shadow_broker_type=str(config.get("shadow_broker_type", "") or ""),
        t_plus_1=bool(_field(config, "t_plus_1", None, default_t_plus_1)),
        price_limit_pct=float(
            _field(cost_cfg, "limit_pct", config.get("price_limit_pct"), default_limit_pct)
        ),
        lot_size=int(_field(cost_cfg, "lot_size", None, default_lot_size)),
        buy_commission_rate=float(_field(cost_cfg, "buy_commission_rate", None, 0.00008)),
        sell_commission_rate=float(_field(cost_cfg, "sell_commission_rate", None, 0.00008)),
        stamp_tax_rate=float(_field(cost_cfg, "stamp_tax_rate", None, default_stamp_tax)),
        slippage_rate=float(_field(cost_cfg, "slippage_rate", None, 0.001)),
        min_commission=float(_field(cost_cfg, "min_commission", None, 0.0)),
        optimizer=str(_field(opt_cfg, "kind", config.get("optimizer"), "") or ""),
        optimizer_params=opt_cfg or None,
        risk_control=bool(_field(risk_cfg, "enabled", config.get("risk_control"), False)),
        risk_params=risk_cfg or None,
        rebal_weekday=config.get("rebal_weekday"),
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


def _parse_gate_verdict_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_shadow_account_id(spec: DeploymentSpec | None) -> str:
    if spec is None:
        return ""
    risk_params = spec.risk_params
    if not isinstance(risk_params, dict):
        return ""
    shadow_cfg = risk_params.get("shadow_broker_config")
    if not isinstance(shadow_cfg, dict):
        return ""
    return str(shadow_cfg.get("account_id", "") or "")


def _build_qmt_submit_gate_preview(
    spec: DeploymentSpec | None,
    *,
    total_asset: float | None = None,
) -> dict[str, Any] | None:
    if spec is None:
        return None
    is_qmt_related = spec.broker_type == "qmt" or spec.shadow_broker_type == "qmt"
    if not is_qmt_related:
        return None
    policy = build_qmt_real_submit_policy(spec.risk_params)
    account_id = _extract_shadow_account_id(spec) or None
    gate = build_qmt_submit_gate_decision(
        None,
        policy=policy,
        account_id=account_id,
        total_asset=total_asset,
        initial_cash=float(spec.initial_cash),
    ).to_dict()
    gate["source"] = "preview"
    return gate


def _build_qmt_release_gate(
    *,
    record: DeploymentRecord,
    spec: DeploymentSpec | None,
    qmt_submit_gate: dict[str, Any] | None,
    hard_gate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if spec is None:
        return None
    is_qmt_related = spec.broker_type == "qmt" or spec.shadow_broker_type == "qmt"
    if not is_qmt_related:
        return None
    effective_submit_gate = qmt_submit_gate or _build_qmt_submit_gate_preview(spec)
    # V3.3.27 Fix-A Issue #1: fold hard_gate into release decision so runtime
    # reconcile blockers surface in release.blockers. Preview path passes
    # hard_gate=None (no runtime truth yet). build_qmt_release_gate_decision
    # should be tolerant of None.
    release_kwargs: dict[str, Any] = {
        "deployment_status": record.status,
        "gate_verdict": _parse_gate_verdict_json(record.gate_verdict),
        "submit_gate": effective_submit_gate,
    }
    try:
        gate = build_qmt_release_gate_decision(
            hard_gate=hard_gate,
            **release_kwargs,
        ).to_dict()
    except TypeError:
        # Fallback if Fix-C agent hasn't wired hard_gate into
        # build_qmt_release_gate_decision yet; keep API shape intact.
        gate = build_qmt_release_gate_decision(**release_kwargs).to_dict()
    gate["source"] = "runtime" if qmt_submit_gate is not None else "preview"
    return gate


def _resolve_qmt_account_id(spec: DeploymentSpec | None) -> str:
    if spec is None or not isinstance(spec.risk_params, dict):
        return ""
    risk_params = spec.risk_params
    prefer_real = spec.broker_type == "qmt"
    config_keys = (
        ("qmt_real_broker_config", "shadow_broker_config")
        if prefer_real
        else ("shadow_broker_config", "qmt_real_broker_config")
    )
    for key in config_keys:
        raw_cfg = risk_params.get(key)
        if isinstance(raw_cfg, dict) and raw_cfg.get("account_id"):
            return str(raw_cfg.get("account_id", "") or "")
    return ""


def _runtime_payload(event: dict[str, Any] | Any | None) -> dict[str, Any]:
    if event is None:
        return {}
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    if isinstance(payload, dict):
        nested = payload.get("payload")
        if isinstance(nested, dict):
            return nested
        return payload
    return {}


def _runtime_event_ts(event: dict[str, Any] | Any | None) -> datetime | None:
    if event is None:
        return None
    event_ts = event.get("event_ts") if isinstance(event, dict) else getattr(event, "event_ts", None)
    if event_ts is None:
        event_ts = _runtime_payload(event).get("update_time")
    if event_ts is None:
        return None
    if isinstance(event_ts, datetime):
        return event_ts
    try:
        return datetime.fromisoformat(str(event_ts))
    except (TypeError, ValueError):
        return None


def _runtime_kind(event: dict[str, Any] | Any | None) -> str | None:
    if event is None:
        return None
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    if not isinstance(payload, dict):
        return None
    return str(payload.get("runtime_kind", "") or "") or None


def _resolve_callback_health(
    *,
    latest_session_runtime: dict[str, Any] | Any | None,
    latest_session_owner_runtime: dict[str, Any] | Any | None,
    latest_session_consumer_runtime: dict[str, Any] | Any | None,
    latest_session_consumer_state_runtime: dict[str, Any] | Any | None,
) -> tuple[str | None, str | None]:
    latest_callback_account_mode = None
    latest_callback_account_freshness = None
    if latest_session_consumer_state_runtime is not None:
        payload = _runtime_payload(latest_session_consumer_state_runtime)
        latest_callback_account_mode = (
            str(payload.get("account_sync_mode", "") or "") or None
        )
        latest_callback_account_freshness = (
            str(payload.get("asset_callback_freshness", "") or "") or None
        )
    elif latest_session_consumer_runtime is not None:
        latest_callback_account_mode = "query_fallback"
        latest_callback_account_freshness = "unavailable"

    newest_lifecycle_event = None
    newest_lifecycle_ts = None
    for event in (
        latest_session_runtime,
        latest_session_consumer_runtime,
        latest_session_owner_runtime,
    ):
        event_ts = _runtime_event_ts(event)
        if event_ts is None:
            continue
        if newest_lifecycle_ts is None or event_ts > newest_lifecycle_ts:
            newest_lifecycle_event = event
            newest_lifecycle_ts = event_ts

    newest_lifecycle_kind = _runtime_kind(newest_lifecycle_event)
    state_ts = _runtime_event_ts(latest_session_consumer_state_runtime)
    if (
        newest_lifecycle_kind in _CALLBACK_DEGRADED_RUNTIME_KINDS
        and (state_ts is None or (newest_lifecycle_ts is not None and newest_lifecycle_ts >= state_ts))
    ):
        return "query_fallback", "unavailable"
    return latest_callback_account_mode, latest_callback_account_freshness


def _get_latest_risk_event_with_snapshot_fallback(
    store: DeploymentStore,
    deployment_id: str,
    *,
    event_name: str,
) -> dict[str, Any] | None:
    latest = store.get_latest_risk_event(
        deployment_id,
        event_name=event_name,
    )
    if latest is not None:
        return latest
    latest_snapshot = store.get_latest_snapshot(deployment_id)
    if not latest_snapshot:
        return None
    for risk_event in reversed(list(latest_snapshot.get("risk_events") or [])):
        if isinstance(risk_event, dict) and risk_event.get("event") == event_name:
            return risk_event
    return None
