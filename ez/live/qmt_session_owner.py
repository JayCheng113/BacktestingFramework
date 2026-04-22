"""Resident QMT session owner and callback bridge.

This module owns the process-local XtQuantTrader lifecycle, callback buffer,
session reuse semantics, and the official async submit/cancel surface for QMT.
It is intentionally standalone so qmt_broker can re-export these symbols
without duplicating the resident-owner implementation.
"""
from __future__ import annotations

from bisect import bisect_left
import importlib
import logging
import os
import random
import threading
import weakref
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from ez.live._utils import (
    utc_now as _utc_now,
    coerce_timestamp as _coerce_timestamp,
    get_field as _get_field,
    qmt_request_failed_immediately as _qmt_request_failed_immediately,
)
from ez.live.events import (
    broker_order_status_is_terminal,
    broker_order_status_rank,
    normalize_broker_order_status,
)


logger = logging.getLogger(__name__)


_QMT_CALLBACK_ACCOUNT_FRESHNESS_MAX_AGE = timedelta(minutes=5)
# Execution callback freshness: if the callback buffer has not received any
# execution/runtime event within this window, incremental execution sync
# falls back to callback + query merge. This closes the gap where the
# consumer is alive but the broker simply did not push anything, and a
# fresh query result would otherwise be silently dropped.
_QMT_CALLBACK_MAX_GAP_SECS = 30.0
_QMT_RECONNECT_BASE_BACKOFF = timedelta(seconds=5)
# Cap reconnect backoff so the worst case stays bounded regardless of
# consecutive failures. Jitter is applied multiplicatively so retries from
# multiple owners do not align into a thundering herd.
_QMT_RECONNECT_MAX_BACKOFF = timedelta(seconds=60)
_QMT_RECONNECT_JITTER_FRACTION = 0.2


def _apply_reconnect_jitter(
    backoff: timedelta,
    *,
    max_backoff: timedelta = _QMT_RECONNECT_MAX_BACKOFF,
    jitter_fraction: float = _QMT_RECONNECT_JITTER_FRACTION,
    rng: Callable[[float, float], float] | None = None,
) -> timedelta:
    """Cap reconnect backoff at max_backoff and apply symmetric jitter.

    The cap is applied first so multiplicative jitter expands around the
    capped value (so the upper bound is `max_backoff * (1 + jitter)` and
    the lower bound stays well above zero).
    """
    uniform = rng if rng is not None else random.uniform
    capped_seconds = min(backoff.total_seconds(), max_backoff.total_seconds())
    jitter = uniform(-jitter_fraction, jitter_fraction)
    jittered = max(0.0, capped_seconds * (1.0 + jitter))
    return timedelta(seconds=jittered)


# Official xtquant XtOrderStatus numeric codes. Pre-normalize these into the
# events.py broker-order vocabulary before downstream callers run through
# normalize_broker_order_status(). Keys are strings to match payloads coming
# from JSON / dict-of-str forms; integer keys are also accepted at lookup
# time via str() coercion.
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


def _max_journal_seq(events: list[dict[str, Any]], default: int | None = None) -> int | None:
    seqs = [int(event.get("_journal_seq", 0) or 0) for event in events]
    if seqs:
        return max(seqs)
    return default


def _stable_positive_int(seed: str) -> int:
    value = zlib.crc32(seed.encode("utf-8")) & 0x7FFFFFFF
    return value or 1


def _coerce_qmt_session_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    stripped = str(value).strip()
    if not stripped:
        return None
    try:
        numeric = int(stripped)
    except ValueError:
        return _stable_positive_int(stripped)
    return numeric if numeric > 0 else None


def _coerce_qmt_order_id(value: Any) -> int | None:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    try:
        numeric = int(stripped)
    except ValueError:
        return None
    return numeric if numeric > 0 else None


def _resolve_qmt_session_id(config: "QMTBrokerConfig") -> int:
    configured = _coerce_qmt_session_id(config.session_id)
    if configured is not None:
        return configured
    seed = "|".join(
        [
            str(os.getpid()),
            str(config.install_path or ""),
            str(config.account_type or "stock").lower(),
            str(config.account_id or ""),
        ]
    )
    return _stable_positive_int(seed)


def _resolve_qmt_market(symbol: str) -> int:
    normalized = str(symbol or "").upper()
    if normalized.endswith(".SH"):
        constant_name = "SH_MARKET"
        fallback = 0
    elif normalized.endswith(".SZ"):
        constant_name = "SZ_MARKET"
        fallback = 1
    elif normalized.endswith(".BJ"):
        constant_name = "MARKET_ENUM_BEIJING"
        fallback = None
    else:
        raise ValueError(
            "QMT cancel_order_stock_sysid requires a symbol with .SH/.SZ/.BJ suffix"
        )
    try:
        xtconstant_mod = importlib.import_module("xtquant.xtconstant")
    except ModuleNotFoundError:
        xtconstant_mod = None
    if xtconstant_mod is not None:
        constant_value = getattr(xtconstant_mod, constant_name, None)
        if constant_value is not None:
            return int(constant_value)
    if fallback is not None:
        return fallback
    raise RuntimeError(
        f"xtquant.xtconstant.{constant_name} is required to cancel QMT {normalized[-3:]} orders"
    )


def _resolve_qmt_order_side(side: str) -> int:
    normalized = str(side or "").strip().lower()
    if normalized not in {"buy", "sell"}:
        raise ValueError("QMT submit_order requires side 'buy' or 'sell'")
    try:
        xtconstant_mod = importlib.import_module("xtquant.xtconstant")
    except ModuleNotFoundError as exc:
        raise RuntimeError("xtquant.xtconstant is required to submit QMT orders") from exc
    constant_name = "STOCK_BUY" if normalized == "buy" else "STOCK_SELL"
    constant_value = getattr(xtconstant_mod, constant_name, None)
    if constant_value is None:
        raise RuntimeError(
            f"xtquant.xtconstant.{constant_name} is required to submit QMT {normalized} orders"
        )
    return int(constant_value)


def _resolve_qmt_price_type(price_type: Any | None = None) -> Any:
    if price_type is not None:
        return price_type
    try:
        xtconstant_mod = importlib.import_module("xtquant.xtconstant")
    except ModuleNotFoundError as exc:
        raise RuntimeError("xtquant.xtconstant is required to submit QMT orders") from exc
    constant_value = getattr(xtconstant_mod, "FIX_PRICE", None)
    if constant_value is None:
        raise RuntimeError("xtquant.xtconstant.FIX_PRICE is required to submit QMT orders")
    return constant_value


@dataclass(slots=True)
class QMTBrokerConfig:
    account_id: str
    account_type: str = "stock"
    install_path: str = ""
    session_id: int | str | None = None
    enable_cancel: bool = False
    always_on_owner: bool = False


class QMTClientProtocol(Protocol):
    def query_stock_asset(self, account_id: str) -> Any: ...
    def query_stock_positions(self, account_id: str) -> list[Any]: ...
    def query_stock_orders(self, account_id: str) -> list[Any]: ...
    def query_stock_trades(self, account_id: str) -> list[Any]: ...
    def list_execution_reports(self, since: datetime | None = None) -> list[Any]: ...
    def list_runtime_events(self, since: datetime | None = None) -> list[Any]: ...
    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        shares: int,
        price: float,
        strategy_name: str,
        order_remark: str,
        price_type: Any | None = None,
    ) -> Any: ...
    def cancel_order(self, order_id: str, symbol: str = "") -> Any: ...


@dataclass(frozen=True, slots=True)
class QMTSessionKey:
    account_id: str
    account_type: str
    install_path: str
    session_id: str
    factory_key: str


@dataclass(slots=True)
class QMTSessionState:
    session_key: QMTSessionKey
    status: str
    acquisition_count: int = 0
    owner_count: int = 0
    process_owner_count: int = 0
    host_owner_pinned: bool = False
    attached_deployments: tuple[str, ...] = ()
    process_owner_ids: tuple[str, ...] = ()
    created_at: datetime | None = None
    last_acquired_at: datetime | None = None
    last_error: str = ""
    consumer_status: str = "unknown"
    consumer_restart_count: int = 0
    last_consumer_event_at: datetime | None = None


class QMTSessionManager:
    """Process-local shared owner for xtquant sessions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients: dict[QMTSessionKey, QMTClientProtocol] = {}
        self._states: dict[QMTSessionKey, QMTSessionState] = {}
        self._events: dict[QMTSessionKey, list[dict[str, Any]]] = {}
        self._event_timestamps: dict[QMTSessionKey, list[datetime]] = {}
        self._event_sequences: dict[QMTSessionKey, int] = {}
        self._owners: dict[QMTSessionKey, set[str]] = {}
        self._process_owners: dict[QMTSessionKey, set[str]] = {}
        self._host_owner_pins: set[QMTSessionKey] = set()
        self._client_callback_listener_keys: set[QMTSessionKey] = set()
        self._deployment_callback_listeners: dict[int, Any] = {}
        self._next_deployment_callback_listener_token = 0
        # Persistent listeners survive `clear()` so callers (e.g. Scheduler) can
        # register once at startup and still receive projection-dirty callbacks
        # even after the session table has been recycled. Each listener is a
        # small `(key_ref, callback_ref)` pair; keys let us reuse the same weak-
        # method semantics that `_deployment_callback_listeners` already uses.
        self._persistent_refresh_listeners: dict[int, Any] = {}
        self._next_persistent_refresh_listener_token = 0

    @staticmethod
    def _copy_state(state: QMTSessionState) -> QMTSessionState:
        return QMTSessionState(
            session_key=state.session_key,
            status=state.status,
            acquisition_count=state.acquisition_count,
            owner_count=state.owner_count,
            process_owner_count=state.process_owner_count,
            host_owner_pinned=state.host_owner_pinned,
            attached_deployments=state.attached_deployments,
            process_owner_ids=state.process_owner_ids,
            created_at=state.created_at,
            last_acquired_at=state.last_acquired_at,
            last_error=state.last_error,
            consumer_status=state.consumer_status,
            consumer_restart_count=state.consumer_restart_count,
            last_consumer_event_at=state.last_consumer_event_at,
        )

    def _is_host_owner_pinned_locked(self, key: QMTSessionKey) -> bool:
        return key in self._host_owner_pins or bool(self._process_owners.get(key))

    def _update_state_owner_fields_locked(
        self,
        key: QMTSessionKey,
        state: QMTSessionState,
    ) -> None:
        deployment_owners = self._owners.get(key, set())
        process_owners = self._process_owners.get(key, set())
        state.owner_count = len(deployment_owners)
        state.process_owner_count = len(process_owners)
        state.host_owner_pinned = self._is_host_owner_pinned_locked(key)
        state.attached_deployments = tuple(sorted(deployment_owners))
        state.process_owner_ids = tuple(sorted(process_owners))

    def resolve(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
    ) -> QMTClientProtocol:
        key = self.make_key(config=config, factory=factory)
        event_ts = _utc_now()
        with self._lock:
            current = self._clients.get(key)
            if current is not None:
                self._ensure_client_callback_listener_locked(key, current)
                state = self._states.get(key)
                if state is None:
                    state = QMTSessionState(session_key=key, status="reused")
                    self._states[key] = state
                state.status = "reused"
                state.acquisition_count += 1
                if config.always_on_owner:
                    self._host_owner_pins.add(key)
                self._update_state_owner_fields_locked(key, state)
                state.last_acquired_at = event_ts
                self._record_event(
                    key,
                    {
                        "_report_kind": "session_owner_reused",
                        "update_time": event_ts,
                        "account_id": key.account_id,
                        "account_type": key.account_type,
                        "session_id": key.session_id,
                        "acquisition_count": state.acquisition_count,
                    },
                )
                return current
            try:
                client = factory(config)
            except Exception as exc:
                state = self._states.get(key)
                if state is None:
                    state = QMTSessionState(session_key=key, status="create_failed")
                    self._states[key] = state
                state.status = "create_failed"
                state.last_acquired_at = event_ts
                state.last_error = str(exc)
                state.acquisition_count += 1
                self._record_event(
                    key,
                    {
                        "_report_kind": "session_owner_create_failed",
                        "update_time": event_ts,
                        "account_id": key.account_id,
                        "account_type": key.account_type,
                        "session_id": key.session_id,
                        "acquisition_count": state.acquisition_count,
                        "error_msg": str(exc),
                    },
                )
                raise
            self._ensure_client_callback_listener_locked(key, client)
            self._clients[key] = client
            state = self._states.get(key)
            if state is None:
                state = QMTSessionState(session_key=key, status="created")
                self._states[key] = state
            state.status = "created"
            state.acquisition_count += 1
            if config.always_on_owner:
                self._host_owner_pins.add(key)
            self._update_state_owner_fields_locked(key, state)
            state.created_at = state.created_at or event_ts
            state.last_acquired_at = event_ts
            state.last_error = ""
            self._record_event(
                key,
                {
                    "_report_kind": "session_owner_created",
                    "update_time": event_ts,
                    "account_id": key.account_id,
                    "account_type": key.account_type,
                    "session_id": key.session_id,
                    "acquisition_count": state.acquisition_count,
                },
            )
            return client

    def attach_owner(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
        deployment_id: str,
    ) -> QMTClientProtocol:
        client = self.resolve(config=config, factory=factory)
        key = self.make_key(config=config, factory=factory)
        event_ts = _utc_now()
        should_start_consumer = False
        with self._lock:
            if config.always_on_owner:
                self._host_owner_pins.add(key)
            owners = self._owners.setdefault(key, set())
            if deployment_id in owners:
                return client
            owners.add(deployment_id)
            state = self._states.setdefault(
                key, QMTSessionState(session_key=key, status="attached")
            )
            state.status = "attached"
            self._update_state_owner_fields_locked(key, state)
            state.last_acquired_at = event_ts
            self._record_event(
                key,
                {
                    "_report_kind": "session_owner_attached",
                    "update_time": event_ts,
                    "account_id": key.account_id,
                    "account_type": key.account_type,
                    "session_id": key.session_id,
                    "deployment_id": deployment_id,
                    "owner_count": state.owner_count,
                },
            )
            should_start_consumer = state.owner_count == 1
        ensure_consumer = getattr(client, "ensure_callback_consumer", None)
        if should_start_consumer and callable(ensure_consumer):
            ensure_consumer()
        return client

    def detach_owner(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
        deployment_id: str,
    ) -> QMTSessionState | None:
        key = self.make_key(config=config, factory=factory)
        event_ts = _utc_now()
        with self._lock:
            state = self._states.get(key)
            owners = self._owners.setdefault(key, set())
            if deployment_id not in owners:
                return None if state is None else self._copy_state(state)
            owners.remove(deployment_id)
            state = self._states.setdefault(
                key, QMTSessionState(session_key=key, status="detached")
            )
            state.status = "detached"
            self._update_state_owner_fields_locked(key, state)
            self._record_event(
                key,
                {
                    "_report_kind": "session_owner_detached",
                    "update_time": event_ts,
                    "account_id": key.account_id,
                    "account_type": key.account_type,
                    "session_id": key.session_id,
                    "deployment_id": deployment_id,
                    "owner_count": state.owner_count,
                },
            )
            current = self._clients.get(key)
            if owners or current is None:
                return self._copy_state(state)
            if self._is_host_owner_pinned_locked(key):
                state.status = "resident"
                state.last_error = ""
                self._record_event(
                    key,
                    {
                        "_report_kind": "session_owner_resident",
                        "update_time": event_ts,
                        "account_id": key.account_id,
                        "account_type": key.account_type,
                        "session_id": key.session_id,
                        "resident_owner": True,
                    },
                )
                return self._copy_state(state)
            close_result = "closed"
            close_error = ""
            close_fn = (
                getattr(current, "close", None)
                or getattr(current, "stop", None)
                or getattr(current, "shutdown", None)
            )
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as exc:
                    close_result = "close_failed"
                    close_error = str(exc)
            self._harvest_client_runtime_events_locked(key, current)
            if close_result == "closed":
                self._record_event(
                    key,
                    {
                        "_report_kind": "session_owner_closed",
                        "update_time": event_ts,
                        "account_id": key.account_id,
                        "account_type": key.account_type,
                        "session_id": key.session_id,
                    },
                )
            else:
                self._record_event(
                    key,
                    {
                        "_report_kind": "session_owner_close_failed",
                        "update_time": event_ts,
                        "account_id": key.account_id,
                        "account_type": key.account_type,
                        "session_id": key.session_id,
                        "error_msg": close_error,
                    },
                )
            self._clients.pop(key, None)
            self._client_callback_listener_keys.discard(key)
            state.status = close_result
            state.last_error = close_error
            return self._copy_state(state)

    def pin_process_owner(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
        owner_id: str,
        start_consumer: bool = True,
    ) -> QMTClientProtocol:
        client = self.resolve(config=config, factory=factory)
        key = self.make_key(config=config, factory=factory)
        event_ts = _utc_now()
        owner_id = str(owner_id or "").strip()
        if not owner_id:
            raise ValueError("QMT process owner requires a non-empty owner_id")
        should_start_consumer = False
        with self._lock:
            owners = self._process_owners.setdefault(key, set())
            if owner_id in owners:
                return client
            owners.add(owner_id)
            state = self._states.setdefault(
                key, QMTSessionState(session_key=key, status="process_pinned")
            )
            state.status = "process_pinned"
            self._update_state_owner_fields_locked(key, state)
            state.last_acquired_at = event_ts
            self._record_event(
                key,
                {
                    "_report_kind": "session_owner_process_pinned",
                    "update_time": event_ts,
                    "account_id": key.account_id,
                    "account_type": key.account_type,
                    "session_id": key.session_id,
                    "process_owner_id": owner_id,
                    "process_owner_count": state.process_owner_count,
                    "owner_count": state.owner_count,
                },
            )
            should_start_consumer = start_consumer and state.process_owner_count == 1
        if should_start_consumer:
            ensure_consumer = getattr(client, "ensure_callback_consumer", None)
            if callable(ensure_consumer):
                ensure_consumer()
        return client

    def unpin_process_owner(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
        owner_id: str,
    ) -> QMTSessionState | None:
        key = self.make_key(config=config, factory=factory)
        event_ts = _utc_now()
        owner_id = str(owner_id or "").strip()
        with self._lock:
            state = self._states.get(key)
            owners = self._process_owners.setdefault(key, set())
            if owner_id not in owners:
                return None if state is None else self._copy_state(state)
            owners.remove(owner_id)
            if not owners:
                self._process_owners.pop(key, None)
            state = self._states.setdefault(
                key, QMTSessionState(session_key=key, status="process_unpinned")
            )
            state.status = "process_unpinned"
            self._update_state_owner_fields_locked(key, state)
            self._record_event(
                key,
                {
                    "_report_kind": "session_owner_process_unpinned",
                    "update_time": event_ts,
                    "account_id": key.account_id,
                    "account_type": key.account_type,
                    "session_id": key.session_id,
                    "process_owner_id": owner_id,
                    "process_owner_count": state.process_owner_count,
                    "owner_count": state.owner_count,
                },
            )
            current = self._clients.get(key)
            if state.owner_count > 0 or current is None:
                return self._copy_state(state)
            if self._is_host_owner_pinned_locked(key):
                state.status = "resident"
                state.last_error = ""
                self._record_event(
                    key,
                    {
                        "_report_kind": "session_owner_resident",
                        "update_time": event_ts,
                        "account_id": key.account_id,
                        "account_type": key.account_type,
                        "session_id": key.session_id,
                        "resident_owner": True,
                    },
                )
                return self._copy_state(state)
            close_result = "closed"
            close_error = ""
            close_fn = (
                getattr(current, "close", None)
                or getattr(current, "stop", None)
                or getattr(current, "shutdown", None)
            )
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as exc:
                    close_result = "close_failed"
                    close_error = str(exc)
            self._harvest_client_runtime_events_locked(key, current)
            if close_result == "closed":
                self._record_event(
                    key,
                    {
                        "_report_kind": "session_owner_closed",
                        "update_time": event_ts,
                        "account_id": key.account_id,
                        "account_type": key.account_type,
                        "session_id": key.session_id,
                    },
                )
            else:
                self._record_event(
                    key,
                    {
                        "_report_kind": "session_owner_close_failed",
                        "update_time": event_ts,
                        "account_id": key.account_id,
                        "account_type": key.account_type,
                        "session_id": key.session_id,
                        "error_msg": close_error,
                    },
                )
            self._clients.pop(key, None)
            self._client_callback_listener_keys.discard(key)
            state.status = close_result
            state.last_error = close_error
            return self._copy_state(state)

    def clear(self) -> None:
        """Reset all session state but preserve persistent refresh listeners.

        Deployment-scoped callback listeners (`register_deployment_callback_listener`)
        are cleared because they are tied to specific scheduler run-loops, but
        persistent refresh listeners registered via `register_refresh_listener`
        are retained so long-lived owners (Scheduler, Monitor) keep receiving
        projection-dirty notifications across manager recycles.
        """
        clients: list[tuple[QMTSessionKey, QMTClientProtocol]]
        with self._lock:
            clients = list(self._clients.items())
            self._clients.clear()
            self._states.clear()
            self._events.clear()
            self._event_timestamps.clear()
            self._event_sequences.clear()
            self._owners.clear()
            self._process_owners.clear()
            self._host_owner_pins.clear()
            self._client_callback_listener_keys.clear()
            self._deployment_callback_listeners.clear()
            self._next_deployment_callback_listener_token = 0
            # Persistent listeners intentionally survive clear().
        for _, client in clients:
            close_fn = (
                getattr(client, "close", None)
                or getattr(client, "stop", None)
                or getattr(client, "shutdown", None)
            )
            if not callable(close_fn):
                continue
            try:
                close_fn()
            except Exception:
                logger.warning("QMT session manager client close failed during clear()", exc_info=True)

    def active_session_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def register_deployment_callback_listener(
        self,
        listener: Callable[..., None],
    ) -> int:
        with self._lock:
            self._next_deployment_callback_listener_token += 1
            token = self._next_deployment_callback_listener_token
            self._deployment_callback_listeners[token] = self._make_listener_ref(listener)
            return token

    def unregister_deployment_callback_listener(self, token: int) -> None:
        with self._lock:
            self._deployment_callback_listeners.pop(int(token), None)

    def has_deployment_callback_listener(self, token: int) -> bool:
        with self._lock:
            return int(token) in self._deployment_callback_listeners

    def register_refresh_listener(
        self,
        listener: Callable[..., None],
    ) -> int:
        """Register a projection-dirty listener that survives `clear()`.

        Unlike `register_deployment_callback_listener`, listeners registered
        here are *not* cleared by `clear()`, so long-lived owners can survive
        session manager recycles without silently losing callback routing.
        Newly-created client sessions automatically have these listeners
        attached via the same client-side dirty-listener hook used for the
        deployment-scoped path.
        """
        with self._lock:
            self._next_persistent_refresh_listener_token += 1
            token = self._next_persistent_refresh_listener_token
            self._persistent_refresh_listeners[token] = self._make_listener_ref(listener)
            return token

    def unregister_refresh_listener(self, token: int) -> None:
        with self._lock:
            self._persistent_refresh_listeners.pop(int(token), None)

    def has_refresh_listener(self, token: int) -> bool:
        with self._lock:
            return int(token) in self._persistent_refresh_listeners

    def has_active_client(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
    ) -> bool:
        key = self.make_key(config=config, factory=factory)
        with self._lock:
            return key in self._clients

    def get_state(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
    ) -> QMTSessionState | None:
        key = self.make_key(config=config, factory=factory)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return None
            return self._copy_state(state)

    def list_session_states(self) -> list[QMTSessionState]:
        with self._lock:
            states = list(self._states.values())
        return [self._copy_state(state) for state in states]

    def list_runtime_events(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
        since: datetime | None = None,
        since_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        key = self.make_key(config=config, factory=factory)
        with self._lock:
            events = list(self._events.get(key, ()))
        if since is None or not events:
            if since_seq is None:
                return events
        if since_seq is not None:
            return [
                event
                for event in events
                if int(event.get("_journal_seq", 0) or 0) > since_seq
            ]
        return [
            event
            for event in events
            if _coerce_timestamp(event.get("update_time")) >= since
        ]

    def get_runtime_journal_seq(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
    ) -> int:
        key = self.make_key(config=config, factory=factory)
        with self._lock:
            return int(self._event_sequences.get(key, 0) or 0)

    def ensure_session_supervision(
        self,
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
    ) -> QMTSessionState | None:
        key = self.make_key(config=config, factory=factory)
        with self._lock:
            state = self._states.get(key)
            current = self._clients.get(key)
        if state is None:
            return None
        if current is None:
            return self.get_state(config=config, factory=factory)

        event_ts = _utc_now()
        is_alive = getattr(current, "is_callback_consumer_alive", None)
        alive = bool(is_alive()) if callable(is_alive) else False
        ensure_resident = getattr(current, "ensure_resident_session", None)
        needs_reconnect = getattr(current, "needs_resident_session_reconnect", None)
        reconnect_required = (
            bool(needs_reconnect()) if callable(needs_reconnect) else False
        )
        if alive and not reconnect_required:
            with self._lock:
                live_state = self._states.get(key)
                if live_state is not None:
                    live_state.consumer_status = "running"
                    live_state.last_consumer_event_at = event_ts
            return self.get_state(config=config, factory=factory)

        ensure_consumer = ensure_resident or getattr(current, "ensure_callback_consumer", None)
        if not callable(ensure_consumer):
            with self._lock:
                live_state = self._states.get(key)
                if live_state is not None:
                    live_state.consumer_status = "unsupported"
                    live_state.last_consumer_event_at = event_ts
                    self._record_event(
                        key,
                        {
                            "_report_kind": "session_consumer_restart_failed",
                            "update_time": event_ts,
                            "account_id": key.account_id,
                            "account_type": key.account_type,
                            "session_id": key.session_id,
                            "error_msg": "callback consumer is not supported by the underlying QMT client",
                        },
                    )
            return self.get_state(config=config, factory=factory)

        try:
            started = bool(ensure_consumer())
        except Exception as exc:
            with self._lock:
                live_state = self._states.get(key)
                if live_state is not None:
                    live_state.consumer_status = "restart_failed"
                    live_state.last_error = str(exc)
                    live_state.last_consumer_event_at = event_ts
                    self._record_event(
                        key,
                        {
                            "_report_kind": "session_consumer_restart_failed",
                            "update_time": event_ts,
                            "account_id": key.account_id,
                            "account_type": key.account_type,
                            "session_id": key.session_id,
                            "error_msg": str(exc),
                        },
                    )
            return self.get_state(config=config, factory=factory)

        with self._lock:
            live_state = self._states.get(key)
            if live_state is not None:
                if started:
                    live_state.consumer_status = "running"
                    live_state.last_consumer_event_at = event_ts
                    if not alive:
                        live_state.consumer_restart_count += 1
                        self._record_event(
                            key,
                            {
                                "_report_kind": "session_consumer_restarted",
                                "update_time": event_ts,
                                "account_id": key.account_id,
                                "account_type": key.account_type,
                                "session_id": key.session_id,
                                "owner_count": live_state.owner_count,
                                "consumer_restart_count": live_state.consumer_restart_count,
                            },
                        )
                else:
                    live_state.consumer_status = "unsupported"
                    live_state.last_consumer_event_at = event_ts
        return self.get_state(config=config, factory=factory)

    @staticmethod
    def make_key(
        *,
        config: QMTBrokerConfig,
        factory: Callable[[QMTBrokerConfig], QMTClientProtocol],
    ) -> QMTSessionKey:
        return QMTSessionKey(
            account_id=config.account_id,
            account_type=(config.account_type or "stock").lower(),
            install_path=config.install_path or "",
            session_id=str(_resolve_qmt_session_id(config)),
            factory_key=_factory_cache_key(factory),
        )

    def _record_event(self, key: QMTSessionKey, payload: dict[str, Any]) -> None:
        next_seq = int(self._event_sequences.get(key, 0) or 0) + 1
        event_payload = dict(payload)
        event_payload["_journal_seq"] = next_seq
        self._events.setdefault(key, []).append(event_payload)
        self._event_timestamps.setdefault(key, []).append(
            _coerce_timestamp(event_payload.get("update_time"))
        )
        self._event_sequences[key] = next_seq

    def _harvest_client_runtime_events_locked(
        self,
        key: QMTSessionKey,
        client: QMTClientProtocol,
    ) -> None:
        list_runtime_events = getattr(client, "list_runtime_events", None)
        if not callable(list_runtime_events):
            return
        try:
            raw_events = list_runtime_events()
        except Exception:
            return
        for raw_event in raw_events or []:
            if isinstance(raw_event, dict):
                kind = str(raw_event.get("_report_kind", "") or "")
                if not kind.startswith("session_consumer_"):
                    continue
                self._record_event(key, dict(raw_event))
                continue
            kind = str(getattr(raw_event, "event_kind", "") or "")
            if not kind.startswith("session_consumer_"):
                continue
            payload = getattr(raw_event, "payload", None)
            self._record_event(
                key,
                {
                    "_report_kind": kind,
                    "update_time": getattr(raw_event, "as_of", _utc_now()),
                    **(dict(payload) if isinstance(payload, dict) else {}),
                },
            )

    @staticmethod
    def _make_listener_ref(listener: Callable[..., None]) -> Any:
        bound_self = getattr(listener, "__self__", None)
        if bound_self is not None:
            return weakref.WeakMethod(listener)
        return lambda: listener

    def _ensure_client_callback_listener_locked(
        self,
        key: QMTSessionKey,
        client: QMTClientProtocol,
    ) -> None:
        if key in self._client_callback_listener_keys:
            return
        register = getattr(client, "register_projection_dirty_listener", None)
        if not callable(register):
            return
        register(
            lambda event_payload, session_key=key: self._handle_client_projection_dirty(
                session_key,
                event_payload,
            )
        )
        self._client_callback_listener_keys.add(key)

    def _handle_client_projection_dirty(
        self,
        key: QMTSessionKey,
        event_payload: dict[str, Any],
    ) -> None:
        with self._lock:
            deployment_ids = tuple(sorted(self._owners.get(key, ())))
            deployment_listener_refs = list(
                self._deployment_callback_listeners.items()
            )
            persistent_listener_refs = list(
                self._persistent_refresh_listeners.items()
            )
        # Deployment-scoped listeners still require at least one owner so they
        # do not fire for owner-free sessions. Persistent refresh listeners
        # intentionally fire regardless of attached owners so long-lived
        # consumers can route events even for resident/process-owned sessions.
        if deployment_ids and deployment_listener_refs:
            dead_tokens: list[int] = []
            for token, listener_ref in deployment_listener_refs:
                callback = listener_ref()
                if callback is None:
                    dead_tokens.append(token)
                    continue
                try:
                    callback(
                        session_key=key,
                        deployment_ids=deployment_ids,
                        event=dict(event_payload),
                    )
                except Exception:
                    logger.warning(
                        "QMT session callback listener failed for %s",
                        key.account_id,
                        exc_info=True,
                    )
            if dead_tokens:
                with self._lock:
                    for token in dead_tokens:
                        self._deployment_callback_listeners.pop(token, None)
        if persistent_listener_refs:
            dead_persistent: list[int] = []
            for token, listener_ref in persistent_listener_refs:
                callback = listener_ref()
                if callback is None:
                    dead_persistent.append(token)
                    continue
                try:
                    callback(
                        session_key=key,
                        deployment_ids=deployment_ids,
                        event=dict(event_payload),
                    )
                except Exception:
                    logger.warning(
                        "QMT persistent refresh listener failed for %s",
                        key.account_id,
                        exc_info=True,
                    )
            if dead_persistent:
                with self._lock:
                    for token in dead_persistent:
                        self._persistent_refresh_listeners.pop(token, None)


def _factory_cache_key(factory: Callable[[QMTBrokerConfig], QMTClientProtocol]) -> str:
    bound_self = getattr(factory, "__self__", None)
    bound_func = getattr(factory, "__func__", None)
    if bound_self is XtQuantShadowClient and bound_func is XtQuantShadowClient.from_config.__func__:
        return "xtquant-default"
    return f"{getattr(factory, '__module__', '')}:{getattr(factory, '__qualname__', '')}:{id(factory)}"


_DEFAULT_QMT_SESSION_MANAGER = QMTSessionManager()


def get_default_qmt_session_manager() -> QMTSessionManager:
    return _DEFAULT_QMT_SESSION_MANAGER


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


class XtQuantShadowClient:
    """Minimal lazy wrapper around an xtquant trader/account pair."""

    def __init__(self, trader: Any, account_ref: Any, account_id: str):
        self._trader = trader
        self._account_ref = account_ref
        self._account_id = account_id
        self._callback_bridge = _XtQuantTraderCallbackBridge(account_id=account_id)
        self._consumer_lock = threading.Lock()
        self._consumer_thread: threading.Thread | None = None
        self._consumer_started_at: datetime | None = None
        self._consumer_last_transition_at: datetime | None = None
        self._consumer_last_error: str = ""
        self._consumer_next_retry_at: datetime | None = None
        self._consumer_restart_attempts = 0
        self._last_submit_mode: str = ""
        # Latest execution sync mode. One of
        # ``callback_only`` / ``callback_query_merge`` / ``query_only`` /
        # ``unknown`` (before any call). Written under ``_sync_lock`` by
        # ``list_execution_reports`` / ``collect_sync_state`` so concurrent
        # callers observe a consistent label alongside their bundle.
        self._last_execution_sync_mode: str = "unknown"
        # Serialises cursor + callback-buffer reads so concurrent
        # `collect_sync_state()` / `list_execution_reports()` /
        # `describe_last_submit_ack()` calls cannot race each other.
        # The callback-bridge thread still writes to the underlying deque
        # under its own lock; this lock only wraps the cursor-aware read
        # + snapshot windows.
        self._sync_lock = threading.Lock()

    @classmethod
    def from_config(cls, config: QMTBrokerConfig) -> "XtQuantShadowClient":
        if not config.install_path:
            raise RuntimeError(
                "QMTShadowBroker requires shadow_broker_config.install_path when no client is injected"
            )
        xttrader_mod = cls._import_xtquant_module("xtquant.xttrader")
        trader_cls = getattr(xttrader_mod, "XtQuantTrader", None) or getattr(
            xttrader_mod, "XtTrader", None
        )
        if trader_cls is None:
            raise RuntimeError(
                "xtquant.xttrader is installed but does not expose XtQuantTrader/XtTrader"
            )
        session_id = _resolve_qmt_session_id(config)
        account_ref = cls._build_account_ref(config)
        trader = cls._instantiate_trader(trader_cls, config.install_path, session_id)
        client = cls(trader=trader, account_ref=account_ref, account_id=config.account_id)
        try:
            client._prepare_runtime()
        except Exception:
            try:
                client.close()
            except Exception:
                pass
            raise
        return client

    @staticmethod
    def _import_xtquant_module(module_name: str):
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "xtquant is not installed or not importable; install/configure QMT first "
                "or inject a QMT client into QMTShadowBroker"
            ) from exc

    @staticmethod
    def _instantiate_trader(trader_cls: Any, install_path: str, session_id: Any) -> Any:
        normalized_session = _coerce_qmt_session_id(session_id)
        if normalized_session is None:
            normalized_session = _stable_positive_int(str(session_id))
        attempts = [
            (install_path, normalized_session),
            (normalized_session, install_path),
        ]
        last_error: Exception | None = None
        for args in attempts:
            try:
                return trader_cls(*args)
            except Exception as exc:  # pragma: no cover - depends on local xtquant signature
                last_error = exc
        raise RuntimeError(
            "Failed to construct xtquant trader from install_path/session_id"
        ) from last_error

    @classmethod
    def _build_account_ref(cls, config: QMTBrokerConfig) -> Any:
        try:
            xttype_mod = cls._import_xtquant_module("xtquant.xttype")
        except RuntimeError:
            return config.account_id
        account_cls = getattr(xttype_mod, "StockAccount", None) or getattr(
            xttype_mod, "XtAccount", None
        )
        if account_cls is None:
            return config.account_id
        try:
            account_type = (config.account_type or "").upper()
            if account_type and account_type != "STOCK":
                return account_cls(config.account_id, account_type)
            return account_cls(config.account_id)
        except Exception:  # pragma: no cover - depends on local xtquant signature
            return config.account_id

    def _connect_and_subscribe(self, *, reconnect: bool = False) -> None:
        session_id = getattr(self._trader, "session_id", None)
        connected_kind = "session_reconnected" if reconnect else "session_connected"
        connect_failed_kind = "session_reconnect_failed" if reconnect else "session_connect_failed"
        subscribed_kind = "session_resubscribed" if reconnect else "session_subscribed"
        subscribe_failed_kind = (
            "session_resubscribe_failed" if reconnect else "session_subscribe_failed"
        )
        connect = getattr(self._trader, "connect", None)
        if callable(connect):
            result = connect()
            if isinstance(result, (int, float)) and result != 0:
                self._callback_bridge.record_runtime_event(
                    connect_failed_kind,
                    account_id=self._account_id,
                    session_id=session_id,
                    connect_result=result,
                )
                raise RuntimeError(f"xtquant trader connect() failed with code {result}")
            self._callback_bridge.record_runtime_event(
                connected_kind,
                account_id=self._account_id,
                session_id=session_id,
                connect_result=result,
            )
        subscribe = getattr(self._trader, "subscribe", None)
        if callable(subscribe):
            result = subscribe(self._account_ref)
            if isinstance(result, (int, float)) and result != 0:
                self._callback_bridge.record_runtime_event(
                    subscribe_failed_kind,
                    account_id=self._account_id,
                    session_id=session_id,
                    subscribe_result=result,
                )
                raise RuntimeError(f"xtquant trader subscribe() failed with code {result}")
            self._callback_bridge.record_runtime_event(
                subscribed_kind,
                account_id=self._account_id,
                session_id=session_id,
                subscribe_result=result,
            )

    def _prepare_runtime(self) -> None:
        session_id = getattr(self._trader, "session_id", None)
        self._callback_bridge.record_runtime_event(
            "session_bootstrap_started",
            account_id=self._account_id,
            session_id=session_id,
        )
        register_callback = getattr(self._trader, "register_callback", None)
        if callable(register_callback):
            register_callback(self._callback_bridge)
        start = getattr(self._trader, "start", None)
        if callable(start):
            start()
            self._callback_bridge.record_runtime_event(
                "session_started",
                account_id=self._account_id,
                session_id=session_id,
            )
        self._connect_and_subscribe(reconnect=False)

    def _reconnect_session(self) -> None:
        session_id = getattr(self._trader, "session_id", None)
        reconnect_at = _utc_now()
        self._callback_bridge.record_runtime_event(
            "session_reconnect_started",
            account_id=self._account_id,
            session_id=session_id,
            update_time=reconnect_at,
        )
        self._connect_and_subscribe(reconnect=True)

    def ensure_resident_session(self) -> bool:
        if self.is_callback_consumer_alive() and not self.needs_resident_session_reconnect():
            return True
        now = _utc_now()
        with self._consumer_lock:
            next_retry_at = self._consumer_next_retry_at
        if isinstance(next_retry_at, datetime) and now < next_retry_at:
            self._callback_bridge.record_runtime_event(
                "session_reconnect_deferred",
                account_id=self._account_id,
                session_id=getattr(self._trader, "session_id", None),
                update_time=now,
                next_retry_at=next_retry_at.isoformat(),
            )
            return False
        try:
            self._reconnect_session()
        except Exception as exc:
            with self._consumer_lock:
                self._consumer_restart_attempts += 1
                # Cap linear base-backoff at _QMT_RECONNECT_MAX_BACKOFF and
                # apply symmetric ±jitter so retry storms from multiple
                # owners do not lock into the same retry cadence.
                raw_backoff = (
                    _QMT_RECONNECT_BASE_BACKOFF * self._consumer_restart_attempts
                )
                self._consumer_next_retry_at = now + _apply_reconnect_jitter(raw_backoff)
                self._consumer_last_transition_at = now
                self._consumer_last_error = str(exc)
            raise
        with self._consumer_lock:
            self._consumer_restart_attempts = 0
            self._consumer_next_retry_at = None
            self._consumer_last_error = ""
        return self.ensure_callback_consumer()

    def needs_resident_session_reconnect(self) -> bool:
        connection_state = self._callback_bridge.get_connection_state()
        if not isinstance(connection_state, dict):
            return False
        return str(connection_state.get("status", "") or "") == "disconnected"

    def ensure_callback_consumer(self) -> bool:
        run_forever = getattr(self._trader, "run_forever", None)
        if not callable(run_forever):
            return False
        session_id = getattr(self._trader, "session_id", None)
        event_ts = _utc_now()
        with self._consumer_lock:
            if self._consumer_thread is not None and self._consumer_thread.is_alive():
                self._callback_bridge.record_runtime_event(
                    "session_consumer_reused",
                    account_id=self._account_id,
                    session_id=session_id,
                )
                return True
            thread = threading.Thread(
                target=self._run_consumer_loop,
                name=f"qmt-shadow-consumer-{self._account_id}",
                daemon=True,
            )
            self._consumer_thread = thread
            self._consumer_started_at = event_ts
            self._consumer_last_transition_at = event_ts
            self._consumer_last_error = ""
            self._consumer_next_retry_at = None
            thread.start()
        self._callback_bridge.record_runtime_event(
            "session_consumer_started",
            account_id=self._account_id,
            session_id=session_id,
        )
        return True

    def is_callback_consumer_alive(self) -> bool:
        with self._consumer_lock:
            return self._consumer_thread is not None and self._consumer_thread.is_alive()

    def _run_consumer_loop(self) -> None:
        session_id = getattr(self._trader, "session_id", None)
        try:
            self._trader.run_forever()
            stopped_at = _utc_now()
            with self._consumer_lock:
                if self._consumer_thread is threading.current_thread():
                    self._consumer_thread = None
                self._consumer_last_transition_at = stopped_at
                self._consumer_last_error = ""
            self._callback_bridge.record_runtime_event(
                "session_consumer_stopped",
                account_id=self._account_id,
                session_id=session_id,
                update_time=stopped_at,
            )
        except Exception as exc:  # pragma: no cover - exercised via tests with fake traders
            failed_at = _utc_now()
            with self._consumer_lock:
                if self._consumer_thread is threading.current_thread():
                    self._consumer_thread = None
                self._consumer_last_transition_at = failed_at
                self._consumer_last_error = str(exc)
            self._callback_bridge.record_runtime_event(
                "session_consumer_failed",
                account_id=self._account_id,
                session_id=session_id,
                error_msg=str(exc),
                update_time=failed_at,
            )
        finally:
            with self._consumer_lock:
                if self._consumer_thread is threading.current_thread():
                    self._consumer_thread = None

    def query_stock_asset(self, account_id: str) -> Any:
        return self._trader.query_stock_asset(self._account_ref)

    def query_stock_positions(self, account_id: str) -> list[Any]:
        return self._trader.query_stock_positions(self._account_ref)

    def query_stock_orders(self, account_id: str) -> list[Any]:
        return self._trader.query_stock_orders(self._account_ref)

    def query_stock_trades(self, account_id: str) -> list[Any]:
        return self._trader.query_stock_trades(self._account_ref)

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        shares: int,
        price: float,
        strategy_name: str,
        order_remark: str,
        price_type: Any | None = None,
    ) -> Any:
        order_side = _resolve_qmt_order_side(side)
        resolved_price_type = _resolve_qmt_price_type(price_type)
        submit_args = (
            self._account_ref,
            symbol,
            order_side,
            int(shares),
            resolved_price_type,
            float(price),
            strategy_name,
            order_remark,
        )
        last_error: Exception | None = None
        for submit_name in ("order_stock_async", "order_stock"):
            submit = getattr(self._trader, submit_name, None)
            if not callable(submit):
                continue
            try:
                result = _invoke_xtquant_order_api(submit, *submit_args)
            except NotImplementedError as exc:
                last_error = exc
                continue
            if _qmt_request_failed_immediately(result):
                last_error = RuntimeError(
                    f"xtquant trader {submit_name} returned failure {result!r}"
                )
                continue
            with self._sync_lock:
                self._last_submit_mode = submit_name
            return result
        if last_error is not None:
            raise RuntimeError("xtquant trader failed to submit QMT order") from last_error
        raise NotImplementedError(
            "xtquant trader does not expose a supported order submission API"
        )

    def describe_last_submit_ack(self, result: Any) -> dict[str, str]:
        # Critical #2: submit_mode is written inside `submit_order` (which is
        # driven by caller threads and never by the callback loop), but the
        # sync lock keeps the read consistent with concurrent
        # `collect_sync_state()` / `list_execution_reports()` callers so a
        # read-describe-ack race cannot observe a stale mode.
        with self._sync_lock:
            submit_mode = str(self._last_submit_mode or "").strip()
        if submit_mode == "order_stock_async":
            return {
                "broker_submit_id": str(result or "").strip(),
                "broker_order_id": "",
            }
        value = str(result or "").strip()
        return {
            "broker_submit_id": value,
            "broker_order_id": value,
        }

    def register_projection_dirty_listener(
        self,
        listener: Callable[[dict[str, Any]], None],
    ) -> None:
        self._callback_bridge.register_projection_dirty_listener(listener)

    def get_lifecycle_closure(
        self,
        identity_token: str,
        *,
        account_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return per-order callback lifecycle closure tracked on the bridge.

        ``identity_token`` can be any of ``broker_submit_id`` (submit-ack
        ``seq``), ``broker_order_id`` / ``order_id`` / ``order_sysid``, or
        ``client_order_id`` / ``order_remark``. Returns ``None`` when no
        callback has referenced the identity yet.
        """
        return self._callback_bridge.get_lifecycle_closure(
            identity_token, account_id=account_id
        )

    def snapshot_lifecycle_closures(self) -> list[dict[str, Any]]:
        """Return a copy of every tracked lifecycle closure on the bridge."""
        return self._callback_bridge.snapshot_lifecycle_closures()

    def _determine_execution_sync_mode(
        self,
        *,
        since: datetime | None,
        callback_reports: list[Any],
        consumer_alive: bool,
        now: datetime | None = None,
    ) -> str:
        """Decide which sync mode ``list_execution_reports`` should use.

        Returns one of:
        - ``callback_only``: consumer alive, either every known submit-ack
          has reached a terminal callback (or has a fresh
          ``last_order_callback_ts``), **or** there are no outstanding
          submit-acks and the callback buffer is actively fresh. In both
          cases pure callback truth is sufficient and the query fallback
          is skipped entirely — matching the task's
          "full callback order-state closure" contract.
        - ``callback_query_merge``: consumer alive, but either
          (a) at least one submit-ack is past ``_QMT_CALLBACK_MAX_GAP_SECS``
          without any lifecycle callback (the task's degraded path), or
          (b) we have no submit-ack handle and the callback buffer has
          gone stale (Critical #1: do not drop fresh query rows just
          because the bridge happens to be quiet), or
          (c) ``since is None`` (cold-start / full-snapshot sync).
        - ``query_only``: consumer dead / unavailable — no callback truth,
          fall back to raw query output.
        """
        if not consumer_alive:
            return "query_only"
        # ``since is None`` is a cold-start/full-snapshot path — keep the
        # query merge so newly-observed orders that predate our callback
        # consumer still surface. Lifecycle-closure tightening only
        # applies to incremental (``since != None``) sync paths.
        if since is None:
            return "callback_query_merge"
        reference = now or _utc_now()
        closures = self._callback_bridge.snapshot_lifecycle_closures()
        has_submit_ack = False
        for closure in closures:
            if not bool(closure.get("submit_ack_received", False)):
                continue
            has_submit_ack = True
            # Terminal callback already seen: this order is closed by
            # callbacks alone, nothing to query.
            if closure.get("terminal_callback_ts") is not None:
                continue
            last_order_ts = closure.get("last_order_callback_ts")
            if isinstance(last_order_ts, datetime):
                gap = (reference - last_order_ts).total_seconds()
                if 0.0 <= gap <= _QMT_CALLBACK_MAX_GAP_SECS:
                    # Recent lifecycle callback exists but not yet terminal.
                    # Callbacks are actively flowing — callback_only is safe
                    # for this order.
                    continue
            # Submit-ack was received but no lifecycle callback has arrived
            # within the freshness window. Degraded path: the broker
            # acknowledged our submit but is not pushing order state, so
            # we must merge with query truth to catch up.
            return "callback_query_merge"
        if has_submit_ack:
            # Every submit-ack'd order either has a terminal callback or
            # a recent live callback. Pure callback truth is sufficient —
            # skip query entirely. This is the core tightening the task
            # requires.
            return "callback_only"
        # No submit-ack handle to reason about. Fall back on the older
        # Critical #1 buffer-freshness heuristic: if the callback bridge
        # is actively flowing (either we already pulled incremental
        # callback reports or the latest callback timestamp is within
        # the freshness window) keep callback_only; otherwise still
        # degrade to merge so a quiet bridge cannot silently drop fresh
        # broker-query rows.
        if callback_reports:
            return "callback_only"
        if self._callback_buffer_is_fresh(now=reference):
            return "callback_only"
        return "callback_query_merge"

    def _list_callback_execution_events(
        self,
        since: datetime | None = None,
        *,
        since_seq: int | None = None,
    ) -> list[Any]:
        return self._callback_bridge.list_events(
            since=since,
            since_seq=since_seq,
            kinds={"order", "trade", "order_error"},
        )

    def _callback_buffer_is_fresh(self, *, now: datetime | None = None) -> bool:
        """Return True if the callback bridge has produced an event recently.

        "Recent" is defined by ``_QMT_CALLBACK_MAX_GAP_SECS``. If the callback
        consumer is not alive or we have never seen a callback, the buffer is
        considered stale and the caller should fall back to query-driven sync.
        """
        if not self.is_callback_consumer_alive():
            return False
        stats = self._callback_bridge.snapshot_stats()
        latest_callback_at = stats.get("latest_callback_at")
        if not isinstance(latest_callback_at, datetime):
            return False
        reference = now or _utc_now()
        gap = (reference - latest_callback_at).total_seconds()
        return gap >= 0.0 and gap <= _QMT_CALLBACK_MAX_GAP_SECS

    def _execution_callback_freshness_label(self, *, now: datetime | None = None) -> str:
        if not self.is_callback_consumer_alive():
            return "unavailable"
        stats = self._callback_bridge.snapshot_stats()
        latest_execution_at = stats.get("latest_execution_at")
        if not isinstance(latest_execution_at, datetime):
            # Consumer alive but no execution events yet — treat as stale so
            # the first fetch still merges query fallback.
            return "stale"
        reference = now or _utc_now()
        gap = (reference - latest_execution_at).total_seconds()
        if 0.0 <= gap <= _QMT_CALLBACK_MAX_GAP_SECS:
            return "fresh"
        return "stale"

    def _should_query_execution_fallback(
        self,
        *,
        since: datetime | None,
        callback_reports: list[Any],
    ) -> bool:
        if since is None:
            return True
        if not self.is_callback_consumer_alive():
            return True
        # Critical #1: even when the consumer is alive and ``since != None``,
        # we must not drop fresh query events just because the callback
        # buffer happens to be empty or stale. Fall back to callback+query
        # merge whenever the callback buffer has nothing to say in the
        # incremental window and the callback bridge itself has gone quiet
        # beyond the freshness threshold.
        if not callback_reports and not self._callback_buffer_is_fresh():
            return True
        return False

    def list_execution_reports(self, since: datetime | None = None) -> list[Any]:
        with self._sync_lock:
            callback_reports = self._list_callback_execution_events(since=since)
            consumer_alive = self.is_callback_consumer_alive()
            sync_mode = self._determine_execution_sync_mode(
                since=since,
                callback_reports=callback_reports,
                consumer_alive=consumer_alive,
            )
            self._last_execution_sync_mode = sync_mode
        if sync_mode == "callback_only":
            # Lifecycle tracker proved that every submit-ack'd order either
            # already has a terminal callback or a fresh ``on_stock_order``
            # callback. Pure callback truth — no query round-trip.
            return list(callback_reports)
        queried_orders = self.query_stock_orders(self._account_id) or []
        queried_trades = self.query_stock_trades(self._account_id) or []
        if sync_mode == "query_only":
            return [*callback_reports, *queried_orders, *queried_trades]
        # callback_query_merge
        if since is None:
            merged_orders, merged_trades = self._build_callback_aware_snapshot_execution_state(
                queried_orders=queried_orders,
                queried_trades=queried_trades,
            )
            return self._build_callback_aware_execution_report_view(
                callback_reports=callback_reports,
                queried_orders=queried_orders,
                merged_orders=merged_orders,
                merged_trades=merged_trades,
            )
        # Stale-buffer incremental path: return callback reports for
        # terminal-state preference, plus any query rows whose timestamp
        # meets the `since` filter. Downstream dedupe is keyed by
        # report_id so duplicates merge cleanly.
        filtered_orders = [
            raw for raw in queried_orders
            if self._raw_order_meets_since(raw, since)
        ]
        filtered_trades = [
            raw for raw in queried_trades
            if self._raw_trade_meets_since(raw, since)
        ]
        return [*callback_reports, *filtered_orders, *filtered_trades]

    def last_execution_sync_mode(self) -> str:
        """Return the sync mode used by the most recent execution sync call.

        One of ``callback_only`` / ``callback_query_merge`` / ``query_only`` /
        ``unknown`` (before any call). The attribute read is safe without
        ``_sync_lock`` under CPython: simple string assignment is atomic
        under the GIL, and callers only need eventual-consistency semantics
        (the "latest observed" mode).
        """
        return str(self._last_execution_sync_mode or "unknown")

    @staticmethod
    def _raw_order_meets_since(raw: Any, since: datetime) -> bool:
        order_time = _coerce_timestamp(_get_field(raw, "order_time", "update_time", default=_utc_now()))
        return order_time >= since

    @staticmethod
    def _raw_trade_meets_since(raw: Any, since: datetime) -> bool:
        traded_time = _coerce_timestamp(
            _get_field(raw, "traded_time", "update_time", default=_utc_now())
        )
        return traded_time >= since

    def list_runtime_events(
        self,
        since: datetime | None = None,
        *,
        since_seq: int | None = None,
    ) -> list[Any]:
        events = self._callback_bridge.list_events(
            since=since,
            since_seq=since_seq,
            kinds={
                "connected",
                "stock_asset",
                "stock_position",
                "account_status",
                "order_stock_async_response",
                "cancel_order_stock_async_response",
                "cancel_error",
                "disconnected",
                *_QMT_SESSION_RUNTIME_KINDS,
            },
        )
        state_event = self._build_consumer_state_event()
        if state_event is not None:
            state_ts = _coerce_timestamp(state_event.get("update_time"))
            if since is None or state_ts >= since:
                events = [*events, state_event]
        return events

    def collect_sync_state(
        self,
        *,
        since_reports: datetime | None = None,
        since_runtime: datetime | None = None,
        cursor_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        callback_runtime_seq = None
        callback_execution_seq = None
        if isinstance(cursor_state, dict):
            raw_callback_runtime_seq = cursor_state.get("callback_runtime_seq")
            raw_callback_execution_seq = cursor_state.get("callback_execution_seq")
            try:
                callback_runtime_seq = (
                    int(raw_callback_runtime_seq)
                    if raw_callback_runtime_seq is not None
                    else None
                )
            except (TypeError, ValueError):
                callback_runtime_seq = None
            try:
                callback_execution_seq = (
                    int(raw_callback_execution_seq)
                    if raw_callback_execution_seq is not None
                    else None
                )
            except (TypeError, ValueError):
                callback_execution_seq = None
        # Critical #2: read the cursor, snapshot the callback bridge, and
        # compute the next cursor under one lock so concurrent callers
        # cannot observe the same events twice or skip events that landed
        # between the read and the cursor bump. The callback-buffer mutators
        # still append under their own internal lock, but no callers update
        # `_sync_lock`-protected cursor state from inside that append path,
        # so the critical section stays short and never blocks the
        # callback thread.
        with self._sync_lock:
            consumer_alive = self.is_callback_consumer_alive()
            callback_asset = (
                self._callback_bridge.get_latest_asset() if consumer_alive else None
            )
            callback_reports = self._list_callback_execution_events(
                since=since_reports,
                since_seq=callback_execution_seq,
            )
            runtime_events = self.list_runtime_events(
                since=since_runtime,
                since_seq=callback_runtime_seq,
            )
            execution_sync_mode = self._determine_execution_sync_mode(
                since=since_reports,
                callback_reports=callback_reports,
                consumer_alive=consumer_alive,
            )
            # Callback-bridge cursor truth at snapshot time. Returning these
            # values from inside the critical section guarantees that any
            # concurrent caller which acquires the lock next observes a
            # cursor that is ≥ our returned cursor, so no event is skipped
            # and no event is replayed across adjacent calls.
            next_cursor_runtime = _max_journal_seq(
                runtime_events,
                callback_runtime_seq,
            )
            next_cursor_execution = _max_journal_seq(
                callback_reports,
                callback_execution_seq,
            )
            callback_state = self.get_callback_loop_state()
            self._last_execution_sync_mode = execution_sync_mode
        account_sync_mode = str(callback_state.get("account_sync_mode", "") or "")
        # Query fallbacks intentionally happen *outside* the sync lock so
        # slow broker round-trips do not stall the callback-append path.
        asset_sync_mode: str
        trades_sync_mode: str
        orders_sync_mode: str
        # Snapshot orders/trades always query the broker because they
        # represent the full current state (not just recent lifecycle
        # deltas). This keeps the pre-existing "fresher query overrides
        # older callback" semantics intact for the snapshot view even
        # when ``execution_sync_mode == callback_only``. Only the
        # ``execution_reports`` channel (recent lifecycle events) is
        # subject to the lifecycle-closure tightening.
        queried_orders = self.query_stock_orders(self._account_id) or []
        queried_trades = self.query_stock_trades(self._account_id) or []
        if execution_sync_mode == "callback_only":
            execution_reports = list(callback_reports)
            orders_sync_mode = "callback_only"
            trades_sync_mode = "callback_only"
        elif execution_sync_mode == "query_only":
            execution_reports = [
                *callback_reports,
                *queried_orders,
                *queried_trades,
            ]
            orders_sync_mode = "query_only"
            trades_sync_mode = "query_only"
        else:  # callback_query_merge
            if since_reports is not None and consumer_alive:
                filtered_orders = [
                    raw for raw in queried_orders
                    if self._raw_order_meets_since(raw, since_reports)
                ]
                filtered_trades = [
                    raw for raw in queried_trades
                    if self._raw_trade_meets_since(raw, since_reports)
                ]
                execution_reports = [
                    *callback_reports,
                    *filtered_orders,
                    *filtered_trades,
                ]
            else:
                execution_reports = [
                    *callback_reports,
                    *queried_orders,
                    *queried_trades,
                ]
            orders_sync_mode = "merge"
            trades_sync_mode = "merge"
        snapshot_orders = queried_orders
        snapshot_trades = queried_trades
        if consumer_alive:
            snapshot_orders, snapshot_trades = self._build_callback_aware_snapshot_execution_state(
                queried_orders=queried_orders,
                queried_trades=queried_trades,
            )
        if callback_asset is not None and account_sync_mode == "callback_preferred":
            asset_value = callback_asset
            asset_sync_mode = "callback_preferred"
        else:
            asset_value = self.query_stock_asset(self._account_id)
            asset_sync_mode = "query_fallback"
        return {
            "asset": asset_value,
            "positions": self.query_stock_positions(self._account_id) or [],
            "orders": snapshot_orders,
            "trades": snapshot_trades,
            "execution_reports": execution_reports,
            "runtime_events": runtime_events,
            "cursor_state": {
                "callback_runtime_seq": next_cursor_runtime,
                "callback_execution_seq": next_cursor_execution,
            },
            "execution_sync_mode": execution_sync_mode,
            "sync_mode_details": {
                "orders": orders_sync_mode,
                "asset": asset_sync_mode,
                "trades": trades_sync_mode,
            },
        }

    @staticmethod
    def _snapshot_order_keys(raw: Any) -> tuple[str, ...]:
        keys: list[str] = []
        for value in (
            _get_field(raw, "order_sysid", default=""),
            _get_field(raw, "order_id", default=""),
            _get_field(raw, "client_order_id", "order_remark", "remark", default=""),
        ):
            key = str(value or "").strip()
            if key and key not in keys:
                keys.append(key)
        return tuple(keys)

    @staticmethod
    def _snapshot_trade_key(raw: Any) -> str:
        for value in (
            _get_field(raw, "trade_no", "traded_id", "business_no", default=""),
            _get_field(raw, "order_sysid", default=""),
            _get_field(raw, "order_id", default=""),
        ):
            key = str(value or "").strip()
            if key:
                return key
        traded_time = _coerce_timestamp(_get_field(raw, "traded_time", "update_time", default=_utc_now()))
        client_order_id = str(
            _get_field(raw, "client_order_id", "order_remark", "remark", default="") or ""
        ).strip()
        traded_volume = int(_get_field(raw, "traded_volume", "business_amount", default=0) or 0)
        return "|".join([client_order_id, str(traded_volume), traded_time.isoformat()])

    @staticmethod
    def _snapshot_order_dict(raw: Any, *, normalized_status: str | None = None) -> dict[str, Any]:
        requested = int(_get_field(raw, "order_volume", "entrust_amount", default=0) or 0)
        filled = int(_get_field(raw, "traded_volume", "filled_volume", "business_amount", default=0) or 0)
        remaining = int(
            _get_field(
                raw,
                "remaining_volume",
                "left_volume",
                default=max(requested - filled, 0),
            )
            or 0
        )
        status = normalized_status or normalize_broker_order_status(
            str(_get_field(raw, "order_status", "status", default="unknown") or ""),
            filled_shares=filled,
            remaining_shares=remaining,
        )
        return {
            "client_order_id": str(
                _get_field(raw, "client_order_id", "order_remark", "remark", default="") or ""
            ).strip(),
            "order_remark": str(
                _get_field(raw, "order_remark", "client_order_id", "remark", default="") or ""
            ).strip(),
            "order_id": str(_get_field(raw, "order_id", default="") or "").strip(),
            "order_sysid": str(_get_field(raw, "order_sysid", default="") or "").strip(),
            "stock_code": str(_get_field(raw, "stock_code", "symbol", default="") or "").strip(),
            "offset_flag": str(_get_field(raw, "offset_flag", "side", "order_type", default="") or "").strip(),
            "order_status": status,
            "order_volume": requested,
            "traded_volume": filled,
            "remaining_volume": remaining,
            "traded_price": float(_get_field(raw, "traded_price", "price", default=0.0) or 0.0),
            "status_msg": str(_get_field(raw, "status_msg", "error_msg", default="") or ""),
            "order_time": _get_field(raw, "order_time", "update_time", default=_utc_now()),
        }

    @staticmethod
    def _snapshot_trade_dict(raw: Any) -> dict[str, Any]:
        return {
            "client_order_id": str(
                _get_field(raw, "client_order_id", "order_remark", "remark", default="") or ""
            ).strip(),
            "order_remark": str(
                _get_field(raw, "order_remark", "client_order_id", "remark", default="") or ""
            ).strip(),
            "order_id": str(_get_field(raw, "order_id", default="") or "").strip(),
            "order_sysid": str(_get_field(raw, "order_sysid", default="") or "").strip(),
            "traded_id": str(_get_field(raw, "trade_no", "traded_id", "business_no", default="") or "").strip(),
            "trade_no": str(_get_field(raw, "trade_no", "traded_id", "business_no", default="") or "").strip(),
            "stock_code": str(_get_field(raw, "stock_code", "symbol", default="") or "").strip(),
            "offset_flag": str(_get_field(raw, "offset_flag", "side", "order_type", default="") or "").strip(),
            "traded_volume": int(_get_field(raw, "traded_volume", "business_amount", default=0) or 0),
            "traded_price": float(_get_field(raw, "traded_price", "price", default=0.0) or 0.0),
            "traded_time": _get_field(raw, "traded_time", "update_time", default=_utc_now()),
        }

    def _build_callback_aware_snapshot_execution_state(
        self,
        *,
        queried_orders: list[Any],
        queried_trades: list[Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        callback_events = self._callback_bridge.list_events(
            kinds={"order", "trade", "order_error"},
        )

        callback_orders_by_key: dict[str, dict[str, Any]] = {}
        callback_orders_by_primary: dict[str, dict[str, Any]] = {}
        callback_trades_by_key: dict[str, dict[str, Any]] = {}

        for event in callback_events:
            kind = str(event.get("_report_kind", "") or "")
            if kind == "trade":
                trade = self._snapshot_trade_dict(event)
                trade_key = self._snapshot_trade_key(trade)
                callback_trades_by_key[trade_key] = trade
                continue
            snapshot_order = self._snapshot_order_dict(event)
            normalized_status = normalize_broker_order_status(
                str(snapshot_order.get("order_status", "") or ""),
                filled_shares=int(snapshot_order.get("traded_volume", 0) or 0),
                remaining_shares=int(snapshot_order.get("remaining_volume", 0) or 0),
            )
            snapshot_order["order_status"] = normalized_status
            event_ts = _coerce_timestamp(snapshot_order.get("order_time"))
            keys = self._snapshot_order_keys(snapshot_order)
            if not keys:
                continue
            candidate = {
                "primary_key": keys[0],
                "keys": keys,
                "order": snapshot_order,
                "event_ts": event_ts,
                "rank": broker_order_status_rank(normalized_status),
                "terminal": broker_order_status_is_terminal(normalized_status),
            }
            current = next(
                (callback_orders_by_key.get(key) for key in keys if key in callback_orders_by_key),
                None,
            )
            should_replace = current is None or (
                int(candidate["rank"]) > int(current["rank"])
                or (
                    int(candidate["rank"]) == int(current["rank"])
                    and candidate["event_ts"] >= current["event_ts"]
                )
            )
            if not should_replace:
                continue
            for key in keys:
                callback_orders_by_key[key] = candidate
            callback_orders_by_primary[candidate["primary_key"]] = candidate

        merged_orders: list[dict[str, Any]] = []
        consumed_callback_orders: set[str] = set()
        emitted_order_keys: set[str] = set()

        for raw in queried_orders:
            snapshot_order = self._snapshot_order_dict(raw)
            callback_record = next(
                (
                    callback_orders_by_key[key]
                    for key in self._snapshot_order_keys(raw)
                    if key in callback_orders_by_key
                ),
                None,
            )
            if callback_record is not None:
                primary_key = str(callback_record["primary_key"])
                consumed_callback_orders.add(primary_key)
                preferred_order, suppress_order = self._prefer_callback_snapshot_order(
                    queried_order=snapshot_order,
                    callback_record=callback_record,
                )
                if suppress_order:
                    continue
                preferred_primary_key = next(
                    iter(self._snapshot_order_keys(preferred_order)),
                    primary_key,
                )
                if preferred_primary_key in emitted_order_keys:
                    continue
                merged_orders.append(dict(preferred_order))
                emitted_order_keys.add(preferred_primary_key)
                continue
            primary_key = next(iter(self._snapshot_order_keys(snapshot_order)), "")
            if primary_key and primary_key in emitted_order_keys:
                continue
            merged_orders.append(snapshot_order)
            if primary_key:
                emitted_order_keys.add(primary_key)

        for primary_key, record in callback_orders_by_primary.items():
            if primary_key in consumed_callback_orders or bool(record["terminal"]):
                continue
            if primary_key in emitted_order_keys:
                continue
            merged_orders.append(dict(record["order"]))
            emitted_order_keys.add(primary_key)

        merged_trades: list[dict[str, Any]] = []
        emitted_trade_keys: set[str] = set()

        for raw in queried_trades:
            snapshot_trade = self._snapshot_trade_dict(raw)
            trade_key = self._snapshot_trade_key(snapshot_trade)
            callback_trade = callback_trades_by_key.get(trade_key)
            if callback_trade is not None:
                if trade_key in emitted_trade_keys:
                    continue
                merged_trades.append(
                    self._prefer_callback_snapshot_trade(
                        queried_trade=snapshot_trade,
                        callback_trade=callback_trade,
                    )
                )
                emitted_trade_keys.add(trade_key)
                continue
            if trade_key and trade_key in emitted_trade_keys:
                continue
            merged_trades.append(snapshot_trade)
            if trade_key:
                emitted_trade_keys.add(trade_key)

        for trade_key, trade in callback_trades_by_key.items():
            if trade_key in emitted_trade_keys:
                continue
            merged_trades.append(dict(trade))
            emitted_trade_keys.add(trade_key)

        return merged_orders, merged_trades

    def _build_callback_aware_execution_report_view(
        self,
        *,
        callback_reports: list[Any],
        queried_orders: list[Any],
        merged_orders: list[dict[str, Any]],
        merged_trades: list[dict[str, Any]],
    ) -> list[Any]:
        queried_by_key: dict[str, Any] = {}
        for raw in queried_orders:
            for key in self._snapshot_order_keys(raw):
                queried_by_key.setdefault(key, raw)

        supplemental_reports: list[Any] = []
        for event in callback_reports:
            kind = str(event.get("_report_kind", "") or "")
            if kind == "order_error":
                supplemental_reports.append(event)
                continue
            if kind != "order":
                continue
            snapshot_order = self._snapshot_order_dict(event)
            normalized_status = normalize_broker_order_status(
                str(snapshot_order.get("order_status", "") or ""),
                filled_shares=int(snapshot_order.get("traded_volume", 0) or 0),
                remaining_shares=int(snapshot_order.get("remaining_volume", 0) or 0),
            )
            if not broker_order_status_is_terminal(normalized_status):
                continue
            snapshot_order["order_status"] = normalized_status
            keys = self._snapshot_order_keys(snapshot_order)
            matching_query = next(
                (queried_by_key[key] for key in keys if key in queried_by_key),
                None,
            )
            if matching_query is not None:
                preferred_order, suppress_order = self._prefer_callback_snapshot_order(
                    queried_order=self._snapshot_order_dict(matching_query),
                    callback_record={
                        "order": snapshot_order,
                        "rank": broker_order_status_rank(normalized_status),
                        "terminal": True,
                    },
                )
                if not suppress_order or preferred_order.get("order_status") != normalized_status:
                    continue
            supplemental_reports.append(event)
        return [*merged_orders, *merged_trades, *supplemental_reports]

    @staticmethod
    def _prefer_callback_snapshot_order(
        *,
        queried_order: dict[str, Any],
        callback_record: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        callback_order = dict(callback_record["order"])
        callback_ts = _coerce_timestamp(callback_order.get("order_time"))
        queried_ts = _coerce_timestamp(queried_order.get("order_time"))
        queried_status = normalize_broker_order_status(
            str(queried_order.get("order_status", "") or ""),
            filled_shares=int(queried_order.get("traded_volume", 0) or 0),
            remaining_shares=int(queried_order.get("remaining_volume", 0) or 0),
        )
        queried_rank = broker_order_status_rank(queried_status)
        callback_rank = int(callback_record.get("rank", 0) or 0)

        if queried_ts is not None and callback_ts is not None:
            if queried_ts > callback_ts:
                return queried_order, False
            if queried_ts == callback_ts and queried_rank > callback_rank:
                return queried_order, False
        elif queried_ts is not None and callback_ts is None:
            return queried_order, False

        if bool(callback_record["terminal"]):
            return callback_order, True
        return callback_order, False

    @staticmethod
    def _prefer_callback_snapshot_trade(
        *,
        queried_trade: dict[str, Any],
        callback_trade: dict[str, Any],
    ) -> dict[str, Any]:
        callback_ts = _coerce_timestamp(callback_trade.get("traded_time"))
        queried_ts = _coerce_timestamp(queried_trade.get("traded_time"))
        if queried_ts is not None and callback_ts is not None and queried_ts > callback_ts:
            return queried_trade
        if queried_ts is not None and callback_ts is None:
            return queried_trade
        return dict(callback_trade)

    def get_callback_loop_state(self) -> dict[str, Any]:
        stats = self._callback_bridge.snapshot_stats()
        connection_state = self._callback_bridge.get_connection_state() or {}
        session_id = getattr(self._trader, "session_id", None)
        with self._consumer_lock:
            consumer_alive = self._consumer_thread is not None and self._consumer_thread.is_alive()
            started_at = self._consumer_started_at
            last_transition_at = self._consumer_last_transition_at
            last_error = self._consumer_last_error
            next_retry_at = self._consumer_next_retry_at
            reconnect_attempt_count = self._consumer_restart_attempts
        if consumer_alive:
            consumer_status = "running"
        elif last_error:
            consumer_status = "failed"
        elif started_at or last_transition_at:
            consumer_status = "stopped"
        else:
            consumer_status = "unknown"
        latest_callback_at = stats["latest_callback_at"]
        latest_runtime_at = stats["latest_runtime_at"]
        latest_asset_callback_at = stats["latest_asset_callback_at"]
        connection_status = str(connection_state.get("status", "") or "") or None
        connection_update_at = _coerce_timestamp(connection_state.get("update_time"))
        account_sync_anchor = latest_runtime_at or latest_callback_at
        if connection_status == "disconnected":
            account_sync_mode = "query_fallback"
            asset_callback_freshness = "unavailable"
        elif not consumer_alive:
            account_sync_mode = "query_fallback"
            asset_callback_freshness = "unavailable"
        elif not isinstance(latest_asset_callback_at, datetime):
            account_sync_mode = "query_fallback"
            asset_callback_freshness = "unavailable"
        elif (
            isinstance(account_sync_anchor, datetime)
            and account_sync_anchor - latest_asset_callback_at
            > _QMT_CALLBACK_ACCOUNT_FRESHNESS_MAX_AGE
        ):
            account_sync_mode = "query_fallback"
            asset_callback_freshness = "stale"
        else:
            account_sync_mode = "callback_preferred"
            asset_callback_freshness = "fresh"
        # Critical #1: surface execution-callback freshness alongside the
        # existing account-callback signal so downstream gates can
        # distinguish "callbacks are flowing" from "account callbacks stale
        # but executions still current" and vice-versa.
        if not consumer_alive or connection_status == "disconnected":
            execution_callback_freshness = "unavailable"
        else:
            execution_callback_freshness = self._execution_callback_freshness_label()
        # Atomic read of ``_last_execution_sync_mode``. A plain attribute
        # read is safe under CPython's GIL for small string assignments,
        # and we deliberately avoid acquiring ``_sync_lock`` here so
        # ``collect_sync_state`` can safely call
        # ``get_callback_loop_state`` from *inside* the sync-lock
        # critical section without deadlocking.
        execution_sync_mode = str(self._last_execution_sync_mode or "unknown")
        return {
            "account_id": self._account_id,
            "session_id": session_id,
            "consumer_alive": consumer_alive,
            "consumer_status": consumer_status,
            "consumer_started_at": started_at.isoformat() if isinstance(started_at, datetime) else None,
            "consumer_last_transition_at": (
                last_transition_at.isoformat()
                if isinstance(last_transition_at, datetime)
                else None
            ),
            "latest_callback_at": (
                stats["latest_callback_at"].isoformat()
                if isinstance(stats["latest_callback_at"], datetime)
                else None
            ),
            "latest_execution_at": (
                stats["latest_execution_at"].isoformat()
                if isinstance(stats["latest_execution_at"], datetime)
                else None
            ),
            "latest_runtime_at": (
                stats["latest_runtime_at"].isoformat()
                if isinstance(stats["latest_runtime_at"], datetime)
                else None
            ),
            "latest_asset_callback_at": (
                stats["latest_asset_callback_at"].isoformat()
                if isinstance(stats["latest_asset_callback_at"], datetime)
                else None
            ),
            "latest_position_callback_at": (
                stats["latest_position_callback_at"].isoformat()
                if isinstance(stats["latest_position_callback_at"], datetime)
                else None
            ),
            "connection_status": connection_status,
            "connection_update_at": (
                connection_update_at.isoformat()
                if isinstance(connection_update_at, datetime)
                else None
            ),
            "account_sync_mode": account_sync_mode,
            "asset_callback_freshness": asset_callback_freshness,
            "execution_callback_freshness": execution_callback_freshness,
            "execution_sync_mode": execution_sync_mode,
            "buffered_event_count": int(stats["buffered_event_count"]),
            "execution_event_count": int(stats["execution_event_count"]),
            "runtime_event_count": int(stats["runtime_event_count"]),
            "last_error": str(last_error or ""),
            "next_retry_at": (
                next_retry_at.isoformat()
                if isinstance(next_retry_at, datetime)
                else None
            ),
            "reconnect_attempt_count": int(reconnect_attempt_count),
            "loop_mode": "process_owned",
        }

    def _build_consumer_state_event(self) -> dict[str, Any] | None:
        state = self.get_callback_loop_state()
        if not any(
            [
                bool(state.get("consumer_alive")),
                bool(state.get("consumer_started_at")),
                bool(state.get("consumer_last_transition_at")),
            ]
        ):
            return None
        timestamp_values = [
            _coerce_timestamp(value)
            for value in (
                state.get("consumer_started_at"),
                state.get("consumer_last_transition_at"),
                state.get("latest_callback_at"),
                state.get("latest_execution_at"),
                state.get("latest_runtime_at"),
            )
            if value
        ]
        event_ts = max(timestamp_values) if timestamp_values else _utc_now()
        state_key = "|".join(
            [
                str(state.get("consumer_status", "")),
                str(int(bool(state.get("consumer_alive")))),
                str(state.get("reconnect_attempt_count", 0)),
                str(state.get("next_retry_at", "") or ""),
                str(state.get("buffered_event_count", 0)),
                str(state.get("execution_event_count", 0)),
                str(state.get("runtime_event_count", 0)),
                str(state.get("latest_callback_at", "") or ""),
                str(state.get("consumer_last_transition_at", "") or ""),
                str(state.get("connection_status", "") or ""),
                str(state.get("connection_update_at", "") or ""),
                str(state.get("last_error", "") or ""),
                str(state.get("asset_callback_freshness", "") or ""),
                str(state.get("execution_callback_freshness", "") or ""),
                str(state.get("execution_sync_mode", "") or ""),
            ]
        )
        return {
            "_report_kind": "session_consumer_state",
            "update_time": event_ts,
            "state_key": state_key,
            **state,
        }

    def close(self) -> None:
        close_fn = (
            getattr(self._trader, "stop", None)
            or getattr(self._trader, "shutdown", None)
            or getattr(self._trader, "close", None)
        )
        if callable(close_fn):
            close_fn()
        with self._consumer_lock:
            thread = self._consumer_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)

    def cancel_order(self, order_id: str, symbol: str = "") -> Any:
        order_ref = str(order_id or "").strip()
        normalized_order_id = _coerce_qmt_order_id(order_ref)
        cancel_sysid_async = getattr(self._trader, "cancel_order_stock_sysid_async", None)
        cancel_sysid = getattr(self._trader, "cancel_order_stock_sysid", None)
        if symbol and (callable(cancel_sysid_async) or callable(cancel_sysid)):
            try:
                market = _resolve_qmt_market(symbol)
            except (RuntimeError, ValueError):
                market = None
            if market is not None:
                if callable(cancel_sysid_async):
                    try:
                        result = _invoke_xtquant_cancel_api(
                            cancel_sysid_async,
                            self._account_ref,
                            market,
                            order_ref,
                        )
                        if not (
                            isinstance(result, (int, float))
                            and result < 0
                            and normalized_order_id is not None
                        ):
                            return result
                    except NotImplementedError:
                        pass
                elif callable(cancel_sysid):
                    try:
                        result = _invoke_xtquant_cancel_api(
                            cancel_sysid,
                            self._account_ref,
                            market,
                            order_ref,
                        )
                        if not (
                            isinstance(result, (int, float))
                            and result < 0
                            and normalized_order_id is not None
                        ):
                            return result
                    except NotImplementedError:
                        pass
        cancel_async = getattr(self._trader, "cancel_order_stock_async", None)
        cancel = getattr(self._trader, "cancel_order_stock", None)
        if normalized_order_id is not None:
            if callable(cancel_async):
                return _invoke_xtquant_cancel_api(
                    cancel_async,
                    self._account_ref,
                    normalized_order_id,
                )
            if callable(cancel):
                return _invoke_xtquant_cancel_api(
                    cancel,
                    self._account_ref,
                    normalized_order_id,
                )
        cancel = getattr(self._trader, "cancel_order", None)
        if callable(cancel):
            return _invoke_xtquant_cancel_api(cancel, self._account_ref, order_ref)
        raise NotImplementedError("xtquant trader does not expose a supported cancel_order API")


__all__ = [
    "QMTBrokerConfig",
    "QMTClientProtocol",
    "QMTSessionKey",
    "QMTSessionManager",
    "QMTSessionState",
    "XtQuantShadowClient",
    "get_default_qmt_session_manager",
]
