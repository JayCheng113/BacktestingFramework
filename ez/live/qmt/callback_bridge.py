"""XtQuant callback bridge and related helpers.

Extracted from ``qmt_session_owner.py`` to reduce file size. Contains:

- ``_XtQuantTraderCallbackBridge`` — thread-safe buffer for official XtQuantTrader callbacks
- ``_invoke_xtquant_cancel_api`` / ``_invoke_xtquant_order_api`` — small xtquant call helpers
- ``_pre_normalize_qmt_numeric_order_status`` — xtquant numeric status → events.py vocabulary
- ``_latest_timestamp`` — monotonic-safe timestamp helper
- QMT numeric/runtime kind constants used by the bridge and sibling modules
"""
from __future__ import annotations

import logging
import threading
from bisect import bisect_left
from datetime import datetime, timezone
from typing import Any, Callable

from ez.live._utils import (
    utc_now as _utc_now,
    coerce_timestamp as _coerce_timestamp,
    get_field as _get_field,
)
from ez.live.events import (
    broker_order_status_is_terminal,
    normalize_broker_order_status,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QMT numeric order-status aliases
# ---------------------------------------------------------------------------

_QMT_NUMERIC_ORDER_STATUS_ALIASES: dict[str, str] = {
    "48": "unreported",                      # UNREPORTED
    "49": "unreported",                      # WAIT_REPORTING -> same pre-terminal bucket
    "50": "reported",                        # REPORTED
    "51": "reported_cancel_pending",         # REPORTED_CANCEL
    "52": "partially_filled_cancel_pending", # PARTSUCC_CANCEL
    "53": "partially_canceled",              # PART_CANCEL (terminal)
    "54": "canceled",                        # CANCELED (terminal)
    "55": "partially_filled",                # PART_SUCC
    "56": "filled",                          # SUCCEEDED (terminal)
    "57": "junk",                            # JUNK (terminal)
    "255": "unknown",                        # UNKNOWN
}


def _pre_normalize_qmt_numeric_order_status(status: Any) -> str:
    """Map official xtquant numeric order_status codes to the events.py vocabulary.

    Returns the normalized string if the input is one of the covered numeric
    codes, otherwise returns the input stringified+stripped so downstream
    `normalize_broker_order_status` can continue its own alias handling.
    """
    if status is None:
        return ""
    raw = str(status).strip()
    return _QMT_NUMERIC_ORDER_STATUS_ALIASES.get(raw, raw)


def _latest_timestamp(current: datetime | None, candidate: datetime | None) -> datetime | None:
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


# ---------------------------------------------------------------------------
# QMT runtime kind sets
# ---------------------------------------------------------------------------

_QMT_SESSION_RUNTIME_KINDS = {
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
    "session_consumer_started",
    "session_consumer_state",
    "session_consumer_reused",
    "session_consumer_restarted",
    "session_consumer_stopped",
    "session_consumer_failed",
    "session_consumer_restart_failed",
    "session_owner_created",
    "session_owner_reused",
    "session_owner_create_failed",
    "session_owner_attached",
    "session_owner_detached",
    "session_owner_process_pinned",
    "session_owner_process_unpinned",
    "session_owner_resident",
    "session_owner_closed",
    "session_owner_close_failed",
}

_QMT_PROJECTION_PUSH_RUNTIME_KINDS = {
    "session_connected",
    "session_reconnected",
    "session_connect_failed",
    "session_subscribe_failed",
    "session_reconnect_failed",
    "session_resubscribe_failed",
    "session_reconnect_deferred",
    "session_consumer_started",
    "session_consumer_failed",
    "session_consumer_stopped",
    "session_consumer_restart_failed",
    "disconnected",
}


# ---------------------------------------------------------------------------
# Small xtquant call helpers
# ---------------------------------------------------------------------------

def _invoke_xtquant_cancel_api(method: Callable[..., Any], account_ref: Any, *args: Any) -> Any:
    try:
        return method(account_ref, *args)
    except TypeError:
        return method(*args)


def _invoke_xtquant_order_api(method: Callable[..., Any], account_ref: Any, *args: Any) -> Any:
    try:
        return method(account_ref, *args)
    except TypeError:
        return method(*args)


# ---------------------------------------------------------------------------
# Main callback bridge class
# ---------------------------------------------------------------------------

class _XtQuantTraderCallbackBridge:
    """Thread-safe buffer for official XtQuantTrader callback payloads."""

    def __init__(self, account_id: str = ""):
        self._events: list[dict[str, Any]] = []
        self._event_timestamps: list[datetime] = []
        self._event_keys: set[str] = set()
        self._journal_seq = 0
        self._timestamps_monotonic = True
        self._lock = threading.Lock()
        self._account_id = str(account_id or "")
        self._latest_asset: dict[str, Any] | None = None
        self._latest_positions_by_symbol: dict[str, dict[str, Any]] = {}
        self._latest_connection_event: dict[str, Any] | None = None
        self._projection_dirty_listeners: list[Callable[[dict[str, Any]], None]] = []
        # Per-order callback lifecycle closure tracker. Keyed by
        # ``(account_id, identity_token)`` where ``identity_token`` is the
        # first non-empty match from ``order_remark/client_order_id``,
        # ``order_sysid``, or ``order_id`` — whichever a given callback
        # carries first. We also mirror the closure under the submit-ack
        # ``seq`` so ``get_lifecycle_closure(submit_seq)`` resolves before
        # the real ``broker_order_id`` arrives.
        #
        # Value shape:
        #   {
        #     "submit_ack_received": bool,       # on_order_stock_async_response
        #     "submit_ack_ts": datetime | None,  # latest submit-ack receipt time
        #     "last_order_callback_ts": datetime | None,  # on_stock_order any status
        #     "last_order_status": str,          # latest normalized broker status
        #     "terminal_callback_ts": datetime | None,  # first terminal on_stock_order / on_order_error
        #     "order_error_received": bool,      # on_order_error flag
        #     "trade_callback_count": int,       # on_stock_trade count
        #     "last_trade_callback_ts": datetime | None,
        #     "known_identities": tuple[str, ...],  # all identity tokens we've linked
        #   }
        self._lifecycle_closures: dict[tuple[str, str], dict[str, Any]] = {}
        # Secondary index so lookups can reach the same closure dict through
        # any known alias (broker_order_id / broker_submit_id /
        # order_sysid / client_order_id). Values are the canonical key used in
        # ``_lifecycle_closures``.
        self._lifecycle_closure_aliases: dict[tuple[str, str], tuple[str, str]] = {}

    def on_connected(self) -> None:  # pragma: no cover - exercised via adapter tests
        """Optional / implementation-specific callback.

        Official ``XtQuantTraderCallback`` does not list ``on_connected`` — some
        miniQMT forks emit it, but platform code cannot depend on it. Connected
        status is derived from ``connect()`` return values and successful
        ``query_stock_asset`` responses; this hook is best-effort audit only.
        """
        payload = {
            "_report_kind": "connected",
            "update_time": _utc_now(),
            "status": "connected",
            "account_id": self._account_id,
        }
        event_payload = self._append(payload)
        self._notify_projection_dirty(event_payload)
        with self._lock:
            self._latest_connection_event = dict(payload)

    def on_stock_asset(self, asset) -> None:  # pragma: no cover - exercised via adapter tests
        payload = {
            "_report_kind": "stock_asset",
            "update_time": _get_field(asset, "update_time", default=_utc_now()),
            "account_id": _get_field(asset, "account_id", default=self._account_id),
            "cash": _get_field(asset, "cash", "enable_balance", "m_dAvailable", default=0.0),
            "total_asset": _get_field(
                asset,
                "total_asset",
                "total_balance",
                "m_dBalance",
                default=0.0,
            ),
        }
        event_payload = self._append(payload)
        self._notify_projection_dirty(event_payload)
        with self._lock:
            self._latest_asset = dict(payload)

    def on_stock_position(self, position) -> None:  # pragma: no cover - exercised via adapter tests
        payload = {
            "_report_kind": "stock_position",
            "update_time": _get_field(position, "update_time", default=_utc_now()),
            "account_id": _get_field(position, "account_id", default=self._account_id),
            "stock_code": _get_field(position, "stock_code", "symbol", default=""),
            "volume": _get_field(position, "volume", "current_amount", "m_nVolume", default=0),
        }
        event_payload = self._append(payload)
        self._notify_projection_dirty(event_payload)
        symbol = str(payload.get("stock_code", "") or "")
        if symbol:
            with self._lock:
                self._latest_positions_by_symbol[symbol] = dict(payload)

    def on_stock_order(self, order) -> None:  # pragma: no cover - exercised via adapter tests
        payload = {
            "_report_kind": "order",
            "update_time": _get_field(order, "order_time", "update_time"),
            "account_id": _get_field(order, "account_id", default=self._account_id),
            "client_order_id": _get_field(order, "order_remark", "client_order_id", default=""),
            "order_id": _get_field(order, "order_id", default=""),
            "order_sysid": _get_field(order, "order_sysid", default=""),
            "stock_code": _get_field(order, "stock_code", default=""),
            "side": _get_field(order, "offset_flag", "order_type", "side", default=""),
            "order_status": _get_field(order, "order_status", default="unknown"),
            "order_volume": _get_field(order, "order_volume", default=0),
            "traded_volume": _get_field(order, "traded_volume", default=0),
            "traded_price": _get_field(order, "traded_price", "price", default=0.0),
            "status_msg": _get_field(order, "status_msg", default=""),
        }
        appended = self._append(payload)
        self._update_lifecycle_closure_from_callback(payload, kind="order")
        self._notify_projection_dirty(appended)

    def on_stock_trade(self, trade) -> None:  # pragma: no cover - exercised via adapter tests
        traded_volume = _get_field(trade, "traded_volume", default=0)
        payload = {
            "_report_kind": "trade",
            "update_time": _get_field(trade, "traded_time", "update_time"),
            "account_id": _get_field(trade, "account_id", default=self._account_id),
            "client_order_id": _get_field(trade, "order_remark", "client_order_id", default=""),
            "order_id": _get_field(trade, "order_id", default=""),
            "order_sysid": _get_field(trade, "order_sysid", default=""),
            "trade_no": _get_field(trade, "traded_id", "trade_no", default=""),
            "stock_code": _get_field(trade, "stock_code", default=""),
            "side": _get_field(trade, "offset_flag", "order_type", "side", default=""),
            "order_status": "trade",
            "order_volume": traded_volume,
            "traded_volume": traded_volume,
            "traded_price": _get_field(trade, "traded_price", default=0.0),
            "status_msg": "",
        }
        appended = self._append(payload)
        if appended is not None:
            # Only count a trade callback toward the lifecycle tracker when the
            # bridge actually accepted it as a new event. Duplicate payloads
            # (same trade_no / order / volume / timestamp) would otherwise
            # inflate ``trade_callback_count`` on repeated delivery.
            self._update_lifecycle_closure_from_callback(payload, kind="trade")
        self._notify_projection_dirty(appended)

    def on_order_error(self, order_error) -> None:  # pragma: no cover - exercised via adapter tests
        # Official xtquant ``on_order_error`` corresponds to ORDER_JUNK (57),
        # which ``events.normalize_broker_order_status`` maps into the
        # terminal vocabulary as ``order_error`` (rank 30). We keep the
        # callback's ``order_status="order_error"`` distinct from ``junk``
        # on purpose: downstream execution-report plumbing preserves the
        # distinction ("submit rejected / invalid" vs "active cancel /
        # discard"), but lifecycle-closure treats both as terminal via
        # ``broker_order_status_is_terminal``.
        payload = {
            "_report_kind": "order_error",
            "update_time": _get_field(order_error, "error_time", "update_time"),
            "account_id": _get_field(order_error, "account_id", default=self._account_id),
            "client_order_id": _get_field(order_error, "order_remark", "client_order_id", default=""),
            "order_id": _get_field(order_error, "order_id", default=""),
            "order_sysid": _get_field(order_error, "order_sysid", default=""),
            "stock_code": _get_field(order_error, "stock_code", default=""),
            "side": _get_field(order_error, "offset_flag", "order_type", "side", default=""),
            "order_status": "order_error",
            "order_volume": _get_field(order_error, "order_volume", default=0),
            "traded_volume": 0,
            "traded_price": 0.0,
            "status_msg": _get_field(order_error, "error_msg", "status_msg", default=""),
        }
        appended = self._append(payload)
        self._update_lifecycle_closure_from_callback(payload, kind="order_error")
        self._notify_projection_dirty(appended)

    def on_cancel_error(self, cancel_error) -> None:  # pragma: no cover - exercised via adapter tests
        self._notify_projection_dirty(self._append(
            {
                "_report_kind": "cancel_error",
                "update_time": _get_field(cancel_error, "error_time", "update_time"),
                "account_id": _get_field(cancel_error, "account_id", default=self._account_id),
                "seq": _get_field(cancel_error, "seq", default=""),
                "client_order_id": _get_field(cancel_error, "order_remark", "client_order_id", default=""),
                "order_remark": _get_field(cancel_error, "order_remark", "remark", default=""),
                "order_id": _get_field(cancel_error, "order_id", default=""),
                "order_sysid": _get_field(cancel_error, "order_sysid", default=""),
                "stock_code": _get_field(cancel_error, "stock_code", default=""),
                "side": _get_field(cancel_error, "offset_flag", "order_type", "side", default=""),
                "order_status": "cancel_error",
                "order_volume": 0,
                "traded_volume": 0,
                "traded_price": 0.0,
                "status_msg": _get_field(cancel_error, "error_msg", "status_msg", default=""),
            }
        ))

    def on_order_stock_async_response(self, response) -> None:  # pragma: no cover - exercised via adapter tests
        payload = {
            "_report_kind": "order_stock_async_response",
            "update_time": _get_field(response, "update_time", "order_time", default=_utc_now()),
            "account_id": _get_field(response, "account_id", default=self._account_id),
            "order_id": _get_field(response, "order_id", default=""),
            "seq": _get_field(response, "seq", default=""),
            "strategy_name": _get_field(response, "strategy_name", default=""),
            "order_remark": _get_field(response, "order_remark", "remark", default=""),
            "error_msg": _get_field(response, "error_msg", "status_msg", default=""),
        }
        appended = self._append(payload)
        # Submit-ack is the "intent reached broker" signal. Persist it into
        # the lifecycle closure so ``list_execution_reports`` and
        # ``collect_sync_state`` can distinguish "no callback yet because
        # the broker has not processed the submit" from "broker processed
        # the submit but never pushed lifecycle callbacks" (degraded path).
        self._update_lifecycle_closure_from_callback(payload, kind="submit_ack")
        self._notify_projection_dirty(appended)

    def on_cancel_order_stock_async_response(self, response) -> None:  # pragma: no cover - exercised via adapter tests
        """Optional / implementation-specific callback.

        Official ``XtQuantTraderCallback`` lists ``on_cancel_error`` but does
        *not* list a success counterpart for ``cancel_order_stock_async``;
        success confirmation is supposed to arrive via ``on_stock_order`` with
        the order advancing into status 51/52/53/54. Some miniQMT forks do
        surface this callback — persist it best-effort, but do not rely on it
        for terminal cancel semantics.
        """
        self._notify_projection_dirty(self._append(
            {
                "_report_kind": "cancel_order_stock_async_response",
                "update_time": _get_field(response, "update_time", default=_utc_now()),
                "account_id": _get_field(response, "account_id", default=self._account_id),
                "account_type": _get_field(response, "account_type", default=""),
                "client_order_id": _get_field(response, "order_remark", "client_order_id", default=""),
                "order_id": _get_field(response, "order_id", default=""),
                "order_sysid": _get_field(response, "order_sysid", default=""),
                "cancel_result": _get_field(response, "cancel_result", default=""),
                "seq": _get_field(response, "seq", default=""),
                "order_remark": _get_field(response, "order_remark", "remark", default=""),
                "error_msg": _get_field(response, "error_msg", "status_msg", default=""),
            }
        ))

    def on_account_status(self, status) -> None:  # pragma: no cover - exercised via adapter tests
        """Optional / implementation-specific callback.

        Official ``XtQuantTraderCallback`` does not list ``on_account_status``;
        session health should be derived from ``connect()`` / ``subscribe()``
        return values plus periodic ``query_stock_asset`` liveness. This hook
        is retained as best-effort audit for miniQMT forks that emit it.
        """
        self._notify_projection_dirty(self._append(
            {
                "_report_kind": "account_status",
                "update_time": _get_field(status, "update_time", default=_utc_now()),
                "account_id": _get_field(status, "account_id", default=self._account_id),
                "account_type": _get_field(status, "account_type", default=""),
                "status": _get_field(status, "status", default="unknown"),
            }
        ))

    def on_disconnected(self) -> None:  # pragma: no cover - exercised via adapter tests
        payload = {
            "_report_kind": "disconnected",
            "update_time": _utc_now(),
            "status": "disconnected",
            "account_id": self._account_id,
        }
        event_payload = self._append(payload)
        self._notify_projection_dirty(event_payload)
        with self._lock:
            self._latest_connection_event = dict(payload)

    def record_runtime_event(
        self,
        kind: str,
        *,
        update_time: datetime | None = None,
        **payload: Any,
    ) -> None:
        event_payload = {
            "_report_kind": str(kind or "unknown"),
            "update_time": update_time or _utc_now(),
        }
        event_payload.update(payload)
        appended = self._append(event_payload)
        if str(kind or "") in _QMT_PROJECTION_PUSH_RUNTIME_KINDS:
            self._notify_projection_dirty(appended)
        if str(kind or "") in {
            "session_connected",
            "session_reconnected",
            "connected",
            "disconnected",
            "session_connect_failed",
            "session_reconnect_failed",
            "session_reconnect_deferred",
        }:
            connection_status = str(event_payload.get("status", "") or "")
            if not connection_status:
                if str(kind or "") in {"session_connected", "session_reconnected", "connected"}:
                    connection_status = "connected"
                else:
                    connection_status = "disconnected"
            with self._lock:
                self._latest_connection_event = {
                    "_report_kind": str(kind or "unknown"),
                    "update_time": event_payload.get("update_time"),
                    "status": connection_status,
                }

    def list_events(
        self,
        since: datetime | None = None,
        *,
        since_seq: int | None = None,
        kinds: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
            timestamps = list(self._event_timestamps)
            monotonic = self._timestamps_monotonic
        if since_seq is not None:
            filtered = [
                event
                for event in events
                if int(event.get("_journal_seq", 0) or 0) > since_seq
            ]
        elif since is None or not events:
            filtered = events
        elif monotonic:
            start = bisect_left(timestamps, since)
            filtered = events[start:]
        else:
            filtered = [
                event
                for event in events
                if _coerce_timestamp(event.get("update_time")) >= since
            ]
        if not kinds:
            return filtered
        return [
            event
            for event in filtered
            if str(event.get("_report_kind", "")) in kinds
        ]

    def current_journal_seq(self) -> int:
        with self._lock:
            return int(self._journal_seq)

    def snapshot_stats(self) -> dict[str, Any]:
        execution_kinds = {"order", "trade", "order_error"}
        runtime_kinds = {
            "connected",
            "stock_asset",
            "stock_position",
            "account_status",
            "order_stock_async_response",
            "cancel_order_stock_async_response",
            "cancel_error",
            "disconnected",
            *_QMT_SESSION_RUNTIME_KINDS,
        }
        callback_runtime_kinds = {
            "connected",
            "stock_asset",
            "stock_position",
            "account_status",
            "order_stock_async_response",
            "cancel_order_stock_async_response",
            "cancel_error",
            "disconnected",
        }
        with self._lock:
            events = list(self._events)
            timestamps = list(self._event_timestamps)
        latest_event_at = max(timestamps) if timestamps else None
        latest_callback_at = None
        latest_execution_at = None
        latest_runtime_at = None
        latest_asset_callback_at = None
        latest_position_callback_at = None
        execution_event_count = 0
        runtime_event_count = 0
        for event, event_ts in zip(events, timestamps, strict=False):
            kind = str(event.get("_report_kind", "") or "")
            if kind in execution_kinds:
                execution_event_count += 1
                latest_execution_at = _latest_timestamp(latest_execution_at, event_ts)
                latest_callback_at = _latest_timestamp(latest_callback_at, event_ts)
            elif kind in runtime_kinds:
                runtime_event_count += 1
                if kind in callback_runtime_kinds:
                    latest_runtime_at = _latest_timestamp(latest_runtime_at, event_ts)
                    latest_callback_at = _latest_timestamp(latest_callback_at, event_ts)
                    if kind == "stock_asset":
                        latest_asset_callback_at = _latest_timestamp(
                            latest_asset_callback_at,
                            event_ts,
                        )
                    elif kind == "stock_position":
                        latest_position_callback_at = _latest_timestamp(
                            latest_position_callback_at,
                            event_ts,
                        )
        return {
            "buffered_event_count": len(events),
            "execution_event_count": execution_event_count,
            "runtime_event_count": runtime_event_count,
            "latest_event_at": latest_event_at,
            "latest_callback_at": latest_callback_at,
            "latest_execution_at": latest_execution_at,
            "latest_runtime_at": latest_runtime_at,
            "latest_asset_callback_at": latest_asset_callback_at,
            "latest_position_callback_at": latest_position_callback_at,
        }

    def get_latest_asset(self) -> dict[str, Any] | None:
        with self._lock:
            if self._latest_asset is None:
                return None
            return dict(self._latest_asset)

    def get_latest_positions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(payload)
                for _, payload in sorted(self._latest_positions_by_symbol.items())
            ]

    def get_connection_state(self) -> dict[str, Any] | None:
        with self._lock:
            if self._latest_connection_event is None:
                return None
            return dict(self._latest_connection_event)

    def register_projection_dirty_listener(
        self,
        listener: Callable[[dict[str, Any]], None],
    ) -> None:
        with self._lock:
            self._projection_dirty_listeners.append(listener)

    # ------------------------------------------------------------------
    # Lifecycle-closure tracker: records per-order callback lifecycle so
    # downstream sync code can decide whether pure-callback order-state
    # closure is sufficient, or whether the caller must still merge with
    # ``query_stock_orders / query_stock_trades`` output for degraded
    # observation paths.
    # ------------------------------------------------------------------

    @staticmethod
    def _lifecycle_identity_tokens(payload: dict[str, Any]) -> list[str]:
        """Return the non-empty identity tokens a callback payload carries.

        Official xtquant callbacks usually carry ``order_remark`` (our
        ``client_order_id``), ``order_sysid``, and ``order_id``; the submit
        ack additionally carries ``seq``. Any of these can identify the
        same order as it transitions through the lifecycle, so we index
        the closure under all non-empty tokens.
        """
        tokens: list[str] = []
        for field in (
            "order_remark",
            "client_order_id",
            "order_sysid",
            "order_id",
            "seq",
        ):
            value = str(payload.get(field, "") or "").strip()
            if value and value not in tokens:
                tokens.append(value)
        return tokens

    def _lookup_lifecycle_closure_locked(
        self, account_id: str, identity_tokens: list[str]
    ) -> tuple[tuple[str, str] | None, dict[str, Any] | None]:
        for token in identity_tokens:
            key = (account_id, token)
            if key in self._lifecycle_closures:
                return key, self._lifecycle_closures[key]
            canonical = self._lifecycle_closure_aliases.get(key)
            if canonical is not None and canonical in self._lifecycle_closures:
                return canonical, self._lifecycle_closures[canonical]
        return None, None

    def _ensure_lifecycle_closure_locked(
        self, account_id: str, identity_tokens: list[str]
    ) -> tuple[tuple[str, str], dict[str, Any]] | None:
        if not identity_tokens:
            return None
        canonical_key, closure = self._lookup_lifecycle_closure_locked(
            account_id, identity_tokens
        )
        if closure is None:
            canonical_key = (account_id, identity_tokens[0])
            closure = {
                "submit_ack_received": False,
                "submit_ack_ts": None,
                "last_order_callback_ts": None,
                "last_order_status": "",
                "terminal_callback_ts": None,
                "order_error_received": False,
                "trade_callback_count": 0,
                "last_trade_callback_ts": None,
                "known_identities": tuple(identity_tokens),
            }
            self._lifecycle_closures[canonical_key] = closure
        # Merge any new identity tokens into the known set and alias index.
        known = list(closure.get("known_identities", ()) or ())
        added = False
        for token in identity_tokens:
            if token and token not in known:
                known.append(token)
                added = True
            alias_key = (account_id, token)
            if alias_key != canonical_key:
                self._lifecycle_closure_aliases[alias_key] = canonical_key
        if added:
            closure["known_identities"] = tuple(known)
        return canonical_key, closure

    def _update_lifecycle_closure_from_callback(
        self,
        payload: dict[str, Any],
        *,
        kind: str,
    ) -> None:
        account_id = str(payload.get("account_id", "") or self._account_id or "")
        tokens = self._lifecycle_identity_tokens(payload)
        if not tokens:
            return
        event_ts = _coerce_timestamp(payload.get("update_time"))
        with self._lock:
            result = self._ensure_lifecycle_closure_locked(account_id, tokens)
            if result is None:
                return
            _, closure = result
            if kind == "submit_ack":
                closure["submit_ack_received"] = True
                closure["submit_ack_ts"] = _latest_timestamp(
                    closure.get("submit_ack_ts"), event_ts
                )
                return
            if kind == "order":
                status_raw = payload.get("order_status", "")
                # Pre-map xtquant numeric order_status codes (48..57, 255)
                # to the events.py vocabulary before normalization. Raw
                # ``"55"`` / ``55`` payloads would otherwise pass through
                # ``normalize_broker_order_status`` untouched and stay as
                # their numeric string, defeating lifecycle-terminal
                # detection.
                status_pre = _pre_normalize_qmt_numeric_order_status(status_raw)
                traded_volume = int(payload.get("traded_volume", 0) or 0)
                order_volume = int(payload.get("order_volume", 0) or 0)
                remaining = max(order_volume - traded_volume, 0)
                normalized = normalize_broker_order_status(
                    status_pre,
                    filled_shares=traded_volume,
                    remaining_shares=remaining,
                )
                closure["last_order_callback_ts"] = _latest_timestamp(
                    closure.get("last_order_callback_ts"), event_ts
                )
                closure["last_order_status"] = normalized
                if broker_order_status_is_terminal(normalized):
                    closure["terminal_callback_ts"] = (
                        closure.get("terminal_callback_ts") or event_ts
                    )
                return
            if kind == "trade":
                closure["trade_callback_count"] = int(
                    closure.get("trade_callback_count", 0) or 0
                ) + 1
                closure["last_trade_callback_ts"] = _latest_timestamp(
                    closure.get("last_trade_callback_ts"), event_ts
                )
                return
            if kind == "order_error":
                closure["order_error_received"] = True
                closure["last_order_status"] = "order_error"
                closure["last_order_callback_ts"] = _latest_timestamp(
                    closure.get("last_order_callback_ts"), event_ts
                )
                # ``on_order_error`` corresponds to ORDER_JUNK=57 — terminal.
                # Downstream ``normalize_broker_order_status`` treats both
                # ``order_error`` and ``junk`` as terminal rank 30, and the
                # broker execution report path preserves ``order_error`` to
                # distinguish "broker rejected the ack" from
                # "broker actively discarded a valid order".
                closure["terminal_callback_ts"] = (
                    closure.get("terminal_callback_ts") or event_ts
                )
                return

    def get_lifecycle_closure(
        self,
        identity_token: str,
        *,
        account_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the lifecycle-closure snapshot for an order identity.

        ``identity_token`` can be any of ``broker_submit_id`` (submit ack
        ``seq``), ``broker_order_id`` / ``order_id`` / ``order_sysid``, or
        ``client_order_id`` / ``order_remark``. Lookup is scoped by
        ``account_id`` when provided; otherwise the bridge's owning
        account id is used.
        """
        token = str(identity_token or "").strip()
        if not token:
            return None
        scope = str(account_id or self._account_id or "")
        with self._lock:
            _, closure = self._lookup_lifecycle_closure_locked(scope, [token])
            if closure is None:
                return None
            return {
                "submit_ack_received": bool(closure.get("submit_ack_received", False)),
                "submit_ack_ts": closure.get("submit_ack_ts"),
                "last_order_callback_ts": closure.get("last_order_callback_ts"),
                "last_order_status": str(closure.get("last_order_status", "") or ""),
                "terminal_callback_ts": closure.get("terminal_callback_ts"),
                "order_error_received": bool(closure.get("order_error_received", False)),
                "trade_callback_count": int(closure.get("trade_callback_count", 0) or 0),
                "last_trade_callback_ts": closure.get("last_trade_callback_ts"),
                "known_identities": tuple(closure.get("known_identities", ()) or ()),
            }

    def snapshot_lifecycle_closures(self) -> list[dict[str, Any]]:
        """Return a snapshot of every lifecycle-closure currently tracked."""
        with self._lock:
            result: list[dict[str, Any]] = []
            for (account_id, _token), closure in self._lifecycle_closures.items():
                entry = dict(closure)
                entry["account_id"] = account_id
                entry["known_identities"] = tuple(
                    entry.get("known_identities", ()) or ()
                )
                result.append(entry)
            return result

    def _append(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        event_ts = _coerce_timestamp(payload.get("update_time"))
        event_key = self._event_key(payload, event_ts)
        with self._lock:
            if event_key in self._event_keys:
                return None
            next_seq = self._journal_seq + 1
            if self._event_timestamps and event_ts < self._event_timestamps[-1]:
                self._timestamps_monotonic = False
            event_payload = dict(payload)
            event_payload["_journal_seq"] = next_seq
            self._events.append(event_payload)
            self._event_timestamps.append(event_ts)
            self._event_keys.add(event_key)
            self._journal_seq = next_seq
            return dict(event_payload)

    def _notify_projection_dirty(self, event_payload: dict[str, Any] | None) -> None:
        if not isinstance(event_payload, dict):
            return
        with self._lock:
            listeners = list(self._projection_dirty_listeners)
        for listener in listeners:
            try:
                listener(dict(event_payload))
            except Exception:
                logger.warning("QMT projection dirty listener failed", exc_info=True)

    @staticmethod
    def _event_key(payload: dict[str, Any], event_ts: datetime) -> str:
        return "|".join(
            [
                str(payload.get("_report_kind", "")),
                str(payload.get("account_id", "")),
                str(payload.get("stock_code", "")),
                str(payload.get("trade_no", "")),
                str(payload.get("order_id", "")),
                str(payload.get("order_sysid", "")),
                str(payload.get("client_order_id", "")),
                str(payload.get("order_status", "")),
                str(payload.get("traded_volume", "")),
                str(payload.get("volume", "")),
                str(payload.get("cash", "")),
                event_ts.isoformat(),
            ]
        )
