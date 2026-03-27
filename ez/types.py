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
    time: datetime
    symbol: str
    market: str
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int


@dataclass
class TradeRecord:
    """Single completed trade."""
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    weight: float
    pnl: float
    pnl_pct: float
    commission: float


@dataclass
class SignificanceTest:
    """Statistical significance of backtest results."""
    sharpe_ci_lower: float
    sharpe_ci_upper: float
    monte_carlo_p_value: float
    is_significant: bool


@dataclass
class BacktestResult:
    """Complete backtest output."""
    equity_curve: pd.Series
    benchmark_curve: pd.Series
    trades: list[TradeRecord]
    metrics: dict[str, float]
    signals: pd.Series
    daily_returns: pd.Series
    significance: SignificanceTest


@dataclass
class FactorAnalysis:
    """Factor evaluation results."""
    ic_series: pd.Series
    rank_ic_series: pd.Series
    ic_mean: float
    rank_ic_mean: float
    icir: float
    rank_icir: float
    ic_decay: dict[int, float]
    turnover: float
    quintile_returns: pd.DataFrame


@dataclass
class WalkForwardResult:
    """Walk-forward validation output."""
    splits: list[BacktestResult]
    oos_equity_curve: pd.Series
    oos_metrics: dict[str, float]
    is_vs_oos_degradation: float
    overfitting_score: float
