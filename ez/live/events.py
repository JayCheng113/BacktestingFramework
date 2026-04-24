"""Live OMS event and order models for V3.0-lite Phase 1.

Phase 1 scope:
- Minimal order lifecycle only: NEW -> SUBMITTED -> FILLED/CANCELED/REJECTED
- Append-only event log payloads must be JSON-safe
- Deterministic IDs support idempotent persistence/replay
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class EventType(StrEnum):
    MARKET_SNAPSHOT = "market_snapshot"
    MARKET_BAR_RECORDED = "market_bar_recorded"
    BROKER_ACCOUNT_RECORDED = "broker_account_recorded"
    BROKER_RUNTIME_RECORDED = "broker_runtime_recorded"
    BROKER_EXECUTION_RECORDED = "broker_execution_recorded"
    BROKER_CANCEL_REQUESTED = "broker_cancel_requested"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_REJECTED = "order_rejected"
    RISK_RECORDED = "risk_recorded"
    SNAPSHOT_SAVED = "snapshot_saved"
    TICK_COMPLETED = "tick_completed"


class OrderStatus(StrEnum):
    NEW = "new"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


_BROKER_ORDER_STATUS_ALIASES: dict[str, str] = {
    "waiting_reporting": "unreported",
    "waiting_report": "unreported",
    "wait_reporting": "unreported",
    "submitted": "reported",
    "accepted": "reported",
    "active": "reported",
    "working": "reported",
    "queued": "reported",
    "partially_cancelled": "partially_canceled",
    "partial_canceled": "partially_canceled",
    "partial_cancelled": "partially_canceled",
    "cancelled": "canceled",
    "order_rejected": "order_error",
    "order_failed": "order_error",
    "cancel_rejected": "cancel_error",
    "cancel_failed": "cancel_error",
    "error": "order_error",
}

# Broker-status rank is split into two bands so forward-only guards can treat
# terminal states as absorbing while still allowing legitimate non-terminal
# progression (including the xtquant ``ORDER_PART_CANCEL=53`` path which moves
# from ``partially_filled`` to ``partially_canceled``).
#
# Non-terminal band: 10-29 (various open/intermediate states)
# Terminal band:     30 (mutually exclusive completion states)
#
# Callers enforce the rule ``terminal -> anything`` is rejected; equal-rank
# advances are only allowed for non-terminal states (timestamp tie-break).
_BROKER_ORDER_STATUS_RANK: dict[str, int] = {
    "unknown": 0,
    # cancel_error is a cancel-attempt outcome, not an order terminal state.
    # Keep it below all open-order ranks so later open/terminal order reports
    # can still advance.
    "cancel_error": 5,
    "unreported": 10,
    "reported": 12,
    "reported_cancel_pending": 14,
    "partially_filled": 20,
    "partially_filled_cancel_pending": 22,
    # Terminal band — all mutually exclusive completion states share rank 30.
    "partially_canceled": 30,
    "filled": 30,
    "canceled": 30,
    "junk": 30,
    "order_error": 30,
}

_BROKER_ORDER_TERMINAL_STATUSES = frozenset(
    {
        "partially_canceled",
        "filled",
        "canceled",
        "junk",
        "order_error",
    }
)

_BROKER_ORDER_TERMINAL_RANK = 30

# A broker order that has reached a terminal status but is observed mutating
# to a DIFFERENT terminal status is almost certainly a stale replay or a bogus
# callback. The only legal cross-terminal move within xtquant semantics is
# ``partially_canceled -> canceled`` (ORDER_PART_CANCEL=53 upgraded to a full
# cancel confirm), so the guard keeps that one case open while blocking the
# rest (``filled <-> canceled``, ``canceled <-> filled``, etc.).
_BROKER_ORDER_TERMINAL_FOLLOWUPS: dict[str, frozenset[str]] = {
    "partially_canceled": frozenset({"canceled"}),
}


def normalize_broker_order_status(
    status: str,
    *,
    filled_shares: int = 0,
    remaining_shares: int = 0,
) -> str:
    """Normalize broker-side lifecycle statuses to a forward-only vocabulary."""
    raw = str(status or "").strip().lower()
    if not raw:
        return "unknown"
    raw = _BROKER_ORDER_STATUS_ALIASES.get(raw, raw)
    if raw in _BROKER_ORDER_STATUS_RANK:
        return raw
    if "cancel" in raw and "pending" in raw:
        return (
            "partially_filled_cancel_pending"
            if int(filled_shares) > 0 or int(remaining_shares) > 0
            else "reported_cancel_pending"
        )
    if "partial" in raw and "fill" in raw:
        return "partially_filled"
    if "partial" in raw and "cancel" in raw:
        return "partially_canceled"
    if "fill" in raw and int(filled_shares) > 0:
        return "filled" if int(remaining_shares) <= 0 else "partially_filled"
    if raw in {"reject", "rejected", "rejecting"}:
        return "order_error"
    if raw in {"junk", "discarded"}:
        return "junk"
    if raw in {"pending", "report_pending", "reported_pending"}:
        return "reported"
    if raw in {"reported", "report", "ack", "acknowledged", "submitted"}:
        return "reported"
    return raw


def broker_order_status_rank(status: str) -> int:
    """Return the monotonic rank used for forward-only broker lifecycle updates."""
    normalized = normalize_broker_order_status(status)
    return _BROKER_ORDER_STATUS_RANK.get(normalized, 0)


def broker_order_status_is_terminal(status: str) -> bool:
    """Return True when a broker order should no longer regress."""
    return normalize_broker_order_status(status) in _BROKER_ORDER_TERMINAL_STATUSES


def broker_order_status_can_transition(current: str, incoming: str) -> bool:
    """Return True when ``current -> incoming`` is a legal broker status move.

    Rules:
    - If there is no current state, any incoming state is allowed.
    - Non-terminal -> anything with higher rank is allowed.
    - Non-terminal -> terminal is allowed (terminal rank 30 > any non-terminal).
    - Terminal is absorbing: ``filled / canceled / rejected`` cannot move to
      a different terminal state, and cannot regress to a non-terminal state.
    - The only tolerated cross-terminal move is
      ``partially_canceled -> canceled`` (xtquant ``ORDER_PART_CANCEL=53``
      upgraded to a full cancel confirm).
    """
    cur = normalize_broker_order_status(current)
    inc = normalize_broker_order_status(incoming)
    cur_rank = _BROKER_ORDER_STATUS_RANK.get(cur, 0)
    inc_rank = _BROKER_ORDER_STATUS_RANK.get(inc, 0)
    if cur_rank >= _BROKER_ORDER_TERMINAL_RANK:
        # Current is terminal — only a whitelisted follow-up is legal.
        allowed = _BROKER_ORDER_TERMINAL_FOLLOWUPS.get(cur, frozenset())
        return inc in allowed
    # Current is non-terminal — standard forward-only comparison.
    return inc_rank > cur_rank


def broker_order_status_to_order_status(status: str) -> OrderStatus:
    """Map a broker lifecycle state to the coarser OMS order status enum."""
    normalized = normalize_broker_order_status(status)
    if normalized in {"filled"}:
        return OrderStatus.FILLED
    if normalized in {"partially_filled", "partially_filled_cancel_pending"}:
        return OrderStatus.PARTIALLY_FILLED
    if normalized in {"canceled", "partially_canceled"}:
        return OrderStatus.CANCELED
    if normalized in {"junk", "order_error"}:
        return OrderStatus.REJECTED
    if normalized in {"cancel_error"}:
        return OrderStatus.SUBMITTED
    return OrderStatus.SUBMITTED


@dataclass(slots=True)
class Order:
    order_id: str
    client_order_id: str
    deployment_id: str
    symbol: str
    side: str
    shares: int
    business_date: date
    requested_shares: int = 0
    remaining_shares: int = 0
    status: OrderStatus = OrderStatus.NEW
    rejected_reason: str = ""
    rejected_message: str = ""
    rejected_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["business_date"] = self.business_date.isoformat()
        if not data["requested_shares"]:
            data["requested_shares"] = data["shares"]
        return data


@dataclass(slots=True)
class Fill:
    fill_id: str
    order_id: str
    client_order_id: str
    deployment_id: str
    symbol: str
    side: str
    shares: int
    price: float
    amount: float
    commission: float
    stamp_tax: float
    cost: float
    business_date: date
    requested_shares: int = 0
    remaining_shares: int = 0
    slice_index: int = 1
    total_slices: int = 1

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["business_date"] = self.business_date.isoformat()
        return data


@dataclass(slots=True)
class DeploymentEvent:
    event_id: str
    deployment_id: str
    event_type: EventType
    event_ts: datetime
    client_order_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "deployment_id": self.deployment_id,
            "event_type": self.event_type.value,
            "event_ts": self.event_ts.isoformat(),
            "client_order_id": self.client_order_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeploymentEvent":
        ts = data["event_ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return cls(
            event_id=data["event_id"],
            deployment_id=data["deployment_id"],
            event_type=EventType(data["event_type"]),
            event_ts=ts,
            client_order_id=data.get("client_order_id", ""),
            payload=dict(data.get("payload") or {}),
        )


def make_client_order_id(
    deployment_id: str,
    business_date: date,
    symbol: str,
    side: str,
) -> str:
    return f"{deployment_id}:{business_date.isoformat()}:{symbol}:{side}"


def make_order_id(client_order_id: str) -> str:
    return client_order_id


def make_fill_id(client_order_id: str) -> str:
    return f"{client_order_id}:fill"


def make_event_id(client_order_id: str, event_type: EventType) -> str:
    return f"{client_order_id}:{event_type.value}"


def _stable_shadow_hash(*parts: str) -> str:
    """Return a stable 16-char hex digest for shadow client-order-id fallbacks."""
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def make_shadow_broker_client_order_id(
    deployment_id: str,
    *,
    broker_type: str,
    broker_order_id: str = "",
    report_id: str = "",
    event_ts: datetime | str | None = None,
    symbol: str = "",
    side: str = "",
) -> str:
    """Return a deterministic shadow client-order-id.

    Preference order:

    1. ``broker_order_id`` present -> canonical per-order key.
    2. ``report_id`` present -> canonical per-report key.
    3. Both missing but ``broker_type`` known -> stable hash of
       ``broker_type:broker_order_id:event_ts`` so report-less callback
       executions still produce per-event unique keys instead of colliding.
    4. ``broker_type`` also unknown -> stable hash of
       ``deployment_id:symbol:side:event_ts`` so callback-only reports still
       resolve to a deterministic key per (deployment, leg, time).

    The literal string ``"unknown"`` is never used, so two report-less
    executions in the same tick no longer collide into one client_order_id.
    """
    if broker_order_id:
        return f"{deployment_id}:broker_order:{broker_type}:{broker_order_id}"
    if report_id:
        return f"{deployment_id}:broker_report:{report_id}"
    ts_token = ""
    if event_ts is not None:
        ts_token = event_ts.isoformat() if isinstance(event_ts, datetime) else str(event_ts)
    if broker_type:
        digest = _stable_shadow_hash(broker_type, broker_order_id, ts_token)
        return f"{deployment_id}:broker_report:hash:{digest}"
    digest = _stable_shadow_hash(deployment_id, symbol, side, ts_token)
    return f"{deployment_id}:broker_report:hash:{digest}"


def make_broker_execution_event(
    deployment_id: str,
    *,
    report_id: str,
    broker_type: str,
    report_ts: datetime,
    client_order_id: str,
    broker_order_id: str,
    symbol: str,
    side: str,
    status: str,
    filled_shares: int,
    remaining_shares: int,
    avg_price: float,
    message: str = "",
    raw_payload: dict[str, Any] | None = None,
    account_id: str = "",
) -> DeploymentEvent:
    event_client_order_id = client_order_id or make_shadow_broker_client_order_id(
        deployment_id,
        broker_type=broker_type,
        broker_order_id=broker_order_id,
        report_id=report_id,
        event_ts=report_ts,
        symbol=symbol,
        side=side,
    )
    return DeploymentEvent(
        event_id=f"{deployment_id}:broker_report:{report_id}",
        deployment_id=deployment_id,
        event_type=EventType.BROKER_EXECUTION_RECORDED,
        event_ts=report_ts,
        client_order_id=event_client_order_id,
        payload={
            "report_id": str(report_id),
            "broker_type": str(broker_type),
            "broker_order_id": str(broker_order_id),
            "symbol": str(symbol),
            "side": str(side),
            "status": str(status),
            "filled_shares": int(filled_shares),
            "remaining_shares": int(remaining_shares),
            "avg_price": float(avg_price),
            "message": str(message or ""),
            "account_id": str(account_id or ""),
            "raw_payload": dict(raw_payload) if isinstance(raw_payload, dict) else None,
        },
    )


def make_broker_runtime_event(
    deployment_id: str,
    *,
    runtime_event_id: str,
    broker_type: str,
    runtime_kind: str,
    event_ts: datetime,
    client_order_id: str = "",
    payload: dict[str, Any] | None = None,
) -> DeploymentEvent:
    event_client_order_id = client_order_id or f"{deployment_id}:broker_runtime:{broker_type}:{runtime_kind}"
    return DeploymentEvent(
        event_id=f"{deployment_id}:broker_runtime:{runtime_event_id}",
        deployment_id=deployment_id,
        event_type=EventType.BROKER_RUNTIME_RECORDED,
        event_ts=event_ts,
        client_order_id=event_client_order_id,
        payload={
            "broker_type": str(broker_type),
            "runtime_kind": str(runtime_kind),
            "payload": dict(payload) if isinstance(payload, dict) else {},
        },
    )


def make_broker_account_event(
    deployment_id: str,
    *,
    broker_type: str,
    account_ts: datetime,
    account_id: str = "",
    cash: float,
    total_asset: float,
    positions: dict[str, int],
    open_orders: list[dict[str, Any]],
    fill_count: int,
) -> DeploymentEvent:
    client_order_id = f"{deployment_id}:broker_account:{broker_type}"
    account_key = account_ts.isoformat()
    return DeploymentEvent(
        event_id=f"{deployment_id}:broker_account:{broker_type}:{account_key}",
        deployment_id=deployment_id,
        event_type=EventType.BROKER_ACCOUNT_RECORDED,
        event_ts=account_ts,
        client_order_id=client_order_id,
        payload={
            "broker_type": str(broker_type),
            "account_id": str(account_id or ""),
            "cash": float(cash),
            "total_asset": float(total_asset),
            "positions": {
                str(symbol): int(shares)
                for symbol, shares in (positions or {}).items()
            },
            "open_orders": list(open_orders or []),
            "fill_count": int(fill_count),
        },
    )


def make_broker_cancel_requested_event(
    deployment_id: str,
    *,
    broker_type: str,
    request_ts: datetime,
    client_order_id: str,
    broker_order_id: str,
    symbol: str,
    account_id: str = "",
) -> DeploymentEvent:
    event_client_order_id = client_order_id or make_shadow_broker_client_order_id(
        deployment_id,
        broker_type=broker_type,
        broker_order_id=broker_order_id,
        report_id=f"cancel:{broker_order_id or client_order_id}",
        event_ts=request_ts,
        symbol=symbol,
        side="",
    )
    request_key = request_ts.isoformat()
    return DeploymentEvent(
        event_id=(
            f"{deployment_id}:broker_cancel:{broker_type}:"
            f"{broker_order_id or event_client_order_id}:{request_key}"
        ),
        deployment_id=deployment_id,
        event_type=EventType.BROKER_CANCEL_REQUESTED,
        event_ts=request_ts,
        client_order_id=event_client_order_id,
        payload={
            "broker_type": str(broker_type),
            "broker_order_id": str(broker_order_id),
            "symbol": str(symbol),
            "account_id": str(account_id or ""),
        },
    )


def make_snapshot_event(
    deployment_id: str,
    business_date: date,
    equity: float,
    cash: float,
    rebalanced: bool,
    trade_count: int,
    holdings: dict[str, int] | None = None,
    weights: dict[str, float] | None = None,
    prev_returns: dict[str, float] | None = None,
    liquidation: bool = False,
    event_ts: datetime | None = None,
) -> DeploymentEvent:
    client_order_id = f"{deployment_id}:{business_date.isoformat()}:snapshot"
    payload = {
        "snapshot_date": business_date.isoformat(),
        "equity": equity,
        "cash": cash,
        "rebalanced": rebalanced,
        "trade_count": trade_count,
        "liquidation": liquidation,
    }
    if holdings is not None:
        payload["holdings"] = {
            str(symbol): int(shares)
            for symbol, shares in holdings.items()
        }
    if weights is not None:
        payload["weights"] = {
            str(symbol): float(weight)
            for symbol, weight in weights.items()
        }
    if prev_returns is not None:
        payload["prev_returns"] = {
            str(symbol): float(ret)
            for symbol, ret in prev_returns.items()
        }
    return DeploymentEvent(
        event_id=make_event_id(client_order_id, EventType.SNAPSHOT_SAVED),
        deployment_id=deployment_id,
        event_type=EventType.SNAPSHOT_SAVED,
        event_ts=event_ts or utcnow(),
        client_order_id=client_order_id,
        payload=payload,
    )


def make_risk_event(
    deployment_id: str,
    business_date: date,
    risk_index: int,
    risk_event: dict[str, Any],
    event_ts: datetime | None = None,
) -> DeploymentEvent:
    client_order_id = f"{deployment_id}:{business_date.isoformat()}:risk:{risk_index}"
    return DeploymentEvent(
        event_id=make_event_id(client_order_id, EventType.RISK_RECORDED),
        deployment_id=deployment_id,
        event_type=EventType.RISK_RECORDED,
        event_ts=event_ts or utcnow(),
        client_order_id=client_order_id,
        payload={
            "business_date": business_date.isoformat(),
            "risk_index": int(risk_index),
            "risk_event": dict(risk_event),
        },
    )


def make_tick_completed_event(
    deployment_id: str,
    business_date: date,
    *,
    execution_ms: float,
    rebalanced: bool,
    trade_count: int,
    risk_event_count: int,
    equity: float,
    cash: float,
    event_ts: datetime | None = None,
) -> DeploymentEvent:
    client_order_id = f"{deployment_id}:{business_date.isoformat()}:tick"
    return DeploymentEvent(
        event_id=make_event_id(client_order_id, EventType.TICK_COMPLETED),
        deployment_id=deployment_id,
        event_type=EventType.TICK_COMPLETED,
        event_ts=event_ts or utcnow(),
        client_order_id=client_order_id,
        payload={
            "business_date": business_date.isoformat(),
            "execution_ms": float(execution_ms),
            "rebalanced": bool(rebalanced),
            "trade_count": int(trade_count),
            "risk_event_count": int(risk_event_count),
            "equity": float(equity),
            "cash": float(cash),
        },
    )


def make_market_snapshot_event(
    deployment_id: str,
    business_date: date,
    *,
    prices: dict[str, float],
    has_bar_symbols: list[str],
    source: str = "live",
    event_ts: datetime | None = None,
) -> DeploymentEvent:
    client_order_id = f"{deployment_id}:{business_date.isoformat()}:market"
    return DeploymentEvent(
        event_id=make_event_id(client_order_id, EventType.MARKET_SNAPSHOT),
        deployment_id=deployment_id,
        event_type=EventType.MARKET_SNAPSHOT,
        event_ts=event_ts or utcnow(),
        client_order_id=client_order_id,
        payload={
            "business_date": business_date.isoformat(),
            "prices": {str(symbol): float(price) for symbol, price in prices.items()},
            "has_bar_symbols": [str(symbol) for symbol in has_bar_symbols],
            "source": source,
        },
    )


def make_market_bar_event(
    deployment_id: str,
    business_date: date,
    *,
    symbol: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    adj_close: float,
    volume: float,
    source: str = "live",
    event_ts: datetime | None = None,
) -> DeploymentEvent:
    client_order_id = f"{deployment_id}:{business_date.isoformat()}:{symbol}:bar"
    return DeploymentEvent(
        event_id=make_event_id(client_order_id, EventType.MARKET_BAR_RECORDED),
        deployment_id=deployment_id,
        event_type=EventType.MARKET_BAR_RECORDED,
        event_ts=event_ts or utcnow(),
        client_order_id=client_order_id,
        payload={
            "business_date": business_date.isoformat(),
            "symbol": str(symbol),
            "open": float(open_price),
            "high": float(high_price),
            "low": float(low_price),
            "close": float(close_price),
            "adj_close": float(adj_close),
            "volume": float(volume),
            "source": source,
        },
    )
