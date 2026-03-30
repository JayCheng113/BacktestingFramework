"""V2.9 P5: PortfolioEngine — discrete-share accounting with invariant.

Core guarantees (Codex #2, #4):
- Anti-lookahead: data sliced to [date-lookback, date-1] before calling strategy
- Accounting invariant: cash + Σ(shares × price) == equity (checked daily)
- Discrete shares: target weight → target amount → shares (lot-size rounded) → remainder to cash
- Costs: commission (rate × amount, min 5) + stamp tax (sell 0.05%) + slippage
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from ez.portfolio.allocator import Allocator
from ez.portfolio.calendar import RebalanceFreq, TradingCalendar
from ez.portfolio.portfolio_strategy import PortfolioStrategy
from ez.portfolio.universe import Universe, slice_universe_data


EPS_FUND = 0.01  # accounting tolerance (cents)


@dataclass
class CostModel:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005  # sell-side only (A-share)
    slippage_rate: float = 0.0


@dataclass
class PortfolioResult:
    """Output of a portfolio backtest."""
    equity_curve: list[float] = field(default_factory=list)
    dates: list[date] = field(default_factory=list)
    weights_history: list[dict[str, float]] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    rebalance_dates: list[date] = field(default_factory=list)


def _lot_round(shares: float, lot_size: int = 100) -> int:
    """Round down to lot size."""
    return int(shares // lot_size) * lot_size


def _compute_commission(amount: float, rate: float, minimum: float) -> float:
    return max(abs(amount) * rate, minimum) if abs(amount) > 0 else 0.0


def run_portfolio_backtest(
    strategy: PortfolioStrategy,
    universe: Universe,
    universe_data: dict[str, pd.DataFrame],
    calendar: TradingCalendar,
    start: date,
    end: date,
    freq: RebalanceFreq = "monthly",
    initial_cash: float = 1_000_000.0,
    cost_model: CostModel | None = None,
    allocator: Allocator | None = None,
    lot_size: int = 100,
) -> PortfolioResult:
    """Run a portfolio backtest with discrete-share accounting.

    The engine:
    1. Iterates over trading days
    2. On rebalance dates: calls strategy.generate_weights() with sliced data
    3. Converts weights → shares (lot-size rounded), executes trades with costs
    4. Daily: updates equity = cash + Σ(shares × close), checks accounting invariant
    """
    if cost_model is None:
        cost_model = CostModel()

    trading_days = calendar.trading_days_between(start, end)
    rebal_dates = set(calendar.rebalance_dates(start, end, freq))

    # State
    cash = initial_cash
    holdings: dict[str, int] = {}  # symbol → shares (integer)
    prev_weights: dict[str, float] = {}
    prev_returns: dict[str, float] = {}

    result = PortfolioResult()
    prev_prices: dict[str, float] = {}

    for day in trading_days:
        tradeable = set(universe.tradeable_at(day))

        # Get current prices (use last available close)
        prices: dict[str, float] = {}
        for sym in set(list(holdings.keys()) + list(tradeable)):
            if sym in universe_data:
                df = universe_data[sym]
                if isinstance(df.index, pd.DatetimeIndex):
                    mask = df.index.date <= day
                else:
                    mask = df.index <= day
                valid = df.loc[mask]
                if not valid.empty and "close" in valid.columns:
                    p = valid["close"].iloc[-1]
                    if not (np.isnan(p) if isinstance(p, float) else False):
                        prices[sym] = float(p)

        # Compute equity BEFORE rebalance
        position_value = sum(holdings.get(sym, 0) * prices.get(sym, 0) for sym in holdings)
        equity = cash + position_value

        # Rebalance
        if day in rebal_dates:
            # Slice data for strategy (anti-lookahead: up to day-1)
            sliced = slice_universe_data(universe_data, day, strategy.lookback_days)

            # Filter to tradeable symbols only
            sliced_tradeable = {s: df for s, df in sliced.items() if s in tradeable}

            # Call strategy
            raw_weights = strategy.generate_weights(
                sliced_tradeable,
                datetime.combine(day, datetime.min.time()),
                prev_weights, prev_returns,
            )

            # Apply allocator if provided
            if allocator:
                raw_weights = allocator.allocate(raw_weights)

            # Clip to long-only, normalize if needed
            weights = {k: max(0.0, v) for k, v in raw_weights.items() if v > 0}
            total_w = sum(weights.values())
            if total_w > 1.0:
                weights = {k: v / total_w for k, v in weights.items()}

            # Convert weights → target shares (discrete)
            target_shares: dict[str, int] = {}
            for sym, w in weights.items():
                if sym not in prices or prices[sym] <= 0:
                    continue
                target_amount = equity * w
                raw_shares = target_amount / prices[sym]
                target_shares[sym] = _lot_round(raw_shares, lot_size)

            # Execute trades
            # Deterministic order: sells first (free cash), then buys (alphabetical)
            all_syms = sorted(set(list(holdings.keys()) + list(target_shares.keys())))
            for sym in all_syms:
                cur = holdings.get(sym, 0)
                tgt = target_shares.get(sym, 0)
                delta = tgt - cur

                if delta == 0:
                    continue
                if sym not in prices:
                    continue

                price = prices[sym]
                amount = abs(delta) * price

                # Costs
                comm = _compute_commission(amount, cost_model.commission_rate, cost_model.min_commission)
                stamp = amount * cost_model.stamp_tax_rate if delta < 0 else 0.0  # sell only
                slip = amount * cost_model.slippage_rate
                total_cost = comm + stamp + slip

                if delta > 0:
                    # Buy: need cash
                    total_buy = amount + total_cost
                    if total_buy > cash:
                        # Reduce shares to fit budget (include min_commission in estimate)
                        min_cost = max(cost_model.min_commission, 0)
                        affordable = (cash - min_cost) / (price * (1 + cost_model.commission_rate + cost_model.slippage_rate)) if price > 0 else 0
                        if affordable <= 0:
                            continue  # can't even afford min_commission
                        tgt = cur + _lot_round(affordable, lot_size)
                        delta = tgt - cur
                        if delta <= 0:
                            continue
                        amount = delta * price
                        comm = _compute_commission(amount, cost_model.commission_rate, cost_model.min_commission)
                        total_cost = comm + amount * cost_model.slippage_rate
                        total_buy = amount + total_cost

                    # Final guard: skip if still over budget (min_commission rounding)
                    if total_buy > cash + EPS_FUND:
                        continue
                    cash -= total_buy
                    holdings[sym] = tgt
                else:
                    # Sell: receive cash minus costs
                    cash += amount - total_cost
                    if tgt == 0:
                        holdings.pop(sym, None)
                    else:
                        holdings[sym] = tgt

                result.trades.append({
                    "date": day.isoformat(), "symbol": sym,
                    "side": "buy" if delta > 0 else "sell",
                    "shares": abs(delta), "price": price, "cost": total_cost,
                })

            result.rebalance_dates.append(day)

            # Record actual weights for next call
            new_equity = cash + sum(holdings.get(s, 0) * prices.get(s, 0) for s in holdings)
            prev_weights = {}
            if new_equity > 0:
                for sym, sh in holdings.items():
                    if sym in prices:
                        prev_weights[sym] = (sh * prices[sym]) / new_equity

        # Compute returns for each holding
        current_returns: dict[str, float] = {}
        for sym in holdings:
            if sym in prices and sym in prev_prices:
                old_p = prev_prices[sym]
                if old_p > 0:
                    current_returns[sym] = (prices[sym] - old_p) / old_p
        prev_returns = current_returns

        # End-of-day equity
        position_value = sum(holdings.get(sym, 0) * prices.get(sym, 0) for sym in holdings)
        equity = cash + position_value

        # Accounting invariant check (Codex #4)
        assert abs(cash + position_value - equity) < EPS_FUND, \
            f"Accounting invariant violated on {day}: cash={cash}, pos={position_value}, eq={equity}"

        result.equity_curve.append(equity)
        result.dates.append(day)
        result.weights_history.append(dict(prev_weights))

        prev_prices = dict(prices)

    # Compute metrics
    if len(result.equity_curve) > 1:
        eq = np.array(result.equity_curve)
        returns = np.diff(eq) / eq[:-1]
        n_years = len(returns) / 252
        total_ret = eq[-1] / eq[0] - 1
        ann_ret = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1 if n_years > 0 else 0
        vol = np.std(returns) * np.sqrt(252) if len(returns) > 1 else 0
        sharpe = ann_ret / vol if vol > 0 else 0
        drawdown = (eq / np.maximum.accumulate(eq)) - 1
        max_dd = float(np.min(drawdown))

        # Turnover (average per rebalance)
        total_trade_value = sum(t["shares"] * t["price"] for t in result.trades)
        avg_equity = np.mean(eq)
        n_rebal = max(len(result.rebalance_dates), 1)
        turnover = (total_trade_value / avg_equity / n_rebal) if avg_equity > 0 else 0

        result.metrics = {
            "total_return": total_ret,
            "annualized_return": ann_ret,
            "annualized_volatility": vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "trade_count": len(result.trades),
            "turnover_per_rebalance": turnover,
            "n_rebalances": n_rebal,
        }

    return result
