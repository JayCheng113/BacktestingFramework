from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ez.live.events import (
    DeploymentEvent,
    EventType,
    OrderStatus,
    make_broker_execution_event,
    make_snapshot_event,
    utcnow,
)
from ez.live.ledger import LiveLedger


def test_live_ledger_uses_snapshot_event_as_checkpoint():
    ledger = LiveLedger()
    events = [
        make_snapshot_event(
            deployment_id="dep-1",
            business_date=date(2026, 4, 10),
            equity=100_000.0,
            cash=40_000.0,
            rebalanced=True,
            trade_count=1,
            holdings={"AAA": 600},
            weights={"AAA": 0.6},
            prev_returns={"AAA": 0.01},
        ),
        DeploymentEvent(
            event_id="dep-1:2026-04-11:AAA:sell:order_submitted",
            deployment_id="dep-1",
            event_type=EventType.ORDER_SUBMITTED,
            event_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:sell",
            payload={"symbol": "AAA", "side": "sell", "shares": 100, "business_date": "2026-04-11"},
        ),
        DeploymentEvent(
            event_id="dep-1:2026-04-11:AAA:sell:order_filled",
            deployment_id="dep-1",
            event_type=EventType.ORDER_FILLED,
            event_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:sell",
            payload={
                "symbol": "AAA",
                "side": "sell",
                "shares": 100,
                "price": 100.0,
                "amount": 10_000.0,
                "cost": 10.0,
                "business_date": "2026-04-11",
            },
        ),
    ]

    replayed = ledger.replay(events, initial_cash=1_000_000.0)

    assert replayed.cash == 49_990.0
    assert replayed.holdings == {"AAA": 500}
    assert replayed.order_statuses["dep-1:2026-04-11:AAA:sell"] == "filled"
    assert replayed.latest_snapshot_date == date(2026, 4, 10)
    assert replayed.latest_weights == {"AAA": 0.6}
    assert replayed.latest_prev_returns == {"AAA": 0.01}
    assert replayed.last_prices["AAA"] == 100.0
    assert replayed.trades[-1]["side"] == "sell"


def test_live_ledger_tracks_partially_filled_orders():
    ledger = LiveLedger()
    events = [
        DeploymentEvent(
            event_id="dep-1:2026-04-11:AAA:buy:order_submitted",
            deployment_id="dep-1",
            event_type=EventType.ORDER_SUBMITTED,
            event_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            payload={"symbol": "AAA", "side": "buy", "shares": 5000, "business_date": "2026-04-11"},
        ),
        DeploymentEvent(
            event_id="dep-1:2026-04-11:AAA:buy:order_partially_filled",
            deployment_id="dep-1",
            event_type=EventType.ORDER_PARTIALLY_FILLED,
            event_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            payload={
                "symbol": "AAA",
                "side": "buy",
                "shares": 4900,
                "price": 10.0,
                "amount": 49_000.0,
                "cost": 149.0,
                "requested_shares": 5000,
                "remaining_shares": 100,
                "business_date": "2026-04-11",
            },
        ),
    ]

    replayed = ledger.replay(events, initial_cash=100_000.0)

    assert replayed.cash == 50_851.0
    assert replayed.holdings == {"AAA": 4_900}
    assert replayed.order_statuses["dep-1:2026-04-11:AAA:buy"] == "partially_filled"
    assert replayed.trades[-1]["shares"] == 4_900


def test_live_ledger_tracks_submission_only_broker_reports_as_open_orders():
    ledger = LiveLedger()
    events = [
        DeploymentEvent(
            event_id="dep-1:2026-04-11:AAA:buy:order_submitted",
            deployment_id="dep-1",
            event_type=EventType.ORDER_SUBMITTED,
            event_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            payload={"symbol": "AAA", "side": "buy", "shares": 1000, "business_date": "2026-04-11"},
        ),
        make_broker_execution_event(
            "dep-1",
            report_id="SYS-001",
            broker_type="qmt",
            report_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            broker_order_id="SYS-001",
            symbol="AAA",
            side="buy",
            status="submitted",
            filled_shares=0,
            remaining_shares=1000,
            avg_price=0.0,
            raw_payload={"order_id": 1001, "order_sysid": "SYS-001"},
        ),
    ]

    replayed = ledger.replay(events, initial_cash=100_000.0)

    assert replayed.cash == 100_000.0
    assert replayed.holdings == {}
    assert replayed.order_statuses["dep-1:2026-04-11:AAA:buy"] == "submitted"
    assert replayed.broker_order_states["SYS-001"]["status"] == "reported"
    assert replayed.broker_order_states["SYS-001"]["remaining_shares"] == 1000


def test_live_ledger_applies_callback_status_transitions_to_final_fill():
    ledger = LiveLedger()
    events = [
        DeploymentEvent(
            event_id="dep-1:2026-04-11:AAA:buy:order_submitted",
            deployment_id="dep-1",
            event_type=EventType.ORDER_SUBMITTED,
            event_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            payload={"symbol": "AAA", "side": "buy", "shares": 1000, "business_date": "2026-04-11"},
        ),
        DeploymentEvent(
            event_id="dep-1:2026-04-11:AAA:buy:order_partially_filled",
            deployment_id="dep-1",
            event_type=EventType.ORDER_PARTIALLY_FILLED,
            event_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            payload={
                "symbol": "AAA",
                "side": "buy",
                "shares": 400,
                "price": 10.0,
                "amount": 4_000.0,
                "cost": 1.0,
                "requested_shares": 1000,
                "remaining_shares": 600,
                "business_date": "2026-04-11",
            },
        ),
        DeploymentEvent(
            event_id="dep-1:2026-04-11:AAA:buy:order_filled",
            deployment_id="dep-1",
            event_type=EventType.ORDER_FILLED,
            event_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            payload={
                "symbol": "AAA",
                "side": "buy",
                "shares": 600,
                "price": 10.0,
                "amount": 6_000.0,
                "cost": 1.0,
                "requested_shares": 1000,
                "remaining_shares": 0,
                "business_date": "2026-04-11",
            },
        ),
    ]

    replayed = ledger.replay(events, initial_cash=100_000.0)

    assert replayed.order_statuses["dep-1:2026-04-11:AAA:buy"] == "filled"
    assert replayed.holdings == {"AAA": 1000}
    assert replayed.cash == 89_998.0
    assert [trade["shares"] for trade in replayed.trades] == [400, 600]


def _buy_event(
    event_id: str,
    *,
    event_type: EventType,
    shares: int,
    remaining_shares: int,
    price: float = 10.0,
    cost: float = 1.0,
    business_date: str = "2026-04-11",
) -> DeploymentEvent:
    return DeploymentEvent(
        event_id=event_id,
        deployment_id="dep-1",
        event_type=event_type,
        event_ts=utcnow(),
        client_order_id="dep-1:2026-04-11:AAA:buy",
        payload={
            "symbol": "AAA",
            "side": "buy",
            "shares": shares,
            "price": price,
            "amount": shares * price,
            "cost": cost,
            "requested_shares": shares + remaining_shares,
            "remaining_shares": remaining_shares,
            "business_date": business_date,
        },
    )


def test_live_ledger_replay_dedupes_by_event_id():
    """Fix 1: replaying the same event log N times must equal replaying once."""
    ledger = LiveLedger()
    events = [
        _buy_event(
            "dep-1:2026-04-11:AAA:buy:order_submitted",
            event_type=EventType.ORDER_SUBMITTED,
            shares=0,
            remaining_shares=1000,
        ),
        _buy_event(
            "dep-1:2026-04-11:AAA:buy:order_filled",
            event_type=EventType.ORDER_FILLED,
            shares=1000,
            remaining_shares=0,
        ),
    ]

    baseline = ledger.replay(events, initial_cash=100_000.0)
    assert baseline.holdings == {"AAA": 1000}
    assert baseline.cash == 100_000.0 - 10_000.0 - 1.0
    assert baseline.seen_event_count == 2

    # Replay the same event log 10x via duplication — state must not drift.
    duplicated = events * 10
    replayed = ledger.replay(duplicated, initial_cash=100_000.0)
    assert replayed.holdings == baseline.holdings
    assert replayed.cash == baseline.cash
    assert replayed.trades == baseline.trades
    assert replayed.seen_event_count == baseline.seen_event_count


def test_live_ledger_replay_dedupes_broker_execution_records():
    """Fix 1: duplicate BROKER_EXECUTION_RECORDED events must not double-apply."""
    ledger = LiveLedger()
    submitted = _buy_event(
        "dep-1:2026-04-11:AAA:buy:order_submitted",
        event_type=EventType.ORDER_SUBMITTED,
        shares=0,
        remaining_shares=1000,
    )
    broker_report = make_broker_execution_event(
        "dep-1",
        report_id="SYS-001",
        broker_type="qmt",
        report_ts=utcnow(),
        client_order_id="dep-1:2026-04-11:AAA:buy",
        broker_order_id="SYS-001",
        symbol="AAA",
        side="buy",
        status="reported",
        filled_shares=0,
        remaining_shares=1000,
        avg_price=0.0,
    )

    once = ledger.replay([submitted, broker_report], initial_cash=100_000.0)
    twice = ledger.replay(
        [submitted, broker_report, broker_report, submitted],
        initial_cash=100_000.0,
    )
    assert once.cash == twice.cash
    assert once.holdings == twice.holdings
    assert once.broker_order_states == twice.broker_order_states
    assert twice.seen_event_count == 2


def test_live_ledger_replay_dedupes_cancel_requested():
    """Fix 1: duplicate BROKER_CANCEL_REQUESTED events stay idempotent."""
    ledger = LiveLedger()
    cancel_event_a = DeploymentEvent(
        event_id="dep-1:broker_cancel:qmt:SYS-001:2026-04-11T00:00:00+00:00",
        deployment_id="dep-1",
        event_type=EventType.BROKER_CANCEL_REQUESTED,
        event_ts=datetime(2026, 4, 11, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-11:AAA:buy",
        payload={
            "broker_type": "qmt",
            "broker_order_id": "SYS-001",
            "symbol": "AAA",
            "account_id": "acct-1",
        },
    )
    once = ledger.replay([cancel_event_a], initial_cash=50_000.0)
    multi = ledger.replay([cancel_event_a] * 5, initial_cash=50_000.0)
    assert once.broker_order_states == multi.broker_order_states
    assert once.order_statuses == multi.order_statuses
    assert multi.seen_event_count == 1


def test_live_ledger_partial_filled_to_canceled_is_allowed():
    """Fix 2: PARTIALLY_FILLED -> CANCELED must be a legal transition."""
    ledger = LiveLedger()
    events = [
        _buy_event(
            "dep-1:2026-04-11:AAA:buy:order_submitted",
            event_type=EventType.ORDER_SUBMITTED,
            shares=0,
            remaining_shares=1000,
        ),
        _buy_event(
            "dep-1:2026-04-11:AAA:buy:order_partially_filled",
            event_type=EventType.ORDER_PARTIALLY_FILLED,
            shares=400,
            remaining_shares=600,
        ),
        # Broker reports ORDER_PART_CANCEL (xtquant 53) i.e. partially_canceled,
        # which maps onto OrderStatus.CANCELED.
        make_broker_execution_event(
            "dep-1",
            report_id="SYS-001-part-cancel",
            broker_type="qmt",
            report_ts=utcnow(),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            broker_order_id="SYS-001",
            symbol="AAA",
            side="buy",
            status="partially_canceled",
            filled_shares=400,
            remaining_shares=600,
            avg_price=10.0,
        ),
    ]
    replayed = ledger.replay(events, initial_cash=100_000.0)
    assert replayed.order_statuses["dep-1:2026-04-11:AAA:buy"] == OrderStatus.CANCELED.value
    assert replayed.broker_order_states["SYS-001"]["status"] == "partially_canceled"


def test_live_ledger_filled_cannot_transition_to_canceled():
    """Fix 2: FILLED is absorbing; a later CANCELED must not overwrite it."""
    ledger = LiveLedger()
    events = [
        _buy_event(
            "dep-1:2026-04-11:AAA:buy:order_submitted",
            event_type=EventType.ORDER_SUBMITTED,
            shares=0,
            remaining_shares=1000,
        ),
        _buy_event(
            "dep-1:2026-04-11:AAA:buy:order_filled",
            event_type=EventType.ORDER_FILLED,
            shares=1000,
            remaining_shares=0,
        ),
        # Stale canceled arriving after a terminal filled — must be dropped.
        make_broker_execution_event(
            "dep-1",
            report_id="SYS-001-late-cancel",
            broker_type="qmt",
            report_ts=utcnow() + timedelta(seconds=5),
            client_order_id="dep-1:2026-04-11:AAA:buy",
            broker_order_id="SYS-001",
            symbol="AAA",
            side="buy",
            status="canceled",
            filled_shares=1000,
            remaining_shares=0,
            avg_price=10.0,
        ),
    ]
    replayed = ledger.replay(events, initial_cash=100_000.0)
    assert replayed.order_statuses["dep-1:2026-04-11:AAA:buy"] == OrderStatus.FILLED.value


def test_live_ledger_rejected_cannot_transition_to_canceled():
    """Fix 2: REJECTED and CANCELED are mutually exclusive terminal states."""
    ledger = LiveLedger()
    rejected_event = DeploymentEvent(
        event_id="dep-1:2026-04-11:AAA:buy:order_rejected",
        deployment_id="dep-1",
        event_type=EventType.ORDER_REJECTED,
        event_ts=utcnow(),
        client_order_id="dep-1:2026-04-11:AAA:buy",
        payload={"symbol": "AAA", "side": "buy", "shares": 1000},
    )
    # Simulate a stale broker "canceled" report arriving after the reject.
    stale_cancel = make_broker_execution_event(
        "dep-1",
        report_id="SYS-001-stale-cancel",
        broker_type="qmt",
        report_ts=utcnow() + timedelta(seconds=1),
        client_order_id="dep-1:2026-04-11:AAA:buy",
        broker_order_id="SYS-001",
        symbol="AAA",
        side="buy",
        status="canceled",
        filled_shares=0,
        remaining_shares=0,
        avg_price=0.0,
    )
    replayed = ledger.replay([rejected_event, stale_cancel], initial_cash=100_000.0)
    assert replayed.order_statuses["dep-1:2026-04-11:AAA:buy"] == OrderStatus.REJECTED.value


def test_live_ledger_partial_canceled_may_upgrade_to_canceled():
    """Fix 2: ORDER_PART_CANCEL -> full cancel confirm is the one cross-terminal allow."""
    ledger = LiveLedger()
    submitted = _buy_event(
        "dep-1:2026-04-11:AAA:buy:order_submitted",
        event_type=EventType.ORDER_SUBMITTED,
        shares=0,
        remaining_shares=1000,
    )
    partial_cancel = make_broker_execution_event(
        "dep-1",
        report_id="SYS-001-partial-cancel",
        broker_type="qmt",
        report_ts=utcnow(),
        client_order_id="dep-1:2026-04-11:AAA:buy",
        broker_order_id="SYS-001",
        symbol="AAA",
        side="buy",
        status="partially_canceled",
        filled_shares=400,
        remaining_shares=600,
        avg_price=10.0,
    )
    full_cancel = make_broker_execution_event(
        "dep-1",
        report_id="SYS-001-full-cancel",
        broker_type="qmt",
        report_ts=utcnow() + timedelta(seconds=1),
        client_order_id="dep-1:2026-04-11:AAA:buy",
        broker_order_id="SYS-001",
        symbol="AAA",
        side="buy",
        status="canceled",
        filled_shares=400,
        remaining_shares=600,
        avg_price=10.0,
    )
    replayed = ledger.replay([submitted, partial_cancel, full_cancel], initial_cash=100_000.0)
    assert replayed.broker_order_states["SYS-001"]["status"] == "canceled"


def test_live_ledger_submission_only_broker_report_does_not_move_cash_or_holdings():
    """Fix 3: submission-only reports never mutate cash/holdings, even replayed."""
    ledger = LiveLedger()
    events = [
        _buy_event(
            "dep-1:2026-04-11:AAA:buy:order_submitted",
            event_type=EventType.ORDER_SUBMITTED,
            shares=0,
            remaining_shares=1000,
        ),
    ]
    for i in range(5):
        events.append(
            make_broker_execution_event(
                "dep-1",
                report_id=f"ack-{i}",
                broker_type="qmt",
                report_ts=utcnow() + timedelta(milliseconds=i),
                client_order_id="dep-1:2026-04-11:AAA:buy",
                broker_order_id="SYS-001",
                symbol="AAA",
                side="buy",
                status="reported",
                filled_shares=0,
                remaining_shares=1000,
                avg_price=0.0,
            )
        )
    replayed = ledger.replay(events, initial_cash=100_000.0)
    assert replayed.cash == 100_000.0
    assert replayed.holdings == {}
    assert replayed.broker_order_states["SYS-001"]["status"] == "reported"
    assert replayed.broker_order_states["SYS-001"]["filled_shares"] == 0
    assert replayed.broker_order_states["SYS-001"]["remaining_shares"] == 1000


def test_live_ledger_submission_only_clamps_bogus_filled_shares():
    """Fix 3 double-guard: status=reported with filled_shares>0 is clamped to 0."""
    ledger = LiveLedger()
    submitted = _buy_event(
        "dep-1:2026-04-11:AAA:buy:order_submitted",
        event_type=EventType.ORDER_SUBMITTED,
        shares=0,
        remaining_shares=1000,
    )
    # Deliberately malformed: submission-only status paired with filled>0.
    bogus = make_broker_execution_event(
        "dep-1",
        report_id="BOGUS-001",
        broker_type="qmt",
        report_ts=utcnow(),
        client_order_id="dep-1:2026-04-11:AAA:buy",
        broker_order_id="SYS-001",
        symbol="AAA",
        side="buy",
        status="reported",
        filled_shares=500,  # inconsistent with status
        remaining_shares=500,
        avg_price=10.0,
    )
    replayed = ledger.replay([submitted, bogus], initial_cash=100_000.0)
    # Cash/holdings never moved — BROKER_EXECUTION_RECORDED does not touch them.
    assert replayed.cash == 100_000.0
    assert replayed.holdings == {}
    # Projection still clamps submission-only filled_shares to 0 so downstream
    # reconcile math can't be fooled by a bad report.
    assert replayed.broker_order_states["SYS-001"]["filled_shares"] == 0
