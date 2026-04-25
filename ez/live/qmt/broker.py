"""QMT shadow/read-only and explicit real-submit brokers aligned to official xtquant / XtTrader semantics.

This module intentionally avoids importing xtquant at import time. The current
stage of QMT integration keeps shadow sync separate from real-submit routing,
but the session bridge now follows the official runtime shape:

- XtQuantTrader(path, session_id)
- StockAccount(account_id[, account_type])
- register_callback -> start -> connect -> subscribe
- order_status values normalized from official xtconstant codes
- cancel_order_stock(_async) / cancel_order_stock_sysid(_async) exposed as optional paths
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from ez.live.broker import (
    BrokerAccountSnapshot,
    BrokerAdapter,
    BrokerCapability,
    BrokerExecutionReport,
    BrokerExecutionResult,
    BrokerOrderReport,
    BrokerRuntimeEvent,
    BrokerSyncBundle,
)
import ez.live.qmt.session_owner as _qmt_session_owner_module
from ez.live.qmt.session_owner import (
    QMTBrokerConfig,
    QMTClientProtocol,
    QMTSessionKey,
    QMTSessionManager,
    QMTSessionState,
    XtQuantShadowClient,
    _QMT_NUMERIC_ORDER_STATUS_ALIASES,
    _pre_normalize_qmt_numeric_order_status,
    get_default_qmt_session_manager,
)
from ez.live._utils import (
    utc_now as _utc_now,
    coerce_timestamp as _coerce_timestamp,
    get_field as _get_field,
    qmt_request_failed_immediately as _qmt_request_failed_immediately,
)
from ez.live.events import Order, normalize_broker_order_status
from ez.portfolio.execution import CostModel

# Backward-compatible monkeypatch path for tests and local tooling.
importlib = _qmt_session_owner_module.importlib


_QMT_CALLBACK_ACCOUNT_FRESHNESS_MAX_AGE = timedelta(minutes=5)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return value


def _extract_broker_order_id(raw: Any) -> str:
    """Prefer the broker-side contract id over transient local order ids."""
    return str(
        _get_field(
            raw,
            "order_sysid",
            "entrust_no",
            "order_id",
            "m_nOrderID",
            default="",
        )
        or ""
    )


def _extract_submit_ack(raw: Any) -> tuple[str, str]:
    """Return (broker_submit_id, broker_order_id) for a submit ack.

    Official QMT semantics differ by API:
    - `order_stock_async(...)` returns a request `seq`; the later callback
      carries `order_id`
    - `order_stock(...)` may return an immediate order id / object

    Keep the submit-return identity separate from the broker-order identity so
    async callers do not accidentally treat a request seq as a stable order id.
    """
    if isinstance(raw, bool):
        return "", ""
    if isinstance(raw, int):
        return str(raw), ""
    if isinstance(raw, float):
        if raw.is_integer():
            return str(int(raw)), ""
        return str(raw), ""
    if isinstance(raw, str):
        value = raw.strip()
        return value, ""
    broker_order_id = _extract_broker_order_id(raw).strip()
    submit_id = str(_get_field(raw, "seq", "request_id", "submit_id", default="") or "").strip()
    if not submit_id:
        submit_id = broker_order_id
    return submit_id, broker_order_id


def _normalize_report_id(
    raw: Any,
    *,
    as_of: datetime,
    client_order_id: str,
    broker_order_id: str,
    status: str,
    filled_shares: int,
    remaining_shares: int,
) -> str:
    explicit = str(
        _get_field(
            raw,
            "report_id",
            "trade_no",
            "business_no",
            "m_nTradeID",
            default="",
        )
        or ""
    )
    if explicit:
        return explicit
    key = broker_order_id or client_order_id or "unknown"
    return (
        f"qmt:{key}:{status}:{filled_shares}:{remaining_shares}:"
        f"{as_of.isoformat()}"
    )


def _normalize_order_status(status: Any) -> str:
    # Pre-normalize official xtquant numeric codes 48–57 / 255 into the
    # events.py broker-order vocabulary, then defer the rest of the string
    # alias handling to `normalize_broker_order_status`.
    pre_normalized = _pre_normalize_qmt_numeric_order_status(status)
    return normalize_broker_order_status(pre_normalized)


def _infer_execution_status(raw: Any, status: str) -> str:
    requested = _as_int(_get_field(raw, "order_volume", "entrust_amount", "m_nVolume"))
    filled = _as_int(
        _get_field(raw, "traded_volume", "filled_volume", "business_amount", "m_nTradedVolume")
    )
    remaining = _as_int(
        _get_field(raw, "remaining_volume", "left_volume", default=max(requested - filled, 0))
    )
    trade_ref = str(
        _get_field(raw, "traded_id", "trade_no", "business_no", "m_nTradeID", default="")
        or ""
    )
    if trade_ref and filled > 0:
        return "filled" if remaining <= 0 else "partially_filled"
    return normalize_broker_order_status(
        status,
        filled_shares=filled,
        remaining_shares=remaining,
    )


@dataclass(slots=True)
class QMTReadinessSummary:
    status: str
    ready_for_shadow_sync: bool
    ready_for_real_submit: bool
    real_submit_enabled: bool
    account_sync_mode: str | None
    asset_callback_freshness: str | None
    consumer_status: str | None
    session_runtime_kind: str | None
    session_runtime_status: str | None
    account_reconcile_status: str | None
    order_reconcile_status: str | None
    blockers: tuple[str, ...]
    real_submit_blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ready_for_shadow_sync": self.ready_for_shadow_sync,
            "ready_for_real_submit": self.ready_for_real_submit,
            "real_submit_enabled": self.real_submit_enabled,
            "account_sync_mode": self.account_sync_mode,
            "asset_callback_freshness": self.asset_callback_freshness,
            "consumer_status": self.consumer_status,
            "session_runtime_kind": self.session_runtime_kind,
            "session_runtime_status": self.session_runtime_status,
            "account_reconcile_status": self.account_reconcile_status,
            "order_reconcile_status": self.order_reconcile_status,
            "blockers": list(self.blockers),
            "real_submit_blockers": list(self.real_submit_blockers),
        }


@dataclass(slots=True)
class QMTRealSubmitPolicy:
    enabled: bool
    allowed_account_ids: tuple[str, ...]
    max_total_asset: float | None
    max_initial_cash: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed_account_ids": list(self.allowed_account_ids),
            "max_total_asset": self.max_total_asset,
            "max_initial_cash": self.max_initial_cash,
        }


@dataclass(slots=True)
class QMTSubmitGateDecision:
    status: str
    can_submit_now: bool
    mode: str
    blockers: tuple[str, ...]
    ready_for_shadow_sync: bool
    ready_for_real_submit: bool
    preflight_ok: bool
    policy: dict[str, Any]
    account_id: str | None
    total_asset: float | None
    initial_cash: float | None
    message: str | None = None
    hard_gate: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "can_submit_now": self.can_submit_now,
            "mode": self.mode,
            "blockers": list(self.blockers),
            "ready_for_shadow_sync": self.ready_for_shadow_sync,
            "ready_for_real_submit": self.ready_for_real_submit,
            "preflight_ok": self.preflight_ok,
            "policy": dict(self.policy),
            "account_id": self.account_id,
            "total_asset": self.total_asset,
            "initial_cash": self.initial_cash,
            "message": self.message,
            "hard_gate": dict(self.hard_gate) if isinstance(self.hard_gate, dict) else None,
        }


@dataclass(slots=True)
class QMTReleaseGateDecision:
    status: str
    eligible_for_release_candidate: bool
    eligible_for_real_submit: bool
    blockers: tuple[str, ...]
    deployment_status: str
    deploy_gate_passed: bool | None
    submit_gate_status: str | None
    submit_gate_preflight_ok: bool | None
    submit_gate_can_submit_now: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "eligible_for_release_candidate": self.eligible_for_release_candidate,
            "eligible_for_real_submit": self.eligible_for_real_submit,
            "blockers": list(self.blockers),
            "deployment_status": self.deployment_status,
            "deploy_gate_passed": self.deploy_gate_passed,
            "submit_gate_status": self.submit_gate_status,
            "submit_gate_preflight_ok": self.submit_gate_preflight_ok,
            "submit_gate_can_submit_now": self.submit_gate_can_submit_now,
        }


def build_qmt_real_submit_policy(
    risk_params: dict[str, Any] | None,
) -> QMTRealSubmitPolicy:
    raw_policy = {}
    if isinstance(risk_params, dict):
        maybe_policy = risk_params.get("qmt_real_submit_policy")
        if isinstance(maybe_policy, dict):
            raw_policy = maybe_policy

    raw_allowed_accounts = raw_policy.get("allowed_account_ids") or ()
    if isinstance(raw_allowed_accounts, str):
        allowed_account_ids = (raw_allowed_accounts,) if raw_allowed_accounts else ()
    elif isinstance(raw_allowed_accounts, (list, tuple, set)):
        allowed_account_ids = tuple(
            str(value) for value in raw_allowed_accounts if str(value)
        )
    else:
        allowed_account_ids = ()

    max_total_asset_raw = raw_policy.get("max_total_asset")
    max_initial_cash_raw = raw_policy.get("max_initial_cash")
    return QMTRealSubmitPolicy(
        enabled=bool(raw_policy.get("enabled", False)),
        allowed_account_ids=allowed_account_ids,
        max_total_asset=(
            float(max_total_asset_raw) if max_total_asset_raw is not None else None
        ),
        max_initial_cash=(
            float(max_initial_cash_raw) if max_initial_cash_raw is not None else None
        ),
    )


def _evaluate_qmt_real_submit_preflight(
    policy: QMTRealSubmitPolicy,
    *,
    account_id: str | None,
    total_asset: float | None,
    initial_cash: float | None,
) -> tuple[bool, tuple[str, ...]]:
    blockers: list[str] = []
    if not policy.enabled:
        blockers.append("real_submit_policy_disabled")
    if policy.allowed_account_ids:
        if not account_id:
            blockers.append("broker_account_id_unavailable")
        elif account_id not in policy.allowed_account_ids:
            blockers.append("account_not_whitelisted")
    if policy.max_total_asset is not None:
        if total_asset is None:
            blockers.append("broker_total_asset_unavailable")
        elif total_asset > policy.max_total_asset:
            blockers.append("total_asset_above_policy_cap")
    if policy.max_initial_cash is not None:
        if initial_cash is None:
            blockers.append("deployment_initial_cash_unavailable")
        elif initial_cash > policy.max_initial_cash:
            blockers.append("initial_cash_above_policy_cap")
    return (not blockers, tuple(blockers))


def build_qmt_reconcile_hard_gate(
    *,
    account_reconcile: dict[str, Any] | None,
    order_reconcile: dict[str, Any] | None,
    position_reconcile: dict[str, Any] | None = None,
    trade_reconcile: dict[str, Any] | None = None,
    broker_type: str,
    account_id: str = "",
) -> dict[str, Any] | None:
    """V3.3.44: four-way fail-closed reconcile gate.

    Folds account + order + position + trade reconcile results into one
    fail-closed hard gate. Any ``status != "ok"`` among the supplied
    reconciles keeps the gate ``blocked`` with a structured blocker reason
    per failing path. ``position_reconcile`` and ``trade_reconcile`` default
    to ``None`` for backward compatibility with callers that have not yet
    wired in the position/trade closures — in that case the gate behaves
    exactly like the pre-V3.3.44 two-way version.
    """
    if broker_type != "qmt":
        return None
    blockers: list[str] = []
    details: dict[str, Any] = {
        "broker_reconcile_status": None,
        "broker_order_reconcile_status": None,
        "position_reconcile_status": None,
        "trade_reconcile_status": None,
    }
    for label, reconcile, required in (
        ("broker_reconcile", account_reconcile, True),
        ("broker_order_reconcile", order_reconcile, True),
        ("position_reconcile", position_reconcile, False),
        ("trade_reconcile", trade_reconcile, False),
    ):
        status = None
        if isinstance(reconcile, dict):
            status = str(reconcile.get("status", "") or "").lower() or None
        details[f"{label}_status"] = status
        if required:
            if status != "ok":
                blockers.append(f"{label}_{status or 'unavailable'}")
        else:
            # Only fail closed on explicit non-ok statuses. A ``None`` result
            # means the caller did not supply this reconcile path yet
            # (position/trade are new in V3.3.44) and that should stay
            # backward compatible.
            if reconcile is not None and status != "ok":
                blockers.append(f"{label}_{status or 'unavailable'}")
    hard_gate_status = "open" if not blockers else "blocked"
    date_str = ""
    for candidate in (account_reconcile, order_reconcile, position_reconcile, trade_reconcile):
        if isinstance(candidate, dict) and candidate.get("date"):
            date_str = str(candidate.get("date"))
            break
    gate: dict[str, Any] = {
        "date": date_str,
        "event": "qmt_reconcile_hard_gate",
        "status": hard_gate_status,
        "broker_type": broker_type,
        "message": (
            "QMT reconcile checks passed."
            if hard_gate_status == "open"
            else "QMT reconcile checks failed; fail closed."
        ),
        "blockers": blockers,
        "details": details,
    }
    if account_id:
        gate["account_id"] = account_id
    return gate


def build_qmt_readiness_summary(
    *,
    latest_session_runtime: Any = None,
    latest_session_consumer_runtime: Any = None,
    latest_session_consumer_state_runtime: Any = None,
    latest_reconcile: dict[str, Any] | None = None,
    latest_order_reconcile: dict[str, Any] | None = None,
    real_submit_policy: QMTRealSubmitPolicy | None = None,
) -> QMTReadinessSummary:
    def _runtime_kind(event: Any) -> str | None:
        if event is None:
            return None
        if isinstance(event, dict):
            payload = event.get("payload")
            if isinstance(payload, dict):
                return str(payload.get("runtime_kind", "") or "") or None
        explicit = getattr(event, "event_kind", None)
        if explicit:
            return str(explicit)
        payload = getattr(event, "payload", None)
        if isinstance(payload, dict):
            return str(payload.get("runtime_kind", "") or "") or None
        return None

    def _runtime_payload(event: Any) -> dict[str, Any]:
        if isinstance(event, dict):
            payload = event.get("payload")
            if isinstance(payload, dict):
                nested = payload.get("payload")
                if isinstance(nested, dict):
                    return nested
                return payload
            return {}
        payload = getattr(event, "payload", None)
        if isinstance(payload, dict):
            nested = payload.get("payload")
            if isinstance(nested, dict):
                return nested
            return payload
        return {}

    session_runtime_kind = _runtime_kind(latest_session_runtime)
    session_runtime_payload = _runtime_payload(latest_session_runtime)
    session_runtime_status = (
        str(session_runtime_payload.get("status", "") or "") or None
    )
    consumer_event = latest_session_consumer_state_runtime or latest_session_consumer_runtime
    consumer_payload = _runtime_payload(consumer_event)
    consumer_status = str(consumer_payload.get("consumer_status", "") or "") or None
    account_sync_mode = str(consumer_payload.get("account_sync_mode", "") or "") or None
    asset_callback_freshness = (
        str(consumer_payload.get("asset_callback_freshness", "") or "") or None
    )
    if latest_session_consumer_runtime is not None and account_sync_mode is None:
        account_sync_mode = "query_fallback"
    if latest_session_consumer_runtime is not None and asset_callback_freshness is None:
        asset_callback_freshness = "unavailable"
    account_reconcile_status = (
        str(latest_reconcile.get("status", "") or "") or None
        if isinstance(latest_reconcile, dict)
        else None
    )
    order_reconcile_status = (
        str(latest_order_reconcile.get("status", "") or "") or None
        if isinstance(latest_order_reconcile, dict)
        else None
    )

    blockers: list[str] = []
    if latest_session_runtime is None:
        blockers.append("session_runtime_missing")
    elif session_runtime_kind in {
        "disconnected",
        "session_connect_failed",
        "session_subscribe_failed",
        "session_reconnect_started",
        "session_reconnect_failed",
        "session_resubscribe_failed",
        "session_reconnect_deferred",
    }:
        blockers.append("session_unhealthy")
    elif session_runtime_status == "disconnected":
        blockers.append("session_unhealthy")

    if latest_session_consumer_runtime is None:
        blockers.append("consumer_runtime_missing")
    elif consumer_status not in {None, "", "running"}:
        blockers.append("consumer_not_running")

    if account_sync_mode != "callback_preferred":
        blockers.append("callback_account_not_preferred")
    if asset_callback_freshness != "fresh":
        blockers.append("callback_asset_not_fresh")

    ready_for_shadow_sync = not blockers
    policy_enabled = bool(real_submit_policy.enabled) if real_submit_policy else False
    real_submit_enabled = policy_enabled
    ready_for_real_submit = ready_for_shadow_sync and policy_enabled
    if policy_enabled:
        real_submit_blockers = tuple(blockers)
    else:
        real_submit_blockers = tuple(["shadow_mode_only", *blockers])
    return QMTReadinessSummary(
        status="ready" if ready_for_shadow_sync else "degraded",
        ready_for_shadow_sync=ready_for_shadow_sync,
        ready_for_real_submit=ready_for_real_submit,
        real_submit_enabled=real_submit_enabled,
        account_sync_mode=account_sync_mode,
        asset_callback_freshness=asset_callback_freshness,
        consumer_status=consumer_status,
        session_runtime_kind=session_runtime_kind,
        session_runtime_status=session_runtime_status,
        account_reconcile_status=account_reconcile_status,
        order_reconcile_status=order_reconcile_status,
        blockers=tuple(blockers),
        real_submit_blockers=real_submit_blockers,
    )


def build_qmt_submit_gate_decision(
    readiness: QMTReadinessSummary | None,
    *,
    policy: QMTRealSubmitPolicy | None = None,
    account_id: str | None = None,
    total_asset: float | None = None,
    initial_cash: float | None = None,
    hard_gate: dict[str, Any] | None = None,
) -> QMTSubmitGateDecision:
    effective_policy = policy or QMTRealSubmitPolicy(
        enabled=False,
        allowed_account_ids=(),
        max_total_asset=None,
        max_initial_cash=None,
    )
    preflight_ok, preflight_blockers = _evaluate_qmt_real_submit_preflight(
        effective_policy,
        account_id=account_id,
        total_asset=total_asset,
        initial_cash=initial_cash,
    )
    base_gate: QMTSubmitGateDecision
    if readiness is None:
        base_gate = QMTSubmitGateDecision(
            status="unavailable",
            can_submit_now=False,
            mode="unknown",
            blockers=("qmt_readiness_unavailable", *preflight_blockers),
            ready_for_shadow_sync=False,
            ready_for_real_submit=False,
            preflight_ok=preflight_ok,
            policy=effective_policy.to_dict(),
            account_id=account_id,
            total_asset=total_asset,
            initial_cash=initial_cash,
        )
    elif not readiness.real_submit_enabled:
        base_gate = QMTSubmitGateDecision(
            status="shadow_only",
            can_submit_now=False,
            mode="shadow_only",
            blockers=tuple(dict.fromkeys([*readiness.real_submit_blockers, *preflight_blockers])),
            ready_for_shadow_sync=readiness.ready_for_shadow_sync,
            ready_for_real_submit=readiness.ready_for_real_submit,
            preflight_ok=preflight_ok,
            policy=effective_policy.to_dict(),
            account_id=account_id,
            total_asset=total_asset,
            initial_cash=initial_cash,
        )
    elif readiness.ready_for_real_submit:
        base_gate = QMTSubmitGateDecision(
            status="open",
            can_submit_now=preflight_ok,
            mode="real_submit",
            blockers=preflight_blockers,
            ready_for_shadow_sync=readiness.ready_for_shadow_sync,
            ready_for_real_submit=readiness.ready_for_real_submit,
            preflight_ok=preflight_ok,
            policy=effective_policy.to_dict(),
            account_id=account_id,
            total_asset=total_asset,
            initial_cash=initial_cash,
        )
    else:
        base_gate = QMTSubmitGateDecision(
            status="blocked",
            can_submit_now=False,
            mode="real_submit",
            blockers=tuple(dict.fromkeys([*readiness.real_submit_blockers, *preflight_blockers])),
            ready_for_shadow_sync=readiness.ready_for_shadow_sync,
            ready_for_real_submit=readiness.ready_for_real_submit,
            preflight_ok=preflight_ok,
            policy=effective_policy.to_dict(),
            account_id=account_id,
            total_asset=total_asset,
            initial_cash=initial_cash,
        )
    if (
        not isinstance(hard_gate, dict)
        or str(hard_gate.get("status", "") or "").lower() != "blocked"
    ):
        return base_gate
    blockers = [
        str(value)
        for value in base_gate.blockers
        if str(value or "")
    ]
    blockers.extend(
        str(value)
        for value in (hard_gate.get("blockers") or [])
        if str(value or "")
    )
    message = str(hard_gate.get("message", "") or "") or base_gate.message
    return QMTSubmitGateDecision(
        status="blocked",
        can_submit_now=False,
        mode=base_gate.mode,
        blockers=tuple(dict.fromkeys(blockers)),
        ready_for_shadow_sync=base_gate.ready_for_shadow_sync,
        ready_for_real_submit=base_gate.ready_for_real_submit,
        preflight_ok=base_gate.preflight_ok,
        policy=dict(base_gate.policy),
        account_id=base_gate.account_id,
        total_asset=base_gate.total_asset,
        initial_cash=base_gate.initial_cash,
        message=message,
        hard_gate=dict(hard_gate),
    )


def build_qmt_release_gate_decision(
    *,
    deployment_status: str,
    gate_verdict: dict[str, Any] | None,
    submit_gate: dict[str, Any] | None,
    hard_gate: dict[str, Any] | None = None,
) -> QMTReleaseGateDecision:
    """Compose release-gate decision from deploy verdict, submit gate, and hard gate.

    If ``hard_gate`` is provided and its ``status`` is not ``"open"`` the
    release gate is forced to blocked with ``qmt_reconcile_hard_gate_blocked``
    in the blocker list. This keeps the release path fail-closed when broker
    reconcile has flagged account/order drift, even if the upstream submit
    gate has not yet been recomputed against the same hard-gate truth.
    """
    blockers: list[str] = []
    deploy_gate_passed: bool | None = None
    if isinstance(gate_verdict, dict) and "passed" in gate_verdict:
        deploy_gate_passed = bool(gate_verdict.get("passed"))
        if not deploy_gate_passed:
            blockers.append("deploy_gate_failed")
    else:
        blockers.append("deploy_gate_not_recorded")

    if deployment_status not in {"approved", "running", "paused"}:
        blockers.append("deployment_not_approved")

    submit_gate_status = None
    submit_gate_preflight_ok = None
    submit_gate_can_submit_now = None
    if isinstance(submit_gate, dict):
        submit_gate_status = str(submit_gate.get("status", "") or "") or None
        submit_gate_preflight_ok = bool(submit_gate.get("preflight_ok"))
        submit_gate_can_submit_now = bool(submit_gate.get("can_submit_now"))
        if submit_gate_status in {None, "", "unavailable"}:
            blockers.append("qmt_submit_gate_unavailable")
        elif submit_gate_status == "shadow_only":
            blockers.append("qmt_submit_gate_shadow_only")
        elif submit_gate_status != "open":
            blockers.append("qmt_submit_gate_blocked")
        if not submit_gate_preflight_ok:
            blockers.append("qmt_preflight_not_ok")
    else:
        blockers.append("qmt_submit_gate_unavailable")

    hard_gate_blocked = (
        isinstance(hard_gate, dict)
        and str(hard_gate.get("status", "") or "").lower() != "open"
    )
    if hard_gate_blocked:
        # Dedupe while preserving order: hard-gate evidence wins even if a
        # prior reason (deploy_gate_failed, submit gate shadow_only, ...)
        # has already been appended.
        if "qmt_reconcile_hard_gate_blocked" not in blockers:
            blockers.append("qmt_reconcile_hard_gate_blocked")
        for extra in hard_gate.get("blockers") or []:
            text = str(extra or "")
            if text and text not in blockers:
                blockers.append(text)

    eligible_for_release_candidate = not blockers and not hard_gate_blocked
    eligible_for_real_submit = (
        eligible_for_release_candidate
        and bool(submit_gate_can_submit_now)
        and not hard_gate_blocked
    )
    return QMTReleaseGateDecision(
        status="candidate" if eligible_for_release_candidate else "blocked",
        eligible_for_release_candidate=eligible_for_release_candidate,
        eligible_for_real_submit=eligible_for_real_submit,
        blockers=tuple(blockers),
        deployment_status=deployment_status,
        deploy_gate_passed=deploy_gate_passed,
        submit_gate_status=submit_gate_status,
        submit_gate_preflight_ok=submit_gate_preflight_ok,
        submit_gate_can_submit_now=submit_gate_can_submit_now,
    )


class QMTShadowBroker(BrokerAdapter):
    """Read-only/shadow broker for official QMT integration.

    This class normalizes QMT account state and execution callbacks into the
    framework broker contract. It intentionally refuses execution for now.
    """

    broker_type = "qmt"

    def __init__(
        self,
        config: QMTBrokerConfig,
        client: QMTClientProtocol | None = None,
        client_factory: Callable[[QMTBrokerConfig], QMTClientProtocol] | None = None,
        session_manager: QMTSessionManager | None = None,
    ):
        self.config = config
        self._client = client
        self._client_factory = client_factory
        self._session_manager = session_manager or get_default_qmt_session_manager()
        self._managed_session = client is None
        # Defense-in-depth submit gate. Even when the broker is wired into a
        # scheduler that already checks gate state, `execute_target_weights`
        # refuses to run unless the caller has explicitly opened the submit
        # gate via `open_submit_gate(...)` with a gate-decision dict whose
        # `status == "open"` and `can_submit_now is True`. Shadow brokers
        # never open this gate (they raise in `execute_target_weights` on
        # principle), so keeping the default closed costs nothing for
        # shadow-only paths.
        self._submit_gate_open: bool = False
        self._submit_gate_decision: dict[str, Any] | None = None

    def open_submit_gate(self, decision: dict[str, Any]) -> None:
        """Unlock execute_target_weights for the next call.

        Callers MUST supply the same submit-gate decision that the
        scheduler-side gate check already approved (`status == "open"` and
        `can_submit_now is True`). Any other input leaves the broker
        fail-closed. The gate is not sticky across calls — the scheduler is
        expected to call `close_submit_gate()` immediately after
        `execute_target_weights(...)` returns (or raises).
        """
        if not isinstance(decision, dict):
            raise ValueError("open_submit_gate requires a dict decision payload")
        status = str(decision.get("status", "") or "").lower()
        can_submit_now = bool(decision.get("can_submit_now"))
        if status != "open" or not can_submit_now:
            raise RuntimeError(
                "QMT submit gate refusing to open: decision must be "
                "status='open' and can_submit_now=True "
                f"(got status={status!r}, can_submit_now={can_submit_now!r})"
            )
        self._submit_gate_open = True
        self._submit_gate_decision = dict(decision)

    def close_submit_gate(self) -> None:
        """Re-seal the submit gate. Safe to call even when already closed."""
        self._submit_gate_open = False
        self._submit_gate_decision = None

    def is_submit_gate_open(self) -> bool:
        """Test-only / diagnostic accessor for the current submit-gate state."""
        return bool(self._submit_gate_open)

    @property
    def capabilities(self) -> frozenset[BrokerCapability]:
        caps = {
            BrokerCapability.READ_ACCOUNT_STATE,
            BrokerCapability.STREAM_EXECUTION_REPORTS,
            BrokerCapability.SHADOW_MODE,
        }
        if self.config.enable_cancel:
            caps.add(BrokerCapability.CANCEL_ORDER)
        return frozenset(caps)

    def execute_target_weights(
        self,
        *,
        business_date: date,
        target_weights: dict[str, float],
        holdings: dict[str, int],
        equity: float,
        cash: float,
        prices: dict[str, float],
        raw_close_today: dict[str, float],
        prev_raw_close: dict[str, float],
        has_bar_today: set[str],
        cost_model: CostModel,
        lot_size: int,
        limit_pct: float,
        t_plus_1: bool,
        requested_orders: list[Order] | None = None,
        execution_slices: int = 1,
    ) -> BrokerExecutionResult:
        raise NotImplementedError(
            "QMTShadowBroker is read-only/shadow-only for now; real order execution is not enabled"
        )

    def snapshot_account_state(self) -> BrokerAccountSnapshot | None:
        client = self._require_client()
        if hasattr(client, "collect_sync_state"):
            raw_bundle = client.collect_sync_state(
                since_reports=None,
                since_runtime=None,
                cursor_state=None,
            ) or {}
            if isinstance(raw_bundle, dict):
                return self._build_snapshot(
                    asset=raw_bundle.get("asset"),
                    positions=raw_bundle.get("positions") or [],
                    orders=raw_bundle.get("orders") or [],
                    trades=raw_bundle.get("trades") or [],
                )
        asset = client.query_stock_asset(self.config.account_id)
        positions = client.query_stock_positions(self.config.account_id) or []
        orders = client.query_stock_orders(self.config.account_id) or []
        trades = client.query_stock_trades(self.config.account_id) or []
        return self._build_snapshot(
            asset=asset,
            positions=positions,
            orders=orders,
            trades=trades,
        )

    def list_execution_reports(
        self,
        *,
        since: datetime | None = None,
    ) -> list[BrokerExecutionReport]:
        client = self._require_client()
        if hasattr(client, "list_execution_reports"):
            raw_reports = client.list_execution_reports(since=since) or []
        elif hasattr(client, "collect_sync_state"):
            raw_bundle = client.collect_sync_state(
                since_reports=since,
                since_runtime=None,
                cursor_state=None,
            ) or {}
            raw_reports = (
                raw_bundle.get("execution_reports") or []
                if isinstance(raw_bundle, dict)
                else []
            )
        else:
            raw_reports = client.query_stock_orders(self.config.account_id) or []
        reports = self._dedupe_execution_reports(
            self._normalize_execution_report(item)
            for item in raw_reports
        )
        if since is None:
            return reports
        return [report for report in reports if report.as_of >= since]

    def list_runtime_events(
        self,
        *,
        since: datetime | None = None,
    ) -> list[BrokerRuntimeEvent]:
        factory = self._client_factory_or_default()
        raw_events: list[Any] = []
        if self._uses_session_manager():
            raw_events.extend(
                self._session_manager.list_runtime_events(
                    config=self.config,
                    factory=factory,
                    since=since,
                )
            )
            if not self._session_manager.has_active_client(
                config=self.config,
                factory=factory,
            ):
                events = self._dedupe_runtime_events(
                    self._normalize_runtime_event(item)
                    for item in raw_events
                )
                if since is None:
                    return events
                return [event for event in events if event.as_of >= since]
        client = self._require_client()
        if hasattr(client, "list_runtime_events"):
            raw_events.extend(client.list_runtime_events(since=since) or [])
        events = self._dedupe_runtime_events(
            self._normalize_runtime_event(item)
            for item in raw_events
        )
        if since is None:
            return events
        return [event for event in events if event.as_of >= since]

    def collect_sync_state(
        self,
        *,
        since_reports: datetime | None = None,
        since_runtime: datetime | None = None,
        cursor_state: dict[str, Any] | None = None,
    ) -> BrokerSyncBundle:
        runtime_cursor_state = (
            dict(cursor_state) if isinstance(cursor_state, dict) else {}
        )
        owner_runtime_seq = None
        raw_owner_runtime_seq = runtime_cursor_state.get("owner_runtime_seq")
        try:
            owner_runtime_seq = (
                int(raw_owner_runtime_seq)
                if raw_owner_runtime_seq is not None
                else None
            )
        except (TypeError, ValueError):
            owner_runtime_seq = None
        owner_runtime_events: list[Any] = []
        if self._uses_session_manager():
            owner_runtime_events.extend(
                self._session_manager.list_runtime_events(
                    config=self.config,
                    factory=self._client_factory_or_default(),
                    since=since_runtime,
                    since_seq=owner_runtime_seq,
                )
            )
        client = self._require_client()
        if hasattr(client, "collect_sync_state"):
            raw_bundle = client.collect_sync_state(
                since_reports=since_reports,
                since_runtime=since_runtime,
                cursor_state=runtime_cursor_state,
            ) or {}
            snapshot = self._build_snapshot(
                asset=raw_bundle.get("asset"),
                positions=raw_bundle.get("positions") or [],
                orders=raw_bundle.get("orders") or [],
                trades=raw_bundle.get("trades") or [],
            )
            reports = self._dedupe_execution_reports(
                self._normalize_execution_report(item)
                for item in (raw_bundle.get("execution_reports") or [])
            )
            runtime_events = self._dedupe_runtime_events(
                self._normalize_runtime_event(item)
                for item in [
                    *owner_runtime_events,
                    *(raw_bundle.get("runtime_events") or []),
                ]
            )
            next_cursor_state = dict(
                raw_bundle.get("cursor_state")
                if isinstance(raw_bundle.get("cursor_state"), dict)
                else runtime_cursor_state
            )
            has_execution_cursor = next_cursor_state.get("callback_execution_seq") is not None
            has_runtime_cursor = any(
                next_cursor_state.get(key) is not None
                for key in ("owner_runtime_seq", "callback_runtime_seq")
            )
            if since_reports is not None and not has_execution_cursor:
                reports = [report for report in reports if report.as_of >= since_reports]
            if since_runtime is not None and not has_runtime_cursor:
                runtime_events = [
                    event for event in runtime_events if event.as_of >= since_runtime
                ]
            if self._uses_session_manager():
                next_cursor_state["owner_runtime_seq"] = (
                    self._session_manager.get_runtime_journal_seq(
                        config=self.config,
                        factory=self._client_factory_or_default(),
                    )
                )
            return BrokerSyncBundle(
                snapshot=snapshot,
                execution_reports=reports,
                runtime_events=runtime_events,
                cursor_state=next_cursor_state,
            )
        return super().collect_sync_state(
            since_reports=since_reports,
            since_runtime=since_runtime,
            cursor_state=cursor_state,
        )

    def get_session_state(self) -> QMTSessionState | None:
        if not self._uses_session_manager():
            return None
        factory = self._client_factory_or_default()
        return self._session_manager.get_state(
            config=self.config,
            factory=factory,
        )

    def ensure_session_supervision(self) -> QMTSessionState | None:
        if not self._uses_session_manager():
            return None
        return self._session_manager.ensure_session_supervision(
            config=self.config,
            factory=self._client_factory_or_default(),
        )

    def pin_process_owner(self, owner_id: str) -> QMTSessionState | None:
        if not self._uses_session_manager():
            return None
        self._client = self._session_manager.pin_process_owner(
            config=self.config,
            factory=self._client_factory_or_default(),
            owner_id=owner_id,
        )
        return self.get_session_state()

    def unpin_process_owner(self, owner_id: str) -> QMTSessionState | None:
        if not self._uses_session_manager():
            return None
        state = self._session_manager.unpin_process_owner(
            config=self.config,
            factory=self._client_factory_or_default(),
            owner_id=owner_id,
        )
        if state is not None and state.owner_count == 0 and not getattr(state, "host_owner_pinned", False):
            self._client = None
        return state

    def attach_deployment(self, deployment_id: str) -> None:
        if not self._uses_session_manager():
            return
        self._client = self._session_manager.attach_owner(
            config=self.config,
            factory=self._client_factory_or_default(),
            deployment_id=deployment_id,
        )

    def detach_deployment(self, deployment_id: str) -> QMTSessionState | None:
        if not self._uses_session_manager():
            return None
        state = self._session_manager.detach_owner(
            config=self.config,
            factory=self._client_factory_or_default(),
            deployment_id=deployment_id,
        )
        if state is not None and state.owner_count == 0 and not getattr(state, "host_owner_pinned", False):
            self._client = None
        return state

    def _require_client(self) -> QMTClientProtocol:
        if self._client is None:
            factory = self._client_factory_or_default()
            self._client = self._session_manager.resolve(
                config=self.config,
                factory=factory,
            )
        return self._client

    def _client_factory_or_default(self) -> Callable[[QMTBrokerConfig], QMTClientProtocol]:
        return self._client_factory or XtQuantShadowClient.from_config

    def _uses_session_manager(self) -> bool:
        return self._managed_session

    def cancel_order(self, client_order_id: str, *, symbol: str = "") -> bool:
        if not self.config.enable_cancel:
            raise NotImplementedError(
                "QMTShadowBroker cancel path is disabled; set shadow_broker_config.enable_cancel=true"
            )
        client = self._require_client()
        try:
            result = client.cancel_order(client_order_id, symbol=symbol)
        except TypeError:
            result = client.cancel_order(client_order_id)
        if isinstance(result, bool):
            return result
        if isinstance(result, (int, float)):
            return result >= 0
        if result is None:
            return False
        return True

    def _build_snapshot(
        self,
        *,
        asset: Any,
        positions: list[Any],
        orders: list[Any],
        trades: list[Any],
    ) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            broker_type=self.broker_type,
            as_of=_coerce_timestamp(_get_field(asset, "update_time", "m_nUpdateTime")),
            cash=_as_float(_get_field(asset, "cash", "enable_balance", "m_dAvailable")),
            total_asset=_as_float(
                _get_field(asset, "total_asset", "total_balance", "m_dBalance")
            ),
            positions={
                str(_get_field(pos, "stock_code", "symbol", "m_strInstrumentID")):
                _as_int(_get_field(pos, "volume", "current_amount", "m_nVolume"))
                for pos in positions
                if _get_field(pos, "stock_code", "symbol", "m_strInstrumentID")
            },
            open_orders=[self._normalize_order(order) for order in orders],
            fills=[self._normalize_trade(trade) for trade in trades],
            account_id=str(self.config.account_id or ""),
        )

    def _normalize_order(self, raw: Any) -> dict[str, Any]:
        return {
            "client_order_id": str(
                _get_field(raw, "client_order_id", "order_remark", "remark", default="") or ""
            ),
            "broker_order_id": _extract_broker_order_id(raw),
            "symbol": str(_get_field(raw, "stock_code", "stock_code1", "symbol", "m_strInstrumentID", default="") or ""),
            "side": str(_get_field(raw, "side", "offset_flag", "entrust_bs", "direction", default="") or ""),
            "status": _normalize_order_status(
                _get_field(raw, "status", "order_status", "m_strStatus", default="unknown")
            ),
            "requested_shares": _as_int(_get_field(raw, "order_volume", "entrust_amount", "m_nVolume")),
            "filled_shares": _as_int(_get_field(raw, "traded_volume", "filled_volume", "business_amount", "m_nTradedVolume")),
            "remaining_shares": _as_int(_get_field(raw, "remaining_volume", "left_volume", default=0)),
            "avg_price": _as_float(_get_field(raw, "traded_price", "avg_price", "business_price", "m_dTradedPrice")),
            "updated_at": _coerce_timestamp(_get_field(raw, "update_time", "order_time", "m_nUpdateTime")).isoformat(),
        }

    def _normalize_trade(self, raw: Any) -> dict[str, Any]:
        return {
            "client_order_id": str(
                _get_field(raw, "client_order_id", "order_remark", "remark", default="") or ""
            ),
            "broker_order_id": _extract_broker_order_id(raw),
            "symbol": str(_get_field(raw, "stock_code", "stock_code1", "symbol", "m_strInstrumentID", default="") or ""),
            "side": str(_get_field(raw, "side", "offset_flag", "entrust_bs", "direction", default="") or ""),
            "shares": _as_int(_get_field(raw, "traded_volume", "business_amount", "m_nVolume")),
            "price": _as_float(_get_field(raw, "traded_price", "business_price", "m_dPrice")),
            "traded_at": _coerce_timestamp(_get_field(raw, "traded_time", "business_time", "m_nTradeTime")).isoformat(),
        }

    def _normalize_execution_report(self, raw: Any) -> BrokerExecutionReport:
        as_of = _coerce_timestamp(_get_field(raw, "update_time", "order_time", "traded_time", "m_nUpdateTime"))
        client_order_id = str(
            _get_field(raw, "client_order_id", "order_remark", "remark", default="") or ""
        )
        broker_order_id = _extract_broker_order_id(raw)
        status = _normalize_order_status(
            _get_field(raw, "status", "order_status", "m_strStatus", default="unknown")
        )
        status = _infer_execution_status(raw, status)
        requested = _as_int(_get_field(raw, "order_volume", "entrust_amount", "m_nVolume"))
        filled = _as_int(_get_field(raw, "traded_volume", "filled_volume", "business_amount", "m_nTradedVolume"))
        remaining = _as_int(
            _get_field(raw, "remaining_volume", "left_volume", default=max(requested - filled, 0))
        )
        account_id = str(
            _get_field(raw, "account_id", default=self.config.account_id) or self.config.account_id
        )
        return BrokerExecutionReport(
            report_id=_normalize_report_id(
                raw,
                as_of=as_of,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                status=status,
                filled_shares=filled,
                remaining_shares=remaining,
            ),
            broker_type=self.broker_type,
            as_of=as_of,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            symbol=str(_get_field(raw, "stock_code", "stock_code1", "symbol", "m_strInstrumentID", default="") or ""),
            side=str(_get_field(raw, "side", "offset_flag", "entrust_bs", "direction", default="") or ""),
            status=status,
            filled_shares=filled,
            remaining_shares=remaining,
            avg_price=_as_float(_get_field(raw, "traded_price", "avg_price", "business_price", "m_dTradedPrice")),
            message=str(_get_field(raw, "status_msg", "status_message", "error_msg", "m_strMsg", default="") or ""),
            raw_payload=raw if isinstance(raw, dict) else None,
            account_id=account_id,
        )

    def _normalize_runtime_event(self, raw: Any) -> BrokerRuntimeEvent:
        as_of = _coerce_timestamp(_get_field(raw, "update_time", default=_utc_now()))
        kind = str(_get_field(raw, "_report_kind", default="unknown") or "unknown")
        account_id = str(_get_field(raw, "account_id", default=self.config.account_id) or self.config.account_id)
        state_key = str(_get_field(raw, "state_key", default="") or "")
        if state_key:
            event_id = "|".join(
                [
                    kind,
                    account_id,
                    str(_get_field(raw, "session_id", default="") or ""),
                    state_key,
                ]
            )
        else:
            event_id = "|".join(
                [
                    kind,
                    account_id,
                    str(_get_field(raw, "seq", default="") or ""),
                    str(_get_field(raw, "order_id", default="") or ""),
                    str(_get_field(raw, "status", "error_msg", default="") or ""),
                    as_of.isoformat(),
                ]
            )
        payload = _json_safe_value(dict(raw) if isinstance(raw, dict) else {})
        return BrokerRuntimeEvent(
            event_id=event_id,
            broker_type=self.broker_type,
            as_of=as_of,
            event_kind=kind,
            payload=payload,
        )

    @staticmethod
    def _dedupe_execution_reports(
        reports: list[BrokerExecutionReport] | Any,
    ) -> list[BrokerExecutionReport]:
        deduped: dict[str, BrokerExecutionReport] = {}
        for report in reports:
            current = deduped.get(report.report_id)
            if current is None or (report.as_of, report.report_id) >= (
                current.as_of,
                current.report_id,
            ):
                deduped[report.report_id] = report
        return sorted(
            deduped.values(),
            key=lambda report: (report.as_of, report.report_id),
        )

    @staticmethod
    def _dedupe_runtime_events(
        events: list[BrokerRuntimeEvent] | Any,
    ) -> list[BrokerRuntimeEvent]:
        deduped: dict[str, BrokerRuntimeEvent] = {}
        for event in events:
            current = deduped.get(event.event_id)
            if current is None or (event.as_of, event.event_id) >= (
                current.as_of,
                current.event_id,
            ):
                deduped[event.event_id] = event
        return sorted(
            deduped.values(),
            key=lambda event: (event.as_of, event.event_id),
        )


class QMTRealBroker(QMTShadowBroker):
    """Explicit real-submit QMT broker path.

    The adapter keeps the same session owner / callback bridge as the shadow
    broker, but `execute_target_weights()` submits real orders through the
    official XtQuant async-first API and returns submission confirmations.
    Actual fills still arrive through the callback sync path.
    """

    @property
    def capabilities(self) -> frozenset[BrokerCapability]:
        caps = {
            BrokerCapability.TARGET_WEIGHT_EXECUTION,
            BrokerCapability.READ_ACCOUNT_STATE,
            BrokerCapability.STREAM_EXECUTION_REPORTS,
        }
        if self.config.enable_cancel:
            caps.add(BrokerCapability.CANCEL_ORDER)
        return frozenset(caps)

    def execute_target_weights(
        self,
        *,
        business_date: date,
        target_weights: dict[str, float],
        holdings: dict[str, int],
        equity: float,
        cash: float,
        prices: dict[str, float],
        raw_close_today: dict[str, float],
        prev_raw_close: dict[str, float],
        has_bar_today: set[str],
        cost_model: CostModel,
        lot_size: int,
        limit_pct: float,
        t_plus_1: bool,
        requested_orders: list[Order] | None = None,
        execution_slices: int = 1,
    ) -> BrokerExecutionResult:
        # Defense-in-depth: refuse to submit unless the caller has explicitly
        # opened the submit gate with a decision dict whose status is "open"
        # and can_submit_now is True. Scheduler paths wrap this broker
        # behind `_qmt_real_submit_gate_open(...)` already, but that check
        # lives outside this module so a future refactor / new caller must
        # not be able to bypass the broker-side gate silently.
        if not getattr(self, "_submit_gate_open", False):
            raise RuntimeError(
                "QMTRealBroker submit is fail-closed; caller must open "
                "submit gate explicitly via open_submit_gate(decision)"
            )
        client = self._require_client()
        broker_orders: list[BrokerOrderReport] = []
        for order in requested_orders or []:
            side = str(order.side or "").strip().lower()
            if side not in {"buy", "sell"}:
                raise ValueError(
                    f"QMT real submit requires buy/sell orders, got {order.side!r}"
                )
            price = _as_float(prices.get(order.symbol, 0.0))
            if price <= 0:
                raise RuntimeError(
                    f"QMT real submit requires a positive live price for {order.symbol!r}"
                )
            submit_result = client.submit_order(
                symbol=order.symbol,
                side=side,
                shares=int(order.shares),
                price=price,
                strategy_name=order.deployment_id or self.config.account_id,
                order_remark=order.client_order_id,
            )
            if _qmt_request_failed_immediately(submit_result):
                raise RuntimeError(
                    f"QMT real submit failed for {order.client_order_id!r}: {submit_result!r}"
                )
            broker_submit_id = ""
            broker_order_id = ""
            describe_submit_ack = getattr(client, "describe_last_submit_ack", None)
            if callable(describe_submit_ack):
                ack = describe_submit_ack(submit_result)
                if isinstance(ack, dict):
                    broker_submit_id = str(ack.get("broker_submit_id", "") or "").strip()
                    broker_order_id = str(ack.get("broker_order_id", "") or "").strip()
            if not broker_submit_id and not broker_order_id:
                broker_submit_id, broker_order_id = _extract_submit_ack(submit_result)
            broker_orders.append(
                BrokerOrderReport(
                    order_id=order.order_id,
                    client_order_id=order.client_order_id,
                    deployment_id=order.deployment_id,
                    symbol=order.symbol,
                    side=order.side,
                    requested_shares=order.shares,
                    filled_shares=0,
                    remaining_shares=order.shares,
                    status=normalize_broker_order_status("submitted"),
                    price=price,
                    amount=0.0,
                    commission=0.0,
                    stamp_tax=0.0,
                    cost=0.0,
                    business_date=business_date,
                    broker_order_id=broker_order_id,
                    broker_submit_id=broker_submit_id,
                )
            )
        return BrokerExecutionResult(
            fills=[],
            order_reports=broker_orders,
            holdings=dict(holdings),
            cash=cash,
            trade_volume=0.0,
        )
