"""Shared trade execution logic for portfolio engines (backtest + paper).

Extracted from ez/portfolio/engine.py (V2.15 A1) so both the backtest engine
and the upcoming paper-trading engine use identical fill logic.
C++ fast path (V3 performance) used when available.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS_FUND = 0.01  # accounting tolerance (cents)

_HAS_CPP_REBAL = False
try:
    from ez.core._portfolio_rebalance_cpp import portfolio_rebalance_day as _cpp_rebalance
    _HAS_CPP_REBAL = True
except ImportError:
    pass


@dataclass
class CostModel:
    buy_commission_rate: float = 0.00008   # 万0.8 — QMT量化账户标准
    sell_commission_rate: float = 0.00008  # 万0.8
    min_commission: float = 0.0            # 量化账户通常免五
    stamp_tax_rate: float = 0.0005         # 万5 — 国家规定, 仅卖出
    slippage_rate: float = 0.001           # 万1 — 保守滑点估计


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

    if _HAS_CPP_REBAL and limit_pct <= 0:
        try:
            return _execute_cpp(
                target_weights, holdings, equity, cash, prices,
                raw_close_today, prev_raw_close, has_bar_today,
                cost_model, lot_size, limit_pct, t_plus_1, sold_today,
            )
        except Exception:
            pass  # fallback to Python

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


def _execute_cpp(
    target_weights, holdings, equity, cash, prices,
    raw_close_today, prev_raw_close, has_bar_today,
    cost_model, lot_size, limit_pct, t_plus_1, sold_today,
):
    """C++ fast path: marshal dicts → arrays → C++ → unmarshal."""
    all_syms = sorted(set(holdings.keys()) | set(target_weights.keys()) | set(prices.keys()))
    all_syms = [s for s in all_syms if s in prices and prices[s] > 0]
    n = len(all_syms)
    if n == 0:
        return [], holdings, cash, 0.0

    sym_to_idx = {s: i for i, s in enumerate(all_syms)}

    tw_arr = np.zeros(n, dtype=np.float64)
    px_arr = np.zeros(n, dtype=np.float64)
    rpc_arr = np.zeros(n, dtype=np.float64)
    hld_arr = np.zeros(n, dtype=np.int64)

    for i, s in enumerate(all_syms):
        w = target_weights.get(s, 0.0)
        if t_plus_1 and w > 0 and s in sold_today:
            w = float(holdings.get(s, 0) * prices[s]) / equity if equity > 0 else 0.0
        if s not in has_bar_today:
            w = float(holdings.get(s, 0) * prices[s]) / equity if equity > 0 else 0.0
        tw_arr[i] = w
        px_arr[i] = prices.get(s, 0.0)
        rpc_arr[i] = prev_raw_close.get(s, 0.0) if s in has_bar_today else 0.0
        hld_arr[i] = holdings.get(s, 0)

    sell_rate_with_stamp = cost_model.sell_commission_rate + cost_model.stamp_tax_rate

    result = _cpp_rebalance(
        tw_arr, px_arr, rpc_arr, hld_arr, float(cash),
        float(cost_model.buy_commission_rate), float(sell_rate_with_stamp),
        float(cost_model.min_commission), float(cost_model.slippage_rate),
        int(lot_size), float(limit_pct),
    )

    new_hld_arr, new_cash, tc = result[0], float(result[1]), int(result[2])
    t_syms, t_sides, t_shares, t_prices, t_costs = result[3], result[4], result[5], result[6], result[7]

    trades = []
    trade_volume = 0.0
    for j in range(tc):
        sym = all_syms[int(t_syms[j])]
        side = "buy" if int(t_sides[j]) == 0 else "sell"
        sh = int(t_shares[j])
        pr = float(t_prices[j])
        amt = sh * pr
        cst = float(t_costs[j])
        stamp = amt * cost_model.stamp_tax_rate if side == "sell" else 0.0
        comm = cst - stamp if side == "sell" else cst
        if side == "sell":
            sold_today.add(sym)
        trades.append(TradeResult(
            symbol=sym, side=side, shares=sh, price=pr,
            amount=amt, commission=max(comm, 0.0), stamp_tax=stamp, cost=cst,
        ))
        trade_volume += amt

    for i, s in enumerate(all_syms):
        new_sh = int(new_hld_arr[i])
        if new_sh > 0:
            holdings[s] = new_sh
        elif s in holdings:
            del holdings[s]

    return trades, holdings, float(new_cash), trade_volume
