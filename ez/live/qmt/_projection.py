"""QMT runtime-projection helpers extracted from Scheduler.

All functions are stateless or take an explicit ``store`` parameter.
Scheduler calls them as free functions instead of self-methods.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ez.live.broker import BrokerRuntimeEvent
from ez.live.deployment_spec import DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.events import DeploymentEvent, EventType, utcnow
from ez.live.qmt.broker import (
    build_qmt_readiness_summary,
    build_qmt_real_submit_policy,
    build_qmt_release_gate_decision,
    build_qmt_submit_gate_decision,
)

logger = logging.getLogger(__name__)

_SESSION_RUNTIME_KINDS = {
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
}


# ------------------------------------------------------------------
# Pure parsers / extractors (static)
# ------------------------------------------------------------------

def parse_gate_verdict(raw: str | None) -> dict | None:
    """Parse a JSON gate-verdict string into a dict, or return None."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_qmt_account_id(spec: DeploymentSpec | None) -> str:
    """Extract the QMT account_id from a deployment spec's risk_params."""
    if spec is None or not isinstance(spec.risk_params, dict):
        return ""
    real_cfg = spec.risk_params.get("qmt_real_broker_config")
    if isinstance(real_cfg, dict) and real_cfg.get("account_id"):
        return str(real_cfg.get("account_id", "") or "")
    shadow_cfg = spec.risk_params.get("shadow_broker_config")
    if isinstance(shadow_cfg, dict):
        return str(shadow_cfg.get("account_id", "") or "")
    return ""


def extract_runtime_event_account_id(event: DeploymentEvent | None) -> str:
    """Extract the account_id from a broker-runtime event's nested payload."""
    if event is None or not isinstance(event.payload, dict):
        return ""
    payload = event.payload.get("payload")
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("account_id", "") or "")


def extract_account_event_account_id(event: DeploymentEvent | None) -> str:
    """Extract the account_id from a broker-account event."""
    if event is None or not isinstance(event.payload, dict):
        return ""
    return str(event.payload.get("account_id", "") or "")


# ------------------------------------------------------------------
# Store-reading helpers (take explicit store + deployment_id)
# ------------------------------------------------------------------

def get_latest_runtime_event_for_account(
    store: DeploymentStore,
    deployment_id: str,
    *,
    account_id: str,
    kind: str | None = None,
    prefix: str | None = None,
    kinds: set[str] | None = None,
    limit: int = 200,
) -> DeploymentEvent | None:
    """Return the latest broker-runtime event scoped to *account_id*."""
    if not account_id:
        return store.get_latest_runtime_event(
            deployment_id,
            kind=kind,
            prefix=prefix,
            kinds=tuple(sorted(kinds)) if kinds else None,
        )
    recent_events = store.get_recent_events(
        deployment_id,
        event_type=EventType.BROKER_RUNTIME_RECORDED,
        limit=limit,
    )
    accepted_kinds = set(kinds or ())
    latest_unscoped_event: DeploymentEvent | None = None
    for event in recent_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        runtime_kind = str(payload.get("runtime_kind", "") or "")
        if kind is not None and runtime_kind != kind:
            continue
        if prefix is not None and not runtime_kind.startswith(prefix):
            continue
        if accepted_kinds and runtime_kind not in accepted_kinds:
            continue
        event_account_id = extract_runtime_event_account_id(event)
        if event_account_id == account_id:
            return event
        if not event_account_id and latest_unscoped_event is None:
            latest_unscoped_event = event
    return latest_unscoped_event


def get_latest_account_event_for_account(
    store: DeploymentStore,
    deployment_id: str,
    *,
    account_id: str,
    limit: int = 50,
) -> DeploymentEvent | None:
    """Return the latest broker-account event scoped to *account_id*."""
    if not account_id:
        return store.get_latest_event(
            deployment_id,
            event_type=EventType.BROKER_ACCOUNT_RECORDED,
        )
    recent_events = store.get_recent_events(
        deployment_id,
        event_type=EventType.BROKER_ACCOUNT_RECORDED,
        limit=limit,
    )
    latest_unscoped_event: DeploymentEvent | None = None
    for event in recent_events:
        event_account_id = extract_account_event_account_id(event)
        if event_account_id == account_id:
            return event
        if not event_account_id and latest_unscoped_event is None:
            latest_unscoped_event = event
    return latest_unscoped_event


# ------------------------------------------------------------------
# Projection builder (the big one — ~220 lines in scheduler.py)
# ------------------------------------------------------------------

def build_qmt_runtime_projection(
    store: DeploymentStore,
    *,
    deployment_id: str,
    record,
    spec: DeploymentSpec | None,
) -> dict | None:
    """Build a full QMT runtime-projection dict from persisted state.

    This is the free-function equivalent of the former
    ``Scheduler._build_qmt_runtime_projection``.  All ``self.store``
    access is replaced with the explicit *store* parameter, and all
    internal ``self._extract_*`` / ``self._get_latest_*`` calls are
    replaced with their module-level equivalents.
    """
    if spec is None:
        return None
    is_qmt_related = spec.broker_type == "qmt" or spec.shadow_broker_type == "qmt"
    if not is_qmt_related:
        return None

    target_account_id = extract_qmt_account_id(spec)
    is_real_qmt = str(getattr(spec, "broker_type", "") or "").lower() == "qmt"
    account_reconcile_event_name = (
        "real_broker_reconcile" if is_real_qmt else "broker_reconcile"
    )
    order_reconcile_event_name = (
        "real_broker_order_reconcile" if is_real_qmt else "broker_order_reconcile"
    )
    position_reconcile_event_name = (
        "real_position_reconcile" if is_real_qmt else "position_reconcile"
    )
    trade_reconcile_event_name = (
        "real_trade_reconcile" if is_real_qmt else "trade_reconcile"
    )
    qmt_hard_gate_event_name = (
        "real_qmt_reconcile_hard_gate"
        if is_real_qmt
        else "qmt_reconcile_hard_gate"
    )

    latest_runtime_event = get_latest_runtime_event_for_account(
        store,
        deployment_id,
        account_id=target_account_id,
    )
    latest_session_runtime = get_latest_runtime_event_for_account(
        store,
        deployment_id,
        account_id=target_account_id,
        kinds=_SESSION_RUNTIME_KINDS,
    )
    latest_session_owner_runtime = get_latest_runtime_event_for_account(
        store,
        deployment_id,
        account_id=target_account_id,
        prefix="session_owner_",
    )
    latest_session_consumer_runtime = get_latest_runtime_event_for_account(
        store,
        deployment_id,
        account_id=target_account_id,
        prefix="session_consumer_",
    )
    latest_session_consumer_state_runtime = get_latest_runtime_event_for_account(
        store,
        deployment_id,
        account_id=target_account_id,
        kind="session_consumer_state",
    )
    latest_reconcile = store.get_latest_risk_event(
        deployment_id,
        event_name=account_reconcile_event_name,
    )
    latest_order_reconcile = store.get_latest_risk_event(
        deployment_id,
        event_name=order_reconcile_event_name,
    )
    latest_position_reconcile = store.get_latest_risk_event(
        deployment_id,
        event_name=position_reconcile_event_name,
    )
    latest_trade_reconcile = store.get_latest_risk_event(
        deployment_id,
        event_name=trade_reconcile_event_name,
    )
    latest_qmt_hard_gate = store.get_latest_risk_event(
        deployment_id,
        event_name=qmt_hard_gate_event_name,
    )
    latest_account_event = get_latest_account_event_for_account(
        store,
        deployment_id,
        account_id=target_account_id,
    )

    latest_total_asset = None
    if latest_account_event is not None and isinstance(latest_account_event.payload, dict):
        raw_total_asset = latest_account_event.payload.get("total_asset")
        if raw_total_asset is not None:
            latest_total_asset = float(raw_total_asset)

    submit_policy = build_qmt_real_submit_policy(spec.risk_params)
    runtime_real_submit_policy = submit_policy if spec.broker_type == "qmt" else None
    qmt_readiness_summary = build_qmt_readiness_summary(
        latest_session_runtime=latest_session_runtime,
        latest_session_consumer_runtime=latest_session_consumer_runtime,
        latest_session_consumer_state_runtime=latest_session_consumer_state_runtime,
        latest_reconcile=latest_reconcile,
        latest_order_reconcile=latest_order_reconcile,
        real_submit_policy=runtime_real_submit_policy,
    )
    qmt_readiness = qmt_readiness_summary.to_dict()

    qmt_submit_gate = build_qmt_submit_gate_decision(
        qmt_readiness_summary,
        policy=submit_policy,
        account_id=target_account_id or None,
        total_asset=latest_total_asset,
        initial_cash=float(spec.initial_cash),
        hard_gate=latest_qmt_hard_gate,
    ).to_dict()
    qmt_submit_gate["source"] = "runtime"

    qmt_release_gate = build_qmt_release_gate_decision(
        deployment_status=record.status,
        gate_verdict=parse_gate_verdict(record.gate_verdict),
        submit_gate=qmt_submit_gate,
    ).to_dict()
    qmt_release_gate["source"] = "runtime"

    broker_runtime_kind = None
    broker_runtime_status = None
    if latest_runtime_event is not None:
        runtime_payload = latest_runtime_event.payload or {}
        broker_runtime_kind = str(runtime_payload.get("runtime_kind", "") or "") or None
        nested_runtime_payload = runtime_payload.get("payload") or {}
        if isinstance(nested_runtime_payload, dict):
            broker_runtime_status = str(nested_runtime_payload.get("status", "") or "") or None

    broker_session_runtime_kind = None
    broker_session_runtime_status = None
    if latest_session_runtime is not None:
        session_payload = latest_session_runtime.payload or {}
        broker_session_runtime_kind = (
            str(session_payload.get("runtime_kind", "") or "") or None
        )
        nested_session_payload = session_payload.get("payload") or {}
        if isinstance(nested_session_payload, dict):
            broker_session_runtime_status = (
                str(nested_session_payload.get("status", "") or "") or None
            )

    latest_qmt_hard_gate_blockers = []
    if isinstance(latest_qmt_hard_gate, dict):
        latest_qmt_hard_gate_blockers = [
            str(value)
            for value in (latest_qmt_hard_gate.get("blockers") or [])
            if str(value)
        ]

    return {
        "deployment_id": deployment_id,
        "broker_type": "qmt",
        "target_account_id": target_account_id or None,
        "deployment_status": record.status,
        "projection_source": "runtime",
        "projection_ts": utcnow().isoformat(),
        "latest_broker_account": (
            latest_account_event.to_dict() if latest_account_event is not None else None
        ),
        "latest_runtime_event": (
            latest_runtime_event.to_dict() if latest_runtime_event is not None else None
        ),
        "latest_session_runtime": (
            latest_session_runtime.to_dict() if latest_session_runtime is not None else None
        ),
        "latest_session_owner_runtime": (
            latest_session_owner_runtime.to_dict()
            if latest_session_owner_runtime is not None
            else None
        ),
        "latest_session_consumer_runtime": (
            latest_session_consumer_runtime.to_dict()
            if latest_session_consumer_runtime is not None
            else None
        ),
        "latest_session_consumer_state_runtime": (
            latest_session_consumer_state_runtime.to_dict()
            if latest_session_consumer_state_runtime is not None
            else None
        ),
        "latest_callback_account_mode": qmt_readiness_summary.account_sync_mode,
        "latest_callback_account_freshness": qmt_readiness_summary.asset_callback_freshness,
        "latest_reconcile": latest_reconcile,
        "latest_order_reconcile": latest_order_reconcile,
        "latest_position_reconcile": latest_position_reconcile,
        "latest_trade_reconcile": latest_trade_reconcile,
        "latest_qmt_hard_gate": latest_qmt_hard_gate,
        "broker_reconcile_status": (
            str(latest_reconcile.get("status", "") or "") or None
            if isinstance(latest_reconcile, dict)
            else None
        ),
        "broker_order_reconcile_status": (
            str(latest_order_reconcile.get("status", "") or "") or None
            if isinstance(latest_order_reconcile, dict)
            else None
        ),
        "position_reconcile_status": (
            str(latest_position_reconcile.get("status", "") or "") or None
            if isinstance(latest_position_reconcile, dict)
            else None
        ),
        "trade_reconcile_status": (
            str(latest_trade_reconcile.get("status", "") or "") or None
            if isinstance(latest_trade_reconcile, dict)
            else None
        ),
        "broker_runtime_kind": broker_runtime_kind,
        "broker_runtime_status": broker_runtime_status,
        "broker_session_runtime_kind": broker_session_runtime_kind,
        "broker_session_runtime_status": broker_session_runtime_status,
        "qmt_hard_gate_status": (
            str(latest_qmt_hard_gate.get("status", "") or "") or None
            if isinstance(latest_qmt_hard_gate, dict)
            else None
        ),
        "qmt_hard_gate_blockers": latest_qmt_hard_gate_blockers,
        "qmt_readiness": qmt_readiness,
        "qmt_submit_gate": qmt_submit_gate,
        "qmt_release_gate": qmt_release_gate,
    }


def persist_qmt_runtime_projection(
    store: DeploymentStore,
    *,
    deployment_id: str,
    record,
    spec: DeploymentSpec | None,
) -> dict | None:
    """Build and persist a QMT runtime projection, returning it."""
    projection = build_qmt_runtime_projection(
        store,
        deployment_id=deployment_id,
        record=record,
        spec=spec,
    )
    if projection is not None:
        store.upsert_broker_state_projection(
            deployment_id,
            broker_type="qmt",
            projection=projection,
        )
    return projection
