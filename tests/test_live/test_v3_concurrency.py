"""V3 concurrency / idempotency regression tests.

Covers the hazard classes that were not previously regressed:

1. Same event_id replayed multiple times (e.g. buggy upstream / callback
   bridge delivering the same fill twice) must NOT double-count in the
   ledger.
2. Out-of-order callback arrival (CANCELED appended to the log BEFORE the
   earlier PARTIALLY_FILLED but with event_ts values that reflect the true
   order) must still produce the correct final state once the ledger
   sorts by (event_ts, priority).
3. Duplicate broker execution reports (same broker_order_id + same
   report_id arriving N times) must not create N broker-order-link rows;
   the primary-key upsert keeps a single canonical link.
4. Scheduler restart while a cancel is in flight (`reported_cancel_pending`
   link) must not regress the link back to ``reported`` during recovery /
   resume.
5. Fix-A ledger transitions:
   - ``PARTIALLY_FILLED -> CANCELED`` is allowed (so a ``partial_cancel``
     terminal broker callback can close a partially-filled link).
   - Terminal ``FILLED / CANCELED`` cannot transition to another terminal
     state (``broker_order_status_can_transition`` is absorbing).
6. Fix-A broker report semantics: a submission-only report (filled_shares=0)
   may advance the broker-order projection and the coarse local order
   status, but MUST NOT mutate cash / holdings. Only ``ORDER_FILLED`` /
   ``ORDER_PARTIALLY_FILLED`` events move money.
7. Deadlock regression (bug found during Fix-A..E review): the
   ``DeploymentStore`` non-reentrant lock is re-acquired when an
   ``cancel_order_stock_async_response`` runtime event carrying only
   ``order_sysid`` / ``broker_order_id`` passes through
   ``append_event`` / ``_append_events_locked`` /
   ``_upsert_broker_cancel_ack_link_locked``. This file asserts the
   current broken behaviour with a bounded-time wrapper so the test
   suite can flip to ``strict=False`` once the lock is made reentrant.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import duckdb
import pytest

from ez.live.broker import (
    BrokerAccountSnapshot,
    BrokerCapability,
    BrokerExecutionReport,
    BrokerRuntimeEvent,
)
from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.events import (
    DeploymentEvent,
    EventType,
    OrderStatus,
    broker_order_status_can_transition,
    make_broker_execution_event,
    make_broker_runtime_event,
    make_client_order_id,
    utcnow,
)
from ez.live.ledger import LiveLedger
from ez.live.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _make_store() -> DeploymentStore:
    return DeploymentStore(duckdb.connect(":memory:"))


def _make_spec(**overrides) -> DeploymentSpec:
    defaults = dict(
        strategy_name="TopNRotation",
        strategy_params={"factor": "momentum_rank_20", "top_n": 5},
        symbols=("000001.SZ", "000002.SZ"),
        market="cn_stock",
        freq="weekly",
        initial_cash=1_000_000.0,
        shadow_broker_type="qmt",
        risk_params={
            "shadow_broker_config": {"account_id": "acct-1", "enable_cancel": True},
        },
    )
    defaults.update(overrides)
    return DeploymentSpec(**defaults)


def _make_fill_event(
    deployment_id: str,
    *,
    client_order_id: str,
    shares: int,
    price: float,
    event_id: str,
    event_ts: datetime | None = None,
) -> DeploymentEvent:
    """Build an ORDER_FILLED event with a caller-supplied event_id.

    Giving tests control over event_id is essential for idempotency
    regressions: the production pipeline generates event_ids from the
    client_order_id + event_type, so a real bug path that replays the
    same fill will always re-use the same event_id.
    """
    return DeploymentEvent(
        event_id=event_id,
        deployment_id=deployment_id,
        event_type=EventType.ORDER_FILLED,
        event_ts=event_ts or utcnow(),
        client_order_id=client_order_id,
        payload={
            "symbol": "AAA",
            "side": "buy",
            "shares": int(shares),
            "price": float(price),
            "amount": float(shares) * float(price),
            "cost": 0.0,
            "business_date": "2026-04-14",
        },
    )


class _FakeCancelShadowBroker:
    """Minimal cancel-capable shadow broker for restart-during-cancel test."""

    broker_type = "qmt"

    def __init__(self):
        self._partial_emitted = False
        self._cancel_requested = False
        self.cancel_calls: list[tuple[str, str]] = []

    @property
    def capabilities(self):
        return frozenset(
            {
                BrokerCapability.READ_ACCOUNT_STATE,
                BrokerCapability.SHADOW_MODE,
                BrokerCapability.STREAM_EXECUTION_REPORTS,
                BrokerCapability.CANCEL_ORDER,
            }
        )

    def snapshot_account_state(self):
        open_orders = []
        if not self._cancel_requested:
            open_orders.append(
                {
                    "client_order_id": "dep-concurrency:2026-04-14:000001.SZ:buy",
                    "broker_order_id": "SYS-900",
                    "symbol": "000001.SZ",
                    "status": (
                        "partially_filled_cancel_pending"
                        if self._cancel_requested
                        else "partially_filled"
                    ),
                    "requested_shares": 1000,
                    "filled_shares": 600,
                    "remaining_shares": 400,
                    "avg_price": 12.34,
                    "updated_at": "2026-04-14T15:00:00+00:00",
                }
            )
        return BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2026, 4, 14, 15, 2, tzinfo=timezone.utc),
            cash=0.0,
            total_asset=0.0,
            positions={},
            open_orders=open_orders,
            fills=[],
        )

    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="account_status:acct-1:connected:2026-04-14T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2026, 4, 14, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-1",
                    "account_type": "STOCK",
                    "status": "connected",
                },
            )
        ]
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events

    def list_execution_reports(self, *, since=None):
        report_time = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
        reports = [
            BrokerExecutionReport(
                report_id="qmt:SYS-900:partially_filled:600:400:2026-04-14T15:00:00+00:00",
                broker_type="qmt",
                as_of=report_time,
                client_order_id="dep-concurrency:2026-04-14:000001.SZ:buy",
                broker_order_id="SYS-900",
                symbol="000001.SZ",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=12.34,
                message="partial",
                raw_payload={"order_sysid": "SYS-900"},
            )
        ]
        self._partial_emitted = True
        if since is not None:
            reports = [report for report in reports if report.as_of >= since]
        return reports

    def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
        self._cancel_requested = True
        self.cancel_calls.append((order_id, symbol))
        return True


# ---------------------------------------------------------------------------
# 1. event_id idempotency
# ---------------------------------------------------------------------------


class TestEventIdempotency:
    """Same event_id replayed N times -> ledger state == replayed once."""

    def test_same_event_id_applied_once_even_when_list_duplicates_it(self):
        dep_id = "dep-idem"
        client_order_id = make_client_order_id(dep_id, date(2026, 4, 14), "AAA", "buy")
        event_id = "dep-idem:2026-04-14:AAA:buy:order_filled"
        events: list[DeploymentEvent] = []
        # ORDER_SUBMITTED first so local OrderStatus transitions land cleanly.
        events.append(
            DeploymentEvent(
                event_id=f"{client_order_id}:order_submitted",
                deployment_id=dep_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
                client_order_id=client_order_id,
                payload={
                    "symbol": "AAA",
                    "side": "buy",
                    "shares": 1000,
                    "business_date": "2026-04-14",
                },
            )
        )
        # Now 10 copies of the SAME fill event_id. Good upstream paths never
        # do this, but buggy callback bridges can replay buffered events.
        for _ in range(10):
            events.append(
                _make_fill_event(
                    dep_id,
                    client_order_id=client_order_id,
                    shares=1000,
                    price=10.0,
                    event_id=event_id,
                    event_ts=datetime(2026, 4, 14, 15, 1, tzinfo=timezone.utc),
                )
            )

        state_10x = LiveLedger().replay(events, initial_cash=1_000_000.0)
        state_1x = LiveLedger().replay(
            [events[0], events[1]], initial_cash=1_000_000.0
        )

        # Fill only counted once — cash / holdings identical between replays.
        assert state_10x.cash == state_1x.cash
        assert state_10x.holdings == state_1x.holdings
        # Audit counter confirms dedup (1 unique submitted + 1 unique filled).
        assert state_10x.seen_event_count == 2
        assert state_10x.order_statuses[client_order_id] == OrderStatus.FILLED.value
        # Trade ledger also counts the fill once.
        assert len(state_10x.trades) == 1

    def test_store_append_events_drops_duplicate_event_ids(self):
        """DeploymentStore.append_events with INSERT OR IGNORE is idempotent.

        Even if a caller manages to enqueue the same event_id twice, the
        store refuses to add a second row. This is the persistence-layer
        safeguard that mirrors the ledger-layer dedup above.
        """
        store = _make_store()
        try:
            dep_id = "dep-idem-store"
            event = DeploymentEvent(
                event_id="dep-idem-store:fill-1",
                deployment_id=dep_id,
                event_type=EventType.ORDER_FILLED,
                event_ts=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
                client_order_id=f"{dep_id}:2026-04-14:AAA:buy",
                payload={
                    "symbol": "AAA",
                    "side": "buy",
                    "shares": 100,
                    "amount": 1000.0,
                    "cost": 0.0,
                    "price": 10.0,
                    "business_date": "2026-04-14",
                },
            )
            inserted_first = store.append_events([event])
            inserted_again = store.append_events([event, event, event])

            assert inserted_first == 1
            assert inserted_again == 0
            all_events = store.get_events(dep_id)
            assert len(all_events) == 1
        finally:
            store.close()


# ---------------------------------------------------------------------------
# 2. Out-of-order callback arrival
# ---------------------------------------------------------------------------


class TestOutOfOrderCallbacks:
    """Event appended order != event_ts order -> sort makes final state correct."""

    def test_canceled_appended_before_partial_fill_is_still_ordered_by_event_ts(self):
        dep_id = "dep-oo"
        # t1 = partial fill time (earlier); t2 = cancel terminal time (later).
        t1 = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
        t2 = t1 + timedelta(seconds=30)

        # Append the terminal callback FIRST (as a buggy bridge might do)
        # and only then the earlier partial_filled callback. The ledger
        # should still sort by (event_ts, priority) and apply them in
        # logical order.
        events = [
            make_broker_execution_event(
                dep_id,
                report_id="out-of-order-canceled",
                broker_type="qmt",
                report_ts=t2,
                client_order_id=f"{dep_id}:2026-04-14:AAA:buy",
                broker_order_id="SYS-OOO",
                symbol="AAA",
                side="buy",
                status="canceled",
                filled_shares=600,
                remaining_shares=0,
                avg_price=10.0,
                raw_payload={"order_sysid": "SYS-OOO", "status": "canceled"},
            ),
            make_broker_execution_event(
                dep_id,
                report_id="out-of-order-partial",
                broker_type="qmt",
                report_ts=t1,
                client_order_id=f"{dep_id}:2026-04-14:AAA:buy",
                broker_order_id="SYS-OOO",
                symbol="AAA",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=10.0,
                raw_payload={"order_sysid": "SYS-OOO", "status": "partially_filled"},
            ),
        ]

        state = LiveLedger().replay(events, initial_cash=1_000_000.0)

        broker_state = state.broker_order_states["SYS-OOO"]
        # The terminal state must win (rank 30 > rank 20). Accept any of
        # ``canceled`` / ``partially_canceled`` — both are legal terminal
        # labels depending on the normalization path.
        assert broker_state["status"] in {"canceled", "partially_canceled"}
        # event_id records which callback actually won the forward-only
        # advance. Since cancel is terminal and partial is non-terminal,
        # cancel should be the last applied state.
        assert broker_state["report_id"] == "out-of-order-canceled"
        # event_ts on the winning state must match the later timestamp.
        assert broker_state["event_ts"] == t2

    def test_terminal_callback_is_not_regressed_by_later_partial_in_same_replay(self):
        """Even when a stale partial_filled callback arrives after a terminal one,
        the ledger's forward-only broker-status rank keeps the terminal state.
        """
        dep_id = "dep-terminal-guard"
        t_terminal = datetime(2026, 4, 14, 15, 5, tzinfo=timezone.utc)
        t_stale = t_terminal + timedelta(seconds=60)

        events = [
            make_broker_execution_event(
                dep_id,
                report_id="term-1",
                broker_type="qmt",
                report_ts=t_terminal,
                client_order_id=f"{dep_id}:2026-04-14:AAA:buy",
                broker_order_id="SYS-TT",
                symbol="AAA",
                side="buy",
                status="canceled",
                filled_shares=600,
                remaining_shares=0,
                avg_price=10.0,
            ),
            # A stale partial_filled arriving later (wall-clock) must NOT
            # regress the already-terminal broker state.
            make_broker_execution_event(
                dep_id,
                report_id="stale-partial",
                broker_type="qmt",
                report_ts=t_stale,
                client_order_id=f"{dep_id}:2026-04-14:AAA:buy",
                broker_order_id="SYS-TT",
                symbol="AAA",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=10.0,
            ),
        ]

        state = LiveLedger().replay(events, initial_cash=1_000_000.0)

        assert state.broker_order_states["SYS-TT"]["status"] in {
            "canceled",
            "partially_canceled",
        }


# ---------------------------------------------------------------------------
# 3. Duplicate broker reports
# ---------------------------------------------------------------------------


class TestDuplicateBrokerReports:
    """Same broker_order_id + same report_id N times -> one link row."""

    def test_same_report_delivered_three_times_produces_one_link(self):
        store = _make_store()
        try:
            dep_id = "dep-dup-report"
            report = BrokerExecutionReport(
                report_id="qmt:SYS-500:reported:0:1000:2026-04-14T15:00:00+00:00",
                broker_type="qmt",
                as_of=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
                client_order_id=f"{dep_id}:2026-04-14:AAA:buy",
                broker_order_id="SYS-500",
                symbol="AAA",
                side="buy",
                status="reported",
                filled_shares=0,
                remaining_shares=1000,
                avg_price=12.34,
                message="submitted",
                raw_payload={"order_sysid": "SYS-500"},
            )
            # Three back-to-back sync calls with the same report.
            for _ in range(3):
                store.save_broker_sync_result(
                    deployment_id=dep_id,
                    events=[],
                    broker_reports=[report],
                )

            links = store.list_broker_order_links(dep_id, broker_type="qmt")
            # Only one link row despite three report deliveries.
            assert len(links) == 1
            assert links[0]["broker_order_id"] == "SYS-500"
            assert links[0]["latest_status"] == "reported"
            # latest_report_id must be the canonical report_id — never
            # duplicated or concatenated.
            assert links[0]["latest_report_id"] == report.report_id
        finally:
            store.close()

    def test_duplicate_report_event_is_deduped_at_store_level_by_event_id(self):
        """Broker execution events carry event_id = f'{dep_id}:broker_report:{report_id}'.

        So same report delivered twice produces two events in memory but the
        store's INSERT OR IGNORE by event_id keeps only one row.
        """
        store = _make_store()
        try:
            dep_id = "dep-dup-event"
            event = make_broker_execution_event(
                dep_id,
                report_id="qmt:SYS-501:partially_filled:600:400:2026-04-14T15:00:00+00:00",
                broker_type="qmt",
                report_ts=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
                client_order_id=f"{dep_id}:2026-04-14:AAA:buy",
                broker_order_id="SYS-501",
                symbol="AAA",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=12.34,
            )
            for _ in range(3):
                store.append_events([event])
            # Exactly one event row despite three append attempts.
            all_events = [
                evt for evt in store.get_events(dep_id)
                if evt.event_type == EventType.BROKER_EXECUTION_RECORDED
            ]
            assert len(all_events) == 1
        finally:
            store.close()


# ---------------------------------------------------------------------------
# 4. Restart during cancel
# ---------------------------------------------------------------------------


class TestRestartDuringCancel:
    """Scheduler instance replacement mid-cancel must not regress link state."""

    @pytest.mark.asyncio
    async def test_new_scheduler_resumes_without_regressing_cancel_pending_link(
        self,
    ):
        store = _make_store()
        try:
            spec = _make_spec()
            store.save_spec(spec)
            record = DeploymentRecord(
                spec_id=spec.spec_id,
                name="Restart during cancel",
                status="approved",
            )
            store.save_record(record)

            shadow_broker = _FakeCancelShadowBroker()
            scheduler = Scheduler(
                store=store,
                data_chain=MagicMock(),
                broker_factories={
                    "paper": lambda _spec: MagicMock(
                        capabilities=frozenset(
                            {BrokerCapability.TARGET_WEIGHT_EXECUTION}
                        )
                    ),
                    "qmt": lambda _spec: shadow_broker,
                },
            )
            from ez.portfolio.calendar import TradingCalendar

            scheduler._calendars["cn_stock"] = TradingCalendar.weekday_fallback(
                date(2026, 1, 1), date(2026, 12, 31)
            )

            await scheduler.start_deployment(record.deployment_id)
            # Mock the engine's _last_prices to avoid execute_day call.
            engine = scheduler._engines[record.deployment_id]
            engine._last_prices = {"000001.SZ": 12.34}

            # Force a pump so the partial_filled link enters the store.
            await scheduler.pump_broker_state(record.deployment_id)

            canonical_cid = "dep-concurrency:2026-04-14:000001.SZ:buy"
            link = store.get_broker_order_link(
                record.deployment_id,
                broker_type="qmt",
                client_order_id=canonical_cid,
            )
            assert link is not None
            assert link["latest_status"] == "partially_filled"

            # Request cancel -> link should become reported_cancel_pending.
            await scheduler.cancel_order(
                record.deployment_id,
                broker_order_id="SYS-900",
            )
            link_after_cancel = store.get_broker_order_link(
                record.deployment_id,
                broker_type="qmt",
                client_order_id=canonical_cid,
            )
            assert link_after_cancel is not None
            assert link_after_cancel["latest_status"] in {
                "reported_cancel_pending",
                "partially_filled_cancel_pending",
            }

            # ---- Simulate scheduler restart ----
            # Drop the old scheduler, build a fresh one wired to the same store,
            # and call resume_all(). The cancel-in-flight link must survive.
            new_shadow_broker = _FakeCancelShadowBroker()
            # Seed the new broker's state so the shadow snapshot still reports
            # the cancel-pending open order (real QMT callback state would do
            # this automatically post-restart).
            new_shadow_broker._cancel_requested = True
            new_scheduler = Scheduler(
                store=store,
                data_chain=MagicMock(),
                broker_factories={
                    "paper": lambda _spec: MagicMock(
                        capabilities=frozenset(
                            {BrokerCapability.TARGET_WEIGHT_EXECUTION}
                        )
                    ),
                    "qmt": lambda _spec: new_shadow_broker,
                },
            )
            new_scheduler._calendars["cn_stock"] = TradingCalendar.weekday_fallback(
                date(2026, 1, 1), date(2026, 12, 31)
            )

            restored = await new_scheduler.resume_all()
            assert restored == 1

            # Confirm the cancel-pending link was NOT regressed by recovery.
            link_post_restart = store.get_broker_order_link(
                record.deployment_id,
                broker_type="qmt",
                client_order_id=canonical_cid,
            )
            assert link_post_restart is not None
            # Either still pending or already transitioned to a terminal cancel
            # state — but never regressed to plain ``reported`` / ``partially_filled``.
            assert link_post_restart["latest_status"] not in {
                "reported",
                "partially_filled",
            }
        finally:
            store.close()


# ---------------------------------------------------------------------------
# 5. Fix-A: partial-fill -> canceled allowed, filled -> canceled blocked
# ---------------------------------------------------------------------------


class TestLedgerTerminalTransitions:
    """``broker_order_status_can_transition`` rules after Fix-A."""

    def test_ledger_partial_fill_to_canceled_allowed(self):
        """A ``partially_canceled`` terminal report arriving after a
        ``partially_filled`` callback is the canonical ``ORDER_PART_CANCEL``
        xtquant flow; the ledger must accept the transition so the final
        broker-order state reflects the cancel.
        """
        dep_id = "dep-pf-to-canceled"
        t_partial = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
        t_cancel = t_partial + timedelta(seconds=45)
        client_order_id = f"{dep_id}:2026-04-14:AAA:buy"

        events = [
            make_broker_execution_event(
                dep_id,
                report_id="pf-partial-1",
                broker_type="qmt",
                report_ts=t_partial,
                client_order_id=client_order_id,
                broker_order_id="SYS-PF1",
                symbol="AAA",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=10.0,
            ),
            make_broker_execution_event(
                dep_id,
                report_id="pf-cancel-1",
                broker_type="qmt",
                report_ts=t_cancel,
                client_order_id=client_order_id,
                broker_order_id="SYS-PF1",
                symbol="AAA",
                side="buy",
                status="partially_canceled",
                filled_shares=600,
                remaining_shares=0,
                avg_price=10.0,
            ),
        ]

        state = LiveLedger().replay(events, initial_cash=1_000_000.0)
        broker_state = state.broker_order_states["SYS-PF1"]
        # Terminal state took effect: partial fills stay counted, remaining
        # is zero, lifecycle label is a terminal cancel variant.
        assert broker_state["status"] in {"partially_canceled", "canceled"}
        assert int(broker_state.get("filled_shares", 0)) == 600
        assert int(broker_state.get("remaining_shares", 0)) == 0
        # Forward-only rule is ALLOWING this transition explicitly.
        assert broker_order_status_can_transition(
            "partially_filled", "partially_canceled"
        ) is True
        # OMS coarse status also advances to CANCELED (partially_canceled maps
        # to CANCELED via ``broker_order_status_to_order_status``).
        assert state.order_statuses[client_order_id] == OrderStatus.CANCELED.value

    def test_ledger_filled_to_canceled_blocked(self):
        """``FILLED`` is absorbing; a stale ``canceled`` callback that
        arrives afterwards must NOT flip the broker-order state.
        """
        assert broker_order_status_can_transition("filled", "canceled") is False
        assert broker_order_status_can_transition("canceled", "filled") is False
        # But the canonical xtquant cross-terminal move stays legal.
        assert broker_order_status_can_transition(
            "partially_canceled", "canceled"
        ) is True

        dep_id = "dep-filled-absorbing"
        t_fill = datetime(2026, 4, 14, 15, 5, tzinfo=timezone.utc)
        t_stale_cancel = t_fill + timedelta(seconds=90)
        client_order_id = f"{dep_id}:2026-04-14:AAA:buy"

        events = [
            make_broker_execution_event(
                dep_id,
                report_id="fill-full-1",
                broker_type="qmt",
                report_ts=t_fill,
                client_order_id=client_order_id,
                broker_order_id="SYS-FF1",
                symbol="AAA",
                side="buy",
                status="filled",
                filled_shares=1000,
                remaining_shares=0,
                avg_price=10.0,
            ),
            # A later (wall-clock) stale ``canceled`` callback — must NOT win.
            make_broker_execution_event(
                dep_id,
                report_id="stale-cancel-1",
                broker_type="qmt",
                report_ts=t_stale_cancel,
                client_order_id=client_order_id,
                broker_order_id="SYS-FF1",
                symbol="AAA",
                side="buy",
                status="canceled",
                filled_shares=1000,
                remaining_shares=0,
                avg_price=10.0,
            ),
        ]

        state = LiveLedger().replay(events, initial_cash=1_000_000.0)
        broker_state = state.broker_order_states["SYS-FF1"]
        assert broker_state["status"] == "filled"


# ---------------------------------------------------------------------------
# 6. Fix-A: submission-only report must NOT mutate cash / holdings
# ---------------------------------------------------------------------------


class TestSubmissionOnlyReport:
    """``BROKER_EXECUTION_RECORDED`` never moves money.

    Only ``ORDER_FILLED`` / ``ORDER_PARTIALLY_FILLED`` events touch
    cash / holdings. A submission-only broker report (``filled_shares=0``
    and a non-terminal status) may advance broker lifecycle state and
    local order status, but the cash + holdings projection must remain
    at the initial values.
    """

    def test_submission_only_report_does_not_move_cash_or_holdings(self):
        dep_id = "dep-submit-only"
        t_submit = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
        client_order_id = f"{dep_id}:2026-04-14:AAA:buy"

        events = [
            make_broker_execution_event(
                dep_id,
                report_id="submit-only-1",
                broker_type="qmt",
                report_ts=t_submit,
                client_order_id=client_order_id,
                broker_order_id="SYS-SB1",
                symbol="AAA",
                side="buy",
                status="reported",
                filled_shares=0,
                remaining_shares=1000,
                avg_price=0.0,
            ),
        ]

        state = LiveLedger().replay(events, initial_cash=1_000_000.0)

        # Cash / holdings untouched — money only moves on ORDER_FILLED /
        # ORDER_PARTIALLY_FILLED events, never on broker-report-only events.
        assert state.cash == 1_000_000.0
        assert state.holdings == {}
        # But the broker-order projection DID pick up the submission.
        assert "SYS-SB1" in state.broker_order_states
        assert state.broker_order_states["SYS-SB1"]["status"] == "reported"
        # And the OMS coarse order status advanced to SUBMITTED.
        assert state.order_statuses[client_order_id] == OrderStatus.SUBMITTED.value
        # The trades ledger stays empty — no fill means no trade row.
        assert state.trades == []


# ---------------------------------------------------------------------------
# 7. Deadlock regression for DeploymentStore non-reentrant lock
# ---------------------------------------------------------------------------


class TestStoreReentrantLockDeadlock:
    """Regression for the production deadlock found during Fix-A..E review.

    ``DeploymentStore._upsert_broker_cancel_ack_link_locked`` (called from
    ``_append_events_locked`` while ``self._lock`` is held) calls
    ``list_broker_order_links_by_broker_order_id``, which re-acquires the
    same non-reentrant ``threading.Lock``. When a QMT
    ``cancel_order_stock_async_response`` runtime event carries only
    ``order_sysid`` / ``broker_order_id`` (no ``client_order_id`` /
    ``order_remark``), ``append_event()`` deadlocks forever.

    The test reproduces the hazard on a 5-second watchdog thread. While
    production stays broken, the watchdog trips (``worker_finished`` is
    False) and the test is ``xfail``. Once the lock is made reentrant
    (or the upsert path is split into ``*_locked`` helpers), the worker
    will finish quickly and the test will flip green.
    """

    def test_cancel_ack_with_only_broker_order_id_does_not_deadlock(self):
        # Regression: DeploymentStore._lock is now threading.RLock() so the
        # cancel-ack link upsert path can re-enter through
        # list_broker_order_links_by_broker_order_id. Worker finishes quickly.
        store = _make_store()
        try:
            dep_id = "dep-reentrant-deadlock"
            runtime_event_id = "cancel_async:SYS-RENTRY-1"
            event = make_broker_runtime_event(
                dep_id,
                runtime_event_id=runtime_event_id,
                broker_type="qmt",
                runtime_kind="cancel_order_stock_async_response",
                event_ts=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
                # Crucial: NO client_order_id / order_remark in payload,
                # only order_sysid — this is the field shape the QMT
                # callback bridge emits before the canonical link is known.
                payload={
                    "order_sysid": "SYS-RENTRY-1",
                    "cancel_result": 0,
                    "seq": 88,
                },
            )

            worker_finished = threading.Event()

            def _runner():
                try:
                    store.append_event(event)
                finally:
                    worker_finished.set()

            worker = threading.Thread(target=_runner, daemon=True)
            worker.start()
            # 5s watchdog — today the call deadlocks forever; after the
            # production fix it should finish in milliseconds.
            worker.join(timeout=5.0)
            assert worker_finished.is_set(), (
                "append_event did not return within 5s — the non-reentrant "
                "DeploymentStore._lock is deadlocking the cancel-ack upsert "
                "path. See the class docstring for the required production "
                "fix."
            )
        finally:
            # Don't attempt to close() the store while a background thread
            # still holds the lock — that can hang test teardown.
            # ``DeploymentStore`` objects are short-lived in tests and the
            # in-memory DuckDB connection gets GC'd with the process.
            if worker_finished.is_set():
                store.close()
