"""Shared trade execution logic for portfolio engines (backtest + paper).

Extracted from ez/portfolio/engine.py (V2.15 A1) so both the backtest engine
and the upcoming paper-trading engine use identical fill logic.
"""
from __future__ import annotations

from dataclasses import dataclass


EPS_FUND = 0.01  # accounting tolerance (cents)


@dataclass
class CostModel:
    buy_commission_rate: float = 0.0003
    sell_commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005  # sell-side only (A-share)
    slippage_rate: float = 0.0


@dataclass
class TradeResult:
    """One executed leg (buy or sell)."""
    symbol: str
    side: str          # "buy" | "sell"
    shares: int
    price: float
    amount: float
    commission: float
    stamp_tax: float
    cost: float


def _lot_round(shares: float, lot_size: int = 100) -> int:
    """Round down to lot size."""
    return int(shares // lot_size) * lot_size


def _compute_commission(amount: float, rate: float, minimum: float) -> float:
    return max(abs(amount) * rate, minimum) if abs(amount) > 0 else 0.0


def execute_portfolio_trades(
    target_weights: dict[str, float],
    holdings: dict[str, int],
    equity: float,
    cash: float,
    prices: dict[str, float],
    raw_close_today: dict[str, float],
    prev_raw_close: dict[str, float],
    has_bar_today: set[str],
    cost_model: CostModel,
    lot_size: int = 100,
    limit_pct: float = 0.10,
    t_plus_1: bool = True,
    sold_today: set[str] | None = None,
) -> tuple[list[TradeResult], dict[str, int], float, float]:
    """Execute weight-to-share trades with two-pass sell-then-buy.

    Returns (trades, updated_holdings, updated_cash, trade_volume).

    The caller's *holdings* dict is mutated in-place (same object returned
    for convenience).  *sold_today* is also mutated (sells append to it).
    """
    if sold_today is None:
        sold_today = set()

    # Convert weights -> target shares (discrete)
    target_shares: dict[str, int] = {}
    for sym, w in target_weights.items():
        if sym not in prices or prices[sym] <= 0:
            continue
        target_amount = equity * w
        raw_shares = target_amount / prices[sym]
        target_shares[sym] = _lot_round(raw_shares, lot_size)

    trades: list[TradeResult] = []
    trade_volume = 0.0

    # Two passes: sells first (free cash), then buys
    all_syms = sorted(holdings.keys() | target_shares.keys())
    sell_syms = [s for s in all_syms if target_shares.get(s, 0) < holdings.get(s, 0)]
    buy_syms = [s for s in all_syms if target_shares.get(s, 0) > holdings.get(s, 0)]

    for sym in sell_syms + buy_syms:
        cur = holdings.get(sym, 0)
        tgt = target_shares.get(sym, 0)
        delta = tgt - cur

        if delta == 0:
            continue
        if sym not in prices:
            continue
        # Require today's bar to trade (no stale-price trading)
        if sym not in has_bar_today:
            continue

        # T+1: cannot buy a symbol that was sold today
        if t_plus_1 and delta > 0 and sym in sold_today:
            continue

        # Limit up/down check (use raw close, not adj_close)
        if limit_pct > 0 and sym in raw_close_today and sym in prev_raw_close:
            prev_rc = prev_raw_close[sym]
            change = (raw_close_today[sym] - prev_rc) / prev_rc if prev_rc > 0 else 0
            if delta > 0 and change >= limit_pct - 1e-6:
                continue  # limit-up blocks buy
            if delta < 0 and change <= -limit_pct + 1e-6:
                continue  # limit-down blocks sell

        base_price = prices[sym]
        # Directional slippage
        if delta > 0:
            price = base_price * (1 + cost_model.slippage_rate)
        else:
            price = base_price * (1 - cost_model.slippage_rate)
        amount = abs(delta) * price

        # Costs
        rate = cost_model.buy_commission_rate if delta > 0 else cost_model.sell_commission_rate
        comm = _compute_commission(amount, rate, cost_model.min_commission)
        stamp = amount * cost_model.stamp_tax_rate if delta < 0 else 0.0
        total_cost = comm + stamp

        if delta > 0:
            # Buy: need cash
            total_buy = amount + total_cost
            if total_buy > cash:
                min_cost = max(cost_model.min_commission, 0)
                affordable = (cash - min_cost) / (price * (1 + cost_model.buy_commission_rate)) if price > 0 else 0
                if affordable <= 0:
                    continue
                tgt = cur + _lot_round(affordable, lot_size)
                delta = tgt - cur
                if delta <= 0:
                    continue
                amount = delta * price
                comm = _compute_commission(amount, cost_model.buy_commission_rate, cost_model.min_commission)
                total_cost = comm
                total_buy = amount + total_cost

            # Final guard
            if total_buy > cash + EPS_FUND:
                continue
            cash -= total_buy
            holdings[sym] = tgt
        else:
            # Sell: receive cash minus costs
            cash += amount - total_cost
            sold_today.add(sym)
            if tgt == 0:
                holdings.pop(sym, None)
            else:
                holdings[sym] = tgt

        trades.append(TradeResult(
            symbol=sym,
            side="buy" if delta > 0 else "sell",
            shares=abs(delta),
            price=price,
            amount=amount,
            commission=comm,
            stamp_tax=stamp,
            cost=total_cost,
        ))
        trade_volume += amount

    return trades, holdings, cash, trade_volume
