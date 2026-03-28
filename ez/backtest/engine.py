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
        commission_rate: float = 0.0003,
        min_commission: float = 5.0,
        risk_free_rate: float = 0.03,
        matcher: Matcher | None = None,
    ):
        self._matcher = matcher or SimpleMatcher(commission_rate, min_commission)
        self._metrics = MetricsCalculator(risk_free_rate=risk_free_rate)

    def run(
        self,
        data: pd.DataFrame,
        strategy: Strategy,
        initial_capital: float = 100000.0,
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
            metrics["win_rate"] = len(wins) / len(trades) if trades else 0.0
            metrics["trade_count"] = len(trades)
            avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0.0
            losses = [t for t in trades if t.pnl <= 0]
            avg_loss = abs(np.mean([t.pnl_pct for t in losses])) if losses else 1.0
            metrics["profit_factor"] = avg_win / avg_loss if avg_loss > 0 else float("inf")
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
        daily_ret = np.zeros(n)

        times = df.index if hasattr(df.index, "__iter__") else range(n)
        time_list = list(times)
        matcher = self._matcher

        for i in range(1, n):
            target_weight = weights[i] if i < len(weights) else 0.0
            exec_price = open_prices[i]

            if abs(target_weight - prev_weight) > 1e-6:
                current_equity = cash + shares * exec_price
                target_value = current_equity * target_weight
                current_value = shares * exec_price

                if target_value < current_value and shares > 0:
                    # Reduce or close position
                    if target_weight == 0:
                        sell_shares = shares
                    else:
                        sell_shares = (current_value - target_value) / exec_price

                    fill = matcher.fill_sell(exec_price, sell_shares)
                    cash += fill.net_amount
                    old_shares = shares
                    shares -= fill.shares
                    if shares < 1e-10:
                        shares = 0.0
                    # Record trade when fully closing
                    if old_shares > 0 and shares < 1e-10 and entry_time is not None:
                        pnl = (fill.fill_price - entry_price) * old_shares - fill.commission - entry_comm
                        trades.append(TradeRecord(
                            entry_time=entry_time,
                            exit_time=time_list[i],
                            entry_price=entry_price,
                            exit_price=fill.fill_price,
                            weight=prev_weight,
                            pnl=pnl,
                            pnl_pct=pnl / (entry_price * old_shares) if entry_price * old_shares > 0 else 0,
                            commission=fill.commission + entry_comm,
                        ))
                        entry_time = None
                        entry_comm = 0.0

                elif target_value > current_value:
                    # Increase or open position
                    additional = min(target_value - current_value, cash)
                    if additional > 0:
                        fill = matcher.fill_buy(exec_price, additional)
                        if fill.shares == 0:
                            # Commission would exceed trade value — skip
                            continue
                        if shares == 0:
                            entry_time = time_list[i]
                            entry_price = fill.fill_price
                            entry_comm = fill.commission
                        elif fill.shares > 0:
                            entry_comm += fill.commission
                            # Weighted average entry price
                            entry_price = (entry_price * shares + fill.fill_price * fill.shares) / (shares + fill.shares)
                        shares += fill.shares
                        cash += fill.net_amount  # net_amount is negative for buys

                prev_weight = target_weight

            position_value = shares * prices[i]
            equity_arr[i] = cash + position_value
            if equity_arr[i - 1] > 0:
                daily_ret[i] = (equity_arr[i] / equity_arr[i - 1]) - 1

        equity = pd.Series(equity_arr, index=df.index)
        daily_returns = pd.Series(daily_ret, index=df.index)
        return equity, trades, daily_returns
