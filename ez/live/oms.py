"""Minimal OMS wrapper for V3.0-lite.

OMS owns order semantics and events. Paper execution now sits behind
`PaperBroker`, so OMS no longer calls `execute_portfolio_trades()` directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from ez.live.allocation import AllocationContext, RuntimeAllocator, RuntimeAllocatorConfig
from ez.live.broker import BrokerAdapter
from ez.live.events import (
    DeploymentEvent,
    EventType,
    Fill,
    Order,
    OrderStatus,
    make_broker_execution_event,
    make_client_order_id,
    make_event_id,
    make_fill_id,
    make_order_id,
    normalize_broker_order_status,
    utcnow,
)
from ez.live.ledger import LiveLedger
from ez.live.paper_broker import PaperBroker
from ez.live.capital_policy import CapitalPolicyConfig, CapitalPolicyEngine
from ez.live.risk import PreTradeRiskConfig, PreTradeRiskEngine
from ez.portfolio.execution import CostModel


def _lot_round(shares: float, lot_size: int) -> int:
    return int(shares // lot_size) * lot_size


def _is_submission_only_broker_report(order_report: Any) -> bool:
    normalized_status = normalize_broker_order_status(
        getattr(order_report, "status", ""),
        filled_shares=int(getattr(order_report, "filled_shares", 0) or 0),
        remaining_shares=int(getattr(order_report, "remaining_shares", 0) or 0),
    )
    return (
        int(getattr(order_report, "filled_shares", 0) or 0) <= 0
        and normalized_status in {"unreported", "reported", "reported_cancel_pending"}
    )


@dataclass(slots=True)
class OMSExecutionResult:
    orders: list[Order]
    fills: list[Fill]
    events: list[DeploymentEvent]
    risk_events: list[dict[str, Any]]
    holdings: dict[str, int]
    cash: float
    trade_volume: float
    trades: list[dict[str, Any]]


@dataclass(slots=True)
class OMSReplayState:
    cash: float
    holdings: dict[str, int]
    order_statuses: dict[str, str]


class PaperOMS:
    """Minimal live OMS.

    Phase 1 responsibilities:
    - generate deterministic client_order_ids
    - persist submitted / filled / rejected events
    - replay fill events into holdings/cash state

    Non-goals for Phase 1:
    - broker-specific semantics
    - independent matching engine
    """

    def __init__(self, deployment_id: str, broker: BrokerAdapter | None = None):
        self.deployment_id = deployment_id
        self.broker = broker or PaperBroker()

    def execute_rebalance(
        self,
        *,
        business_date: date,
        target_weights: dict[str, float],
        holdings: dict[str, int],
        equity: float,
        cash: float,
        prices: dict[str, float],
        raw_close_today: dict[str, float],
        prev_raw_close: dict[str, float],
        has_bar_today: set[str],
        cost_model: CostModel,
        lot_size: int,
        limit_pct: float,
        t_plus_1: bool,
        risk_params: dict[str, Any] | None = None,
        allocator_context: AllocationContext | None = None,
        broker_type: str = "paper",
    ) -> OMSExecutionResult:
        risk_config = PreTradeRiskConfig.from_params(risk_params)
        capital_policy_config = CapitalPolicyConfig.from_params(
            (risk_params or {}).get("capital_policy")
        )
        capital_policy_engine = (
            CapitalPolicyEngine(capital_policy_config)
            if capital_policy_config is not None
            else None
        )
        allocation_decision = RuntimeAllocator(
            RuntimeAllocatorConfig.from_params(risk_params)
        ).allocate(
            business_date=business_date,
            target_weights=target_weights,
            context=allocator_context,
        )
        desired = self._build_desired_orders(
            business_date=business_date,
            target_weights=allocation_decision.adjusted_weights,
            holdings=holdings,
            equity=equity,
            prices=prices,
            lot_size=lot_size,
        )
        risk_decision = PreTradeRiskEngine(
            risk_config,
            capital_policy=capital_policy_engine,
            broker_type=broker_type,
        ).evaluate_orders(
            business_date=business_date,
            orders=desired,
            holdings=holdings,
            prices=prices,
            equity=equity,
        )
        accepted_orders = risk_decision.accepted_orders

        if accepted_orders:
            accepted_target_weights = self._accepted_target_weights(
                holdings=holdings,
                accepted_orders=accepted_orders,
                prices=prices,
                equity=equity,
            )
            broker_result = self.broker.execute_target_weights(
                business_date=business_date,
                target_weights=accepted_target_weights,
                holdings=dict(holdings),
                equity=equity,
                cash=cash,
                prices=prices,
                raw_close_today=raw_close_today,
                prev_raw_close=prev_raw_close,
                has_bar_today=has_bar_today,
                cost_model=cost_model,
                lot_size=lot_size,
                limit_pct=limit_pct,
                t_plus_1=t_plus_1,
                requested_orders=accepted_orders,
                execution_slices=int((risk_params or {}).get("execution_slices", 1) or 1),
            )
            executed_reports = {
                report.client_order_id: report
                for report in broker_result.order_reports
            }
            fills_by_order: dict[str, list] = {}
            for fill in broker_result.fills:
                fills_by_order.setdefault(fill.client_order_id, []).append(fill)
            new_holdings = broker_result.holdings
            new_cash = broker_result.cash
            trade_volume = broker_result.trade_volume
        else:
            executed_reports = {}
            fills_by_order = {}
            new_holdings = dict(holdings)
            new_cash = cash
            trade_volume = 0.0

        orders: list[Order] = []
        fills: list[Fill] = []
        events: list[DeploymentEvent] = []

        for rejected_order in risk_decision.rejected_orders:
            order = Order(
                order_id=rejected_order.order.order_id,
                client_order_id=rejected_order.order.client_order_id,
                deployment_id=self.deployment_id,
                symbol=rejected_order.order.symbol,
                side=rejected_order.order.side,
                shares=rejected_order.order.shares,
                business_date=business_date,
                requested_shares=rejected_order.order.shares,
                remaining_shares=rejected_order.order.shares,
                status=OrderStatus.REJECTED,
                rejected_reason=rejected_order.reason,
                rejected_message=rejected_order.message,
                rejected_details=rejected_order.details,
            )
            orders.append(order)
            events.append(
                DeploymentEvent(
                    event_id=make_event_id(order.client_order_id, EventType.ORDER_REJECTED),
                    deployment_id=self.deployment_id,
                    event_type=EventType.ORDER_REJECTED,
                    event_ts=utcnow(),
                    client_order_id=order.client_order_id,
                    payload=order.to_dict(),
                )
            )

        for desired_order in accepted_orders:
            client_order_id = desired_order.client_order_id
            submitted = Order(
                order_id=desired_order.order_id,
                client_order_id=client_order_id,
                deployment_id=self.deployment_id,
                symbol=desired_order.symbol,
                side=desired_order.side,
                shares=desired_order.shares,
                business_date=business_date,
                requested_shares=desired_order.shares,
                remaining_shares=desired_order.shares,
                status=OrderStatus.SUBMITTED,
            )
            orders.append(submitted)
            events.append(
                DeploymentEvent(
                    event_id=make_event_id(client_order_id, EventType.ORDER_SUBMITTED),
                    deployment_id=self.deployment_id,
                    event_type=EventType.ORDER_SUBMITTED,
                    event_ts=utcnow(),
                    client_order_id=client_order_id,
                    payload=submitted.to_dict(),
                )
            )

            order_report = executed_reports.get(client_order_id)
            if order_report is None:
                submitted.status = OrderStatus.REJECTED
                submitted.rejected_reason = "execution:not_executed_by_execution_rules"
                submitted.rejected_message = (
                    "Execution rules blocked the order after submission."
                )
                submitted.remaining_shares = desired_order.shares
                submitted.rejected_details = {
                    "rule": "execution_rules",
                    "symbol": desired_order.symbol,
                    "side": desired_order.side,
                }
                rejected = Order(
                    order_id=desired_order.order_id,
                    client_order_id=client_order_id,
                    deployment_id=self.deployment_id,
                    symbol=desired_order.symbol,
                    side=desired_order.side,
                    shares=desired_order.shares,
                    business_date=business_date,
                    requested_shares=desired_order.shares,
                    remaining_shares=desired_order.shares,
                    status=OrderStatus.REJECTED,
                    rejected_reason="execution:not_executed_by_execution_rules",
                    rejected_message="Execution rules blocked the order after submission.",
                    rejected_details={
                        "rule": "execution_rules",
                        "symbol": desired_order.symbol,
                        "side": desired_order.side,
                    },
                )
                events.append(
                    DeploymentEvent(
                        event_id=make_event_id(client_order_id, EventType.ORDER_REJECTED),
                        deployment_id=self.deployment_id,
                        event_type=EventType.ORDER_REJECTED,
                        event_ts=utcnow(),
                        client_order_id=client_order_id,
                        payload=rejected.to_dict(),
                    )
                )
                continue
            if _is_submission_only_broker_report(order_report):
                submitted.remaining_shares = (
                    order_report.remaining_shares or desired_order.shares
                )
                broker_order_id = str(
                    getattr(order_report, "broker_order_id", "") or ""
                ).strip()
                if broker_order_id:
                    events.append(
                        make_broker_execution_event(
                            self.deployment_id,
                            report_id=f"submit_ack:{broker_order_id}",
                            broker_type=str(getattr(self.broker, "broker_type", "unknown") or "unknown"),
                            report_ts=utcnow(),
                            client_order_id=client_order_id,
                            broker_order_id=broker_order_id,
                            symbol=desired_order.symbol,
                            side=desired_order.side,
                            status=str(getattr(order_report, "status", "reported") or "reported"),
                            filled_shares=0,
                            remaining_shares=int(
                                getattr(order_report, "remaining_shares", desired_order.shares)
                                or desired_order.shares
                            ),
                            avg_price=float(getattr(order_report, "price", 0.0) or 0.0),
                            message=str(getattr(order_report, "broker_submit_id", "") or ""),
                            raw_payload={
                                "source": "submit_ack",
                                "broker_submit_id": str(
                                    getattr(order_report, "broker_submit_id", "") or ""
                                ),
                            },
                        )
                    )
                continue
            if order_report.filled_shares <= 0:
                submitted.status = OrderStatus.REJECTED
                submitted.rejected_reason = "execution:not_executed_by_execution_rules"
                submitted.rejected_message = (
                    "Execution rules blocked the order after submission."
                )
                submitted.remaining_shares = desired_order.shares
                submitted.rejected_details = {
                    "rule": "execution_rules",
                    "symbol": desired_order.symbol,
                    "side": desired_order.side,
                }
                rejected = Order(
                    order_id=desired_order.order_id,
                    client_order_id=client_order_id,
                    deployment_id=self.deployment_id,
                    symbol=desired_order.symbol,
                    side=desired_order.side,
                    shares=desired_order.shares,
                    business_date=business_date,
                    requested_shares=desired_order.shares,
                    remaining_shares=desired_order.shares,
                    status=OrderStatus.REJECTED,
                    rejected_reason="execution:not_executed_by_execution_rules",
                    rejected_message="Execution rules blocked the order after submission.",
                    rejected_details={
                        "rule": "execution_rules",
                        "symbol": desired_order.symbol,
                        "side": desired_order.side,
                    },
                )
                events.append(
                    DeploymentEvent(
                        event_id=make_event_id(client_order_id, EventType.ORDER_REJECTED),
                        deployment_id=self.deployment_id,
                        event_type=EventType.ORDER_REJECTED,
                        event_ts=utcnow(),
                        client_order_id=client_order_id,
                        payload=rejected.to_dict(),
                    )
                )
                continue

            submitted.status = (
                OrderStatus.PARTIALLY_FILLED
                if order_report.status == OrderStatus.PARTIALLY_FILLED.value
                else OrderStatus.FILLED
            )
            submitted.remaining_shares = order_report.remaining_shares
            child_fills = fills_by_order.get(client_order_id, [])
            for child_fill in child_fills:
                filled = Fill(
                    fill_id=make_fill_id(client_order_id)
                    if child_fill.total_slices == 1
                    else f"{make_fill_id(client_order_id)}:{child_fill.slice_index}",
                    order_id=desired_order.order_id,
                    client_order_id=client_order_id,
                    deployment_id=self.deployment_id,
                    symbol=child_fill.symbol,
                    side=child_fill.side,
                    shares=child_fill.shares,
                    price=child_fill.price,
                    amount=child_fill.amount,
                    commission=child_fill.commission,
                    stamp_tax=child_fill.stamp_tax,
                    cost=child_fill.cost,
                    business_date=business_date,
                    requested_shares=child_fill.requested_shares,
                    remaining_shares=child_fill.remaining_shares,
                    slice_index=child_fill.slice_index,
                    total_slices=child_fill.total_slices,
                )
                fills.append(filled)
                event_type = (
                    EventType.ORDER_PARTIALLY_FILLED
                    if child_fill.remaining_shares > 0
                    else EventType.ORDER_FILLED
                )
                event_id = (
                    make_event_id(client_order_id, event_type)
                    if child_fill.total_slices == 1
                    else f"{make_event_id(client_order_id, event_type)}:{child_fill.slice_index}"
                )
                events.append(
                    DeploymentEvent(
                        event_id=event_id,
                        deployment_id=self.deployment_id,
                        event_type=event_type,
                        event_ts=utcnow(),
                        client_order_id=client_order_id,
                        payload=filled.to_dict(),
                    )
                )

        trade_dicts = [
            {
                "symbol": fill.symbol,
                "side": fill.side,
                "shares": fill.shares,
                "price": fill.price,
                "cost": fill.cost,
                "amount": fill.amount,
            }
            for fill in fills
        ]

        return OMSExecutionResult(
            orders=orders,
            fills=fills,
            events=events,
            risk_events=allocation_decision.allocation_events + risk_decision.risk_events,
            holdings=new_holdings,
            cash=new_cash,
            trade_volume=trade_volume,
            trades=trade_dicts,
        )

    def replay_events(
        self,
        events: list[DeploymentEvent],
        *,
        initial_cash: float,
        initial_holdings: dict[str, int] | None = None,
    ) -> OMSReplayState:
        replayed = LiveLedger().replay(
            events,
            initial_cash=initial_cash,
            initial_holdings=initial_holdings,
        )
        return OMSReplayState(
            cash=replayed.cash,
            holdings=replayed.holdings,
            order_statuses=replayed.order_statuses,
        )

    def _build_desired_orders(
        self,
        *,
        business_date: date,
        target_weights: dict[str, float],
        holdings: dict[str, int],
        equity: float,
        prices: dict[str, float],
        lot_size: int,
    ) -> list[Order]:
        target_shares: dict[str, int] = {}
        for sym, weight in target_weights.items():
            price = prices.get(sym)
            if price is None or price <= 0:
                continue
            raw_shares = (equity * weight) / price
            target_shares[sym] = _lot_round(raw_shares, lot_size)

        orders: list[Order] = []
        all_syms = sorted(holdings.keys() | target_shares.keys())
        for sym in all_syms:
            cur = holdings.get(sym, 0)
            tgt = target_shares.get(sym, 0)
            delta = tgt - cur
            if delta == 0:
                continue
            side = "buy" if delta > 0 else "sell"
            client_order_id = make_client_order_id(
                self.deployment_id, business_date, sym, side
            )
            orders.append(
                Order(
                    order_id=make_order_id(client_order_id),
                    client_order_id=client_order_id,
                    deployment_id=self.deployment_id,
                    symbol=sym,
                    side=side,
                    shares=abs(delta),
                    business_date=business_date,
                )
            )
        return orders

    def _accepted_target_weights(
        self,
        *,
        holdings: dict[str, int],
        accepted_orders: list[Order],
        prices: dict[str, float],
        equity: float,
    ) -> dict[str, float]:
        projected_holdings = {sym: int(shares) for sym, shares in holdings.items()}
        for order in accepted_orders:
            delta = order.shares if order.side == "buy" else -order.shares
            next_shares = projected_holdings.get(order.symbol, 0) + delta
            if next_shares > 0:
                projected_holdings[order.symbol] = next_shares
            else:
                projected_holdings.pop(order.symbol, None)

        if equity <= 0:
            return {}
        return {
            sym: (shares * prices[sym]) / equity
            for sym, shares in projected_holdings.items()
            if shares > 0 and sym in prices and prices[sym] > 0
        }
