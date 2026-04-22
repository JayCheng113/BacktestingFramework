"""Host-external QMT orchestration service (V3.3.45).

This module provides a long-running, scheduler-independent QMT session host.
It is the contract + implementation + isolation layer for moving QMT session
lifecycle out of the `Scheduler` process so that:

- A host can run headless; scheduler restarts do not drop the xtquant session.
- Multiple scheduler instances can share a single host process.
- Deployments subscribe to a host rather than bootstrapping one themselves.

``QMTHostService`` owns the underlying ``XtQuantShadowClient`` lifecycle, a
supervisor daemon thread that drives (re)connect and callback health, health
state, and a subscription registry. It does **not** own deployment lifecycle,
order semantics, or ledger events — the scheduler will remain authoritative
for those. Host-level operations that proxy into the underlying client
(submit/cancel, sync-state reads, execution-report reads) are fail-closed:
if the host is not ``READY``, the proxy methods raise ``RuntimeError``
instead of silently running on a cached or stale client.

Intentionally standalone:

- no scheduler or broker imports (avoid import cycles and keep the host
  importable in headless processes)
- ``client_factory`` is a ``Callable[..., Any]`` so tests inject fakes and
  scheduler-era integration injects ``XtQuantShadowClient.from_config``
- integration with the scheduler ``resume`` path is intentionally **not**
  done in this phase; a follow-up phase will teach the scheduler to attach
  to an already-running host instead of bootstrapping one itself

``ensure_ready_or_raise()`` is the fail-closed gate scheduler-side callers
will invoke before any tick/submit/cancel so a dead host makes the deployment
tick fail explicitly instead of silently running off a stale client.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Callable


from ez.live._utils import utc_now as _utc_now

logger = logging.getLogger(__name__)


# Default upper bound for the callback-staleness detector. This is how long
# the supervisor waits between "we have subscribers but no callback" and
# demoting the host to ``DEGRADED``. Kept as a module constant so tests can
# shorten it via constructor argument; we do not read it globally elsewhere.
_DEFAULT_CALLBACK_STALE_GRACE_S = 60.0

# Supervisor loop cadence. The loop wakes at least this often so tests can
# drive state transitions without waiting a full reconnect interval; real
# hosts still pace their reconnect attempts via ``reconnect_interval_s``.
_DEFAULT_SUPERVISOR_TICK_S = 0.05


class HostHealth(StrEnum):
    """Coarse health state of a ``QMTHostService``.

    The values are intentionally small and stable. Finer-grained diagnostics
    live on ``HostStatusSummary.error_message`` and can be expanded later
    without breaking existing gate callers.
    """

    UNINITIALIZED = "uninitialized"
    CONNECTING = "connecting"
    READY = "ready"
    DEGRADED = "degraded"  # session alive but callback consumer failing
    DISCONNECTED = "disconnected"
    STOPPED = "stopped"


@dataclass(slots=True)
class HostStatusSummary:
    """Read-only status summary returned by ``QMTHostService.status()``.

    Scheduler, monitor, and dashboards should treat this as an opaque
    snapshot. Field additions are allowed; field removals are not.
    """

    health: HostHealth
    account_id: str
    broker_type: str
    session_started_at: datetime | None
    last_callback_at: datetime | None
    last_reconnect_at: datetime | None
    reconnect_attempts: int
    subscriber_count: int
    error_message: str


@dataclass(slots=True)
class _HostState:
    """Internal mutable state; never exposed directly."""

    health: HostHealth = HostHealth.UNINITIALIZED
    session_started_at: datetime | None = None
    last_callback_at: datetime | None = None
    last_reconnect_at: datetime | None = None
    reconnect_attempts: int = 0
    error_message: str = ""
    subscribers: set[str] = field(default_factory=set)
    # When the supervisor demotes to DEGRADED/DISCONNECTED it sets this so the
    # next supervisor tick attempts (re)connect instead of idling.
    reconnect_requested: bool = True
    # Track whether a stop() has been requested so the supervisor thread can
    # exit cleanly without relying on an external cancellation primitive.
    stop_requested: bool = False
    # Cached last-stop timestamp for idempotent stop behavior.
    stopped_at: datetime | None = None


class QMTHostService:
    """Long-running, scheduler-independent QMT session host.

    Owns
    ----
    - ``XtQuantShadowClient`` lifecycle (via ``client_factory``)
    - supervisor thread that drives initial connect, reconnect, and callback
      freshness demotion
    - subscription registry so scheduler / API layers can observe usage
    - proxy methods (submit/cancel/sync-state/reports) that fail closed when
      the host is not ``READY``

    Does not own
    ------------
    - deployment lifecycle
    - order submission semantics (only the wire call)
    - ledger events (host is still observable via scheduler-side event
      persistence once scheduler integration lands)

    ``subscribe`` / ``unsubscribe`` only influence supervisor book-keeping;
    the host is long-running and does **not** auto-stop on last unsubscribe.
    This is intentional so a scheduler restart with zero subscribers does not
    teardown the live xtquant session.
    """

    def __init__(
        self,
        *,
        account_id: str,
        broker_type: str,
        client_factory: Callable[..., Any],
        reconnect_interval_s: float = 60.0,
        callback_stale_grace_s: float = _DEFAULT_CALLBACK_STALE_GRACE_S,
        supervisor_tick_s: float = _DEFAULT_SUPERVISOR_TICK_S,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if not str(account_id or "").strip():
            raise ValueError("QMTHostService requires a non-empty account_id")
        if broker_type not in ("qmt", "qmt_shadow"):
            raise ValueError(
                f"QMTHostService broker_type must be 'qmt' or 'qmt_shadow'; got {broker_type!r}"
            )
        if not callable(client_factory):
            raise TypeError("QMTHostService requires a callable client_factory")
        if reconnect_interval_s <= 0:
            raise ValueError("reconnect_interval_s must be positive")
        if callback_stale_grace_s <= 0:
            raise ValueError("callback_stale_grace_s must be positive")
        if supervisor_tick_s <= 0:
            raise ValueError("supervisor_tick_s must be positive")

        self._account_id = str(account_id).strip()
        self._broker_type = broker_type
        self._client_factory = client_factory
        self._reconnect_interval = timedelta(seconds=float(reconnect_interval_s))
        self._callback_stale_grace = timedelta(seconds=float(callback_stale_grace_s))
        self._supervisor_tick_s = float(supervisor_tick_s)
        self._now_fn = now_fn or _utc_now

        self._lock = threading.RLock()
        self._state = _HostState()
        self._client: Any | None = None
        self._supervisor_thread: threading.Thread | None = None
        # Event lets stop() unblock the supervisor's tick-sleep promptly.
        self._wake = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the supervisor thread.

        Idempotent on re-entry when the supervisor is already alive. After
        start, health transitions through ``CONNECTING`` -> (``READY`` |
        ``DISCONNECTED``) depending on whether the client factory succeeds.
        """
        with self._lock:
            if (
                self._supervisor_thread is not None
                and self._supervisor_thread.is_alive()
            ):
                return
            # Starting (or restarting after stop) resets state but keeps
            # reconnect_attempts so health history is not silently wiped.
            self._state.health = HostHealth.CONNECTING
            self._state.error_message = ""
            self._state.reconnect_requested = True
            self._state.stop_requested = False
            self._state.stopped_at = None
            self._wake.clear()
            thread = threading.Thread(
                target=self._supervisor_loop,
                name=f"qmt-host-{self._account_id}",
                daemon=True,
            )
            self._supervisor_thread = thread
        # Start the thread outside the lock so `supervisor_loop` can acquire
        # it without racing `start()` itself.
        thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        """Stop the supervisor thread and close the underlying client.

        Idempotent: repeated calls are no-ops once state is ``STOPPED``.
        Best-effort: if the client ``close()`` raises, the error is captured
        into ``error_message`` but state still advances to ``STOPPED``.
        """
        with self._lock:
            if self._state.health == HostHealth.STOPPED:
                return
            self._state.stop_requested = True
            self._state.reconnect_requested = False
            thread = self._supervisor_thread
            client = self._client
        # Wake the supervisor outside the lock so it can acquire the lock
        # and observe stop_requested promptly.
        self._wake.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_s)))
        close_error = ""
        if client is not None:
            close_fn = (
                getattr(client, "close", None)
                or getattr(client, "stop", None)
                or getattr(client, "shutdown", None)
            )
            if callable(close_fn):
                try:
                    close_fn()
                except Exception as exc:  # pragma: no cover - tested via fake raising
                    close_error = str(exc)
                    logger.warning(
                        "QMTHostService client close failed: %s", exc,
                    )
        with self._lock:
            self._state.health = HostHealth.STOPPED
            self._state.stopped_at = self._now_fn()
            if close_error:
                self._state.error_message = close_error
            self._client = None
            self._supervisor_thread = None

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------
    def subscribe(self, subscriber_id: str) -> None:
        """Register a subscriber. Idempotent on repeated same-id calls.

        The subscriber set is used for visibility and supervisor decisions
        (callback-staleness only matters if someone is listening). It never
        triggers host start/stop — the host is long-running.
        """
        sid = str(subscriber_id or "").strip()
        if not sid:
            raise ValueError("QMTHostService.subscribe requires a non-empty subscriber_id")
        with self._lock:
            self._state.subscribers.add(sid)

    def unsubscribe(self, subscriber_id: str) -> None:
        """Remove a subscriber id. Idempotent for unknown ids.

        Unsubscribing the last subscriber does **not** stop the host: host
        is long-running and only ``stop()`` should shut it down.
        """
        sid = str(subscriber_id or "").strip()
        if not sid:
            return
        with self._lock:
            self._state.subscribers.discard(sid)

    # ------------------------------------------------------------------
    # Status / gates
    # ------------------------------------------------------------------
    def status(self) -> HostStatusSummary:
        """Return a read-only snapshot of current host state.

        This is a pure read; it never mutates supervisor state.
        """
        with self._lock:
            return HostStatusSummary(
                health=self._state.health,
                account_id=self._account_id,
                broker_type=self._broker_type,
                session_started_at=self._state.session_started_at,
                last_callback_at=self._state.last_callback_at,
                last_reconnect_at=self._state.last_reconnect_at,
                reconnect_attempts=self._state.reconnect_attempts,
                subscriber_count=len(self._state.subscribers),
                error_message=self._state.error_message,
            )

    def is_ready(self) -> bool:
        """Convenience gate. True iff ``health == READY``."""
        with self._lock:
            return self._state.health == HostHealth.READY

    def ensure_ready_or_raise(self) -> None:
        """Fail-closed gate for scheduler integration.

        Scheduler / broker callers invoke this before any tick/submit/cancel
        so that a dead host fails the deployment tick with a clear
        ``RuntimeError`` instead of silently running off a stale cached
        client.
        """
        with self._lock:
            health = self._state.health
        if health != HostHealth.READY:
            raise RuntimeError(f"QMTHostService not ready: {health.value}")

    # ------------------------------------------------------------------
    # Client notifications (wired by integration; exposed here for tests
    # to drive state transitions deterministically)
    # ------------------------------------------------------------------
    def notify_callback_activity(self, *, when: datetime | None = None) -> None:
        """Record that the underlying client's callback bridge saw activity.

        The supervisor uses this timestamp to demote to ``DEGRADED`` if
        subscribers exist but no callback has been observed for more than
        ``callback_stale_grace_s``.
        """
        ts = when or self._now_fn()
        with self._lock:
            self._state.last_callback_at = ts

    def notify_disconnected(self, *, reason: str = "") -> None:
        """Record that the underlying client observed a disconnect.

        Scheduler / integration layer will call this from the xtquant
        ``on_disconnected`` callback. This moves the host to
        ``DISCONNECTED`` and requests a reconnect from the supervisor.
        """
        with self._lock:
            self._state.health = HostHealth.DISCONNECTED
            self._state.error_message = reason or "disconnected"
            self._state.reconnect_requested = True
        self._wake.set()

    # ------------------------------------------------------------------
    # Proxy methods — fail closed when not ready
    # ------------------------------------------------------------------
    def collect_sync_state(
        self,
        *,
        cursor_state: dict | None = None,
    ) -> dict:
        """Proxy for ``XtQuantShadowClient.collect_sync_state()``.

        Fails closed when host is not ``READY``.
        """
        self.ensure_ready_or_raise()
        with self._lock:
            client = self._client
        if client is None:  # pragma: no cover - ensure_ready_or_raise guards this
            raise RuntimeError("QMTHostService has no active client")
        fn = getattr(client, "collect_sync_state", None)
        if not callable(fn):
            raise RuntimeError(
                "underlying QMT client does not support collect_sync_state"
            )
        if cursor_state is None:
            return fn()
        return fn(cursor_state=cursor_state)

    def list_execution_reports(
        self,
        since: datetime | None = None,
    ) -> list[dict]:
        """Proxy for ``XtQuantShadowClient.list_execution_reports()``.

        Fails closed when host is not ``READY``.
        """
        self.ensure_ready_or_raise()
        with self._lock:
            client = self._client
        if client is None:  # pragma: no cover
            raise RuntimeError("QMTHostService has no active client")
        fn = getattr(client, "list_execution_reports", None)
        if not callable(fn):
            raise RuntimeError(
                "underlying QMT client does not support list_execution_reports"
            )
        return fn(since=since)

    def submit_order(self, **kwargs: Any) -> Any:
        """Proxy for ``XtQuantShadowClient.submit_order()``.

        Fails closed when host is not ``READY``. The scheduler integration
        layer is expected to apply ``qmt_submit_gate`` *before* calling
        through to here; this method is the last-line defense-in-depth.
        """
        self.ensure_ready_or_raise()
        with self._lock:
            client = self._client
        if client is None:  # pragma: no cover
            raise RuntimeError("QMTHostService has no active client")
        fn = getattr(client, "submit_order", None)
        if not callable(fn):
            raise RuntimeError(
                "underlying QMT client does not support submit_order"
            )
        return fn(**kwargs)

    def cancel_order(self, **kwargs: Any) -> Any:
        """Proxy for ``XtQuantShadowClient.cancel_order()``.

        Fails closed when host is not ``READY``.
        """
        self.ensure_ready_or_raise()
        with self._lock:
            client = self._client
        if client is None:  # pragma: no cover
            raise RuntimeError("QMTHostService has no active client")
        fn = getattr(client, "cancel_order", None)
        if not callable(fn):
            raise RuntimeError(
                "underlying QMT client does not support cancel_order"
            )
        return fn(**kwargs)

    # ------------------------------------------------------------------
    # Supervisor loop — internal
    # ------------------------------------------------------------------
    def _supervisor_loop(self) -> None:
        """Drive connect / reconnect / degraded / callback-freshness state.

        Runs in a daemon thread. Wakes on every tick, on stop(), and on
        ``notify_disconnected()``. Never raises out of the loop; any
        exception from the client factory is captured into state and the
        loop schedules a retry after ``reconnect_interval_s``.
        """
        next_reconnect_allowed_at: datetime | None = None
        while True:
            with self._lock:
                if self._state.stop_requested:
                    return
                health = self._state.health
                reconnect_requested = self._state.reconnect_requested
                subscribers = len(self._state.subscribers)
                last_callback_at = self._state.last_callback_at
                have_client = self._client is not None

            now = self._now_fn()

            # 1) Initial / reconnect attempt
            needs_connect_attempt = (
                health
                in (HostHealth.UNINITIALIZED, HostHealth.CONNECTING, HostHealth.DISCONNECTED)
                and reconnect_requested
                and (
                    next_reconnect_allowed_at is None
                    or now >= next_reconnect_allowed_at
                )
            )
            if needs_connect_attempt:
                try:
                    client = self._client_factory()
                except Exception as exc:
                    with self._lock:
                        self._state.health = HostHealth.DISCONNECTED
                        self._state.error_message = f"client_factory failed: {exc}"
                        self._state.reconnect_attempts += 1
                        self._state.last_reconnect_at = now
                        self._state.reconnect_requested = True
                    logger.warning(
                        "QMTHostService[%s] client_factory failed: %s",
                        self._account_id,
                        exc,
                    )
                    next_reconnect_allowed_at = now + self._reconnect_interval
                else:
                    with self._lock:
                        self._client = client
                        self._state.health = HostHealth.READY
                        self._state.session_started_at = (
                            self._state.session_started_at or now
                        )
                        self._state.last_reconnect_at = now
                        self._state.last_callback_at = now
                        self._state.reconnect_attempts += 1
                        self._state.reconnect_requested = False
                        self._state.error_message = ""
                    next_reconnect_allowed_at = None
            else:
                # 2) Degraded check: if READY but callbacks are stale AND we
                # actually have subscribers listening, demote to DEGRADED.
                if (
                    health == HostHealth.READY
                    and subscribers > 0
                    and last_callback_at is not None
                    and (now - last_callback_at) > self._callback_stale_grace
                ):
                    with self._lock:
                        if self._state.health == HostHealth.READY:
                            self._state.health = HostHealth.DEGRADED
                            self._state.error_message = (
                                "callback consumer stale; no recent callback activity"
                            )

                # 3) DEGRADED -> recover. If callbacks restart (consumer
                # re-emits activity via notify_callback_activity) the next
                # tick promotes back to READY.
                elif (
                    health == HostHealth.DEGRADED
                    and last_callback_at is not None
                    and (now - last_callback_at) <= self._callback_stale_grace
                    and have_client
                ):
                    with self._lock:
                        if self._state.health == HostHealth.DEGRADED:
                            self._state.health = HostHealth.READY
                            self._state.error_message = ""

            # Sleep until next tick, stop(), or external wake. ``Event.wait``
            # returns True if wake was set, False on timeout; in both cases
            # we just loop again.
            self._wake.wait(timeout=self._supervisor_tick_s)
            self._wake.clear()


__all__ = [
    "HostHealth",
    "HostStatusSummary",
    "QMTHostService",
]
