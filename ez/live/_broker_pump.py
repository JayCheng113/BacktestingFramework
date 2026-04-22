"""Broker-pump stateless helpers extracted from Scheduler.

All functions are stateless — they take explicit parameters and return results.
Scheduler calls them as free functions instead of self-methods.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ez.live.broker import (
    BrokerAccountSnapshot,
    BrokerExecutionReport,
    BrokerRuntimeEvent,
)
from ez.live._snapshot_collectors import (
    build_shadow_risk_events,
    sequence_runtime_events,
)
from ez.live.events import (
    DeploymentEvent,
    EventType,
    make_broker_account_event,
    make_broker_execution_event,
    make_broker_runtime_event,
    make_risk_event,
    utcnow,
)


# ------------------------------------------------------------------
# Business-date derivation
# ------------------------------------------------------------------

def derive_shadow_business_date(
    *,
    snapshot: BrokerAccountSnapshot | None,
    broker_reports: list[BrokerExecutionReport],
    broker_runtime_events: list[BrokerRuntimeEvent],
) -> date:
    """Derive the shadow-broker business date from the latest available timestamp."""
    if snapshot is not None:
        return snapshot.as_of.date()
    candidates = [
        *[report.as_of for report in broker_reports],
        *[event.as_of for event in broker_runtime_events],
    ]
    if candidates:
        return max(candidates).date()
    return utcnow().date()


# ------------------------------------------------------------------
# Shadow sync event builder
# ------------------------------------------------------------------

def build_shadow_sync_events(
    *,
    deployment_id: str,
    business_date: date,
    snapshot: BrokerAccountSnapshot | None,
    broker_reports: list[BrokerExecutionReport],
    broker_runtime_events: list[BrokerRuntimeEvent],
    account_reconcile: dict | None,
    order_reconcile: dict | None,
    hard_gate: dict | None = None,
    position_reconcile: dict | None = None,
    trade_reconcile: dict | None = None,
) -> list[DeploymentEvent]:
    """Build the full list of deployment events from a shadow-broker sync cycle."""
    post_events: list[DeploymentEvent] = []
    if snapshot is not None:
        post_events.append(
            make_broker_account_event(
                deployment_id=deployment_id,
                broker_type=snapshot.broker_type,
                account_ts=snapshot.as_of,
                account_id=str(getattr(snapshot, "account_id", "") or ""),
                cash=snapshot.cash,
                total_asset=snapshot.total_asset,
                positions=snapshot.positions,
                open_orders=snapshot.open_orders,
                fill_count=len(snapshot.fills),
            )
        )
    post_events.extend(
        build_shadow_runtime_events(deployment_id, broker_runtime_events)
    )
    post_events.extend(
        build_shadow_execution_report_events(deployment_id, broker_reports)
    )
    post_events.extend(
        build_shadow_risk_events(
            deployment_id=deployment_id,
            business_date=business_date,
            account_reconcile=account_reconcile,
            order_reconcile=order_reconcile,
            position_reconcile=position_reconcile,
            trade_reconcile=trade_reconcile,
        )
    )
    if hard_gate is not None:
        hard_gate_index = sum(
            1 for event in post_events if event.event_type == EventType.RISK_RECORDED
        )
        post_events.append(
            make_risk_event(
                deployment_id=deployment_id,
                business_date=business_date,
                risk_index=hard_gate_index,
                risk_event=hard_gate,
            )
        )
    return sequence_runtime_events(
        pre_events=[],
        oms_events=[],
        post_events=post_events,
    )


# ------------------------------------------------------------------
# Execution-report and runtime-event builders
# ------------------------------------------------------------------

def build_shadow_execution_report_events(
    deployment_id: str,
    reports: list,
) -> list[DeploymentEvent]:
    """Convert broker execution reports into BROKER_EXECUTION_RECORDED events."""
    if not reports:
        return []
    return [
        make_broker_execution_event(
            deployment_id,
            report_id=report.report_id,
            broker_type=report.broker_type,
            report_ts=report.as_of,
            client_order_id=report.client_order_id,
            broker_order_id=report.broker_order_id,
            symbol=report.symbol,
            side=report.side,
            status=report.status,
            filled_shares=report.filled_shares,
            remaining_shares=report.remaining_shares,
            avg_price=report.avg_price,
            message=report.message,
            raw_payload=report.raw_payload,
            account_id=str(getattr(report, "account_id", "") or ""),
        )
        for report in reports
    ]


def build_shadow_runtime_events(
    deployment_id: str,
    runtime_events: list[BrokerRuntimeEvent],
) -> list[DeploymentEvent]:
    """Convert broker runtime events into BROKER_RUNTIME_RECORDED events."""
    if not runtime_events:
        return []
    return [
        make_broker_runtime_event(
            deployment_id,
            runtime_event_id=runtime_event.event_id,
            broker_type=runtime_event.broker_type,
            runtime_kind=runtime_event.event_kind,
            event_ts=runtime_event.as_of,
            payload=runtime_event.payload,
        )
        for runtime_event in runtime_events
    ]
