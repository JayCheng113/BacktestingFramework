from __future__ import annotations

from datetime import date

import pytest

from ez.live.allocation import AllocationContext
from ez.live.broker import BrokerExecutionResult, BrokerOrderReport
from ez.live.events import DeploymentEvent, EventType, OrderStatus, utcnow
from ez.live.oms import PaperOMS
from ez.portfolio.execution import CostModel


def test_execute_rebalance_emits_submitted_and_filled_events():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 13),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
    )

    assert len(result.orders) == 1
    assert len(result.fills) == 1
    assert result.orders[0].status == OrderStatus.FILLED
    assert result.holdings["AAA"] == 50_000
    assert result.cash == 500_000.0
    assert [e.event_type for e in result.events] == [
        EventType.ORDER_SUBMITTED,
        EventType.ORDER_FILLED,
    ]

    replayed = oms.replay_events(result.events, initial_cash=1_000_000.0)
    assert replayed.holdings == {"AAA": 50_000}
    assert replayed.cash == 500_000.0
    assert next(iter(replayed.order_statuses.values())) == OrderStatus.FILLED.value


def test_execute_rebalance_rejects_blocked_buy():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 14),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 11.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
    )

    assert len(result.orders) == 1
    assert result.orders[0].status == OrderStatus.REJECTED
    assert result.fills == []
    assert result.holdings == {}
    assert result.cash == 1_000_000.0
    assert [e.event_type for e in result.events] == [
        EventType.ORDER_SUBMITTED,
        EventType.ORDER_REJECTED,
    ]

    replayed = oms.replay_events(result.events, initial_cash=1_000_000.0)
    assert replayed.holdings == {}
    assert replayed.cash == 1_000_000.0
    assert next(iter(replayed.order_statuses.values())) == OrderStatus.REJECTED.value


def test_execute_rebalance_keeps_async_submission_only_order_open():
    class _AsyncSubmissionBroker:
        def execute_target_weights(self, **kwargs):
            requested_orders = kwargs["requested_orders"]
            business_date = kwargs["business_date"]
            cash = kwargs["cash"]
            holdings = dict(kwargs["holdings"])
            order = requested_orders[0]
            return BrokerExecutionResult(
                fills=[],
                order_reports=[
                    BrokerOrderReport(
                        order_id=order.order_id,
                        client_order_id=order.client_order_id,
                        deployment_id=order.deployment_id,
                        symbol=order.symbol,
                        side=order.side,
                        requested_shares=order.shares,
                        filled_shares=0,
                        remaining_shares=order.shares,
                        status="submitted",
                        price=0.0,
                        amount=0.0,
                        commission=0.0,
                        stamp_tax=0.0,
                        cost=0.0,
                        business_date=business_date,
                    )
                ],
                holdings=holdings,
                cash=cash,
                trade_volume=0.0,
            )

    oms = PaperOMS("dep-1", broker=_AsyncSubmissionBroker())
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 14),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
    )

    assert len(result.orders) == 1
    assert result.orders[0].status == OrderStatus.SUBMITTED
    assert result.fills == []
    assert [event.event_type for event in result.events] == [
        EventType.ORDER_SUBMITTED,
    ]
    replayed = oms.replay_events(result.events, initial_cash=1_000_000.0)
    assert replayed.order_statuses["dep-1:2026-04-14:AAA:buy"] == OrderStatus.SUBMITTED.value


def test_execute_rebalance_emits_broker_submit_ack_when_submission_has_broker_order_id():
    class _AckingBroker:
        broker_type = "qmt"

        def execute_target_weights(self, **kwargs):
            requested_orders = kwargs["requested_orders"]
            business_date = kwargs["business_date"]
            cash = kwargs["cash"]
            holdings = dict(kwargs["holdings"])
            order = requested_orders[0]
            return BrokerExecutionResult(
                fills=[],
                order_reports=[
                    BrokerOrderReport(
                        order_id=order.order_id,
                        client_order_id=order.client_order_id,
                        deployment_id=order.deployment_id,
                        symbol=order.symbol,
                        side=order.side,
                        requested_shares=order.shares,
                        filled_shares=0,
                        remaining_shares=order.shares,
                        status="reported",
                        price=10.0,
                        amount=0.0,
                        commission=0.0,
                        stamp_tax=0.0,
                        cost=0.0,
                        business_date=business_date,
                        broker_order_id="1001",
                        broker_submit_id="77",
                    )
                ],
                holdings=holdings,
                cash=cash,
                trade_volume=0.0,
            )

    oms = PaperOMS("dep-1", broker=_AckingBroker())
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 14),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
    )

    assert [event.event_type for event in result.events] == [
        EventType.ORDER_SUBMITTED,
        EventType.BROKER_EXECUTION_RECORDED,
    ]
    assert result.events[1].payload["broker_order_id"] == "1001"
    assert result.events[1].payload["raw_payload"]["broker_submit_id"] == "77"
    replayed = oms.replay_events(result.events, initial_cash=1_000_000.0)
    assert replayed.order_statuses["dep-1:2026-04-14:AAA:buy"] == OrderStatus.SUBMITTED.value


def test_replay_events_respects_initial_holdings_baseline():
    oms = PaperOMS("dep-1")
    replayed = oms.replay_events(
        [
            DeploymentEvent(
                event_id="dep-1:2026-04-14:AAA:sell:order_filled",
                deployment_id="dep-1",
                event_type=EventType.ORDER_FILLED,
                event_ts=utcnow(),
                client_order_id="dep-1:2026-04-14:AAA:sell",
                payload={
                    "symbol": "AAA",
                    "side": "sell",
                    "shares": 200,
                    "amount": 2_000.0,
                    "cost": 10.0,
                },
            ),
        ],
        initial_cash=500_000.0,
        initial_holdings={"AAA": 1_000},
    )
    assert replayed.cash == 501_990.0
    assert replayed.holdings == {"AAA": 800}
    assert replayed.order_statuses["dep-1:2026-04-14:AAA:sell"] == OrderStatus.FILLED.value


def test_execute_rebalance_rejects_kill_switch_before_submission():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 15),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params={"kill_switch": True},
    )

    assert len(result.orders) == 1
    assert result.orders[0].status == OrderStatus.REJECTED
    assert result.orders[0].rejected_reason == "risk:kill_switch"
    assert result.orders[0].rejected_message == "Kill switch is active; all new orders are blocked."
    assert result.fills == []
    assert result.trades == []
    assert result.events[0].event_type == EventType.ORDER_REJECTED
    assert result.risk_events[0]["rule"] == "kill_switch"
    assert result.events[0].payload["rejected_details"] == {}


def test_execute_rebalance_executes_only_risk_accepted_orders():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 16),
        target_weights={"AAA": 0.6, "BBB": 0.4},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0, "BBB": 10.0},
        raw_close_today={"AAA": 10.0, "BBB": 10.0},
        prev_raw_close={"AAA": 10.0, "BBB": 10.0},
        has_bar_today={"AAA", "BBB"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params={"max_order_notional": 450_000.0},
    )

    assert len(result.risk_events) == 1
    assert result.risk_events[0]["reason"] == "risk:max_order_notional"
    assert any(
        event.event_type == EventType.ORDER_REJECTED and event.payload["symbol"] == "AAA"
        for event in result.events
    )
    assert any(
        event.event_type == EventType.ORDER_FILLED and event.payload["symbol"] == "BBB"
        for event in result.events
    )
    assert result.holdings == {"BBB": 40_000}
    assert result.cash == 600_000.0


def test_execute_rebalance_emits_partial_fill_event_when_broker_partials():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 16),
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
    )

    statuses = {order.symbol: order.status for order in result.orders}
    assert statuses["AAA"] == OrderStatus.FILLED
    assert statuses["BBB"] == OrderStatus.PARTIALLY_FILLED
    partial_fill = next(fill for fill in result.fills if fill.symbol == "BBB")
    assert partial_fill.requested_shares == 5_000
    assert partial_fill.shares == 4_900
    assert partial_fill.remaining_shares == 100
    assert any(
        event.event_type == EventType.ORDER_PARTIALLY_FILLED
        and event.payload["symbol"] == "BBB"
        for event in result.events
    )

    replayed = oms.replay_events(result.events, initial_cash=100_000.0)
    assert replayed.holdings == {"AAA": 5_000, "BBB": 4_900}
    assert replayed.order_statuses["dep-1:2026-04-16:BBB:buy"] == OrderStatus.PARTIALLY_FILLED.value


def test_execute_rebalance_emits_child_fill_events_when_execution_is_sliced():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 16),
        target_weights={"AAA": 0.9},
        holdings={},
        equity=100_000.0,
        cash=100_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params={"execution_slices": 3},
    )

    assert len(result.fills) == 3
    assert [fill.shares for fill in result.fills] == [3_000, 3_000, 3_000]
    assert [fill.slice_index for fill in result.fills] == [1, 2, 3]
    assert [event.event_type for event in result.events] == [
        EventType.ORDER_SUBMITTED,
        EventType.ORDER_PARTIALLY_FILLED,
        EventType.ORDER_PARTIALLY_FILLED,
        EventType.ORDER_FILLED,
    ]
    assert result.orders[0].status == OrderStatus.FILLED

    replayed = oms.replay_events(result.events, initial_cash=100_000.0)
    assert replayed.holdings == {"AAA": 9_000}
    assert replayed.order_statuses["dep-1:2026-04-16:AAA:buy"] == OrderStatus.FILLED.value


def test_execute_rebalance_applies_runtime_allocation_cap_before_orders():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 17),
        target_weights={"AAA": 0.6, "BBB": 0.4},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0, "BBB": 10.0},
        raw_close_today={"AAA": 10.0, "BBB": 10.0},
        prev_raw_close={"AAA": 10.0, "BBB": 10.0},
        has_bar_today={"AAA", "BBB"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params={"runtime_allocation_cap": 0.50},
    )

    assert result.holdings == {"AAA": 30_000, "BBB": 20_000}
    assert result.cash == 500_000.0
    gate_event = result.risk_events[0]
    assert gate_event["event"] == "runtime_allocation_gate"
    assert gate_event["details"]["requested_allocation"] == 1.0
    assert gate_event["details"]["effective_allocation"] == 0.5


def test_execute_rebalance_uses_equal_weight_allocator_with_max_names():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.5, "BBB": 0.3, "CCC": 0.2},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0, "BBB": 10.0, "CCC": 10.0},
        raw_close_today={"AAA": 10.0, "BBB": 10.0, "CCC": 10.0},
        prev_raw_close={"AAA": 10.0, "BBB": 10.0, "CCC": 10.0},
        has_bar_today={"AAA", "BBB", "CCC"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params={
            "allocation_mode": "equal_weight_cap",
            "runtime_allocation_cap": 0.6,
            "max_names": 2,
        },
    )

    assert result.holdings == {"AAA": 30_000, "BBB": 30_000}
    assert result.cash == 400_000.0
    allocation_event = result.risk_events[0]
    assert allocation_event["event"] == "runtime_allocator"
    assert allocation_event["details"]["dropped_symbols"] == ["CCC"]
    assert allocation_event["details"]["adjusted_weights"] == {"AAA": 0.3, "BBB": 0.3}


def test_execute_rebalance_uses_constrained_optimizer_allocator():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 19),
        target_weights={"AAA": 0.1, "BBB": 0.8},
        holdings={"AAA": 50_000},
        equity=1_000_000.0,
        cash=500_000.0,
        prices={"AAA": 10.0, "BBB": 10.0},
        raw_close_today={"AAA": 10.0, "BBB": 10.0},
        prev_raw_close={"AAA": 10.0, "BBB": 10.0},
        has_bar_today={"AAA", "BBB"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params={
            "allocation_mode": "constrained_opt",
            "runtime_allocation_cap": 0.8,
            "max_daily_turnover": 0.4,
        },
        allocator_context=AllocationContext(current_weights={"AAA": 0.5}),
    )

    allocation_event = result.risk_events[0]
    assert allocation_event["event"] == "runtime_allocator"
    assert allocation_event["details"]["allocation_mode"] == "constrained_opt"
    assert allocation_event["details"]["effective_turnover"] == pytest.approx(0.4)
    assert result.holdings == {"AAA": 35_000, "BBB": 25_000}
    assert result.cash == pytest.approx(400_000.0)
    assert allocation_event["details"]["adjusted_weights"] == pytest.approx({"AAA": 0.35, "BBB": 0.25})


def test_execute_rebalance_uses_covariance_aware_constrained_allocator():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 20),
        target_weights={"AAA": 0.4, "BBB": 0.4},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0, "BBB": 10.0},
        raw_close_today={"AAA": 10.0, "BBB": 10.0},
        prev_raw_close={"AAA": 10.0, "BBB": 10.0},
        has_bar_today={"AAA", "BBB"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params={
            "allocation_mode": "constrained_opt",
            "runtime_allocation_cap": 0.8,
            "covariance_risk_aversion": 8.0,
            "risk_budget_strength": 0.5,
        },
        allocator_context=AllocationContext(
            current_weights={},
            volatility_by_symbol={"AAA": 0.30, "BBB": 0.10},
            covariance_symbols=("AAA", "BBB"),
            covariance_matrix=[[0.09, 0.0], [0.0, 0.01]],
        ),
    )

    allocation_event = result.risk_events[0]
    assert allocation_event["details"]["allocation_mode"] == "constrained_opt"
    assert allocation_event["details"]["covariance_used"] is True
    assert allocation_event["details"]["adjusted_weights"]["BBB"] > allocation_event["details"]["adjusted_weights"]["AAA"]
    assert result.holdings["BBB"] > result.holdings["AAA"]


def test_execute_rebalance_uses_risk_budget_allocator_with_vol_target():
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 19),
        target_weights={"AAA": 0.5, "BBB": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0, "BBB": 10.0},
        raw_close_today={"AAA": 10.0, "BBB": 10.0},
        prev_raw_close={"AAA": 10.0, "BBB": 10.0},
        has_bar_today={"AAA", "BBB"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params={
            "allocation_mode": "risk_budget_cap",
            "runtime_allocation_cap": 1.0,
            "target_portfolio_vol": 0.15,
        },
        allocator_context=AllocationContext(
            volatility_by_symbol={"AAA": 0.30, "BBB": 0.30}
        ),
    )

    assert result.holdings == {"AAA": 35_300, "BBB": 35_300}
    assert result.cash == 294_000.0
    allocation_event = result.risk_events[0]
    assert allocation_event["event"] == "runtime_allocator"
    assert allocation_event["details"]["estimated_portfolio_vol"] == pytest.approx(0.2121320343)
    assert allocation_event["details"]["target_portfolio_vol"] == 0.15


def test_replay_events_is_idempotent_under_duplicate_log():
    """Fix 1: replaying the OMS event log 10x equals replaying it once."""
    oms = PaperOMS("dep-1")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 13),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
    )

    baseline = oms.replay_events(result.events, initial_cash=1_000_000.0)
    duplicated = oms.replay_events(result.events * 10, initial_cash=1_000_000.0)

    assert baseline.cash == duplicated.cash == 500_000.0
    assert baseline.holdings == duplicated.holdings == {"AAA": 50_000}
    assert baseline.order_statuses == duplicated.order_statuses


def test_replay_events_ignores_duplicate_broker_submission_acks():
    """Fix 1 + Fix 3: duplicate submission-only broker acks never move cash/holdings."""
    from ez.live.broker import BrokerExecutionResult, BrokerOrderReport

    class _AckingBroker:
        broker_type = "qmt"

        def execute_target_weights(self, **kwargs):
            requested_orders = kwargs["requested_orders"]
            business_date = kwargs["business_date"]
            cash = kwargs["cash"]
            holdings = dict(kwargs["holdings"])
            order = requested_orders[0]
            return BrokerExecutionResult(
                fills=[],
                order_reports=[
                    BrokerOrderReport(
                        order_id=order.order_id,
                        client_order_id=order.client_order_id,
                        deployment_id=order.deployment_id,
                        symbol=order.symbol,
                        side=order.side,
                        requested_shares=order.shares,
                        filled_shares=0,
                        remaining_shares=order.shares,
                        status="reported",
                        price=10.0,
                        amount=0.0,
                        commission=0.0,
                        stamp_tax=0.0,
                        cost=0.0,
                        business_date=business_date,
                        broker_order_id="1001",
                        broker_submit_id="77",
                    )
                ],
                holdings=holdings,
                cash=cash,
                trade_volume=0.0,
            )

    oms = PaperOMS("dep-1", broker=_AckingBroker())
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 14),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
    )

    # Replay the same submission-only ack log 10x — cash/holdings stay put.
    replayed = oms.replay_events(result.events * 10, initial_cash=1_000_000.0)
    assert replayed.cash == 1_000_000.0
    assert replayed.holdings == {}
    assert replayed.order_statuses[result.events[0].client_order_id] == OrderStatus.SUBMITTED.value


def test_capital_policy_kill_switch_rejects_real_broker_orders(monkeypatch):
    """V3.3.46 integration: with kill-switch env active, a real-broker OMS
    rebalance rejects every order with ``kill_switch_capital_downgrade``
    instead of silently submitting real-capital orders to the broker.
    """
    monkeypatch.setenv("EZ_LIVE_QMT_KILL_SWITCH", "1")
    oms = PaperOMS("dep-capital")
    risk_params = {
        "capital_policy": {
            "enabled": True,
            "stage": "small_whitelist",
        }
    }
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 13),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params=risk_params,
        broker_type="qmt",
    )
    # All orders must have been rejected with a capital-policy-scoped rule.
    assert not result.fills
    risk_events = [
        e
        for e in result.events
        if e.event_type == EventType.ORDER_REJECTED
    ]
    assert risk_events, "expected at least one ORDER_REJECTED event under kill-switch"
    reasons = {e.payload.get("rejected_reason", "") for e in risk_events}
    assert any("capital_stage" in r or "kill_switch" in r for r in reasons), (
        f"expected capital-policy reason, got {reasons}"
    )


def test_capital_policy_paper_broker_is_unaffected_by_kill_switch(monkeypatch):
    """Kill-switch downgrades real-broker stages to paper_sim only. A
    deployment already on broker_type=paper keeps running."""
    monkeypatch.setenv("EZ_LIVE_QMT_KILL_SWITCH", "1")
    oms = PaperOMS("dep-paper")
    risk_params = {
        "capital_policy": {
            "enabled": True,
            "stage": "small_whitelist",
        }
    }
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 13),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params=risk_params,
        broker_type="paper",
    )
    # Paper broker path should not be rejected by kill-switch.
    assert result.fills, "paper orders should still fill under kill-switch"


def test_capital_policy_disabled_by_default_preserves_legacy_behavior():
    """When ``risk_params`` has no ``capital_policy`` bucket (or enabled=False),
    OMS behaves exactly like the legacy no-policy path."""
    oms = PaperOMS("dep-legacy")
    result = oms.execute_rebalance(
        business_date=date(2026, 4, 13),
        target_weights={"AAA": 0.5},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"AAA": 10.0},
        raw_close_today={"AAA": 10.0},
        prev_raw_close={"AAA": 10.0},
        has_bar_today={"AAA"},
        cost_model=CostModel(
            buy_commission_rate=0.0,
            sell_commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
            slippage_rate=0.0,
        ),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        risk_params=None,
        broker_type="qmt",  # real broker, but policy is off
    )
    assert result.fills, "legacy path should ignore capital policy"


def test_is_submission_only_broker_report_detects_acks():
    """Fix 3: submission-only detector covers the xtquant open-order vocabulary."""
    from types import SimpleNamespace

    from ez.live.oms import _is_submission_only_broker_report

    for status in ("submitted", "reported", "unreported", "wait_reporting"):
        report = SimpleNamespace(status=status, filled_shares=0, remaining_shares=1000)
        assert _is_submission_only_broker_report(report)

    partial = SimpleNamespace(status="partially_filled", filled_shares=100, remaining_shares=900)
    assert not _is_submission_only_broker_report(partial)

    terminal = SimpleNamespace(status="filled", filled_shares=1000, remaining_shares=0)
    assert not _is_submission_only_broker_report(terminal)
