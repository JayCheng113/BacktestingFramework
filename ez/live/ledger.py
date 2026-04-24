"""Shared live ledger reducer for OMS events and snapshot checkpoints.

This keeps event replay semantics in one place so OMS, recovery, and
consistency checks do not drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ez.live.events import (
    DeploymentEvent,
    EventType,
    OrderStatus,
    broker_order_status_can_transition,
    broker_order_status_is_terminal,
    broker_order_status_rank,
    broker_order_status_to_order_status,
    normalize_broker_order_status,
)

# Non-terminal broker statuses that only advance the broker-order projection.
# Seeing these in a ``BROKER_EXECUTION_RECORDED`` event means the report is a
# submission ack or a pre-fill status update — it MUST NOT touch cash or
# holdings, even if a bad raw payload accidentally sneaks filled_shares > 0.
_SUBMISSION_ONLY_BROKER_STATUSES = frozenset(
    {
        "unreported",
        "reported",
        "reported_cancel_pending",
    }
)

# Local OrderStatus ranks use the same non-terminal/terminal split as the
# broker-status rank table so forward-only guards behave symmetrically.
_LOCAL_ORDER_STATUS_RANK: dict[str, int] = {
    OrderStatus.NEW.value: 0,
    OrderStatus.SUBMITTED.value: 10,
    OrderStatus.PARTIALLY_FILLED.value: 20,
    # Terminal band — all mutually exclusive completion states.
    OrderStatus.FILLED.value: 30,
    OrderStatus.CANCELED.value: 30,
    OrderStatus.REJECTED.value: 30,
}

_LOCAL_TERMINAL_RANK = 30

# Local-status cross-terminal allow-list mirrors the broker-status rule: once
# an OrderStatus lands on FILLED / CANCELED / REJECTED it stays put, so no
# terminal-to-terminal transition is permitted.
_LOCAL_TERMINAL_FOLLOWUPS: dict[str, frozenset[str]] = {}


@dataclass(slots=True)
class LiveLedgerState:
    cash: float
    holdings: dict[str, int]
    order_statuses: dict[str, str] = field(default_factory=dict)
    broker_order_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    risk_events: list[dict[str, Any]] = field(default_factory=list)
    last_prices: dict[str, float] = field(default_factory=dict)
    equity_curve: list[float] = field(default_factory=list)
    dates: list[date] = field(default_factory=list)
    latest_snapshot_date: date | None = None
    latest_snapshot_equity: float | None = None
    latest_weights: dict[str, float] = field(default_factory=dict)
    latest_prev_returns: dict[str, float] = field(default_factory=dict)
    # Audit counter for ``replay()`` idempotency: how many unique event_ids
    # were actually applied (duplicates are skipped). Existing consumers that
    # only read the semantic fields above are unaffected.
    seen_event_count: int = 0


class LiveLedger:
    """Replay append-only deployment events into account state."""

    def replay(
        self,
        events: list[DeploymentEvent],
        *,
        initial_cash: float,
        initial_holdings: dict[str, int] | None = None,
    ) -> LiveLedgerState:
        state = LiveLedgerState(
            cash=float(initial_cash),
            holdings={
                symbol: int(shares)
                for symbol, shares in (initial_holdings or {}).items()
            },
        )
        # event_id dedup guarantees idempotency: replaying the same log N
        # times yields the same cash/holdings/broker_order_states, even if a
        # broken upstream path delivers duplicate events. Events without an
        # event_id are still applied (legacy/uncontrolled callers), but real
        # persistence paths always set one.
        seen_event_ids: set[str] = set()
        for event in sorted(events, key=self._event_sort_key):
            event_id = str(getattr(event, "event_id", "") or "")
            if event_id:
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)
            self._apply_event(state, event)
        state.seen_event_count = len(seen_event_ids)
        return state

    @staticmethod
    def _event_sort_key(event: DeploymentEvent) -> tuple[Any, int, str]:
        priority = {
            EventType.MARKET_SNAPSHOT: 5,
            EventType.MARKET_BAR_RECORDED: 6,
            EventType.BROKER_ACCOUNT_RECORDED: 43,
            EventType.ORDER_SUBMITTED: 10,
            EventType.ORDER_PARTIALLY_FILLED: 20,
            EventType.ORDER_FILLED: 30,
            EventType.ORDER_REJECTED: 40,
            EventType.BROKER_RUNTIME_RECORDED: 44,
            EventType.BROKER_EXECUTION_RECORDED: 45,
            EventType.BROKER_CANCEL_REQUESTED: 46,
            EventType.RISK_RECORDED: 50,
            EventType.SNAPSHOT_SAVED: 60,
            EventType.TICK_COMPLETED: 70,
        }.get(event.event_type, 999)
        return (event.event_ts, priority, event.event_id)

    def _apply_event(self, state: LiveLedgerState, event: DeploymentEvent) -> None:
        payload = event.payload or {}
        client_order_id = event.client_order_id

        if event.event_type == EventType.ORDER_SUBMITTED:
            self._advance_local_order_status(
                state.order_statuses,
                client_order_id,
                OrderStatus.SUBMITTED.value,
            )
            return

        if event.event_type == EventType.ORDER_REJECTED:
            self._advance_local_order_status(
                state.order_statuses,
                client_order_id,
                OrderStatus.REJECTED.value,
            )
            return

        if event.event_type in (EventType.ORDER_FILLED, EventType.ORDER_PARTIALLY_FILLED):
            side = str(payload["side"])
            symbol = str(payload["symbol"])
            shares = int(payload["shares"])
            amount = float(payload["amount"])
            cost = float(payload["cost"])
            price = float(payload.get("price", 0.0) or 0.0)

            if side == "buy":
                state.cash -= amount + cost
                state.holdings[symbol] = state.holdings.get(symbol, 0) + shares
            else:
                state.cash += amount - cost
                remaining = state.holdings.get(symbol, 0) - shares
                if remaining > 0:
                    state.holdings[symbol] = remaining
                else:
                    state.holdings.pop(symbol, None)

            if price > 0:
                state.last_prices[symbol] = price

            self._advance_local_order_status(
                state.order_statuses,
                client_order_id,
                (
                    OrderStatus.PARTIALLY_FILLED.value
                    if event.event_type == EventType.ORDER_PARTIALLY_FILLED
                    else OrderStatus.FILLED.value
                ),
            )
            state.trades.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "shares": shares,
                    "price": price,
                    "cost": cost,
                    "amount": amount,
                }
            )
            return

        if event.event_type == EventType.BROKER_EXECUTION_RECORDED:
            broker_order_id = self._broker_order_key(payload, client_order_id)
            filled_shares = int(payload.get("filled_shares", 0) or 0)
            remaining_shares = int(payload.get("remaining_shares", 0) or 0)
            normalized_status = normalize_broker_order_status(
                str(payload.get("status", "") or ""),
                filled_shares=filled_shares,
                remaining_shares=remaining_shares,
            )
            # Defensive: BROKER_EXECUTION_RECORDED NEVER mutates cash/holdings
            # directly — those changes only flow through ORDER_FILLED /
            # ORDER_PARTIALLY_FILLED events. Broker reports only advance the
            # broker_order_states projection and coarse local order status.
            # Submission-only reports (filled=0 with a non-terminal status)
            # additionally stay out of the filled-shares counter below; see
            # ``_advance_broker_order_state`` for the double-guard.
            self._advance_broker_order_state(
                state,
                broker_order_id=broker_order_id,
                client_order_id=client_order_id,
                event=event,
                normalized_status=normalized_status,
                payload=payload,
            )
            self._advance_local_order_status(
                state.order_statuses,
                client_order_id or broker_order_id,
                broker_order_status_to_order_status(normalized_status).value,
            )
            return

        if event.event_type == EventType.BROKER_CANCEL_REQUESTED:
            broker_order_id = self._broker_order_key(payload, client_order_id)
            current = state.broker_order_states.get(broker_order_id)
            if current is None or not broker_order_status_is_terminal(
                str(current.get("status", "") or "")
            ):
                pending_status = (
                    "partially_filled_cancel_pending"
                    if current is not None
                    and str(current.get("status", "") or "") in {
                        "partially_filled",
                        "partially_filled_cancel_pending",
                    }
                    else "reported_cancel_pending"
                )
                self._advance_broker_order_state(
                    state,
                    broker_order_id=broker_order_id,
                    client_order_id=client_order_id,
                    event=event,
                    normalized_status=pending_status,
                    payload=payload,
                )
                current = state.broker_order_states.get(broker_order_id)
                if current is not None:
                    current["cancel_request_event_id"] = event.event_id
                    current["cancel_requested_at"] = event.event_ts
                    current["cancel_state"] = pending_status
            self._advance_local_order_status(
                state.order_statuses,
                client_order_id or broker_order_id,
                OrderStatus.SUBMITTED.value,
            )
            return

        if event.event_type == EventType.RISK_RECORDED:
            risk_event = payload.get("risk_event")
            if isinstance(risk_event, dict):
                state.risk_events.append(dict(risk_event))
            return

        if event.event_type == EventType.MARKET_SNAPSHOT:
            prices = payload.get("prices")
            if isinstance(prices, dict):
                for symbol, price in prices.items():
                    parsed = float(price or 0.0)
                    if parsed > 0:
                        state.last_prices[str(symbol)] = parsed
            return

        if event.event_type == EventType.MARKET_BAR_RECORDED:
            symbol = payload.get("symbol")
            adj_close = float(payload.get("adj_close", 0.0) or 0.0)
            close_price = float(payload.get("close", 0.0) or 0.0)
            effective_price = adj_close if adj_close > 0 else close_price
            if symbol and effective_price > 0:
                state.last_prices[str(symbol)] = effective_price
            return

        if event.event_type == EventType.SNAPSHOT_SAVED:
            self._apply_snapshot_checkpoint(state, payload)

    @staticmethod
    def _apply_snapshot_checkpoint(
        state: LiveLedgerState,
        payload: dict[str, Any],
    ) -> None:
        snapshot_date = payload.get("snapshot_date")
        if isinstance(snapshot_date, str):
            state.latest_snapshot_date = date.fromisoformat(snapshot_date)
        elif isinstance(snapshot_date, date):
            state.latest_snapshot_date = snapshot_date

        equity = payload.get("equity")
        if equity is not None:
            state.latest_snapshot_equity = float(equity)
            state.equity_curve.append(state.latest_snapshot_equity)

        cash = payload.get("cash")
        if cash is not None:
            state.cash = float(cash)

        holdings = payload.get("holdings")
        if holdings is not None:
            state.holdings = {
                str(symbol): int(shares)
                for symbol, shares in holdings.items()
            }

        weights = payload.get("weights")
        if weights is not None:
            state.latest_weights = {
                str(symbol): float(weight)
                for symbol, weight in weights.items()
            }

        prev_returns = payload.get("prev_returns")
        if prev_returns is not None:
            state.latest_prev_returns = {
                str(symbol): float(ret)
                for symbol, ret in prev_returns.items()
            }

        if state.latest_snapshot_date is not None:
            state.dates.append(state.latest_snapshot_date)

        if state.latest_snapshot_equity and state.latest_snapshot_equity > 0:
            for symbol, shares in state.holdings.items():
                weight = state.latest_weights.get(symbol, 0.0)
                if shares > 0 and weight > 0:
                    state.last_prices[symbol] = (
                        state.latest_snapshot_equity * weight
                    ) / shares

    @staticmethod
    def _local_order_status_rank(status: str) -> int:
        return _LOCAL_ORDER_STATUS_RANK.get(str(status or "").lower(), 0)

    @staticmethod
    def _local_order_status_can_transition(previous: str, incoming: str) -> bool:
        """Forward-only guard for the coarse OrderStatus map.

        Rules:
        - None/empty previous -> any incoming is allowed.
        - Non-terminal previous -> any state with strictly higher rank is
          allowed (same rank is a no-op to keep idempotency).
        - Terminal previous (FILLED / CANCELED / REJECTED) -> absorbing; no
          other terminal state can overwrite it. This blocks the bogus
          ``FILLED -> CANCELED`` move even though both share rank 30.
        """
        prev = str(previous or "").lower()
        inc = str(incoming or "").lower()
        if not prev:
            return True
        prev_rank = _LOCAL_ORDER_STATUS_RANK.get(prev, 0)
        inc_rank = _LOCAL_ORDER_STATUS_RANK.get(inc, 0)
        if prev_rank >= _LOCAL_TERMINAL_RANK:
            return inc in _LOCAL_TERMINAL_FOLLOWUPS.get(prev, frozenset())
        return inc_rank > prev_rank

    def _advance_local_order_status(
        self,
        status_map: dict[str, str],
        order_key: str,
        new_status: str,
    ) -> None:
        key = str(order_key or "")
        if not key:
            return
        previous = status_map.get(key)
        if self._local_order_status_can_transition(previous or "", str(new_status)):
            status_map[key] = str(new_status)

    @staticmethod
    def _broker_order_key(payload: dict[str, Any], client_order_id: str) -> str:
        broker_order_id = str(payload.get("broker_order_id", "") or "").strip()
        if broker_order_id:
            return broker_order_id
        return str(client_order_id or "")

    def _advance_broker_order_state(
        self,
        state: LiveLedgerState,
        *,
        broker_order_id: str,
        client_order_id: str,
        event: DeploymentEvent,
        normalized_status: str,
        payload: dict[str, Any],
    ) -> None:
        key = str(broker_order_id or client_order_id or "")
        if not key:
            return
        current = state.broker_order_states.get(key)
        current_status = str(current.get("status", "") or "") if current else ""
        current_rank = broker_order_status_rank(current_status) if current else -1
        incoming_rank = broker_order_status_rank(normalized_status)
        if current is None:
            should_advance = True
        elif broker_order_status_can_transition(current_status, normalized_status):
            should_advance = True
        elif incoming_rank == current_rank and incoming_rank < _LOCAL_TERMINAL_RANK:
            # Same non-terminal rank — allow advance on newer/equal timestamp
            # so out-of-order duplicates with the same status still refresh
            # the projection (report_id, filled_shares, etc.). Terminal
            # same-rank moves stay blocked by ``can_transition`` above.
            current_ts = current.get("event_ts")
            should_advance = current_ts is None or event.event_ts >= current_ts
        else:
            should_advance = False
        if not should_advance:
            return

        # Fix 3 double-guard: submission-only reports (filled=0 with a
        # non-terminal status in {unreported, reported, reported_cancel_*})
        # must never inject a non-zero filled_shares into the projection.
        # If the payload is inconsistent (filled_shares > 0 but status says
        # submission-only), we clamp filled/remaining so the ledger's
        # cash/holdings reconciliation is never triggered by a bogus report.
        payload_filled = int(payload.get("filled_shares", 0) or 0)
        payload_remaining = int(payload.get("remaining_shares", 0) or 0)
        if normalized_status in _SUBMISSION_ONLY_BROKER_STATUSES:
            projected_filled = 0
            # Preserve any previously observed remaining_shares if the payload
            # happens to be inconsistent and claims filled>0.
            projected_remaining = payload_remaining if payload_filled == 0 else (
                int(current.get("remaining_shares", 0) or 0) if current else payload_remaining
            )
        else:
            projected_filled = payload_filled
            projected_remaining = payload_remaining

        state.broker_order_states[key] = {
            "broker_order_id": key,
            "client_order_id": str(client_order_id or payload.get("client_order_id", "") or ""),
            "status": normalized_status,
            "status_rank": incoming_rank,
            "event_id": event.event_id,
            "event_ts": event.event_ts,
            "report_id": str(payload.get("report_id", "") or ""),
            "filled_shares": projected_filled,
            "remaining_shares": projected_remaining,
            "avg_price": float(payload.get("avg_price", 0.0) or 0.0),
            "message": str(payload.get("message", "") or ""),
            "raw_payload": dict(payload.get("raw_payload") or {}) if isinstance(payload.get("raw_payload"), dict) else None,
            "cancel_request_event_id": current.get("cancel_request_event_id", "") if current else "",
            "cancel_requested_at": current.get("cancel_requested_at") if current else None,
            "cancel_state": current.get("cancel_state", "") if current else "",
        }
