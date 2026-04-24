"""Paper broker implementation of the live broker adapter contract."""
from __future__ import annotations

from datetime import date

from ez.live.broker import (
    BrokerAdapter,
    BrokerCapability,
    BrokerExecutionResult,
    BrokerFillReport,
    BrokerOrderReport,
)
from ez.live.events import Order, OrderStatus
from ez.portfolio.execution import CostModel, execute_portfolio_trades


class PaperBroker(BrokerAdapter):
    """Paper broker wrapper around shared portfolio execution semantics."""

    broker_type = "paper"

    @property
    def capabilities(self) -> frozenset[BrokerCapability]:
        return frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})

    def execute_target_weights(
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
        requested_orders: list[Order] | None = None,
        execution_slices: int = 1,
    ) -> BrokerExecutionResult:
        requested_order_map = {
            (order.symbol, order.side): order
            for order in (requested_orders or [])
        }
        trades, new_holdings, new_cash, trade_volume = execute_portfolio_trades(
            target_weights=target_weights,
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
        )
        fills = _split_broker_fills(
            business_date=business_date,
            trades=trades,
            requested_order_map=requested_order_map,
            execution_slices=execution_slices,
            lot_size=lot_size,
        )
        order_reports = _build_order_reports(
            requested_orders=requested_orders or [],
            fills=fills,
            business_date=business_date,
        )
        return BrokerExecutionResult(
            fills=fills,
            order_reports=order_reports,
            holdings=new_holdings,
            cash=new_cash,
            trade_volume=trade_volume,
        )


def _build_order_reports(
    *,
    requested_orders: list[Order],
    fills: list[BrokerFillReport],
    business_date: date,
) -> list[BrokerOrderReport]:
    fill_groups: dict[str, list[BrokerFillReport]] = {}
    for fill in fills:
        key = fill.client_order_id or f"{fill.symbol}:{fill.side}"
        fill_groups.setdefault(key, []).append(fill)
    reports: list[BrokerOrderReport] = []
    for order in requested_orders:
        child_fills = fill_groups.get(order.client_order_id, [])
        filled_shares = sum(int(fill.shares) for fill in child_fills)
        remaining_shares = max(order.shares - filled_shares, 0)
        if not child_fills:
            status = OrderStatus.REJECTED.value
        elif filled_shares < order.shares:
            status = OrderStatus.PARTIALLY_FILLED.value
        else:
            status = OrderStatus.FILLED.value
        amount = sum(float(fill.amount) for fill in child_fills)
        commission = sum(float(fill.commission) for fill in child_fills)
        stamp_tax = sum(float(fill.stamp_tax) for fill in child_fills)
        cost = sum(float(fill.cost) for fill in child_fills)
        price = (
            amount / filled_shares
            if filled_shares > 0
            else 0.0
        )

        reports.append(
            BrokerOrderReport(
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                deployment_id=order.deployment_id,
                symbol=order.symbol,
                side=order.side,
                requested_shares=order.shares,
                filled_shares=filled_shares,
                remaining_shares=remaining_shares,
                status=status,
                price=price,
                amount=amount,
                commission=commission,
                stamp_tax=stamp_tax,
                cost=cost,
                business_date=business_date,
            )
        )
    return reports


def _split_broker_fills(
    *,
    business_date: date,
    trades: list,
    requested_order_map: dict[tuple[str, str], Order],
    execution_slices: int,
    lot_size: int,
) -> list[BrokerFillReport]:
    fills: list[BrokerFillReport] = []
    effective_slices = max(int(execution_slices), 1)
    for trade in trades:
        requested_order = requested_order_map.get((trade.symbol, trade.side))
        requested_shares = requested_order.shares if requested_order is not None else trade.shares
        share_chunks = _split_shares(trade.shares, effective_slices, lot_size)
        allocated_amount = 0.0
        allocated_commission = 0.0
        allocated_stamp_tax = 0.0
        allocated_cost = 0.0
        cumulative_filled = 0
        total_slices = len(share_chunks)
        for index, chunk in enumerate(share_chunks, start=1):
            is_last = index == total_slices
            if is_last:
                amount = trade.amount - allocated_amount
                commission = trade.commission - allocated_commission
                stamp_tax = trade.stamp_tax - allocated_stamp_tax
                cost = trade.cost - allocated_cost
            else:
                ratio = chunk / trade.shares if trade.shares > 0 else 0.0
                amount = trade.amount * ratio
                commission = trade.commission * ratio
                stamp_tax = trade.stamp_tax * ratio
                cost = trade.cost * ratio
                allocated_amount += amount
                allocated_commission += commission
                allocated_stamp_tax += stamp_tax
                allocated_cost += cost
            cumulative_filled += chunk
            remaining_shares = max(requested_shares - cumulative_filled, 0)
            fills.append(
                BrokerFillReport(
                    order_id=requested_order.order_id if requested_order is not None else "",
                    client_order_id=requested_order.client_order_id if requested_order is not None else "",
                    deployment_id=requested_order.deployment_id if requested_order is not None else "",
                    symbol=trade.symbol,
                    side=trade.side,
                    shares=chunk,
                    price=trade.price,
                    amount=amount,
                    commission=commission,
                    stamp_tax=stamp_tax,
                    cost=cost,
                    business_date=business_date,
                    requested_shares=requested_shares,
                    remaining_shares=remaining_shares,
                    slice_index=index,
                    total_slices=total_slices,
                )
            )
    return fills


def _split_shares(total_shares: int, slices: int, lot_size: int) -> list[int]:
    if total_shares <= 0:
        return []
    effective = max(min(int(slices), total_shares // max(lot_size, 1)), 1)
    chunks: list[int] = []
    remaining = total_shares
    for remaining_slices in range(effective, 0, -1):
        if remaining_slices == 1:
            chunk = remaining
        else:
            chunk = max((remaining // remaining_slices) // lot_size * lot_size, lot_size)
            max_allowed = remaining - lot_size * (remaining_slices - 1)
            chunk = min(chunk, max_allowed)
        chunks.append(chunk)
        remaining -= chunk
    return [chunk for chunk in chunks if chunk > 0]
