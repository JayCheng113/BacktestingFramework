from __future__ import annotations

from datetime import date

from ez.live.broker import BrokerAdapter, BrokerCapability
from ez.live.events import Order
from ez.live.paper_broker import PaperBroker
from ez.portfolio.execution import CostModel


def _cost_model() -> CostModel:
    return CostModel(
        buy_commission_rate=0.0,
        sell_commission_rate=0.0,
        min_commission=0.0,
        stamp_tax_rate=0.0,
        slippage_rate=0.0,
    )


def test_execute_target_weights_returns_fill_reports_for_buys():
    broker = PaperBroker()
    assert isinstance(broker, BrokerAdapter)
    assert broker.capabilities == frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
    requested_order = Order(
        order_id="dep-1:2026-04-21:AAA:buy",
        client_order_id="dep-1:2026-04-21:AAA:buy",
        deployment_id="dep-1",
        symbol="AAA",
        side="buy",
        shares=50_000,
        business_date=date(2026, 4, 21),
    )
    result = broker.execute_target_weights(
        business_date=date(2026, 4, 21),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=_cost_model(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        requested_orders=[requested_order],
    )

    assert len(result.fills) == 1
    assert len(result.order_reports) == 1
    fill = result.fills[0]
    assert fill.symbol == "AAA"
    assert fill.side == "buy"
    assert fill.shares == 50_000
    assert fill.client_order_id == requested_order.client_order_id
    assert result.order_reports[0].status == "filled"
    assert result.holdings == {"AAA": 50_000}
    assert result.cash == 500_000.0
    assert result.trade_volume == 500_000.0


def test_execute_target_weights_handles_liquidation_target():
    broker = PaperBroker()
    result = broker.execute_target_weights(
        business_date=date(2026, 4, 21),
        target_weights={},
        holdings={"AAA": 10_000},
        equity=100_000.0,
        cash=0.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=_cost_model(),
        lot_size=100,
        limit_pct=0.0,
        t_plus_1=False,
    )

    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.side == "sell"
    assert fill.shares == 10_000
    assert result.holdings == {}
    assert result.cash == 100_000.0


def test_execute_target_weights_reports_partial_fill_when_cash_is_insufficient():
    broker = PaperBroker()
    requested_orders = [
        Order(
            order_id="dep-1:2026-04-21:AAA:buy",
            client_order_id="dep-1:2026-04-21:AAA:buy",
            deployment_id="dep-1",
            symbol="AAA",
            side="buy",
            shares=5_000,
            business_date=date(2026, 4, 21),
        ),
        Order(
            order_id="dep-1:2026-04-21:BBB:buy",
            client_order_id="dep-1:2026-04-21:BBB:buy",
            deployment_id="dep-1",
            symbol="BBB",
            side="buy",
            shares=5_000,
            business_date=date(2026, 4, 21),
        ),
    ]
    result = broker.execute_target_weights(
        business_date=date(2026, 4, 21),
        target_weights={"AAA": 0.5, "BBB": 0.5},
        holdings={},
        equity=100_000.0,
        cash=100_000.0,
        prices={"AAA": 10.0, "BBB": 10.0},
        raw_close_today={"AAA": 10.0, "BBB": 10.0},
        prev_raw_close={"AAA": 10.0, "BBB": 10.0},
        has_bar_today={"AAA", "BBB"},
        cost_model=CostModel(
            buy_commission_rate=0.001,
            sell_commission_rate=0.0,
            min_commission=100.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        requested_orders=requested_orders,
    )

    reports = {report.symbol: report for report in result.order_reports}
    assert reports["AAA"].status == "filled"
    assert reports["AAA"].filled_shares == 5_000
    assert reports["BBB"].status == "partially_filled"
    assert reports["BBB"].filled_shares == 4_900
    assert reports["BBB"].remaining_shares == 100


def test_execute_target_weights_splits_child_fills_when_requested():
    broker = PaperBroker()
    requested_order = Order(
        order_id="dep-1:2026-04-21:AAA:buy",
        client_order_id="dep-1:2026-04-21:AAA:buy",
        deployment_id="dep-1",
        symbol="AAA",
        side="buy",
        shares=9_000,
        business_date=date(2026, 4, 21),
    )
    result = broker.execute_target_weights(
        business_date=date(2026, 4, 21),
        target_weights={"AAA": 0.9},
        holdings={},
        equity=100_000.0,
        cash=100_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=_cost_model(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        requested_orders=[requested_order],
        execution_slices=3,
    )

    assert [fill.shares for fill in result.fills] == [3_000, 3_000, 3_000]
    assert [fill.slice_index for fill in result.fills] == [1, 2, 3]
    assert result.fills[-1].remaining_shares == 0
    assert result.order_reports[0].status == "filled"
    assert result.order_reports[0].filled_shares == 9_000
