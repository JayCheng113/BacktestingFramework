"""Isolation tests for V3.3.45 ``QMTHostService``.

These tests use a local fake client (no xtquant import) to exercise
lifecycle, subscription, degraded demotion, reconnect behavior, proxy
fail-closed, and idempotency.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from ez.live.qmt.host import (
    HostHealth,
    HostStatusSummary,
    QMTHostService,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal stand-in for XtQuantShadowClient.

    Implements only the surface the host proxies through. Exceptions /
    delays can be driven by constructor flags so each test controls one
    aspect of behavior without needing its own class.
    """

    def __init__(
        self,
        *,
        submit_result: object = "submit-ok",
        cancel_result: object = "cancel-ok",
        sync_state: dict | None = None,
        reports: list[dict] | None = None,
        close_raises: Exception | None = None,
    ) -> None:
        self.submit_calls: list[dict] = []
        self.cancel_calls: list[dict] = []
        self.sync_calls: list[dict | None] = []
        self.report_calls: list[datetime | None] = []
        self.close_calls = 0
        self._submit_result = submit_result
        self._cancel_result = cancel_result
        self._sync_state = sync_state or {
            "asset": {},
            "positions": [],
            "orders": [],
            "trades": [],
            "execution_reports": [],
            "runtime_events": [],
            "cursor_state": {},
        }
        self._reports = reports or []
        self._close_raises = close_raises

    def submit_order(self, **kwargs):
        self.submit_calls.append(kwargs)
        return self._submit_result

    def cancel_order(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return self._cancel_result

    def collect_sync_state(self, *, cursor_state=None):
        self.sync_calls.append(cursor_state)
        return self._sync_state

    def list_execution_reports(self, since=None):
        self.report_calls.append(since)
        return list(self._reports)

    def close(self):
        self.close_calls += 1
        if self._close_raises is not None:
            raise self._close_raises


def _wait_until(predicate, *, timeout_s: float = 2.0, interval_s: float = 0.01):
    """Poll ``predicate`` until True or timeout; small-scale only.

    Used to let the daemon supervisor thread make the transition the test
    is asserting on without turning tests into long sleeps.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def _default_kwargs(**overrides):
    base = dict(
        account_id="acct-host-1",
        broker_type="qmt_shadow",
        reconnect_interval_s=0.05,
        callback_stale_grace_s=0.05,
        supervisor_tick_s=0.01,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_host_lifecycle_connects_then_stops():
    """start() -> CONNECTING -> READY -> stop() -> STOPPED."""
    client = _FakeClient()

    def factory():
        return client

    host = QMTHostService(client_factory=factory, **_default_kwargs())
    try:
        initial = host.status()
        assert initial.health == HostHealth.UNINITIALIZED
        assert initial.subscriber_count == 0
        assert initial.account_id == "acct-host-1"
        assert initial.broker_type == "qmt_shadow"

        host.start()
        # Either already READY or momentarily CONNECTING — then READY.
        assert _wait_until(host.is_ready), host.status()

        ready = host.status()
        assert ready.health == HostHealth.READY
        assert ready.session_started_at is not None
        assert ready.last_callback_at is not None
        assert ready.reconnect_attempts >= 1
        assert ready.error_message == ""
    finally:
        host.stop(timeout_s=1.0)

    stopped = host.status()
    assert stopped.health == HostHealth.STOPPED
    assert client.close_calls == 1


def test_host_degraded_when_callbacks_stale():
    """If callbacks go stale while subscribers exist, host demotes to DEGRADED."""
    client = _FakeClient()

    host = QMTHostService(
        client_factory=lambda: client,
        **_default_kwargs(callback_stale_grace_s=0.02, supervisor_tick_s=0.005),
    )
    try:
        host.start()
        assert _wait_until(host.is_ready), host.status()

        # Subscribe so the supervisor cares about callback staleness.
        host.subscribe("sub-A")

        # Pin last_callback_at into the past so the staleness check fires on
        # the very next supervisor tick. Without this the host's own
        # on-connect refresh would keep us READY indefinitely at this
        # timescale.
        stale_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
        host.notify_callback_activity(when=stale_ts)

        assert _wait_until(
            lambda: host.status().health == HostHealth.DEGRADED,
            timeout_s=2.0,
        ), host.status()

        summary = host.status()
        assert summary.health == HostHealth.DEGRADED
        assert summary.subscriber_count == 1
        assert "stale" in summary.error_message

        # Recovery: a fresh callback activity restores READY.
        host.notify_callback_activity()
        assert _wait_until(host.is_ready, timeout_s=2.0), host.status()
    finally:
        host.stop(timeout_s=1.0)


def test_host_reconnect_on_disconnected():
    """notify_disconnected() forces DISCONNECTED, then supervisor reconnects."""
    build_count = {"n": 0}

    def factory():
        build_count["n"] += 1
        return _FakeClient()

    host = QMTHostService(client_factory=factory, **_default_kwargs())
    try:
        host.start()
        assert _wait_until(host.is_ready), host.status()
        assert build_count["n"] == 1

        host.notify_disconnected(reason="fake disconnect")
        # Snapshot may be READY or already promoted back; accept either as
        # long as we eventually end READY after another factory call.
        assert _wait_until(
            lambda: build_count["n"] >= 2 and host.is_ready(),
            timeout_s=2.0,
        ), (host.status(), build_count)

        summary = host.status()
        assert summary.health == HostHealth.READY
        assert summary.reconnect_attempts >= 2
    finally:
        host.stop(timeout_s=1.0)


def test_ensure_ready_or_raise_blocks_when_not_ready():
    """When host is not READY, proxy methods raise RuntimeError."""
    host = QMTHostService(client_factory=lambda: _FakeClient(), **_default_kwargs())
    # Never started: health is UNINITIALIZED.

    with pytest.raises(RuntimeError, match="not ready"):
        host.ensure_ready_or_raise()
    with pytest.raises(RuntimeError, match="not ready"):
        host.submit_order(symbol="600000.SH", side="buy", shares=100, price=10.0,
                          strategy_name="t", order_remark="r")
    with pytest.raises(RuntimeError, match="not ready"):
        host.cancel_order(order_id="order-1")
    with pytest.raises(RuntimeError, match="not ready"):
        host.collect_sync_state()
    with pytest.raises(RuntimeError, match="not ready"):
        host.list_execution_reports()


def test_ensure_ready_or_raise_blocks_when_disconnected():
    """Health forced to DISCONNECTED (via factory failure) must fail-close."""
    def failing_factory():
        raise RuntimeError("xtquant connect refused")

    host = QMTHostService(
        client_factory=failing_factory,
        **_default_kwargs(reconnect_interval_s=10.0),
    )
    try:
        host.start()
        assert _wait_until(
            lambda: host.status().health == HostHealth.DISCONNECTED,
            timeout_s=2.0,
        ), host.status()

        summary = host.status()
        assert summary.health == HostHealth.DISCONNECTED
        assert "client_factory failed" in summary.error_message
        assert summary.reconnect_attempts >= 1

        with pytest.raises(RuntimeError, match="not ready"):
            host.submit_order(symbol="x", side="buy", shares=1, price=1.0,
                              strategy_name="s", order_remark="r")
    finally:
        host.stop(timeout_s=1.0)


def test_subscriber_count_idempotent():
    """Repeated subscribe(same_id) does not inflate the count."""
    host = QMTHostService(client_factory=lambda: _FakeClient(), **_default_kwargs())

    assert host.status().subscriber_count == 0
    host.subscribe("dep-1")
    host.subscribe("dep-1")
    host.subscribe("dep-1")
    assert host.status().subscriber_count == 1

    host.subscribe("dep-2")
    assert host.status().subscriber_count == 2

    host.unsubscribe("dep-1")
    assert host.status().subscriber_count == 1

    # Unsubscribe unknown id is a no-op, not an error.
    host.unsubscribe("dep-unknown")
    assert host.status().subscriber_count == 1

    # Blank id is silently ignored on unsubscribe.
    host.unsubscribe("")
    assert host.status().subscriber_count == 1

    # Blank id on subscribe is rejected.
    with pytest.raises(ValueError):
        host.subscribe("")


def test_host_stop_is_idempotent():
    """Repeated stop() calls are no-ops after the first."""
    client = _FakeClient()
    host = QMTHostService(client_factory=lambda: client, **_default_kwargs())
    host.start()
    assert _wait_until(host.is_ready), host.status()

    host.stop(timeout_s=1.0)
    host.stop(timeout_s=1.0)
    host.stop(timeout_s=1.0)

    assert host.status().health == HostHealth.STOPPED
    # close() runs at most once across repeated stop() calls.
    assert client.close_calls == 1


def test_proxy_methods_forward_when_ready():
    """READY host delegates submit/cancel/sync/reports to the underlying client."""
    reports = [{"report_id": "r-1", "status": "filled"}]
    sync_state = {
        "asset": {"cash": 1000.0, "total_asset": 1000.0},
        "positions": [],
        "orders": [],
        "trades": [],
        "execution_reports": [],
        "runtime_events": [],
        "cursor_state": {"callback_execution_seq": 0, "callback_runtime_seq": 0},
    }
    client = _FakeClient(
        submit_result="submit-ack-1",
        cancel_result="cancel-ack-1",
        reports=reports,
        sync_state=sync_state,
    )
    host = QMTHostService(client_factory=lambda: client, **_default_kwargs())
    try:
        host.start()
        assert _wait_until(host.is_ready), host.status()

        ack = host.submit_order(
            symbol="600000.SH",
            side="buy",
            shares=200,
            price=10.1,
            strategy_name="t",
            order_remark="rmk",
        )
        assert ack == "submit-ack-1"
        assert client.submit_calls == [
            dict(
                symbol="600000.SH",
                side="buy",
                shares=200,
                price=10.1,
                strategy_name="t",
                order_remark="rmk",
            )
        ]

        cancel_ack = host.cancel_order(order_id="order-1", symbol="600000.SH")
        assert cancel_ack == "cancel-ack-1"
        assert client.cancel_calls == [
            dict(order_id="order-1", symbol="600000.SH"),
        ]

        state = host.collect_sync_state()
        assert state is sync_state
        assert client.sync_calls == [None]

        state2 = host.collect_sync_state(cursor_state={"callback_execution_seq": 7})
        assert state2 is sync_state
        assert client.sync_calls[-1] == {"callback_execution_seq": 7}

        got_reports = host.list_execution_reports()
        assert got_reports == reports
        assert client.report_calls == [None]

        since_ts = datetime.now(timezone.utc)
        host.list_execution_reports(since=since_ts)
        assert client.report_calls[-1] == since_ts
    finally:
        host.stop(timeout_s=1.0)


def test_host_status_is_a_read_only_snapshot_copy():
    """status() returns a frozen-ish dataclass detached from internal state."""
    host = QMTHostService(client_factory=lambda: _FakeClient(), **_default_kwargs())
    try:
        host.start()
        assert _wait_until(host.is_ready), host.status()

        host.subscribe("dep-A")
        snap_before = host.status()
        assert isinstance(snap_before, HostStatusSummary)
        assert snap_before.subscriber_count == 1

        host.subscribe("dep-B")
        # Previous snapshot object must not reflect the new subscriber —
        # it's a plain dataclass snapshot copy, not a live view.
        assert snap_before.subscriber_count == 1
        assert host.status().subscriber_count == 2
    finally:
        host.stop(timeout_s=1.0)


def test_host_rejects_invalid_construction():
    """Constructor guards configuration errors up front."""
    with pytest.raises(ValueError):
        QMTHostService(
            client_factory=lambda: _FakeClient(),
            **_default_kwargs(account_id=""),
        )
    with pytest.raises(ValueError):
        QMTHostService(
            client_factory=lambda: _FakeClient(),
            **_default_kwargs(broker_type="ibkr"),
        )
    with pytest.raises(TypeError):
        QMTHostService(
            account_id="acct-1",
            broker_type="qmt",
            client_factory=None,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        QMTHostService(
            client_factory=lambda: _FakeClient(),
            **_default_kwargs(reconnect_interval_s=0.0),
        )


def test_start_is_idempotent_while_running():
    """Repeated start() on an already-running host does not spawn new threads."""
    client = _FakeClient()
    build_count = {"n": 0}

    def factory():
        build_count["n"] += 1
        return client

    host = QMTHostService(client_factory=factory, **_default_kwargs())
    try:
        host.start()
        assert _wait_until(host.is_ready), host.status()
        initial_built = build_count["n"]

        # Count live threads with our host's name before and after repeat start().
        def _host_threads():
            name = f"qmt-host-{host.status().account_id}"
            return [t for t in threading.enumerate() if t.name == name]

        before = len(_host_threads())
        host.start()
        host.start()
        after = len(_host_threads())

        assert before == after == 1
        # No extra client builds triggered by repeat start().
        assert build_count["n"] == initial_built
    finally:
        host.stop(timeout_s=1.0)


def test_close_error_is_captured_but_does_not_mask_stopped_state():
    """If client.close() raises, host still reaches STOPPED and records the error."""
    failing_client = _FakeClient(close_raises=RuntimeError("broker hangup"))
    host = QMTHostService(client_factory=lambda: failing_client, **_default_kwargs())
    host.start()
    assert _wait_until(host.is_ready), host.status()

    host.stop(timeout_s=1.0)
    summary = host.status()
    assert summary.health == HostHealth.STOPPED
    assert "broker hangup" in summary.error_message
    assert failing_client.close_calls == 1
