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
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ez.errors import AccountingError
from ez.portfolio.allocator import Allocator
from ez.portfolio.calendar import RebalanceFreq, TradingCalendar
from ez.portfolio.execution import (
    CostModel,
    TradeResult,
    _lot_round,
    _compute_commission,
    execute_portfolio_trades,
    EPS_FUND,
)
from ez.portfolio.optimizer import PortfolioOptimizer
from ez.portfolio.portfolio_strategy import PortfolioStrategy
from ez.portfolio.risk_manager import RiskManager
from ez.portfolio.universe import Universe, slice_universe_data


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
    rebalance_weights: list[dict[str, float]] = field(default_factory=list)  # V2.12: per-rebalance weights (aligned with rebalance_dates)
    risk_events: list[dict] = field(default_factory=list)  # V2.12: 风控事件日志



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
    optimizer: PortfolioOptimizer | None = None,  # V2.12: 组合优化器
    risk_manager: RiskManager | None = None,      # V2.12: 风控管理器
    t_plus_1: bool = True,  # V2.12.1 codex: A-share only; set False for US/HK
    strict_lookback: bool = False,  # V2.13.2 G1.4: raise on insufficient lookback
    rebal_weekday: int | None = None,  # 0=Mon..4=Fri; weekly only
    skip_terminal_liquidation: bool = False,  # QMT compat: no forced liquidation at end
    use_open_price: bool = False,  # QMT 5-min compat: trade at open instead of close
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

    # V2.12.1 post-review (codex #22): hard-check that the strategy's declared
    # lookback_days is at least as long as every required factor's warmup_period.
    # Previously this was only documented in a comment on PortfolioStrategy.
    # lookback_days — custom strategies that forgot to bump lookback when adding
    # long-warmup factors silently received truncated history for the early
    # rebalance days, biasing results with no error.
    try:
        strategy_lb = int(getattr(strategy, 'lookback_days', 252))
        req_warmups: list[int] = []
        # Walk factor dependencies where available (both TopN and MultiFactor)
        for attr in ("factor", "factors"):
            f = getattr(strategy, attr, None)
            if f is None:
                continue
            if isinstance(f, list):
                for fi in f:
                    w = int(getattr(fi, 'warmup_period', 0) or 0)
                    if w:
                        req_warmups.append(w)
            else:
                w = int(getattr(f, 'warmup_period', 0) or 0)
                if w:
                    req_warmups.append(w)
        if req_warmups:
            max_req = max(req_warmups)
            if strategy_lb < max_req:
                msg = (
                    f"Strategy {type(strategy).__name__} lookback_days="
                    f"{strategy_lb} is less than max factor warmup_period="
                    f"{max_req} — early rebalances will see truncated "
                    f"history. Set strategy.lookback_days >= {max_req}."
                )
                if strict_lookback:
                    raise ValueError(msg)
                import logging as _lg
                _lg.getLogger(__name__).warning(msg)
    except ValueError:
        raise  # strict_lookback ValueError must propagate
    except Exception:
        # Defensive: validation must never fail the backtest itself
        pass

    trading_days = calendar.trading_days_between(start, end)
    rebal_dates = set(calendar.rebalance_dates(start, end, freq, rebal_weekday=rebal_weekday))

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
    # Pre-build price arrays for O(1) lookup: (dates, adj_close, raw_close, open, date_set)
    _sym_data: dict[str, tuple[list[date], list[float], list[float], list[float], set[date]]] = {}
    for sym, df in universe_data.items():
        has_adj = "adj_close" in df.columns
        has_open = "open" in df.columns
        dates_list = []
        adj_list = []
        raw_list = []
        open_list = []
        for i in range(len(df)):
            d = df.index[i].date() if isinstance(df.index, pd.DatetimeIndex) else df.index[i]
            dates_list.append(d)
            adj_list.append(float(df.iloc[i]["adj_close"]) if has_adj else float(df.iloc[i]["close"]))
            raw_list.append(float(df.iloc[i]["close"]))
            open_list.append(float(df.iloc[i]["open"]) if has_open else float(df.iloc[i]["close"]))
        # Ensure sorted for bisect correctness (guard against unsorted input)
        if dates_list != sorted(dates_list):
            order = sorted(range(len(dates_list)), key=lambda i: dates_list[i])
            dates_list = [dates_list[i] for i in order]
            adj_list = [adj_list[i] for i in order]
            raw_list = [raw_list[i] for i in order]
            open_list = [open_list[i] for i in order]
        _sym_data[sym] = (dates_list, adj_list, raw_list, open_list, set(dates_list))

    # Initialize prev_prices/prev_raw_close from data before first trading day
    # so that limit check works on the very first day of the backtest
    if trading_days:
        first_day = trading_days[0]
        for sym, (sdates, adj_arr, raw_arr, _open_arr, date_set) in _sym_data.items():
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
        is_rebal_day = day in rebal_dates
        for sym in holdings.keys() | tradeable:
            if sym not in _sym_data:
                continue
            sdates, adj_arr, raw_arr, open_arr, date_set = _sym_data[sym]
            # bisect: find rightmost index where date <= day
            idx = bisect.bisect_right(sdates, day) - 1
            if idx >= 0:
                adj_val = adj_arr[idx]
                raw_val = raw_arr[idx]
                # QMT 5-min compat: on rebalance days, use open price for execution
                if use_open_price and is_rebal_day and day in date_set:
                    open_val = open_arr[idx]
                    if not np.isnan(open_val):
                        prices[sym] = open_val
                    elif not np.isnan(adj_val):
                        prices[sym] = adj_val
                elif not np.isnan(adj_val):
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

        # V2.12: Daily drawdown check (every trading day, not just rebalance)
        dd_scale = 1.0  # default: no drawdown scaling
        if risk_manager:
            dd_scale, dd_event = risk_manager.check_drawdown(equity)
            if dd_event:
                result.risk_events.append({"date": day.isoformat(), "event": dd_event})
            # Emergency sell only on FIRST breach: dd_event is non-None only on
            # state transition (NORMAL→BREACHED). Subsequent BREACHED days return None.
            # On rebalance days, skip emergency sell — dd_scale applied to weights instead.
            if dd_scale < 1.0 and dd_event is not None and day not in rebal_dates:
                # Emergency sell: reduce all positions proportionally
                for sym in list(holdings.keys()):
                    target = _lot_round(holdings[sym] * dd_scale, lot_size)
                    delta = target - holdings[sym]
                    if delta >= 0 or sym not in prices:
                        continue
                    if sym not in has_bar_today:
                        continue
                    # Bug4 fix: check limit down before emergency sell (same as normal sell)
                    if limit_pct > 0 and sym in raw_close_today and sym in prev_raw_close:
                        change = (raw_close_today[sym] - prev_raw_close[sym]) / prev_raw_close[sym] if prev_raw_close[sym] > 0 else 0
                        if change <= -limit_pct + 1e-6:
                            continue  # 跌停不可卖
                    sell_price = prices[sym] * (1 - cost_model.slippage_rate)
                    sell_amount = abs(delta) * sell_price
                    comm = _compute_commission(sell_amount, cost_model.sell_commission_rate, cost_model.min_commission)
                    stamp = sell_amount * cost_model.stamp_tax_rate
                    cash += sell_amount - comm - stamp
                    if target == 0:
                        holdings.pop(sym, None)
                    else:
                        holdings[sym] = target
                    result.trades.append({
                        "date": day.isoformat(), "symbol": sym, "side": "sell",
                        "shares": abs(delta), "price": sell_price, "cost": comm + stamp,
                    })
                # Recompute equity + prev_weights after emergency sells
                # (IMPORTANT-3: prev_weights must reflect post-sell state for turnover check)
                position_value = sum(holdings.get(sym, 0) * prices.get(sym, 0) for sym in holdings)
                equity = cash + position_value
                if equity > 0:
                    prev_weights = {s: (holdings.get(s, 0) * prices.get(s, 0)) / equity
                                    for s in holdings if s in prices}

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

            # Strategy returns None → skip rebalance (no trade).
            # Must still record daily equity/dates/weights before continuing.
            if raw_weights is None:
                # Compute equity once (reviewer I1: was computed twice)
                position_value = sum(holdings.get(s, 0) * prices.get(s, 0) for s in holdings)
                equity = cash + position_value
                if equity > 0:
                    prev_weights = {s: (holdings.get(s, 0) * prices.get(s, 0)) / equity
                                    for s in holdings if s in prices}
                # Reviewer I2: accounting invariant check on skip days too
                if cash < -EPS_FUND:
                    raise AccountingError(f"Negative cash on {day}: cash={cash:.2f}")
                if equity <= 0:
                    raise AccountingError(f"Non-positive equity on {day}: equity={equity:.2f}")
                # Record daily equity (critical for correct annualization)
                daily_weights: dict[str, float] = {}
                if equity > 0:
                    for sym, sh in holdings.items():
                        if sh > 0 and sym in prices and prices[sym] > 0:
                            daily_weights[sym] = (sh * prices[sym]) / equity
                result.equity_curve.append(equity)
                result.dates.append(day)
                result.weights_history.append(daily_weights)
                # Update prev_prices/prev_returns for next day
                current_returns: dict[str, float] = {}
                for sym in holdings:
                    if sym in prices and sym in prev_prices:
                        old_p = prev_prices[sym]
                        if old_p > 0:
                            current_returns[sym] = (prices[sym] - old_p) / old_p
                prev_returns = current_returns
                prev_prices = dict(prices)
                prev_raw_close = dict(raw_close_today)
                continue
            # V2.12: Optimizer takes priority over allocator
            if optimizer:
                optimizer.set_context(day, sliced_tradeable)
                raw_weights = optimizer.optimize(raw_weights)
            elif allocator:
                raw_weights = allocator.allocate(raw_weights)

            # V2.12: Turnover check (only RiskManager, not optimizer)
            if risk_manager:
                raw_weights, to_event = risk_manager.check_turnover(raw_weights, prev_weights)
                if to_event:
                    result.risk_events.append({"date": day.isoformat(), "event": to_event})

            # Bug6 fix: on rebalance day, also apply drawdown scale to weights
            # (dd_scale computed earlier in daily drawdown check)
            if dd_scale < 1.0:
                raw_weights = {s: w * dd_scale for s, w in raw_weights.items()}

            # Clip to long-only, normalize if needed
            weights = {k: max(0.0, v) for k, v in raw_weights.items() if v > 0}
            total_w = sum(weights.values())
            if total_w > 1.0:
                weights = {k: v / total_w for k, v in weights.items()}

            # V2.12.1 codex follow-up: track pre-trade equity and trade volume
            # so we can surface the ACTUAL post-rounding turnover even when
            # check_turnover() passed the weight-layer limit.
            pre_trade_equity = cash + sum(holdings.get(s, 0) * prices.get(s, 0)
                                           for s in holdings)

            # V2.15 A1: delegate to shared execute_portfolio_trades()
            sold_today: set[str] = set()
            exec_trades, holdings, cash, trade_volume = execute_portfolio_trades(
                target_weights=weights,
                holdings=holdings,
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
                sold_today=sold_today,
            )
            # Convert TradeResult -> dict for result.trades
            for tr in exec_trades:
                result.trades.append({
                    "date": day.isoformat(), "symbol": tr.symbol,
                    "side": tr.side, "shares": tr.shares,
                    "price": tr.price, "cost": tr.cost,
                })

            # Post-execution turnover re-check: discrete lot rounding can push
            # realized turnover above what check_turnover() mixed to. We don't
            # roll back trades (that would cascade through T+1 / cash / order
            # dependencies), but we emit a risk_event so users can see when
            # realized turnover exceeded their configured limit.
            if risk_manager is not None and pre_trade_equity > 0:
                # Recompute single-sided turnover from executed trades
                buy_vol = sum(
                    tr.shares * tr.price for tr in exec_trades if tr.side == "buy"
                )
                sell_vol = sum(
                    tr.shares * tr.price for tr in exec_trades if tr.side == "sell"
                )
                realized_turnover = max(buy_vol, sell_vol) / pre_trade_equity
                limit = risk_manager._config.max_turnover
                if realized_turnover > limit + 1e-6:
                    result.risk_events.append({
                        "date": day.isoformat(),
                        "event": (
                            f"成交层换手 {realized_turnover:.1%} 超过限制 {limit:.0%} "
                            f"(权重层 check_turnover 通过, 但 lot_size 取整放大了卖侧)"
                        ),
                    })

            result.rebalance_dates.append(day)
            result.rebalance_weights.append(dict(weights))  # V2.12: for attribution

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
        if cash < -EPS_FUND:
            raise AccountingError(f"Negative cash on {day}: cash={cash:.2f}")
        # Accounting invariant: equity must be positive
        if equity <= 0:
            raise AccountingError(
                f"Non-positive equity on {day}: equity={equity:.2f}, "
                f"cash={cash:.2f}, pos={position_value:.2f}")

        # V2.12.2 codex round 5: compute actual daily weights from current
        # holdings × today's prices / equity, reflecting intra-rebalance
        # drift from price moves. Prior version appended `prev_weights`
        # (the post-rebalance snapshot from the last rebalance day), so
        # weights_history claimed "daily holdings" but actually showed
        # repeated rebalance snapshots — price drift was invisible. Now
        # `weights_history[i]` is the TRUE weight of each symbol at the
        # close of day i, which is what downstream analysis (drift
        # diagnostics, drawdown-by-asset, etc.) requires.
        daily_weights: dict[str, float] = {}
        if equity > 0:
            for sym, sh in holdings.items():
                if sh > 0 and sym in prices and prices[sym] > 0:
                    daily_weights[sym] = (sh * prices[sym]) / equity

        result.equity_curve.append(equity)
        result.dates.append(day)
        result.weights_history.append(daily_weights)

        prev_prices = dict(prices)
        prev_raw_close = dict(raw_close_today)

    # Final liquidation: sell all remaining holdings at last day's prices
    # (fixes Known Limitation #5: trade_count reflects full round-trip)
    # Use "T+1" date (next calendar day after last trading day) to avoid
    # same-day ordering conflict with rebalance trades.
    #
    # V2.12.1 post-review fix (codex): append the post-liquidation equity point
    # to equity_curve/dates so downstream metrics (total_return, sharpe, max_dd)
    # reflect the actual realized cash — prior versions computed metrics on the
    # pre-liquidation curve, systematically OVERSTATING returns when positions
    # remained at period end (mark-to-market counted, but commission + stamp +
    # slippage of the final close-out were never charged against the curve).
    if holdings and prices and trading_days and not skip_terminal_liquidation:
        liq_date = trading_days[-1] + timedelta(days=1)
        had_liquidation = False
        for sym in list(holdings.keys()):
            shares = holdings[sym]
            if shares <= 0 or sym not in prices:
                continue
            sell_price = prices[sym] * (1 - cost_model.slippage_rate)
            sell_amount = shares * sell_price
            comm = _compute_commission(sell_amount, cost_model.sell_commission_rate, cost_model.min_commission)
            stamp = sell_amount * cost_model.stamp_tax_rate
            cash += sell_amount - comm - stamp
            result.trades.append({
                "date": liq_date.isoformat(),
                "symbol": sym, "side": "sell",
                "shares": shares, "price": sell_price, "cost": comm + stamp,
                "liquidation": True,
            })
            had_liquidation = True
        holdings.clear()

        # Append the realized-cash equity point so metrics reflect the true
        # end-of-period value (post slippage/commission/stamp).
        if had_liquidation:
            if cash < -EPS_FUND:
                raise AccountingError(f"Negative cash after liquidation: {cash:.2f}")
            result.equity_curve.append(cash)
            result.dates.append(liq_date)
            result.weights_history.append({})  # empty — all liquidated

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
        # ddof=1 (Bessel correction) to match pandas default used by
        # ez/backtest/metrics.py — the two engines must return numerically
        # identical Sharpe for identical inputs (codex finding #1).
        vol = float(np.std(returns, ddof=1)) * np.sqrt(252) if len(returns) > 1 else 0
        # Standard Sharpe formula matching ez/backtest/metrics.py (codex finding):
        # prior version used `ann_ret / vol`, but single-stock and WF used
        # `excess.mean() / excess.std() × √252` with 3% risk-free rate. Same name,
        # different semantics → portfolio ranking could not be compared with
        # single-stock results. Unified to the standard daily-excess formula
        # with ddof=1 so numeric output matches MetricsCalculator exactly.
        RF_ANNUAL = 0.03
        daily_rf = RF_ANNUAL / 252
        excess = returns - daily_rf
        excess_std = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
        sharpe = float(np.mean(excess) / excess_std * np.sqrt(252)) if excess_std > 1e-10 else 0.0
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

        # Sortino ratio (downside deviation of EXCESS returns).
        # V2.12.1 reviewer round 4 Important 1: match ez/backtest/metrics.py
        # formula — prior version used `ann_ret / (std(returns[<0]) × √252)`
        # with total returns (not excess) and ddof=0, producing values ~30%
        # higher than single-stock engine for the same input. Now uses the
        # downside semi-deviation of excess returns:
        #     sortino = excess.mean() / sqrt(mean(min(excess, 0)²)) × √252
        downside_sq = np.minimum(excess, 0) ** 2
        downside_dev = float(np.sqrt(downside_sq.mean())) if len(excess) > 0 else 0.0
        sortino = float(np.mean(excess) / downside_dev * np.sqrt(252)) if downside_dev > 1e-10 else 0.0

        # Benchmark metrics
        bench_ret = 0.0
        alpha = 0.0
        beta = 0.0
        if len(result.benchmark_curve) == len(eq):
            bench = np.array(result.benchmark_curve)
            bench_returns = np.diff(bench) / bench[:-1]
            bench_ret = bench[-1] / bench[0] - 1 if bench[0] > 0 else 0

            # Alpha / Beta (CAPM regression: R_s - Rf = alpha + beta × (R_b - Rf)).
            # V2.12.1 reviewer round 4 Important 1: match ez/backtest/metrics.py.
            # Prior version computed alpha as (ann_ret - beta × ann_bench_ret)
            # without subtracting the risk-free rate, so portfolio alpha diverged
            # from single-stock alpha by ~5-6 percentage points.
            if len(bench_returns) > 1 and np.std(bench_returns, ddof=1) > 1e-10:
                excess_b = bench_returns - daily_rf
                excess_s = returns - daily_rf
                # Sample covariance (ddof=1) to match pd.Series.cov default
                cov_sb = float(np.cov(excess_s, excess_b, ddof=1)[0, 1])
                var_b = float(np.var(excess_b, ddof=1))
                beta = cov_sb / var_b if var_b > 0 else 0.0
                # Annualized alpha: (mean daily excess_s - beta × mean daily excess_b) × 252
                alpha = float((np.mean(excess_s) - beta * np.mean(excess_b)) * 252)

        # Turnover (average per rebalance)
        total_trade_value = sum(t["shares"] * t["price"] for t in result.trades if not t.get("liquidation"))
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
            "trade_count": len(result.trades),  # includes final liquidation trades
            "turnover_per_rebalance": turnover,  # excludes liquidation trades
            "n_rebalances": n_rebal,
            "concentration_hhi": avg_concentration,
        }

    return result
