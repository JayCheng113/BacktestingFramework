"""Snapshot serializers and pure data-collection helpers extracted from Scheduler.

All functions are stateless — they take explicit parameters and return results.
Scheduler calls them as free functions instead of self-methods.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ez.live.broker import BrokerAccountSnapshot, BrokerRuntimeEvent
from ez.live.deployment_spec import DeploymentSpec
from ez.live.events import (
    DeploymentEvent,
    EventType,
    make_event_id,
    make_risk_event,
    utcnow,
)
from ez.live.ledger import LiveLedger
from ez.live.paper_engine import PaperTradingEngine


# ------------------------------------------------------------------
# Position / trade reconcile serializers
# ------------------------------------------------------------------

def serialize_position_reconcile(
    *,
    business_date: date,
    broker_type: str,
    event_name: str,
    result,
    account_id: str = "",
) -> dict[str, Any]:
    """Serialize a position-reconcile result into a risk-event dict."""
    payload: dict[str, Any] = {
        "date": str(business_date),
        "event": event_name,
        "status": result.status,
        "broker_type": result.broker_type or broker_type,
        "message": result.message,
        "details": {
            "compared_at": result.compared_at.isoformat(),
            "position_drifts": [
                {
                    "symbol": drift.symbol,
                    "local_shares": drift.local_shares,
                    "broker_shares": drift.broker_shares,
                    "share_delta": drift.share_delta,
                }
                for drift in result.position_drifts
            ],
        },
    }
    if account_id:
        payload["account_id"] = account_id
    return payload


def serialize_trade_reconcile(
    *,
    business_date: date,
    broker_type: str,
    event_name: str,
    result,
    account_id: str = "",
) -> dict[str, Any]:
    """Serialize a trade-reconcile result into a risk-event dict."""
    payload: dict[str, Any] = {
        "date": str(business_date),
        "event": event_name,
        "status": result.status,
        "broker_type": result.broker_type or broker_type,
        "message": result.message,
        "details": {
            "compared_at": result.compared_at.isoformat(),
            "business_date": result.business_date.isoformat(),
            "broker_trade_count": result.broker_trade_count,
            "local_trade_count": result.local_trade_count,
            "trade_drifts": [
                {
                    "broker_trade_id": drift.broker_trade_id,
                    "symbol": drift.symbol,
                    "side": drift.side,
                    "broker_volume": drift.broker_volume,
                    "local_volume": drift.local_volume,
                    "volume_delta": drift.volume_delta,
                    "reason": drift.reason,
                }
                for drift in result.trade_drifts
            ],
        },
    }
    if account_id:
        payload["account_id"] = account_id
    return payload


# ------------------------------------------------------------------
# Broker snapshot → normalized dicts
# ------------------------------------------------------------------

def broker_positions_from_snapshot(
    snapshot: BrokerAccountSnapshot | None,
) -> list[dict[str, Any]]:
    """Normalize a broker-account snapshot's positions into reconcile-ready dicts."""
    if snapshot is None:
        return []
    return [
        {
            "symbol": str(symbol or ""),
            "volume": int(volume or 0),
            "can_use_volume": int(volume or 0),
            "frozen_volume": 0,
            "on_road_volume": 0,
        }
        for symbol, volume in (snapshot.positions or {}).items()
        if str(symbol or "")
    ]


def broker_trades_from_snapshot(
    snapshot: BrokerAccountSnapshot | None,
) -> list[dict[str, Any]]:
    """Normalize a broker-account snapshot's fills into reconcile-ready dicts."""
    if snapshot is None:
        return []
    normalized: list[dict[str, Any]] = []
    for fill in snapshot.fills or []:
        if not isinstance(fill, dict):
            continue
        normalized.append(
            {
                "traded_id": str(
                    fill.get("traded_id")
                    or fill.get("broker_trade_id")
                    or fill.get("broker_order_id")
                    or ""
                ),
                "symbol": str(fill.get("symbol") or ""),
                "side": str(fill.get("side") or ""),
                "shares": int(fill.get("shares", 0) or 0),
                "price": float(fill.get("price", 0.0) or 0.0),
            }
        )
    return normalized


def local_trades_from_engine(
    engine: PaperTradingEngine,
    *,
    business_date: date,
) -> list[dict[str, Any]]:
    """Return local fills for the given business date from engine state.

    Engines record fills in ``engine.trades`` via
    ``ledger.trades`` (``{symbol, side, shares, price, cost, amount}``).
    Callers do not always carry a per-trade date, so we simply return
    the full in-memory trade list; the reconcile step aggregates by
    ``(symbol, side)`` and both sides of the reconcile are scoped to
    the same tick / business-date.
    """
    trades = list(getattr(engine, "trades", []) or [])
    return [trade for trade in trades if isinstance(trade, dict)]


# ------------------------------------------------------------------
# Risk-event builders
# ------------------------------------------------------------------

def build_shadow_risk_events(
    *,
    deployment_id: str,
    business_date: date,
    account_reconcile: dict | None,
    order_reconcile: dict | None,
    position_reconcile: dict | None = None,
    trade_reconcile: dict | None = None,
) -> list[DeploymentEvent]:
    """Build RISK_RECORDED events from reconcile dicts."""
    risk_events = [
        risk_event
        for risk_event in (
            account_reconcile,
            order_reconcile,
            position_reconcile,
            trade_reconcile,
        )
        if risk_event is not None
    ]
    return [
        make_risk_event(
            deployment_id=deployment_id,
            business_date=business_date,
            risk_index=index,
            risk_event=risk_event,
        )
        for index, risk_event in enumerate(risk_events)
    ]


def build_runtime_reconcile_event(
    *,
    deployment_id: str,
    business_date: date,
    risk_event: dict[str, Any],
) -> DeploymentEvent:
    """Build a single runtime-reconcile RISK_RECORDED event."""
    event_name = str(risk_event.get("event", "") or "risk_event")
    broker_type = str(risk_event.get("broker_type", "") or "unknown")
    account_id = str(risk_event.get("account_id", "") or "unknown")
    details = risk_event.get("details")
    compared_at = ""
    if isinstance(details, dict):
        compared_at = str(details.get("compared_at", "") or "")
    if not compared_at:
        compared_at = str(risk_event.get("date", "") or business_date.isoformat())
    client_order_id = (
        f"{deployment_id}:{business_date.isoformat()}:runtime-risk:"
        f"{event_name}:{broker_type}:{account_id}:{compared_at}"
    )
    return DeploymentEvent(
        event_id=make_event_id(client_order_id, EventType.RISK_RECORDED),
        deployment_id=deployment_id,
        event_type=EventType.RISK_RECORDED,
        event_ts=utcnow(),
        client_order_id=client_order_id,
        payload={
            "business_date": business_date.isoformat(),
            "risk_event": dict(risk_event),
        },
    )


# ------------------------------------------------------------------
# Event sequencer
# ------------------------------------------------------------------

def sequence_runtime_events(
    *,
    pre_events: list[DeploymentEvent],
    oms_events: list[DeploymentEvent],
    post_events: list[DeploymentEvent],
) -> list[DeploymentEvent]:
    """Force runtime backbone events into a deterministic logical timeline.

    Market-data events should precede OMS order/fill events, while risk,
    snapshot, and tick-completed events should follow them. OMS events
    already carry their own event_ts from execution; scheduler-generated
    runtime events need to be anchored around those timestamps so replay
    order matches the logical execution order.
    """
    step = timedelta(microseconds=1)
    ordered_oms = sorted(oms_events, key=LiveLedger._event_sort_key)
    anchored_post_events = [
        event
        for event in post_events
        if event.event_type in {
            EventType.BROKER_ACCOUNT_RECORDED,
            EventType.BROKER_EXECUTION_RECORDED,
            EventType.BROKER_RUNTIME_RECORDED,
        }
    ]
    sequenced_post_events = [
        event
        for event in post_events
        if event.event_type not in {
            EventType.BROKER_ACCOUNT_RECORDED,
            EventType.BROKER_EXECUTION_RECORDED,
            EventType.BROKER_RUNTIME_RECORDED,
        }
    ]
    ordered_anchored_post = sorted(
        anchored_post_events,
        key=LiveLedger._event_sort_key,
    )
    if ordered_oms:
        earliest = ordered_oms[0].event_ts
        latest = ordered_oms[-1].event_ts
    else:
        earliest = latest = utcnow()

    for index, event in enumerate(pre_events):
        event.event_ts = earliest - step * (len(pre_events) - index)
    if ordered_anchored_post:
        latest = max(latest, ordered_anchored_post[-1].event_ts)
    for index, event in enumerate(sequenced_post_events):
        event.event_ts = latest + step * (index + 1)

    return [*pre_events, *ordered_oms, *ordered_anchored_post, *sequenced_post_events]


# ------------------------------------------------------------------
# Runtime event filters (for broker-runtime event lists)
# ------------------------------------------------------------------

def latest_runtime_event_by_kinds(
    runtime_events: list[BrokerRuntimeEvent],
    *,
    kinds: set[str],
) -> BrokerRuntimeEvent | None:
    """Return the first event whose kind is in *kinds*."""
    for event in runtime_events:
        if event.event_kind in kinds:
            return event
    return None


def latest_runtime_event_by_prefix(
    runtime_events: list[BrokerRuntimeEvent],
    *,
    prefix: str,
) -> BrokerRuntimeEvent | None:
    """Return the first event whose kind starts with *prefix*."""
    for event in runtime_events:
        if event.event_kind.startswith(prefix):
            return event
    return None


def latest_runtime_event_by_kind(
    runtime_events: list[BrokerRuntimeEvent],
    *,
    kind: str,
) -> BrokerRuntimeEvent | None:
    """Return the first event with exactly the given *kind*."""
    for event in runtime_events:
        if event.event_kind == kind:
            return event
    return None


# ------------------------------------------------------------------
# Market-rule mismatch detector
# ------------------------------------------------------------------

def historical_non_cn_market_rule_mismatches(spec: DeploymentSpec) -> list[str]:
    """Detect pre-V2.16.2 specs that accidentally carried CN market rules."""
    if spec.market == "cn_stock":
        return []

    mismatches = []
    if spec.t_plus_1:
        mismatches.append("t_plus_1=True")
    if spec.stamp_tax_rate > 0:
        mismatches.append(f"stamp_tax_rate={spec.stamp_tax_rate}")
    if spec.price_limit_pct > 0:
        mismatches.append(f"price_limit_pct={spec.price_limit_pct}")
    if spec.lot_size > 1:
        mismatches.append(f"lot_size={spec.lot_size}")
    return mismatches
