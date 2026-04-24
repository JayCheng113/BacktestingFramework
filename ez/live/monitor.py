"""V2.15 B2: Monitor — deployment health dashboard and alert checks.

Provides:
- DeploymentHealth dataclass: per-deployment health summary
- Monitor class: get_dashboard() + check_alerts()

Metric computation mirrors ez/backtest/metrics.py conventions (ddof=1, rf=0.03).
No network or strategy calls; pure DB reads + arithmetic.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np

from ez.live.deployment_store import DeploymentStore
from ez.live.events import EventType
from ez.live.events import broker_order_status_is_terminal, normalize_broker_order_status
from ez.live.qmt_broker import (
    build_qmt_real_submit_policy,
    build_qmt_readiness_summary,
    build_qmt_release_gate_decision,
    build_qmt_submit_gate_decision,
)

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

# Risk-free rate used for Sharpe (annualised); matches MetricsCalculator default
_RF = 0.03
_TRADING_DAYS = 252


def _coerce_runtime_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _runtime_payload(event: Any) -> dict[str, Any]:
    if event is None:
        return {}
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    if isinstance(payload, dict):
        nested = payload.get("payload")
        if isinstance(nested, dict):
            return nested
        return payload
    return {}


def _get_latest_risk_event_with_snapshot_fallback(
    store: DeploymentStore,
    deployment_id: str,
    *,
    event_name: str,
    snapshots: list[dict] | None = None,
) -> dict[str, Any] | None:
    latest = store.get_latest_risk_event(
        deployment_id,
        event_name=event_name,
    )
    if latest is not None:
        return latest
    for snapshot in reversed(list(snapshots or [])):
        for risk_event in reversed(list(snapshot.get("risk_events") or [])):
            if isinstance(risk_event, dict) and risk_event.get("event") == event_name:
                return risk_event
    return None


def _order_identity_candidates(payload: dict[str, Any] | None, client_order_id: str) -> set[str]:
    candidates: set[str] = set()
    if client_order_id:
        candidates.add(str(client_order_id))
    if not isinstance(payload, dict):
        return candidates
    for key in (
        "client_order_id",
        "order_remark",
        "broker_order_id",
        "order_sysid",
        "order_id",
    ):
        value = str(payload.get(key, "") or "")
        if value:
            candidates.add(value)
    return candidates


def _event_matches_link(event: Any, link: dict[str, Any]) -> bool:
    event_payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    event_broker_type = (
        str(event_payload.get("broker_type", "") or "")
        if isinstance(event_payload, dict)
        else ""
    )
    link_broker_type = str(link.get("broker_type", "") or "")
    if event_broker_type and link_broker_type and event_broker_type != link_broker_type:
        return False
    payload = _runtime_payload(event)
    event_candidates = _order_identity_candidates(
        payload,
        str(getattr(event, "client_order_id", "") or ""),
    )
    link_candidates = _order_identity_candidates(
        {
            "client_order_id": link.get("client_order_id", ""),
            "broker_order_id": link.get("broker_order_id", ""),
        },
        "",
    )
    return bool(event_candidates & link_candidates)


def _cancel_error_message(event: Any) -> str:
    payload = _runtime_payload(event)
    if not isinstance(payload, dict):
        return ""
    for key in ("status_msg", "error_msg"):
        value = str(payload.get(key, "") or "")
        if value:
            return value
    cancel_result = payload.get("cancel_result")
    if cancel_result not in {"", None, 0, "0"}:
        return f"cancel_result={cancel_result}"
    return ""


def _project_broker_order_link(
    link: dict[str, Any],
    events: list[Any],
) -> dict[str, Any]:
    projected = dict(link)
    projected.pop("account_id", None)
    latest_status = normalize_broker_order_status(
        str(projected.get("latest_status", "") or ""),
    )
    projected["latest_status"] = latest_status

    latest_report_ts = projected.get("last_report_ts")
    latest_cancel_request_ts = None
    latest_cancel_error_ts = None
    latest_cancel_error_message = ""

    for event in events:
        if not _event_matches_link(event, projected):
            continue
        event_ts = _runtime_event_ts(event)
        if event_ts is None:
            continue
        event_type = getattr(event, "event_type", None)
        if event_type == EventType.BROKER_CANCEL_REQUESTED:
            if latest_cancel_request_ts is None or event_ts >= latest_cancel_request_ts:
                latest_cancel_request_ts = event_ts
        elif event_type == EventType.BROKER_RUNTIME_RECORDED:
            runtime_kind = _runtime_kind(event)
            if runtime_kind == "cancel_error":
                if latest_cancel_error_ts is None or event_ts >= latest_cancel_error_ts:
                    latest_cancel_error_ts = event_ts
                    latest_cancel_error_message = _cancel_error_message(event)
            elif runtime_kind == "cancel_order_stock_async_response":
                payload = _runtime_payload(event)
                cancel_result = payload.get("cancel_result")
                error_msg = str(payload.get("error_msg", "") or payload.get("status_msg", "") or "")
                if cancel_result not in {"", None, 0, "0"} or error_msg:
                    if latest_cancel_error_ts is None or event_ts >= latest_cancel_error_ts:
                        latest_cancel_error_ts = event_ts
                        latest_cancel_error_message = error_msg or _cancel_error_message(event)

    cancel_state = "none"
    if latest_status in {"canceled", "partially_canceled"}:
        cancel_state = "canceled"
    elif latest_status == "cancel_error":
        cancel_state = "cancel_error"
    elif latest_status in {"reported_cancel_pending", "partially_filled_cancel_pending"}:
        cancel_state = "cancel_inflight"
    elif latest_cancel_error_ts is not None and (
        latest_cancel_request_ts is None
        or latest_cancel_error_ts >= latest_cancel_request_ts
    ):
        cancel_state = "cancel_error"
    elif latest_cancel_request_ts is not None and (
        latest_report_ts is None
        or latest_cancel_request_ts >= latest_report_ts
    ):
        cancel_state = "cancel_inflight"

    projected["cancel_state"] = cancel_state
    projected["cancel_requested_at"] = latest_cancel_request_ts
    projected["cancel_error_at"] = latest_cancel_error_ts
    projected["cancel_error_message"] = latest_cancel_error_message
    return projected


def build_persisted_broker_order_view(
    store: DeploymentStore,
    deployment_id: str,
    *,
    broker_type: str | None = None,
) -> list[dict[str, Any]]:
    """Project broker-order links from persisted links + event log only."""
    links = store.list_broker_order_links(deployment_id, broker_type=broker_type)
    if not links:
        return []
    events = store.get_events(deployment_id)
    return [
        _project_broker_order_link(link, events)
        for link in links
    ]


def resolve_qmt_runtime_projection(
    store: DeploymentStore,
    deployment_id: str,
    *,
    deployment_status: str | None = None,
) -> dict[str, Any] | None:
    projection = store.get_broker_state_projection(
        deployment_id,
        broker_type="qmt",
    )
    if not isinstance(projection, dict):
        return None
    if deployment_status is not None:
        projected_status = str(projection.get("deployment_status", "") or "")
        if projected_status and projected_status != deployment_status:
            return None
    projection_ts = _coerce_runtime_timestamp(projection.get("projection_ts"))
    if projection_ts is None:
        return None
    latest_relevant_ts = max(
        (
            ts
            for ts in (
                store.get_latest_event_ts(
                    deployment_id,
                    event_type=EventType.BROKER_ACCOUNT_RECORDED,
                ),
                store.get_latest_event_ts(
                    deployment_id,
                    event_type=EventType.BROKER_RUNTIME_RECORDED,
                ),
                store.get_latest_event_ts(
                    deployment_id,
                    event_type=EventType.RISK_RECORDED,
                ),
            )
            if ts is not None
        ),
        default=None,
    )
    # Fail closed on timestamp ties too: callback/runtime/risk events can share a
    # broker-side timestamp with the stored projection, and in that case the
    # append-only truth should still win over the cached projection.
    if latest_relevant_ts is not None and latest_relevant_ts >= projection_ts:
        return None
    return projection


def _runtime_event_ts(event: Any) -> datetime | None:
    if event is None:
        return None
    event_ts = event.get("event_ts") if isinstance(event, dict) else getattr(event, "event_ts", None)
    if event_ts is None:
        event_ts = _runtime_payload(event).get("update_time")
    return _coerce_runtime_timestamp(event_ts)


def _runtime_kind(event: Any) -> str | None:
    if event is None:
        return None
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    if not isinstance(payload, dict):
        return None
    return str(payload.get("runtime_kind", "") or "") or None


def _resolve_callback_health(
    *,
    latest_session_runtime: Any,
    latest_session_owner_runtime: Any,
    latest_session_consumer_runtime: Any,
    latest_session_consumer_state_runtime: Any,
) -> tuple[str | None, str | None]:
    account_sync_mode = None
    asset_callback_freshness = None
    if latest_session_consumer_state_runtime is not None:
        runtime_payload = _runtime_payload(latest_session_consumer_state_runtime)
        account_sync_mode = (
            str(runtime_payload.get("account_sync_mode", "") or "") or None
        )
        asset_callback_freshness = (
            str(runtime_payload.get("asset_callback_freshness", "") or "") or None
        )
    elif latest_session_consumer_runtime is not None:
        account_sync_mode = "query_fallback"
        asset_callback_freshness = "unavailable"

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
    return account_sync_mode, asset_callback_freshness


def _resolve_qmt_account_id(spec: Any) -> str | None:
    if spec is None or not isinstance(getattr(spec, "risk_params", None), dict):
        return None
    risk_params = spec.risk_params
    prefer_real = getattr(spec, "broker_type", "") == "qmt"
    config_keys = (
        ("qmt_real_broker_config", "shadow_broker_config")
        if prefer_real
        else ("shadow_broker_config", "qmt_real_broker_config")
    )
    for key in config_keys:
        raw_cfg = risk_params.get(key)
        if isinstance(raw_cfg, dict) and raw_cfg.get("account_id"):
            return str(raw_cfg.get("account_id", "") or "") or None
    return None


# ---------------------------------------------------------------------------
# DeploymentHealth
# ---------------------------------------------------------------------------

@dataclass
class DeploymentHealth:
    deployment_id: str
    name: str
    status: str  # running / paused / stopped / error

    # Performance
    cumulative_return: float
    max_drawdown: float
    sharpe_ratio: float | None

    # Today
    today_pnl: float
    today_trades: int

    # Risk
    risk_events_today: int
    total_risk_events: int
    consecutive_loss_days: int

    # System
    last_execution_date: date | None
    last_execution_duration_ms: float
    days_since_last_trade: int
    error_count: int
    broker_reconcile_status: str | None = None
    broker_order_reconcile_status: str | None = None
    broker_runtime_kind: str | None = None
    broker_runtime_status: str | None = None
    broker_session_runtime_kind: str | None = None
    broker_session_runtime_status: str | None = None
    broker_account_sync_mode: str | None = None
    broker_asset_callback_freshness: str | None = None
    qmt_hard_gate_status: str | None = None
    qmt_hard_gate_blockers: list[str] = field(default_factory=list)
    qmt_release_gate_status: str | None = None
    qmt_release_candidate: bool | None = None
    qmt_release_blockers: list[str] = field(default_factory=list)
    qmt_projection_source: str | None = None
    qmt_projection_ts: str | None = None
    qmt_target_account_id: str | None = None


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class Monitor:
    """Read-only view over DeploymentStore — health dashboard + alert checks."""

    # Default alert thresholds (can be overridden per-instance)
    DEFAULT_MAX_DRAWDOWN_THRESHOLD: float = 0.25
    DEFAULT_MAX_EXECUTION_DURATION_MS: float = 60_000.0  # 60 s
    DEFAULT_MAX_CONSECUTIVE_ERRORS: int = 3
    DEFAULT_MAX_CONSECUTIVE_LOSS_DAYS: int = 5
    DEFAULT_MAX_DAYS_SINCE_LAST_TRADE: int = 30

    def __init__(self, store: DeploymentStore):
        self.store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_dashboard(self) -> list[DeploymentHealth]:
        """Return health summary for all active deployments (running + paused + error)."""
        active_statuses = ("running", "paused", "error")
        records = [
            r for r in self.store.list_deployments()
            if r.status in active_statuses
        ]
        return [self._build_health(r) for r in records]

    def check_alerts(self) -> list[dict[str, Any]]:
        """Check 5 alert conditions across all active deployments.

        Alert types:
        1. consecutive_loss_days  — streak > DEFAULT_MAX_CONSECUTIVE_LOSS_DAYS
        2. high_drawdown          — max_drawdown < -threshold (i.e. worse than threshold)
        3. slow_execution         — last_execution_duration_ms > DEFAULT_MAX_EXECUTION_DURATION_MS
        4. consecutive_errors     — error_count > DEFAULT_MAX_CONSECUTIVE_ERRORS
        5. inactivity             — days_since_last_trade > DEFAULT_MAX_DAYS_SINCE_LAST_TRADE
        6. broker_account_drift   — latest broker_reconcile is drift/error/unavailable
        7. broker_order_drift     — latest broker_order_reconcile is drift/error/unavailable
        8. broker_session_disconnected — latest broker runtime status indicates disconnect
        9. broker_session_unhealthy — latest broker runtime indicates consumer/session failure
        10. broker_callback_degraded — callback account-state path is stale/unavailable and query fallback is active
        11. qmt_release_gate_blocked — qmt deployment passed deploy gate but is not yet a release candidate

        Returns a list of {deployment_id, alert_type, message} dicts.
        """
        dashboard = self.get_dashboard()
        alerts: list[dict[str, Any]] = []

        for h in dashboard:
            # 1. Consecutive loss days
            if h.consecutive_loss_days > self.DEFAULT_MAX_CONSECUTIVE_LOSS_DAYS:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "consecutive_loss_days",
                    "message": (
                        f"{h.name}: {h.consecutive_loss_days} consecutive loss days "
                        f"(threshold {self.DEFAULT_MAX_CONSECUTIVE_LOSS_DAYS})"
                    ),
                })

            # 2. High drawdown (max_drawdown is negative, e.g. -0.30)
            if h.max_drawdown < -self.DEFAULT_MAX_DRAWDOWN_THRESHOLD:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "high_drawdown",
                    "message": (
                        f"{h.name}: max drawdown {h.max_drawdown:.1%} exceeds "
                        f"threshold -{self.DEFAULT_MAX_DRAWDOWN_THRESHOLD:.0%}"
                    ),
                })

            # 3. Slow execution
            if h.last_execution_duration_ms > self.DEFAULT_MAX_EXECUTION_DURATION_MS:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "slow_execution",
                    "message": (
                        f"{h.name}: last execution took "
                        f"{h.last_execution_duration_ms / 1000:.1f}s "
                        f"(threshold {self.DEFAULT_MAX_EXECUTION_DURATION_MS / 1000:.0f}s)"
                    ),
                })

            # 4. Consecutive errors (alert at 2, scheduler escalates to error at 3)
            if h.error_count >= self.DEFAULT_MAX_CONSECUTIVE_ERRORS - 1:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "consecutive_errors",
                    "message": (
                        f"{h.name}: {h.error_count} consecutive errors "
                        f"(threshold {self.DEFAULT_MAX_CONSECUTIVE_ERRORS})"
                    ),
                })

            # 5. Inactivity
            if h.days_since_last_trade > self.DEFAULT_MAX_DAYS_SINCE_LAST_TRADE:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "inactivity",
                    "message": (
                        f"{h.name}: {h.days_since_last_trade} days since last trade "
                        f"(threshold {self.DEFAULT_MAX_DAYS_SINCE_LAST_TRADE})"
                    ),
                })

            if h.broker_reconcile_status in {"drift", "error", "unavailable"}:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "broker_account_drift",
                    "message": (
                        f"{h.name}: broker account reconcile status is "
                        f"{h.broker_reconcile_status}"
                    ),
                })

            if h.broker_order_reconcile_status in {"drift", "error", "unavailable"}:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "broker_order_drift",
                    "message": (
                        f"{h.name}: broker order reconcile status is "
                        f"{h.broker_order_reconcile_status}"
                    ),
                })

            session_runtime_kind = h.broker_session_runtime_kind or h.broker_runtime_kind
            session_runtime_status = (
                h.broker_session_runtime_status or h.broker_runtime_status
            )

            if (
                session_runtime_kind == "disconnected"
                or session_runtime_status == "disconnected"
            ):
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "broker_session_disconnected",
                    "message": (
                        f"{h.name}: broker runtime indicates disconnected session"
                    ),
                })

            if session_runtime_kind in {
                "session_connect_failed",
                "session_subscribe_failed",
            } or h.broker_runtime_kind in {
                "session_consumer_failed",
                "session_consumer_restart_failed",
                "session_owner_close_failed",
            }:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "broker_session_unhealthy",
                    "message": (
                        f"{h.name}: broker runtime indicates unhealthy session "
                        f"({h.broker_runtime_kind or session_runtime_kind})"
                    ),
                })

            if (
                h.broker_account_sync_mode is not None
                and (
                    h.broker_account_sync_mode == "query_fallback"
                    or h.broker_asset_callback_freshness in {"stale", "unavailable"}
                )
            ):
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "broker_callback_degraded",
                    "message": (
                        f"{h.name}: broker callback account-state degraded "
                        f"(mode={h.broker_account_sync_mode}, "
                        f"freshness={h.broker_asset_callback_freshness})"
                    ),
                })

            actionable_qmt_blockers = [
                blocker
                for blocker in h.qmt_release_blockers
                if blocker != "qmt_submit_gate_shadow_only"
            ]
            if h.qmt_release_gate_status == "blocked" and actionable_qmt_blockers:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "qmt_release_gate_blocked",
                    "message": (
                        f"{h.name}: qmt release gate blocked "
                        f"({', '.join(actionable_qmt_blockers)})"
                    ),
                })

        return alerts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_health(self, record) -> DeploymentHealth:
        """Build a DeploymentHealth from a DeploymentRecord + its snapshots."""
        dep_id = record.deployment_id
        spec = self.store.get_spec(record.spec_id)
        snapshots = self.store.get_all_snapshots(dep_id)

        # --- Equity curve ---
        equity_curve = [float(s["equity"]) for s in snapshots if s.get("equity") is not None]

        # --- Cumulative return ---
        if len(equity_curve) >= 2 and equity_curve[0] > 0:
            cumulative_return = (equity_curve[-1] / equity_curve[0]) - 1.0
        elif len(equity_curve) == 1:
            cumulative_return = 0.0
        else:
            cumulative_return = 0.0

        # --- Max drawdown ---
        max_drawdown = _compute_max_drawdown(equity_curve)

        # --- Sharpe ratio ---
        sharpe_ratio = _compute_sharpe(equity_curve, rf=_RF, trading_days=_TRADING_DAYS)

        # --- Today's PnL (last equity - second-to-last equity) ---
        if len(equity_curve) >= 2:
            today_pnl = equity_curve[-1] - equity_curve[-2]
        else:
            today_pnl = 0.0

        # --- Today's trade count (last snapshot) ---
        today_trades = 0
        if snapshots:
            last_snap = snapshots[-1]
            trades = last_snap.get("trades") or []
            today_trades = len(trades)

        # --- Risk events ---
        risk_events_today = 0
        total_risk_events = 0
        if snapshots:
            for snap in snapshots:
                evts = snap.get("risk_events") or []
                total_risk_events += len(evts)
            last_snap = snapshots[-1]
            risk_events_today = len(last_snap.get("risk_events") or [])

        # --- Consecutive loss days ---
        consecutive_loss_days = _count_consecutive_loss_days(equity_curve)

        # --- Last execution date ---
        last_execution_date: date | None = None
        if snapshots:
            raw_date = snapshots[-1].get("snapshot_date")
            if raw_date is not None:
                if isinstance(raw_date, date):
                    last_execution_date = raw_date
                else:
                    try:
                        last_execution_date = date.fromisoformat(str(raw_date))
                    except (ValueError, TypeError):
                        last_execution_date = None

        # --- Last execution duration ---
        last_execution_duration_ms = 0.0
        if snapshots:
            raw_ms = snapshots[-1].get("execution_ms")
            if raw_ms is not None and not (isinstance(raw_ms, float) and math.isnan(raw_ms)):
                last_execution_duration_ms = float(raw_ms)

        # --- Days since last trade ---
        days_since_last_trade = _compute_days_since_last_trade(snapshots)

        # --- Consecutive errors ---
        error_count = self.store.get_error_count(dep_id)

        runtime_projection = resolve_qmt_runtime_projection(
            self.store,
            dep_id,
            deployment_status=record.status,
        )
        if isinstance(runtime_projection, dict):
            latest_reconcile = runtime_projection.get("latest_reconcile")
            latest_order_reconcile = runtime_projection.get("latest_order_reconcile")
            latest_qmt_hard_gate = runtime_projection.get("latest_qmt_hard_gate")
            broker_reconcile_status = runtime_projection.get("broker_reconcile_status")
            broker_order_reconcile_status = runtime_projection.get("broker_order_reconcile_status")
            broker_runtime_kind = runtime_projection.get("broker_runtime_kind")
            broker_runtime_status = runtime_projection.get("broker_runtime_status")
            broker_session_runtime_kind = runtime_projection.get("broker_session_runtime_kind")
            broker_session_runtime_status = runtime_projection.get("broker_session_runtime_status")
            broker_account_sync_mode = runtime_projection.get("latest_callback_account_mode")
            broker_asset_callback_freshness = runtime_projection.get(
                "latest_callback_account_freshness"
            )
            qmt_projection_source = str(runtime_projection.get("projection_source", "") or "") or None
            qmt_projection_ts = str(runtime_projection.get("projection_ts", "") or "") or None
            qmt_target_account_id = str(runtime_projection.get("target_account_id", "") or "") or None
        else:
            account_reconcile_event_name = (
                "real_broker_reconcile"
                if spec is not None and str(getattr(spec, "broker_type", "") or "").lower() == "qmt"
                else "broker_reconcile"
            )
            order_reconcile_event_name = (
                "real_broker_order_reconcile"
                if spec is not None and str(getattr(spec, "broker_type", "") or "").lower() == "qmt"
                else "broker_order_reconcile"
            )
            qmt_hard_gate_event_name = (
                "real_qmt_reconcile_hard_gate"
                if spec is not None and str(getattr(spec, "broker_type", "") or "").lower() == "qmt"
                else "qmt_reconcile_hard_gate"
            )
            latest_reconcile = _get_latest_risk_event_with_snapshot_fallback(
                self.store,
                dep_id,
                event_name=account_reconcile_event_name,
                snapshots=snapshots,
            )
            latest_order_reconcile = _get_latest_risk_event_with_snapshot_fallback(
                self.store,
                dep_id,
                event_name=order_reconcile_event_name,
                snapshots=snapshots,
            )
            latest_qmt_hard_gate = _get_latest_risk_event_with_snapshot_fallback(
                self.store,
                dep_id,
                event_name=qmt_hard_gate_event_name,
                snapshots=snapshots,
            )
            broker_reconcile_status = (
                str(latest_reconcile.get("status", "") or "") or None
                if isinstance(latest_reconcile, dict)
                else None
            )
            broker_order_reconcile_status = (
                str(latest_order_reconcile.get("status", "") or "") or None
                if isinstance(latest_order_reconcile, dict)
                else None
            )

            latest_runtime_event = self.store.get_latest_event(
                dep_id,
                event_type=EventType.BROKER_RUNTIME_RECORDED,
            )
            broker_runtime_kind: str | None = None
            broker_runtime_status: str | None = None
            broker_session_runtime_kind: str | None = None
            broker_session_runtime_status: str | None = None
            broker_account_sync_mode: str | None = None
            broker_asset_callback_freshness: str | None = None
            if latest_runtime_event is not None:
                payload = latest_runtime_event.payload or {}
                broker_runtime_kind = str(payload.get("runtime_kind", "") or "") or None
                runtime_payload = payload.get("payload") or {}
                if isinstance(runtime_payload, dict):
                    broker_runtime_status = str(runtime_payload.get("status", "") or "") or None
            latest_consumer_state_runtime = self.store.get_latest_runtime_event(
                dep_id,
                kind="session_consumer_state",
            )
            latest_session_owner_runtime = self.store.get_latest_runtime_event(
                dep_id,
                prefix="session_owner_",
            )
            latest_session_consumer_runtime = self.store.get_latest_runtime_event(
                dep_id,
                prefix="session_consumer_",
            )
            (
                broker_account_sync_mode,
                broker_asset_callback_freshness,
            ) = _resolve_callback_health(
                latest_session_runtime=latest_runtime_event,
                latest_session_owner_runtime=latest_session_owner_runtime,
                latest_session_consumer_runtime=latest_session_consumer_runtime,
                latest_session_consumer_state_runtime=latest_consumer_state_runtime,
            )
            qmt_projection_source = None
            qmt_projection_ts = None
            qmt_target_account_id = None

        qmt_release_gate_status: str | None = None
        qmt_release_candidate: bool | None = None
        qmt_release_blockers: list[str] = []
        qmt_hard_gate_status: str | None = None
        qmt_hard_gate_blockers: list[str] = []
        is_qmt_related = bool(
            spec is not None
            and (spec.broker_type == "qmt" or spec.shadow_broker_type == "qmt")
        )
        if isinstance(latest_qmt_hard_gate, dict):
            qmt_hard_gate_status = (
                str(latest_qmt_hard_gate.get("status", "") or "") or None
            )
            qmt_hard_gate_blockers = [
                str(value)
                for value in (latest_qmt_hard_gate.get("blockers") or [])
                if str(value)
            ]
        if is_qmt_related and spec is not None:
            submit_gate = (
                runtime_projection.get("qmt_submit_gate")
                if isinstance(runtime_projection, dict)
                else None
            )
            latest_account_event = self.store.get_latest_event(
                dep_id,
                event_type=EventType.BROKER_ACCOUNT_RECORDED,
            )
            latest_total_asset = None
            if latest_account_event is not None and isinstance(latest_account_event.payload, dict):
                raw_total_asset = latest_account_event.payload.get("total_asset")
                if raw_total_asset is not None:
                    latest_total_asset = float(raw_total_asset)
            if submit_gate is None:
                latest_session_runtime = self.store.get_latest_runtime_event(
                    dep_id,
                    kinds=_SESSION_RUNTIME_KINDS,
                )
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
                latest_session_consumer_runtime = self.store.get_latest_runtime_event(
                    dep_id,
                    prefix="session_consumer_",
                )
                latest_session_consumer_state_runtime = self.store.get_latest_runtime_event(
                    dep_id,
                    kind="session_consumer_state",
                )
                submit_policy = build_qmt_real_submit_policy(spec.risk_params)
                account_id = _resolve_qmt_account_id(spec)
                submit_gate = build_qmt_submit_gate_decision(
                    build_qmt_readiness_summary(
                        latest_session_runtime=latest_session_runtime,
                        latest_session_consumer_runtime=latest_session_consumer_runtime,
                        latest_session_consumer_state_runtime=latest_session_consumer_state_runtime,
                        latest_reconcile=latest_reconcile,
                        latest_order_reconcile=latest_order_reconcile,
                        real_submit_policy=(
                            submit_policy if spec.broker_type == "qmt" else None
                        ),
                    ),
                    policy=submit_policy,
                    account_id=account_id,
                    total_asset=latest_total_asset,
                    initial_cash=float(spec.initial_cash),
                    hard_gate=latest_qmt_hard_gate,
                ).to_dict()
                submit_gate["source"] = "runtime"
            if qmt_target_account_id is None and isinstance(submit_gate, dict):
                qmt_target_account_id = str(submit_gate.get("account_id", "") or "") or None

            gate_verdict = None
            if record.gate_verdict:
                try:
                    parsed = json.loads(record.gate_verdict)
                    if isinstance(parsed, dict):
                        gate_verdict = parsed
                except json.JSONDecodeError:
                    gate_verdict = None

            release_gate = build_qmt_release_gate_decision(
                deployment_status=record.status,
                gate_verdict=gate_verdict,
                submit_gate=submit_gate,
            ).to_dict()
            qmt_release_gate_status = str(release_gate.get("status", "") or "") or None
            qmt_release_candidate = bool(release_gate.get("eligible_for_release_candidate"))
            qmt_release_blockers = [
                str(value) for value in (release_gate.get("blockers") or []) if str(value)
            ]

        return DeploymentHealth(
            deployment_id=dep_id,
            name=record.name,
            status=record.status,
            cumulative_return=cumulative_return,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            today_pnl=today_pnl,
            today_trades=today_trades,
            risk_events_today=risk_events_today,
            total_risk_events=total_risk_events,
            consecutive_loss_days=consecutive_loss_days,
            last_execution_date=last_execution_date,
            last_execution_duration_ms=last_execution_duration_ms,
            days_since_last_trade=days_since_last_trade,
            error_count=error_count,
            broker_reconcile_status=broker_reconcile_status,
            broker_order_reconcile_status=broker_order_reconcile_status,
            broker_runtime_kind=broker_runtime_kind,
            broker_runtime_status=broker_runtime_status,
            broker_session_runtime_kind=broker_session_runtime_kind,
            broker_session_runtime_status=broker_session_runtime_status,
            broker_account_sync_mode=broker_account_sync_mode,
            broker_asset_callback_freshness=broker_asset_callback_freshness,
            qmt_hard_gate_status=qmt_hard_gate_status,
            qmt_hard_gate_blockers=qmt_hard_gate_blockers,
            qmt_release_gate_status=qmt_release_gate_status,
            qmt_release_candidate=qmt_release_candidate,
            qmt_release_blockers=qmt_release_blockers,
            qmt_projection_source=qmt_projection_source,
            qmt_projection_ts=qmt_projection_ts,
            qmt_target_account_id=qmt_target_account_id,
        )


# ---------------------------------------------------------------------------
# Pure-function metric helpers (no store I/O)
# ---------------------------------------------------------------------------

def _compute_max_drawdown(equity_curve: list[float]) -> float:
    """Return max drawdown as a non-positive float (e.g. -0.20 = -20%).

    Returns 0.0 when there are fewer than 2 points.
    """
    if len(equity_curve) < 2:
        return 0.0
    arr = np.array(equity_curve, dtype=float)
    running_max = np.maximum.accumulate(arr)
    # Avoid divide-by-zero on zero peaks
    mask = running_max > 0
    drawdown = np.where(mask, (arr - running_max) / running_max, 0.0)
    return float(drawdown.min())


def _compute_sharpe(
    equity_curve: list[float],
    rf: float = _RF,
    trading_days: int = _TRADING_DAYS,
) -> float | None:
    """Annualised Sharpe ratio (excess returns, ddof=1).

    Returns None if fewer than 3 points (need at least 2 daily returns
    to compute std with ddof=1 without returning 0).
    Returns None on degenerate inputs (zero std, NaN).
    """
    if len(equity_curve) < 3:
        return None
    arr = np.array(equity_curve, dtype=float)
    daily_returns = np.diff(arr) / arr[:-1]
    daily_rf = rf / trading_days
    excess = daily_returns - daily_rf
    n = len(excess)
    if n < 2:
        return None
    std = float(np.std(excess, ddof=1))
    if std < 1e-10 or math.isnan(std):
        return None
    sharpe = float(np.mean(excess) / std * np.sqrt(trading_days))
    if math.isnan(sharpe) or math.isinf(sharpe):
        return None
    return sharpe


def _count_consecutive_loss_days(equity_curve: list[float]) -> int:
    """Count consecutive loss days from the END of the equity curve.

    A loss day is one where equity declined vs the previous point.
    Returns 0 when fewer than 2 points.
    """
    if len(equity_curve) < 2:
        return 0
    count = 0
    for i in range(len(equity_curve) - 1, 0, -1):
        if equity_curve[i] < equity_curve[i - 1]:
            count += 1
        else:
            break
    return count


def _compute_days_since_last_trade(snapshots: list[dict]) -> int:
    """Return the number of snapshots (trading days) since the last trade.

    Looks backwards from the most recent snapshot. If no trade has ever
    occurred, returns len(snapshots) as a conservative estimate.
    """
    if not snapshots:
        return 0
    for offset, snap in enumerate(reversed(snapshots)):
        trades = snap.get("trades") or []
        if len(trades) > 0:
            return offset
    # No trade found in any snapshot
    return len(snapshots)
