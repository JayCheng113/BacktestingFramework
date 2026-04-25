"""Core data models for ez-trading. All modules import types from here.

This file MUST NOT import from any ez submodule to avoid circular dependencies.
[CORE] — interface frozen after V1. Append-only: new fields must have defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class Bar:
    """Single OHLCV bar."""
    time: datetime          # bar timestamp (date for daily data, datetime for intraday)
    symbol: str             # exchange-qualified symbol, e.g. '000001.SZ' or '600000.SH'
    market: str             # market identifier, e.g. 'CN', 'US', 'HK'
    open: float             # raw open price (not adjusted)
    high: float             # raw high price (not adjusted)
    low: float              # raw low price (not adjusted)
    close: float            # raw close price; use for limit-up/down rule checks
    adj_close: float        # dividend/split-adjusted close; use for return calculations and factor inputs
    volume: int             # trading volume in shares (not lots)


@dataclass
class TradeRecord:
    """Single completed trade."""
    entry_time: datetime    # bar timestamp when position was opened
    exit_time: datetime     # bar timestamp when position was closed
    entry_price: float      # fill price at entry including slippage (adjusted units)
    exit_price: float       # fill price at exit including slippage (adjusted units)
    weight: float           # target portfolio weight at entry (0.0–1.0, long-only)
    pnl: float              # realized P&L in currency units, net of all commissions
    pnl_pct: float          # realized P&L as a fraction of peak capital deployed (not initial capital)
    commission: float       # total commission paid for the round-trip (entry + exit)


@dataclass
class SignificanceTest:
    """Statistical significance of backtest results."""
    sharpe_ci_lower: float      # lower bound of bootstrapped Sharpe ratio confidence interval
    sharpe_ci_upper: float      # upper bound of bootstrapped Sharpe ratio confidence interval
    monte_carlo_p_value: float  # p-value: probability of achieving this Sharpe under the null (random)
    is_significant: bool        # True when p_value < significance threshold (typically 0.05)


@dataclass
class BacktestResult:
    """Complete backtest output."""
    equity_curve: pd.Series     # daily portfolio value indexed by date; starts at initial_capital
    benchmark_curve: pd.Series  # daily benchmark value indexed by date; same start as equity_curve
    trades: list[TradeRecord]   # chronological list of completed round-trips; empty list if no trades
    metrics: dict[str, float]   # computed performance metrics (Sharpe, CAGR, max drawdown, etc.)
    signals: pd.Series          # raw strategy signal series indexed by date (pre-execution)
    daily_returns: pd.Series    # daily portfolio return fractions indexed by date
    significance: SignificanceTest  # statistical significance summary; may hold NaN fields if skipped


@dataclass
class FactorAnalysis:
    """Factor evaluation results."""
    ic_series: pd.Series            # daily/periodic Pearson IC between factor value and forward return
    rank_ic_series: pd.Series       # daily/periodic Spearman rank IC (more robust to outliers)
    ic_mean: float                  # mean of ic_series over the evaluation window
    rank_ic_mean: float             # mean of rank_ic_series over the evaluation window
    icir: float                     # IC information ratio: ic_mean / ic_std (signal quality measure)
    rank_icir: float                # rank IC information ratio: rank_ic_mean / rank_ic_std
    ic_decay: dict[int, float]      # forward-lag (in days) -> mean IC; maps decay of predictive power
    turnover: float                 # average daily turnover of factor quintile portfolios (0.0–1.0)
    quintile_returns: pd.DataFrame  # columns are quintile labels; rows are periods; values are returns


@dataclass
class WalkForwardResult:
    """Walk-forward validation output."""
    splits: list[BacktestResult]    # per-fold BacktestResult objects (in-sample + out-of-sample each)
    oos_equity_curve: pd.Series     # concatenated out-of-sample equity curve across all folds
    oos_metrics: dict[str, float]   # aggregate metrics computed on the full oos_equity_curve
    is_vs_oos_degradation: float    # (IS Sharpe - OOS Sharpe) / IS Sharpe; positive = overfitting signal
    overfitting_score: float        # composite overfitting score (0.0 = none, 1.0 = severe)
