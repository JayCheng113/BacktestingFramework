"""Vectorized backtest engine. [CORE] -- engine loop steps frozen."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ez.backtest.metrics import MetricsCalculator
from ez.backtest.significance import compute_significance
from ez.core.matcher import Matcher, SimpleMatcher
from ez.strategy.base import Strategy
from ez.types import BacktestResult, TradeRecord


class VectorizedBacktestEngine:
    """Run a vectorized backtest: factor compute -> signal generation -> simulation."""

    def __init__(
        self,
        commission_rate: float = 0.00008,
        min_commission: float = 0.0,
        risk_free_rate: float = 0.03,
        matcher: Matcher | None = None,
    ):
        self._matcher = matcher or SimpleMatcher(commission_rate, min_commission)
        self._metrics = MetricsCalculator(risk_free_rate=risk_free_rate)

    def run(
        self,
        data: pd.DataFrame,
        strategy: Strategy,
        initial_capital: float = 1000000.0,
    ) -> BacktestResult:
        df = data.copy()

        # 1. Compute factors
        warmup = 0
        for factor in strategy.required_factors():
            df = factor.compute(df)
            warmup = max(warmup, factor.warmup_period)

        # 2. Generate signals and shift to avoid look-ahead bias
        raw_signals = strategy.generate_signals(df)
        signals = raw_signals.shift(1).fillna(0.0).clip(0.0, 1.0)

        # 3. Trim warmup
        df = df.iloc[warmup:]
        signals = signals.iloc[warmup:]

        # Guard: if no data left after warmup, return a minimal result
        if len(df) == 0:
            empty_equity = pd.Series([initial_capital], dtype=float)
            empty_returns = pd.Series([0.0], dtype=float)
            empty_sig = compute_significance(empty_returns, risk_free_rate=self._metrics._rf)
            return BacktestResult(
                equity_curve=empty_equity,
                benchmark_curve=empty_equity.copy(),
                trades=[],
                metrics={"sharpe_ratio": 0.0, "total_return": 0.0, "max_drawdown": 0.0,
                         "win_rate": 0.0, "trade_count": 0, "profit_factor": 0.0},
                signals=pd.Series(dtype=float),
                daily_returns=empty_returns,
                significance=empty_sig,
            )

        # 4. Simulate
        equity, trades, daily_returns = self._simulate(df, signals, initial_capital)

        # 5. Benchmark (buy & hold)
        bench_returns = df["adj_close"].pct_change().fillna(0.0)
        benchmark = (1 + bench_returns).cumprod() * initial_capital

        # 6. Metrics
        metrics = self._metrics.compute(equity, benchmark)
        if trades:
            wins = [t for t in trades if t.pnl > 0]
            losses = [t for t in trades if t.pnl <= 0]
            metrics["win_rate"] = len(wins) / len(trades) if trades else 0.0
            metrics["trade_count"] = len(trades)
            # V2.12.1 post-review (codex #1 sub-issue): standard Profit Factor
            # definition is gross_profit / gross_loss (sum of absolute P&L),
            # not avg_win_pct / avg_loss_pct. Prior version computed the ratio
            # of average pnl_pct which is dimensionally wrong and ignores
            # position sizing — a few large wins and many small losses would
            # give the wrong answer.
            gross_profit = float(sum(t.pnl for t in wins)) if wins else 0.0
            gross_loss = abs(float(sum(t.pnl for t in losses))) if losses else 0.0
            if gross_loss > 1e-10:
                metrics["profit_factor"] = gross_profit / gross_loss
            elif gross_profit > 0:
                metrics["profit_factor"] = float("inf")  # all winners, no losses
            else:
                metrics["profit_factor"] = 0.0
            # Average holding period in days
            try:
                holding_days = [(t.exit_time - t.entry_time).days for t in trades]
                metrics["avg_holding_days"] = float(np.mean(holding_days)) if holding_days else 0.0
            except (TypeError, AttributeError):
                metrics["avg_holding_days"] = 0.0
        else:
            metrics["win_rate"] = 0.0
            metrics["trade_count"] = 0
            metrics["profit_factor"] = 0.0
            metrics["avg_holding_days"] = 0.0

        # 7. Significance — permute signals (not returns) for Monte Carlo
        asset_returns = df["adj_close"].pct_change().fillna(0.0)
        significance = compute_significance(
            daily_returns, risk_free_rate=self._metrics._rf,
            signals=signals, asset_returns=asset_returns,
        )

        return BacktestResult(
            equity_curve=equity,
            benchmark_curve=benchmark,
            trades=trades,
            metrics=metrics,
            signals=signals,
            daily_returns=daily_returns,
            significance=significance,
        )

    def _simulate(
        self,
        df: pd.DataFrame,
        signals: pd.Series,
        capital: float,
    ) -> tuple[pd.Series, list[TradeRecord], pd.Series]:
        prices = df["adj_close"].values
        open_prices = df["open"].values if "open" in df.columns else prices
        raw_close = df["close"].values if "close" in df.columns else prices
        weights = signals.values
        n = len(prices)

        if n == 0:
            return (
                pd.Series([capital], dtype=float),
                [],
                pd.Series([0.0], dtype=float),
            )

        equity_arr = np.zeros(n)
        equity_arr[0] = capital
        cash = capital
        shares = 0.0
        prev_weight = 0.0
        trades: list[TradeRecord] = []
        entry_time: datetime | None = None
        entry_price: float = 0.0
        entry_comm: float = 0.0
        peak_shares: float = 0.0       # total shares at peak (for pnl_pct)
        partial_pnl: float = 0.0       # accumulated PnL from partial sells
        partial_comm: float = 0.0      # accumulated commission from partial sells
        # V2.12.2 codex round 6: track cumulative net invested and peak
        # at-risk capital across a holding cycle. Prior version computed
        # cost_basis as `entry_price * peak_shares + entry_comm`, which is
        # wrong for strategies that reinforce a position (buy → partial
        # sell → buy again): peak_shares × VWAP-entry does not equal the
        # actual capital deployed. `cycle_net_invested` tracks running
        # (buys − partial_sell_proceeds) and `cycle_peak_invested` tracks
        # the maximum net capital at risk during the cycle. The cost basis
        # used for pnl_pct is `cycle_peak_invested` which represents the
        # maximum capital the strategy ever had tied up in this position.
        cycle_net_invested: float = 0.0    # running net (buys - partial_sell_proceeds)
        cycle_peak_invested: float = 0.0   # max cycle_net_invested seen during cycle
        daily_ret = np.zeros(n)

        times = df.index if hasattr(df.index, "__iter__") else range(n)
        time_list = list(times)
        matcher = self._matcher

        for i in range(1, n):
            # Guard: skip trading on NaN prices, carry equity forward
            if np.isnan(prices[i]) or np.isnan(open_prices[i]):
                equity_arr[i] = equity_arr[i - 1]
                daily_ret[i] = 0.0
                continue

            target_weight = weights[i] if i < len(weights) else 0.0
            exec_price = open_prices[i]

            # V2.6: notify matcher of bar context (MarketRules uses this)
            # Use raw close (not adj_close) for price limit checks —
            # 涨跌停 is based on unadjusted previous close.
            if hasattr(matcher, 'on_bar'):
                matcher.on_bar(bar_index=i, prev_close=raw_close[i - 1])

            # V2.12.2 codex round 6 reviewer: raise threshold from 1e-6 to
            # 1e-3. Round 6 changed prev_weight to reflect the ACTUAL
            # achieved weight (not target), which correctly fixes the
            # lot-rounding residual gap retry but introduced a secondary
            # issue: commission-induced drift (~7.5e-5 for standard rates)
            # exceeds 1e-6, triggering a phantom retry every bar that
            # pays min_commission for a ~0.05-share tiny trade. 1e-3 is
            # well above float-precision noise but below any meaningful
            # weight change, so real rebalancing still fires while
            # fractional drift is tolerated.
            if abs(target_weight - prev_weight) > 1e-3:
                current_equity = cash + shares * exec_price
                target_value = current_equity * target_weight
                current_value = shares * exec_price

                filled = False

                if target_value < current_value and shares > 0:
                    # Reduce or close position
                    if target_weight == 0:
                        sell_shares = shares
                    else:
                        sell_shares = (current_value - target_value) / exec_price

                    fill = matcher.fill_sell(exec_price, sell_shares)
                    if fill.shares > 0:
                        filled = True
                        cash += fill.net_amount
                        old_shares = shares
                        shares -= fill.shares
                        if shares < 1e-10:
                            shares = 0.0
                        # Update cycle net invested (sell proceeds reduce it)
                        cycle_net_invested -= fill.net_amount  # net_amount > 0 for sells
                        # Record trade when fully closing; include partial sell PnL
                        if old_shares > 0 and shares < 1e-10 and entry_time is not None:
                            final_pnl = (fill.fill_price - entry_price) * old_shares - fill.commission
                            total_pnl = partial_pnl + final_pnl - entry_comm
                            total_comm = entry_comm + partial_comm + fill.commission
                            # V2.12.2 codex round 6: use tracked cycle_peak_invested
                            # as cost basis. This is the max capital at risk during
                            # the cycle, accurately reflecting reinforce patterns
                            # (buy → partial sell → buy again) that break the prior
                            # `entry_price * peak_shares` approximation.
                            cost_basis = cycle_peak_invested if cycle_peak_invested > 0 else (entry_price * old_shares + entry_comm)
                            trades.append(TradeRecord(
                                entry_time=entry_time,
                                exit_time=time_list[i],
                                entry_price=entry_price,
                                exit_price=fill.fill_price,
                                weight=prev_weight,
                                pnl=total_pnl,
                                pnl_pct=total_pnl / cost_basis if cost_basis > 0 else 0,
                                commission=total_comm,
                            ))
                            entry_time = None
                            entry_comm = 0.0
                            partial_pnl = 0.0
                            partial_comm = 0.0
                            peak_shares = 0.0
                            cycle_net_invested = 0.0
                            cycle_peak_invested = 0.0
                        elif entry_time is not None:
                            # Partial sell — accumulate realized PnL for later
                            partial_pnl += (fill.fill_price - entry_price) * fill.shares - fill.commission
                            partial_comm += fill.commission

                elif target_value > current_value:
                    # Increase or open position
                    additional = min(target_value - current_value, cash)
                    if additional > 0:
                        fill = matcher.fill_buy(exec_price, additional)
                        if fill.shares > 0:
                            filled = True
                            if shares == 0:
                                entry_time = time_list[i]
                                entry_price = fill.fill_price
                                entry_comm = fill.commission
                                partial_pnl = 0.0
                                partial_comm = 0.0
                                # Reset cycle tracking at start of new position
                                cycle_net_invested = 0.0
                                cycle_peak_invested = 0.0
                            else:
                                entry_comm += fill.commission
                                entry_price = (entry_price * shares + fill.fill_price * fill.shares) / (shares + fill.shares)
                            shares += fill.shares
                            peak_shares = max(peak_shares, shares)
                            cash += fill.net_amount
                            # V2.12.2 codex round 6: track cumulative cash out
                            # (buy cost + commission) for accurate pnl_pct
                            # cost basis on reinforced positions.
                            buy_cash_out = -fill.net_amount  # net_amount < 0 for buys
                            cycle_net_invested += buy_cash_out
                            if cycle_net_invested > cycle_peak_invested:
                                cycle_peak_invested = cycle_net_invested

                # V2.12.2 codex round 6: update prev_weight to reflect the
                # ACTUAL achieved weight after a (possibly partial) fill,
                # not the target. Prior version set prev_weight=target_weight
                # whenever fill.shares > 0, so lot_size rounding (A-share
                # 100 shares) silently left residual weight gaps — engine
                # saw "already at target" on the next bar and refused to
                # top up. With actual-weight tracking, the next bar's
                # `abs(target - prev)` check correctly detects the gap
                # and retries.
                if filled:
                    current_equity_after = cash + shares * exec_price
                    if current_equity_after > 0:
                        prev_weight = (shares * exec_price) / current_equity_after
                    else:
                        prev_weight = 0.0

            position_value = shares * prices[i]
            equity_arr[i] = cash + position_value
            if equity_arr[i - 1] > 0:
                daily_ret[i] = (equity_arr[i] / equity_arr[i - 1]) - 1

        # V2.12.2 codex round 5: terminal liquidation TRADE RECORD so
        # held-to-end strategies get proper round-trip metrics. Prior
        # version only recorded a TradeRecord when a position was
        # explicitly closed by a signal flip to zero. Buy-and-hold and
        # "hold to period end" strategies produced trade_count=0,
        # win_rate=0.0, profit_factor=0.0, avg_holding_days=0 — the
        # metrics silently dropped the final open position.
        #
        # IMPORTANT: this synthesizes a virtual TradeRecord only; it
        # does NOT modify equity_arr or daily_ret. The equity curve
        # remains mark-to-market consistent with the shadow
        # reconstruction invariants (test_shadow_equity_matches_engine).
        # The assumption is that the user sees the equity curve as
        # "what you had at period end" and the trade record as
        # "what you would have realized if you closed it". Keeping
        # equity_arr untouched preserves the existing accounting
        # invariants while letting trade-level metrics see the
        # terminal position.
        #
        # Use last bar's adj_close as the theoretical exit price.
        # No slippage/commission is charged into equity because the
        # trade is virtual — but the TradeRecord's pnl and commission
        # fields are populated using the inner matcher (minus market
        # rules wrapper) so profit_factor / avg_holding_days / win_rate
        # reflect realistic cost impact.
        if shares > 0 and entry_time is not None and n > 0:
            liq_idx = n - 1
            liq_price = prices[liq_idx]
            if not np.isnan(liq_price) and liq_price > 0:
                # Unwrap MarketRulesMatcher (skip T+1/limit/lot for terminal
                # close; keep _SellSideTaxMatcher + base commission layer).
                try:
                    from ez.core.market_rules import MarketRulesMatcher
                    inner_matcher = matcher
                    while isinstance(inner_matcher, MarketRulesMatcher):
                        inner_matcher = inner_matcher._inner
                except ImportError:
                    inner_matcher = matcher
                fill = inner_matcher.fill_sell(liq_price, shares)
                if fill.shares > 0:
                    virtual_pnl = (fill.fill_price - entry_price) * fill.shares - fill.commission
                    total_pnl = partial_pnl + virtual_pnl - entry_comm
                    total_comm = entry_comm + partial_comm + fill.commission
                    # V2.12.2 codex round 6 reviewer sibling miss: use
                    # cycle_peak_invested here too. Prior version used the
                    # stale `entry_price * peak_shares + entry_comm` formula
                    # which diverges from the main-loop close path for
                    # held-to-end strategies that reinforced their position
                    # before the terminal bar (buy → partial sell → buy →
                    # held to end). The main-loop close path got fixed in
                    # round 6 but this terminal path was not updated in the
                    # same commit — reviewer caught it.
                    cost_basis = cycle_peak_invested if cycle_peak_invested > 0 else (
                        (entry_price * peak_shares + entry_comm) if peak_shares > 0
                        else (entry_price * fill.shares + entry_comm)
                    )
                    trades.append(TradeRecord(
                        entry_time=entry_time,
                        exit_time=time_list[liq_idx],
                        entry_price=entry_price,
                        exit_price=fill.fill_price,
                        weight=prev_weight,
                        pnl=total_pnl,
                        pnl_pct=total_pnl / cost_basis if cost_basis > 0 else 0,
                        commission=total_comm,
                    ))

        equity = pd.Series(equity_arr, index=df.index)
        daily_returns = pd.Series(daily_ret, index=df.index)
        return equity, trades, daily_returns
