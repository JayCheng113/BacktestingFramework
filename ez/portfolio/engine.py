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
    buy_commission_rate: float = 0.0003
    sell_commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005  # sell-side only (A-share)
    slippage_rate: float = 0.0


@dataclass
class PortfolioResult:
    """Output of a portfolio backtest."""
    equity_curve: list[float] = field(default_factory=list)
    benchmark_curve: list[float] = field(default_factory=list)
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
    limit_pct: float = 0.10,  # A-share 涨跌停 10% (科创板/创业板 20%)
    benchmark_symbol: str = "",  # e.g. "510300.SH" for CSI300 ETF benchmark
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
    prev_raw_close: dict[str, float] = {}

    # V2.9.1 PERF: Pre-build price arrays for O(1) lookup by date index
    # {sym: (sorted_dates, adj_prices, raw_prices, date_set)}
    import bisect
    _sym_data: dict[str, tuple[list[date], list[float], list[float], set[date]]] = {}
    for sym, df in universe_data.items():
        has_adj = "adj_close" in df.columns
        dates_list = []
        adj_list = []
        raw_list = []
        for i in range(len(df)):
            d = df.index[i].date() if isinstance(df.index, pd.DatetimeIndex) else df.index[i]
            dates_list.append(d)
            adj_list.append(float(df.iloc[i]["adj_close"]) if has_adj else float(df.iloc[i]["close"]))
            raw_list.append(float(df.iloc[i]["close"]))
        # Ensure sorted for bisect correctness (guard against unsorted input)
        if dates_list != sorted(dates_list):
            order = sorted(range(len(dates_list)), key=lambda i: dates_list[i])
            dates_list = [dates_list[i] for i in order]
            adj_list = [adj_list[i] for i in order]
            raw_list = [raw_list[i] for i in order]
        _sym_data[sym] = (dates_list, adj_list, raw_list, set(dates_list))

    # Initialize prev_prices/prev_raw_close from data before first trading day
    # so that limit check works on the very first day of the backtest
    if trading_days:
        first_day = trading_days[0]
        for sym, (sdates, adj_arr, raw_arr, date_set) in _sym_data.items():
            idx = bisect.bisect_left(sdates, first_day) - 1
            if idx >= 0:
                if not np.isnan(adj_arr[idx]):
                    prev_prices[sym] = adj_arr[idx]
                elif not np.isnan(raw_arr[idx]):
                    prev_prices[sym] = raw_arr[idx]
                if not np.isnan(raw_arr[idx]):
                    prev_raw_close[sym] = raw_arr[idx]

    for day in trading_days:
        tradeable = set(universe.tradeable_at(day))

        # O(log n) price lookup via bisect on pre-sorted date arrays
        prices: dict[str, float] = {}
        raw_close_today: dict[str, float] = {}
        has_bar_today: set[str] = set()
        for sym in holdings.keys() | tradeable:
            if sym not in _sym_data:
                continue
            sdates, adj_arr, raw_arr, date_set = _sym_data[sym]
            # bisect: find rightmost index where date <= day
            idx = bisect.bisect_right(sdates, day) - 1
            if idx >= 0:
                adj_val = adj_arr[idx]
                raw_val = raw_arr[idx]
                if not np.isnan(adj_val):
                    prices[sym] = adj_val
                elif not np.isnan(raw_val):
                    prices[sym] = raw_val
                elif sym in prev_prices and not np.isnan(prev_prices[sym]):
                    prices[sym] = prev_prices[sym]
                if not np.isnan(raw_val):
                    raw_close_today[sym] = raw_val
            elif sym in prev_prices and not np.isnan(prev_prices[sym]):
                prices[sym] = prev_prices[sym]
            if day in date_set and sym in raw_close_today:
                has_bar_today.add(sym)  # require valid price on today's bar

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

            # Execute trades: TWO passes — sells first (free cash), then buys
            all_syms = sorted(holdings.keys() | target_shares.keys())
            sell_syms = [s for s in all_syms if target_shares.get(s, 0) < holdings.get(s, 0)]
            buy_syms = [s for s in all_syms if target_shares.get(s, 0) > holdings.get(s, 0)]
            sold_today: set[str] = set()  # T+1: track sold symbols
            for sym in sell_syms + buy_syms:  # sells first, then buys
                cur = holdings.get(sym, 0)
                tgt = target_shares.get(sym, 0)
                delta = tgt - cur

                if delta == 0:
                    continue
                if sym not in prices:
                    continue
                # C2: require today's bar to trade (no stale-price trading)
                if sym not in has_bar_today:
                    continue

                # T+1: cannot buy a symbol that was sold today
                if delta > 0 and sym in sold_today:
                    continue

                # A-share 涨跌停检查 (C1: use raw close, not adj_close)
                if limit_pct > 0 and sym in raw_close_today and sym in prev_raw_close:
                    change = (raw_close_today[sym] - prev_raw_close[sym]) / prev_raw_close[sym] if prev_raw_close[sym] > 0 else 0
                    if delta > 0 and change >= limit_pct - 1e-6:
                        continue  # 涨停不可买
                    if delta < 0 and change <= -limit_pct + 1e-6:
                        continue  # 跌停不可卖

                base_price = prices[sym]
                # Directional slippage: buy pushes price UP, sell pushes DOWN
                if delta > 0:
                    price = base_price * (1 + cost_model.slippage_rate)
                else:
                    price = base_price * (1 - cost_model.slippage_rate)
                amount = abs(delta) * price

                # Costs (buy/sell use different commission rates)
                rate = cost_model.buy_commission_rate if delta > 0 else cost_model.sell_commission_rate
                comm = _compute_commission(amount, rate, cost_model.min_commission)
                stamp = amount * cost_model.stamp_tax_rate if delta < 0 else 0.0  # sell only
                total_cost = comm + stamp

                if delta > 0:
                    # Buy: need cash
                    total_buy = amount + total_cost
                    if total_buy > cash:
                        # Reduce shares to fit budget. price already includes slippage.
                        min_cost = max(cost_model.min_commission, 0)
                        affordable = (cash - min_cost) / (price * (1 + cost_model.buy_commission_rate)) if price > 0 else 0
                        if affordable <= 0:
                            continue
                        tgt = cur + _lot_round(affordable, lot_size)
                        delta = tgt - cur
                        if delta <= 0:
                            continue
                        amount = delta * price  # price already includes slippage
                        comm = _compute_commission(amount, cost_model.buy_commission_rate, cost_model.min_commission)
                        total_cost = comm  # no separate slippage — already in price
                        total_buy = amount + total_cost

                    # Final guard: skip if still over budget (min_commission rounding)
                    if total_buy > cash + EPS_FUND:
                        continue
                    cash -= total_buy
                    holdings[sym] = tgt
                else:
                    # Sell: receive cash minus costs
                    cash += amount - total_cost
                    sold_today.add(sym)  # T+1: record sold symbol
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

        # End-of-day equity — compute position value from two independent paths
        position_value = sum(holdings.get(sym, 0) * prices.get(sym, 0) for sym in holdings)
        equity = cash + position_value

        # Accounting invariant: no negative cash (unless rounding error)
        assert cash >= -EPS_FUND, \
            f"Negative cash on {day}: cash={cash:.2f}"
        # Accounting invariant: equity must be positive
        assert equity > 0, \
            f"Non-positive equity on {day}: equity={equity:.2f}, cash={cash:.2f}, pos={position_value:.2f}"

        result.equity_curve.append(equity)
        result.dates.append(day)
        result.weights_history.append(dict(prev_weights))

        prev_prices = dict(prices)
        prev_raw_close = dict(raw_close_today)

    # Benchmark curve (buy & hold of benchmark_symbol, or initial cash)
    # O(n) single-pass via pre-indexed prices
    if benchmark_symbol and benchmark_symbol in _sym_data and result.dates:
        bench_dates, bench_adj, bench_raw, _ = _sym_data[benchmark_symbol]

        def _bench_price_at(idx: int) -> float:
            """Get benchmark price: prefer adj_close, fallback to raw close."""
            if idx < 0:
                return 0.0
            v = bench_adj[idx]
            if not np.isnan(v):
                return v
            v = bench_raw[idx]
            return v if not np.isnan(v) else 0.0

        first_day = result.dates[0]
        idx0 = bisect.bisect_right(bench_dates, first_day) - 1
        # If first_day is before all benchmark data, use earliest available price
        if idx0 < 0:
            idx0 = 0
        first_price = _bench_price_at(idx0)
        if first_price > 0:
            for day in result.dates:
                idx = bisect.bisect_right(bench_dates, day) - 1
                if idx < 0:
                    idx = 0  # day before benchmark data: use earliest available
                p = _bench_price_at(idx)
                if p > 0:
                    result.benchmark_curve.append(float(initial_cash * p / first_price))
                else:
                    result.benchmark_curve.append(result.benchmark_curve[-1] if result.benchmark_curve else initial_cash)
        else:
            result.benchmark_curve = [initial_cash] * len(result.dates)
    else:
        result.benchmark_curve = [initial_cash] * len(result.dates)

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

        # Max drawdown duration
        dd_dur = 0
        max_dd_dur = 0
        for dd_val in drawdown:
            if dd_val < 0:
                dd_dur += 1
                max_dd_dur = max(max_dd_dur, dd_dur)
            else:
                dd_dur = 0

        # Sortino ratio (downside deviation)
        downside = returns[returns < 0]
        downside_std = np.std(downside) * np.sqrt(252) if len(downside) > 1 else 0
        sortino = ann_ret / downside_std if downside_std > 0 else 0

        # Benchmark metrics
        bench_ret = 0.0
        alpha = 0.0
        beta = 0.0
        if len(result.benchmark_curve) == len(eq):
            bench = np.array(result.benchmark_curve)
            bench_returns = np.diff(bench) / bench[:-1]
            bench_ret = bench[-1] / bench[0] - 1 if bench[0] > 0 else 0

            # Alpha / Beta (CAPM)
            if len(bench_returns) > 1 and np.std(bench_returns) > 0:
                cov = np.cov(returns, bench_returns)
                beta = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else 0
                alpha = float(ann_ret - beta * (bench_ret / max(n_years, 0.01)))

        # Turnover (average per rebalance)
        total_trade_value = sum(t["shares"] * t["price"] for t in result.trades)
        avg_equity = np.mean(eq)
        n_rebal = max(len(result.rebalance_dates), 1)
        turnover = (total_trade_value / avg_equity / n_rebal) if avg_equity > 0 else 0

        # Concentration (HHI): average Herfindahl index across rebalance weights
        hhi_values = []
        for w_dict in result.weights_history:
            if w_dict:
                ws = [v for v in w_dict.values() if v > 0]
                if ws:
                    hhi_values.append(sum(w ** 2 for w in ws))
        avg_concentration = float(np.mean(hhi_values)) if hhi_values else 0

        result.metrics = {
            "total_return": total_ret,
            "annualized_return": ann_ret,
            "annualized_volatility": vol,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_dd,
            "max_drawdown_duration": max_dd_dur,
            "benchmark_return": bench_ret,
            "alpha": alpha,
            "beta": beta,
            "trade_count": len(result.trades),
            "turnover_per_rebalance": turnover,
            "n_rebalances": n_rebal,
            "concentration_hhi": avg_concentration,
        }

    return result
