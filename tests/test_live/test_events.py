from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ez.live.events import (
    DeploymentEvent,
    EventType,
    broker_order_status_can_transition,
    broker_order_status_is_terminal,
    broker_order_status_rank,
    make_broker_execution_event,
    make_client_order_id,
    make_event_id,
    make_market_bar_event,
    make_market_snapshot_event,
    make_risk_event,
    make_shadow_broker_client_order_id,
    make_snapshot_event,
    make_tick_completed_event,
)


def test_make_client_order_id_is_deterministic():
    dep_id = "dep-1"
    biz_date = date(2026, 4, 13)
    cid1 = make_client_order_id(dep_id, biz_date, "510300.SH", "buy")
    cid2 = make_client_order_id(dep_id, biz_date, "510300.SH", "buy")
    assert cid1 == cid2


def test_snapshot_event_round_trip():
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_010_000.0,
        cash=10_000.0,
        rebalanced=True,
        trade_count=2,
        holdings={"AAA": 100},
        weights={"AAA": 0.1},
        prev_returns={"AAA": 0.02},
    )
    assert event.event_type == EventType.SNAPSHOT_SAVED
    assert event.event_id == make_event_id(event.client_order_id, EventType.SNAPSHOT_SAVED)

    restored = DeploymentEvent.from_dict(event.to_dict())
    assert restored.event_id == event.event_id
    assert restored.payload["trade_count"] == 2
    assert restored.payload["holdings"] == {"AAA": 100}
    assert restored.payload["weights"] == {"AAA": 0.1}


def test_risk_and_tick_events_round_trip():
    risk_event = make_risk_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        risk_index=0,
        risk_event={"event": "runtime_allocator", "rule": "max_names"},
    )
    tick_event = make_tick_completed_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        execution_ms=12.5,
        rebalanced=True,
        trade_count=3,
        risk_event_count=1,
        equity=1_020_000.0,
        cash=420_000.0,
    )

    assert risk_event.event_type == EventType.RISK_RECORDED
    assert tick_event.event_type == EventType.TICK_COMPLETED
    assert DeploymentEvent.from_dict(risk_event.to_dict()).payload["risk_event"]["rule"] == "max_names"
    assert DeploymentEvent.from_dict(tick_event.to_dict()).payload["trade_count"] == 3


def test_market_snapshot_event_round_trip():
    event = make_market_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        prices={"AAA": 10.0, "BBB": 20.0},
        has_bar_symbols=["AAA"],
        source="live",
    )
    restored = DeploymentEvent.from_dict(event.to_dict())
    assert restored.event_type == EventType.MARKET_SNAPSHOT
    assert restored.payload["prices"]["AAA"] == 10.0
    assert restored.payload["has_bar_symbols"] == ["AAA"]


def test_market_bar_event_round_trip():
    event = make_market_bar_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        symbol="AAA",
        open_price=10.0,
        high_price=11.0,
        low_price=9.5,
        close_price=10.5,
        adj_close=10.4,
        volume=12345,
        source="live",
    )
    restored = DeploymentEvent.from_dict(event.to_dict())
    assert restored.event_type == EventType.MARKET_BAR_RECORDED
    assert restored.payload["symbol"] == "AAA"
    assert restored.payload["adj_close"] == 10.4


def test_broker_execution_event_round_trip():
    event = make_broker_execution_event(
        "dep-1",
        report_id="qmt:SYS-001:partially_filled:600:400:2026-04-13T09:32:00+00:00",
        broker_type="qmt",
        report_ts=datetime(2026, 4, 13, 9, 32, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:AAA:buy",
        broker_order_id="SYS-001",
        symbol="AAA",
        side="buy",
        status="partially_filled",
        filled_shares=600,
        remaining_shares=400,
        avg_price=12.34,
        message="partial",
        raw_payload={"entrust_no": "SYS-001"},
    )
    restored = DeploymentEvent.from_dict(event.to_dict())
    assert restored.event_type == EventType.BROKER_EXECUTION_RECORDED
    assert restored.payload["report_id"].startswith("qmt:SYS-001:")
    assert restored.payload["status"] == "partially_filled"


def test_shadow_broker_client_order_id_prefers_broker_order_id():
    assert make_shadow_broker_client_order_id(
        "dep-1",
        broker_type="qmt",
        broker_order_id="SYS-001",
    ) == "dep-1:broker_order:qmt:SYS-001"


def test_shadow_broker_client_order_id_never_emits_unknown_literal():
    """Fix 4: two report-less broker reports must not collide into the same id."""
    ts_a = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
    ts_b = datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc)
    id_a = make_shadow_broker_client_order_id(
        "dep-1",
        broker_type="qmt",
        event_ts=ts_a,
        symbol="AAA",
        side="buy",
    )
    id_b = make_shadow_broker_client_order_id(
        "dep-1",
        broker_type="qmt",
        event_ts=ts_b,
        symbol="AAA",
        side="buy",
    )
    assert "unknown" not in id_a
    assert "unknown" not in id_b
    assert id_a != id_b


def test_shadow_broker_client_order_id_is_deterministic_for_same_inputs():
    """Fix 4: hash fallback is stable — same inputs reproduce the same id."""
    ts = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
    id_1 = make_shadow_broker_client_order_id(
        "dep-1",
        broker_type="qmt",
        event_ts=ts,
        symbol="AAA",
        side="buy",
    )
    id_2 = make_shadow_broker_client_order_id(
        "dep-1",
        broker_type="qmt",
        event_ts=ts,
        symbol="AAA",
        side="buy",
    )
    assert id_1 == id_2


def test_shadow_broker_client_order_id_fallback_without_broker_type():
    """Fix 4: even with broker_type missing, the id stays deterministic and unique."""
    ts = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
    id_a = make_shadow_broker_client_order_id(
        "dep-1",
        broker_type="",
        event_ts=ts,
        symbol="AAA",
        side="buy",
    )
    id_b = make_shadow_broker_client_order_id(
        "dep-1",
        broker_type="",
        event_ts=ts,
        symbol="AAA",
        side="sell",
    )
    assert "unknown" not in id_a
    assert "unknown" not in id_b
    assert id_a != id_b


def test_shadow_broker_client_order_id_prefers_report_id_when_present():
    """Regression: existing report_id canonical path still wins over hash fallback."""
    ts = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
    value = make_shadow_broker_client_order_id(
        "dep-1",
        broker_type="qmt",
        report_id="R-42",
        event_ts=ts,
        symbol="AAA",
        side="buy",
    )
    assert value == "dep-1:broker_report:R-42"


@pytest.mark.parametrize(
    ("current", "incoming", "expected"),
    [
        # Non-terminal forward moves.
        ("unreported", "reported", True),
        ("reported", "partially_filled", True),
        ("partially_filled", "filled", True),
        ("partially_filled", "canceled", True),
        ("partially_filled", "partially_canceled", True),
        # Same-rank non-terminal is a no-op (strict > required).
        ("reported", "reported", False),
        # Terminal is absorbing.
        ("filled", "canceled", False),
        ("filled", "partially_canceled", False),
        ("canceled", "filled", False),
        ("canceled", "partially_canceled", False),
        ("order_error", "canceled", False),
        ("order_error", "filled", False),
        ("junk", "filled", False),
        # Terminal cannot regress.
        ("filled", "partially_filled", False),
        ("canceled", "reported", False),
        # Cross-terminal allow-list: partial cancel -> full cancel confirm.
        ("partially_canceled", "canceled", True),
        # Missing current -> any incoming is allowed.
        ("", "reported", True),
        ("", "filled", True),
    ],
)
def test_broker_order_status_transition_matrix(current, incoming, expected):
    """Fix 2: terminal-aware transition rules follow xtquant semantics."""
    assert broker_order_status_can_transition(current, incoming) is expected


def test_broker_order_status_rank_splits_terminal_and_non_terminal():
    """Fix 2: all terminal statuses share rank 30; non-terminal stay below."""
    for terminal in ("partially_canceled", "filled", "canceled", "junk", "order_error"):
        assert broker_order_status_rank(terminal) == 30
        assert broker_order_status_is_terminal(terminal)
    for non_terminal in (
        "unreported",
        "reported",
        "reported_cancel_pending",
        "partially_filled",
        "partially_filled_cancel_pending",
    ):
        assert broker_order_status_rank(non_terminal) < 30
        assert not broker_order_status_is_terminal(non_terminal)
