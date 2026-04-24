"""V2.16.2 regression: single-stock backtest engine unit consistency on dividend days.

Parity with V2.18.1 portfolio engine fix. Prior single-stock engine
mixed price units: executed at raw open (`df["open"]`) but valued at
adj close (`df["adj_close"]`). On dividend days where adj_close >
raw_close (post-dividend drop absorbed into adjustment factor), this
was incoherent:

- Buy strategy on dividend day:
  - Buy shares = additional_cash / raw_open   (lots of shares, low price)
  - End-of-day value = shares * adj_close      (high price)
  - -> phantom profit of (adj_close - raw_open) * shares from nothing

- Sell strategy on dividend day:
  - Sell at raw_open, receive raw_open * shares cash
  - -> under-captures the dividend-adjusted value

Fix: adj_open = raw_open * (adj_close / raw_close). On non-dividend
bars adj_close == raw_close so adj_open == raw_open (no-op). On
dividend bars, adj_open scales proportionally to adj_close, restoring
unit consistency across execution and valuation.

This test uses synthetic data with a hard-coded ETF-style dividend:
day T has raw_close drop -50% (5 -> 2.5) but adj_close stays at 5.
Strategy = always-long (weight=1.0). Without the fix, equity would
show phantom gains/losses around the dividend day. With the fix,
equity evolves smoothly.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ez.backtest.engine import VectorizedBacktestEngine


def _always_long_signals(n: int) -> pd.Series:
    """Signal series: 100% long from day 1 onwards."""
    return pd.Series([1.0] * n, dtype=float)


def _flat_data_with_dividend(n_days: int = 20, div_day: int = 10) -> pd.DataFrame:
    """Flat raw prices with a -50% drop on `div_day` (dividend), plus
    matching adj_close that stays at the pre-dividend level.

    Raw:  5, 5, 5, ..., 5, 2.5, 2.5, ..., 2.5
    Adj:  5, 5, 5, ...,  5,   5,   5, ...,   5   (dividend absorbed)
    """
    raw = np.full(n_days, 5.0)
    raw[div_day:] = 2.5
    adj = np.full(n_days, 5.0)
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n_days)]
    df = pd.DataFrame({
        "open": raw,   # open == close for flat bars
        "high": raw,
        "low": raw,
        "close": raw,  # raw close
        "adj_close": adj,  # dividend-adjusted
        "volume": np.full(n_days, 1000),
    }, index=pd.to_datetime(dates))
    return df


class _AlwaysLongStrategy:
    """Strategy that always returns signal 1.0 (target 100% long)."""
    def required_factors(self):
        return []

    def generate_signals(self, df):
        return pd.Series([1.0] * len(df), index=df.index, dtype=float)


class _BuyOnDayStrategy:
    """Strategy that buys on a specific bar index (by issuing target=1 at
    that bar — engine's shift(1) means the actual trade happens on the
    NEXT bar). Used to pin down behavior on a specific trading day."""
    def __init__(self, buy_day_idx: int):
        self.buy_day_idx = buy_day_idx

    def required_factors(self):
        return []

    def generate_signals(self, df):
        sig = pd.Series([0.0] * len(df), index=df.index, dtype=float)
        # Signal on buy_day_idx - 1 → shifts to buy_day_idx
        if self.buy_day_idx - 1 >= 0:
            sig.iloc[self.buy_day_idx - 1:] = 1.0
        return sig


def test_buy_on_dividend_day_no_phantom_gain() -> None:
    """V2.16.2 CRITICAL canary — THIS is the bug-path test.

    Strategy: no position days 0-9, buy 100% long on day 10 (dividend
    day), hold. Prior bug: execution at raw_open=2.5, shares bought =
    cash/2.5 (lots of shares); end-of-day valuation at adj_close=5
    doubles the equity out of thin air (phantom +100% return).

    With the fix (adj_open = raw_open * adj_close/raw_close = 2.5 * 2 = 5):
    shares bought at adj_open=5, equity end-of-day unchanged, return
    near 0 (just commission drag — 0 here).

    Revert the fix in ez/backtest/engine.py and this test FAILS — that
    is the mutation-verified canary.
    """
    div_day = 10
    df = _flat_data_with_dividend(n_days=20, div_day=div_day)
    engine = VectorizedBacktestEngine(commission_rate=0.0, min_commission=0.0)
    strategy = _BuyOnDayStrategy(buy_day_idx=div_day)
    result = engine.run(df, strategy, initial_capital=100_000)

    equity = result.equity_curve.values
    # Days 0-9: no position, equity stays at 100_000
    # Day 10: buy on dividend day
    # After fix: adj_open == adj_close on div day, equity stays at ~100_000
    # Before fix: buy at raw_open (half price), value at adj_close → ~200_000
    day_10_equity = equity[div_day]
    initial = equity[0]

    assert 99_000 <= day_10_equity <= 101_000, (
        f"Buy on dividend day should NOT create phantom gain. "
        f"Expected ~{initial}, got {day_10_equity}. Prior bug path "
        f"would produce ~{initial * 2} (raw_open execution + adj_close "
        f"valuation)."
    )


def test_buy_on_dividend_day_no_phantom_gain_when_open_is_missing() -> None:
    """Missing `open` must fall back to raw close before adjustment.

    Prior bug path: if `open` was missing, the engine used `adj_close`
    as `_raw_open`, then multiplied by `(adj_close / raw_close)` again.
    On a 5 / 2.5 dividend bar that turned the execution price into 10.0.
    """
    div_day = 10
    df = _flat_data_with_dividend(n_days=20, div_day=div_day).drop(columns=["open"])
    engine = VectorizedBacktestEngine(commission_rate=0.0, min_commission=0.0)
    strategy = _BuyOnDayStrategy(buy_day_idx=div_day)
    result = engine.run(df, strategy, initial_capital=100_000)

    day_10_equity = result.equity_curve.values[div_day]
    initial = result.equity_curve.values[0]

    assert 99_000 <= day_10_equity <= 101_000, (
        f"Buy on dividend day without open should NOT create phantom gain. "
        f"Expected ~{initial}, got {day_10_equity}."
    )


def test_no_behavior_change_on_non_dividend_data() -> None:
    """Sanity: data where raw_close == adj_close (no dividends) must
    produce the same result as before the fix. adj_open = raw_open *
    (adj_close/raw_close) = raw_open * 1 = raw_open."""
    n = 30
    raw = np.linspace(10.0, 12.0, n)  # smooth trend, no dividends
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    df = pd.DataFrame({
        "open": raw, "high": raw, "low": raw,
        "close": raw, "adj_close": raw,  # identical: no dividends
        "volume": np.full(n, 1000),
    }, index=pd.to_datetime(dates))

    engine = VectorizedBacktestEngine(commission_rate=0.0, min_commission=0.0)
    strategy = _AlwaysLongStrategy()
    result = engine.run(df, strategy, initial_capital=100_000)

    # Expected: bought in bar 1 at ~raw[1], held to end. Final equity
    # approximately matches raw[-1]/raw[1] * initial.
    final_return = result.equity_curve.iloc[-1] / 100_000 - 1
    expected_return = raw[-1] / raw[1] - 1  # bought at bar 1
    assert abs(final_return - expected_return) < 0.01, (
        f"On non-dividend data expected ~{expected_return:.3f} "
        f"got {final_return:.3f}"
    )


def test_nan_close_does_not_break_ratio() -> None:
    """If raw close is NaN or zero for any bar, the ratio falls back to
    1.0 (adj_open = raw_open). Must not crash or produce NaN ratios
    that poison downstream equity math."""
    n = 15
    raw_close = np.full(n, 5.0)
    raw_close[5] = np.nan  # suspension gap
    raw_close[8] = 0.0     # data glitch
    adj_close = np.full(n, 5.0)
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    df = pd.DataFrame({
        "open": np.full(n, 5.0),
        "high": raw_close, "low": raw_close,
        "close": raw_close,
        "adj_close": adj_close,
        "volume": np.full(n, 1000),
    }, index=pd.to_datetime(dates))

    engine = VectorizedBacktestEngine(commission_rate=0.0)
    strategy = _AlwaysLongStrategy()
    # Must not raise; NaN guard in engine handles missing bars
    result = engine.run(df, strategy, initial_capital=100_000)
    # Final equity should be finite
    assert np.isfinite(result.equity_curve.iloc[-1])
