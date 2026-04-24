from __future__ import annotations

from datetime import date, datetime, timezone

from ez.live.broker import BrokerAccountSnapshot, BrokerExecutionReport
from ez.live.reconcile import (
    reconcile_broker_orders,
    reconcile_broker_positions,
    reconcile_broker_snapshot,
    reconcile_broker_trades,
)


def test_reconcile_broker_snapshot_ok_when_within_tolerance():
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        cash=100_000.5,
        total_asset=150_000.5,
        positions={"AAA": 5000},
        open_orders=[],
        fills=[],
    )
    result = reconcile_broker_snapshot(
        local_cash=100_000.0,
        local_holdings={"AAA": 5000},
        local_equity=150_000.0,
        prices={"AAA": 10.0},
        broker_snapshot=snapshot,
        cash_tolerance=1.0,
        notional_tolerance=100.0,
    )
    assert result.status == "ok"
    assert result.position_drifts == []


def test_reconcile_broker_snapshot_detects_position_and_cash_drift():
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        cash=95_000.0,
        total_asset=145_000.0,
        positions={"AAA": 4500, "BBB": 1000},
        open_orders=[],
        fills=[],
    )
    result = reconcile_broker_snapshot(
        local_cash=100_000.0,
        local_holdings={"AAA": 5000},
        local_equity=150_000.0,
        prices={"AAA": 10.0, "BBB": 20.0},
        broker_snapshot=snapshot,
        cash_tolerance=1.0,
        notional_tolerance=100.0,
    )
    assert result.status == "drift"
    assert result.cash_delta == -5000.0
    assert len(result.position_drifts) == 2
    assert {d.symbol for d in result.position_drifts} == {"AAA", "BBB"}


def test_reconcile_broker_orders_ok_when_local_links_match_broker_open_orders():
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        cash=100_000.0,
        total_asset=150_000.0,
        positions={},
        open_orders=[
            {
                "client_order_id": "dep-1:2026-04-13:AAA:buy",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "status": "partially_filled",
            }
        ],
        fills=[],
    )
    result = reconcile_broker_orders(
        broker_snapshot=snapshot,
        local_order_links=[
            {
                "client_order_id": "dep-1:2026-04-13:AAA:buy",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "latest_status": "partially_filled",
            }
        ],
    )
    assert result.status == "ok"
    assert result.local_open_order_count == 1
    assert result.broker_open_order_count == 1
    assert result.missing_local_orders == []
    assert result.missing_broker_orders == []
    assert result.status_drifts == []


def test_reconcile_broker_orders_treats_cancel_pending_as_inflight_not_drift():
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        cash=100_000.0,
        total_asset=150_000.0,
        positions={},
        open_orders=[
            {
                "client_order_id": "dep-1:2026-04-13:AAA:buy",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "status": "partially_filled",
            }
        ],
        fills=[],
    )
    result = reconcile_broker_orders(
        broker_snapshot=snapshot,
        local_order_links=[
            {
                "client_order_id": "dep-1:2026-04-13:AAA:buy",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "latest_status": "partially_filled_cancel_pending",
            }
        ],
    )
    assert result.status == "ok"
    assert result.status_drifts == []


def test_reconcile_broker_orders_detects_missing_link_and_status_drift():
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        cash=100_000.0,
        total_asset=150_000.0,
        positions={},
        open_orders=[
            {
                "client_order_id": "",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "status": "reported",
            },
            {
                "client_order_id": "",
                "broker_order_id": "SYS-404",
                "symbol": "BBB",
                "status": "reported",
            },
        ],
        fills=[],
    )
    result = reconcile_broker_orders(
        broker_snapshot=snapshot,
        local_order_links=[
            {
                "client_order_id": "dep-1:2026-04-13:AAA:buy",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "latest_status": "partially_filled",
            },
            {
                "client_order_id": "dep-1:2026-04-13:CCC:buy",
                "broker_order_id": "SYS-003",
                "symbol": "CCC",
                "latest_status": "reported",
            },
        ],
    )
    assert result.status == "drift"
    assert [drift.order_key for drift in result.missing_local_orders] == ["SYS-404"]
    assert [drift.order_key for drift in result.missing_broker_orders] == ["SYS-003"]
    assert [drift.order_key for drift in result.status_drifts] == ["SYS-001"]


def test_reconcile_broker_orders_uses_terminal_callback_report_to_suppress_stale_open_order():
    as_of = datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc)
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=as_of,
        cash=100_000.0,
        total_asset=150_000.0,
        positions={},
        open_orders=[
            {
                "client_order_id": "dep-1:2026-04-13:AAA:buy",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "status": "reported",
            }
        ],
        fills=[],
    )
    result = reconcile_broker_orders(
        broker_snapshot=snapshot,
        local_order_links=[],
        broker_reports=[
            BrokerExecutionReport(
                report_id="R-001",
                broker_type="qmt",
                as_of=as_of,
                client_order_id="dep-1:2026-04-13:AAA:buy",
                broker_order_id="SYS-001",
                symbol="AAA",
                side="buy",
                status="canceled",
                filled_shares=0,
                remaining_shares=0,
                avg_price=0.0,
            )
        ],
    )
    assert result.status == "ok"
    assert result.broker_open_order_count == 0
    assert result.missing_local_orders == []
    assert result.status_drifts == []


def test_reconcile_broker_orders_uses_terminal_filled_report_to_suppress_stale_open_order():
    as_of = datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc)
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=as_of,
        cash=100_000.0,
        total_asset=150_000.0,
        positions={},
        open_orders=[
            {
                "client_order_id": "",
                "broker_order_id": "SYS-002",
                "symbol": "AAA",
                "status": "reported",
            }
        ],
        fills=[],
    )
    result = reconcile_broker_orders(
        broker_snapshot=snapshot,
        local_order_links=[],
        broker_reports=[
            BrokerExecutionReport(
                report_id="R-002",
                broker_type="qmt",
                as_of=as_of,
                client_order_id="dep-1:2026-04-13:AAA:buy",
                broker_order_id="SYS-002",
                symbol="AAA",
                side="buy",
                status="filled",
                filled_shares=100,
                remaining_shares=0,
                avg_price=10.0,
            )
        ],
    )
    assert result.status == "ok"
    assert result.broker_open_order_count == 0
    assert result.missing_local_orders == []
    assert result.status_drifts == []


def test_reconcile_broker_orders_prefers_latest_callback_status_for_open_order():
    as_of = datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc)
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=as_of,
        cash=100_000.0,
        total_asset=150_000.0,
        positions={},
        open_orders=[
            {
                "client_order_id": "",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "status": "reported",
            }
        ],
        fills=[],
    )
    result = reconcile_broker_orders(
        broker_snapshot=snapshot,
        local_order_links=[
            {
                "client_order_id": "dep-1:2026-04-13:AAA:buy",
                "broker_order_id": "SYS-001",
                "symbol": "AAA",
                "latest_status": "partially_filled",
            }
        ],
        broker_reports=[
            BrokerExecutionReport(
                report_id="R-001",
                broker_type="qmt",
                as_of=as_of,
                client_order_id="dep-1:2026-04-13:AAA:buy",
                broker_order_id="SYS-001",
                symbol="AAA",
                side="buy",
                status="partially_filled",
                filled_shares=100,
                remaining_shares=200,
                avg_price=10.0,
            )
        ],
    )
    assert result.status == "ok"
    assert result.broker_open_order_count == 1
    assert result.missing_local_orders == []
    assert result.missing_broker_orders == []
    assert result.status_drifts == []


def test_reconcile_broker_orders_prefers_later_same_timestamp_report_order():
    as_of = datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc)
    snapshot = BrokerAccountSnapshot(
        broker_type="qmt",
        as_of=as_of,
        cash=100_000.0,
        total_asset=150_000.0,
        positions={},
        open_orders=[
            {
                "client_order_id": "",
                "broker_order_id": "SYS-003",
                "symbol": "AAA",
                "status": "reported",
            }
        ],
        fills=[],
    )
    result = reconcile_broker_orders(
        broker_snapshot=snapshot,
        local_order_links=[
            {
                "client_order_id": "dep-1:2026-04-13:AAA:buy",
                "broker_order_id": "",
                "symbol": "AAA",
                "latest_status": "reported",
            }
        ],
        broker_reports=[
            BrokerExecutionReport(
                report_id="z-report",
                broker_type="qmt",
                as_of=as_of,
                client_order_id="dep-1:2026-04-13:AAA:buy",
                broker_order_id="",
                symbol="AAA",
                side="buy",
                status="partially_filled",
                filled_shares=50,
                remaining_shares=50,
                avg_price=10.0,
            ),
            BrokerExecutionReport(
                report_id="a-report",
                broker_type="qmt",
                as_of=as_of,
                client_order_id="dep-1:2026-04-13:AAA:buy",
                broker_order_id="SYS-003",
                symbol="AAA",
                side="buy",
                status="partially_filled",
                filled_shares=50,
                remaining_shares=50,
                avg_price=10.0,
            ),
        ],
    )

    assert result.status == "ok"
    assert result.missing_local_orders == []
    assert result.missing_broker_orders == []
    assert result.status_drifts == []


# ---------------------------------------------------------------------------
# V3.3.44 — reconcile_broker_positions tests
# ---------------------------------------------------------------------------


def test_reconcile_broker_positions_ok_when_holdings_match():
    result = reconcile_broker_positions(
        local_holdings={"AAA": 500, "BBB": 200},
        broker_positions=[
            {"symbol": "AAA", "volume": 500, "can_use_volume": 500},
            {"symbol": "BBB", "volume": 200, "can_use_volume": 200},
        ],
    )
    assert result.status == "ok"
    assert result.position_drifts == []
    assert result.has_drift is False


def test_reconcile_broker_positions_detects_missing_broker_position():
    result = reconcile_broker_positions(
        local_holdings={"AAA": 500, "BBB": 300},
        broker_positions=[
            {"symbol": "AAA", "volume": 500, "can_use_volume": 500},
        ],
    )
    assert result.status == "drift"
    drift = result.position_drifts[0]
    assert drift.symbol == "BBB"
    assert drift.local_shares == 300
    assert drift.broker_shares == 0
    assert drift.share_delta == -300


def test_reconcile_broker_positions_detects_extra_broker_position():
    """Broker reports a holding local doesn't know about."""
    result = reconcile_broker_positions(
        local_holdings={"AAA": 500},
        broker_positions=[
            {"symbol": "AAA", "volume": 500, "can_use_volume": 500},
            {"symbol": "CCC", "volume": 100, "can_use_volume": 100},
        ],
    )
    assert result.status == "drift"
    drift = result.position_drifts[0]
    assert drift.symbol == "CCC"
    assert drift.local_shares == 0
    assert drift.broker_shares == 100
    assert drift.share_delta == 100


def test_reconcile_broker_positions_detects_share_mismatch():
    result = reconcile_broker_positions(
        local_holdings={"AAA": 500},
        broker_positions=[
            {"symbol": "AAA", "volume": 300, "can_use_volume": 300},
        ],
    )
    assert result.status == "drift"
    drift = result.position_drifts[0]
    assert drift.share_delta == -200


def test_reconcile_broker_positions_tplus1_freeze_reflected_in_broker_positions():
    """T+1 freeze keeps volume equal but makes can_use smaller; status stays ok
    because the reconcile only fails on share deltas. Available volume is tracked
    but does not fail closed on its own — that would reject legitimate T+1 holds.
    """
    result = reconcile_broker_positions(
        local_holdings={"AAA": 1000},
        broker_positions=[
            {
                "symbol": "AAA",
                "volume": 1000,
                "can_use_volume": 600,
                "frozen_volume": 400,
            },
        ],
    )
    assert result.status == "ok"
    assert result.position_drifts == []


def test_reconcile_broker_positions_honors_share_tolerance():
    result = reconcile_broker_positions(
        local_holdings={"AAA": 100},
        broker_positions=[{"symbol": "AAA", "volume": 101}],
        share_tolerance=1,
    )
    assert result.status == "ok"


def test_reconcile_broker_positions_handles_stock_code_alias():
    result = reconcile_broker_positions(
        local_holdings={"000001.SZ": 500},
        broker_positions=[
            {"stock_code": "000001.SZ", "volume": 500, "can_use_volume": 500},
        ],
    )
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# V3.3.44 — reconcile_broker_trades tests
# ---------------------------------------------------------------------------


def test_reconcile_broker_trades_ok_when_all_fills_match():
    business_date = date(2026, 4, 13)
    result = reconcile_broker_trades(
        local_trades=[
            {"symbol": "AAA", "side": "buy", "shares": 100, "price": 10.0},
            {"symbol": "AAA", "side": "buy", "shares": 50, "price": 10.0},
        ],
        broker_trades=[
            {
                "traded_id": "T-1",
                "symbol": "AAA",
                "side": "buy",
                "shares": 150,
                "price": 10.0,
            },
        ],
        business_date=business_date,
    )
    assert result.status == "ok"
    assert result.trade_drifts == []
    assert result.broker_trade_count == 1
    assert result.local_trade_count == 2


def test_reconcile_broker_trades_missing_local_when_broker_has_extra_fill():
    business_date = date(2026, 4, 13)
    result = reconcile_broker_trades(
        local_trades=[],
        broker_trades=[
            {
                "traded_id": "T-9",
                "symbol": "AAA",
                "side": "buy",
                "shares": 100,
                "price": 11.0,
            },
        ],
        business_date=business_date,
    )
    assert result.status == "drift"
    drift = result.trade_drifts[0]
    assert drift.reason == "missing_local"
    assert drift.broker_volume == 100
    assert drift.local_volume == 0
    assert drift.volume_delta == 100
    assert drift.broker_trade_id == "T-9"


def test_reconcile_broker_trades_missing_broker_when_local_has_extra_fill():
    business_date = date(2026, 4, 13)
    result = reconcile_broker_trades(
        local_trades=[
            {"symbol": "AAA", "side": "sell", "shares": 100, "price": 10.0},
        ],
        broker_trades=[],
        business_date=business_date,
    )
    assert result.status == "drift"
    drift = result.trade_drifts[0]
    assert drift.reason == "missing_broker"
    assert drift.symbol == "AAA"
    assert drift.side == "sell"
    assert drift.broker_volume == 0
    assert drift.local_volume == 100
    assert drift.volume_delta == -100


def test_reconcile_broker_trades_detects_volume_mismatch():
    business_date = date(2026, 4, 13)
    result = reconcile_broker_trades(
        local_trades=[
            {"symbol": "AAA", "side": "buy", "shares": 200, "price": 10.0},
        ],
        broker_trades=[
            {
                "traded_id": "T-3",
                "symbol": "AAA",
                "side": "buy",
                "shares": 150,
                "price": 10.0,
            },
        ],
        business_date=business_date,
    )
    assert result.status == "drift"
    drift = result.trade_drifts[0]
    assert drift.reason == "volume_mismatch"
    assert drift.broker_volume == 150
    assert drift.local_volume == 200
    assert drift.volume_delta == -50


def test_reconcile_broker_trades_detects_price_mismatch():
    business_date = date(2026, 4, 13)
    result = reconcile_broker_trades(
        local_trades=[
            {"symbol": "AAA", "side": "buy", "shares": 100, "price": 10.00},
        ],
        broker_trades=[
            {
                "traded_id": "T-4",
                "symbol": "AAA",
                "side": "buy",
                "shares": 100,
                "price": 10.10,
            },
        ],
        business_date=business_date,
        price_tolerance=0.01,
    )
    assert result.status == "drift"
    drift = result.trade_drifts[0]
    assert drift.reason == "price_mismatch"
    assert drift.volume_delta == 0
    assert drift.broker_volume == 100
    assert drift.local_volume == 100


def test_reconcile_broker_trades_price_within_tolerance_is_ok():
    business_date = date(2026, 4, 13)
    result = reconcile_broker_trades(
        local_trades=[
            {"symbol": "AAA", "side": "buy", "shares": 100, "price": 10.00},
        ],
        broker_trades=[
            {
                "traded_id": "T-5",
                "symbol": "AAA",
                "side": "buy",
                "shares": 100,
                "price": 10.005,
            },
        ],
        business_date=business_date,
        price_tolerance=0.01,
    )
    assert result.status == "ok"


def test_reconcile_broker_trades_separate_buy_sell_buckets():
    """Same symbol but different sides must not net out."""
    business_date = date(2026, 4, 13)
    result = reconcile_broker_trades(
        local_trades=[
            {"symbol": "AAA", "side": "buy", "shares": 100, "price": 10.0},
        ],
        broker_trades=[
            {
                "traded_id": "T-sell",
                "symbol": "AAA",
                "side": "sell",
                "shares": 100,
                "price": 10.0,
            },
        ],
        business_date=business_date,
    )
    assert result.status == "drift"
    # One missing_broker (local buy) + one missing_local (broker sell)
    reasons = sorted(drift.reason for drift in result.trade_drifts)
    assert reasons == ["missing_broker", "missing_local"]


def test_reconcile_broker_trades_empty_both_sides_is_ok():
    result = reconcile_broker_trades(
        local_trades=[],
        broker_trades=[],
        business_date=date(2026, 4, 13),
    )
    assert result.status == "ok"
    assert result.broker_trade_count == 0
    assert result.local_trade_count == 0
