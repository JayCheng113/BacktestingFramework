"""V2.18.1: Engine dividend handling tests.

Verifies that the unified adj_close unit system correctly handles ETF dividends:
1. Long-hold strategies tracking dividend-paying assets are not underestimated
2. use_open_price=True uses adj_open (open × adj_close/close), not raw open
3. Benchmark curves reflect total return (adj_close), not raw price
4. The equity curve stays continuous across dividend ex-dates
"""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.engine import CostModel, run_portfolio_backtest
from ez.portfolio.portfolio_strategy import PortfolioStrategy
from ez.portfolio.universe import Universe


class BuyAndHold(PortfolioStrategy):
    """100% allocation to a single symbol, no rebalancing."""

    lookback_days = 1

    def __init__(self, symbol: str):
        super().__init__()
        self.symbol = symbol

    def generate_weights(self, universe_data, dt, prev_weights, prev_returns):
        if self.symbol in universe_data:
            return {self.symbol: 1.0}
        return {}


def _make_data_with_dividend(n_days: int = 100, dividend_day: int = 50,
                              dividend_pct: float = 0.5) -> tuple[dict, list[pd.Timestamp]]:
    """Synthetic ETF data with a single dividend event.

    Simulates a dividend at `dividend_day` where:
    - raw close drops by `dividend_pct` (50% by default)
    - adj close continues smoothly (total return unaffected)
    - factor changes: before ex-date factor=0.5, after ex-date factor=1.0
      (Tushare qfq convention: latest_factor=1, earlier factors compensate
      for subsequent dividends/splits)
    """
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    # True underlying "total return" price (smooth random walk)
    rng = np.random.default_rng(42)
    true_prices = 10 * np.cumprod(1 + rng.normal(0.0005, 0.01, n_days))

    raw_close = true_prices.copy()
    adj_close = true_prices.copy()

    # Apply dividend: raw drops, adj continues
    # Before dividend: raw = adj × (1 / (1 - dividend_pct))
    # After dividend: raw = adj (latest factor = 1)
    raw_close[:dividend_day] = adj_close[:dividend_day] * (1 / (1 - dividend_pct))

    # Opens: approximate as close[i-1] * small_noise for day i
    open_prices = np.empty(n_days)
    open_prices[0] = raw_close[0] * 0.999
    for i in range(1, n_days):
        open_prices[i] = raw_close[i - 1] * (1 + rng.normal(0, 0.001))
    # On dividend day, open should also reflect the ex-dividend adjustment
    open_prices[dividend_day] = raw_close[dividend_day] * (1 + rng.normal(0, 0.001))

    df = pd.DataFrame({
        "open": open_prices,
        "high": raw_close * 1.005,
        "low": raw_close * 0.995,
        "close": raw_close,
        "adj_close": adj_close,
        "volume": np.full(n_days, 1_000_000, dtype=np.int64),
    }, index=dates)
    return {"ETF1": df}, list(dates)


class TestDividendEquityContinuity:
    """Holding equity should be continuous across ex-dividend day."""

    def test_buy_and_hold_no_use_open_price(self):
        """Buy and hold without use_open_price — baseline behavior."""
        data, dates = _make_data_with_dividend(n_days=100, dividend_day=50,
                                                dividend_pct=0.5)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(["ETF1"])

        result = run_portfolio_backtest(
            strategy=BuyAndHold("ETF1"),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[1].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            skip_terminal_liquidation=True, use_open_price=False,
        )

        # Equity at end should reflect adj_close total return, not raw close drop
        assert len(result.equity_curve) > 50
        initial = result.equity_curve[0]
        final = result.equity_curve[-1]
        # Use adj_close to verify expected return
        adj_first = data["ETF1"]["adj_close"].iloc[1]
        adj_last = data["ETF1"]["adj_close"].iloc[-1]
        expected_ret = adj_last / adj_first
        actual_ret = final / initial
        # Within 5% tolerance (for lot rounding + commissions)
        assert abs(actual_ret - expected_ret) / expected_ret < 0.05, (
            f"Expected {expected_ret:.3f}, got {actual_ret:.3f}"
        )

    def test_buy_and_hold_use_open_price_TRUE(self):
        """V2.18.1 fix: use_open_price=True must also reflect total return."""
        data, dates = _make_data_with_dividend(n_days=100, dividend_day=50,
                                                dividend_pct=0.5)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(["ETF1"])

        result = run_portfolio_backtest(
            strategy=BuyAndHold("ETF1"),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[1].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            skip_terminal_liquidation=True, use_open_price=True,
        )

        # Must get SAME result as use_open_price=False for buy-and-hold
        # (the open price differs only by a few bps, not 50%)
        adj_first = data["ETF1"]["adj_close"].iloc[1]
        adj_last = data["ETF1"]["adj_close"].iloc[-1]
        expected_ret = adj_last / adj_first
        actual_ret = result.equity_curve[-1] / result.equity_curve[0]
        assert abs(actual_ret - expected_ret) / expected_ret < 0.05, (
            f"use_open_price=True regression: expected {expected_ret:.3f}, "
            f"got {actual_ret:.3f}"
        )

    def test_equity_continuous_across_dividend_day(self):
        """Equity curve should not jump on ex-dividend day."""
        data, dates = _make_data_with_dividend(n_days=100, dividend_day=50,
                                                dividend_pct=0.5)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(["ETF1"])

        result = run_portfolio_backtest(
            strategy=BuyAndHold("ETF1"),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[1].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            skip_terminal_liquidation=True, use_open_price=True,
        )

        # Find index of dividend day in equity curve
        target_date = dates[50].date()
        try:
            div_idx = result.dates.index(target_date)
        except ValueError:
            # Try finding closest
            div_idx = min(range(len(result.dates)),
                          key=lambda i: abs((result.dates[i] - target_date).days))

        if div_idx > 0 and div_idx < len(result.equity_curve):
            before = result.equity_curve[div_idx - 1]
            on_day = result.equity_curve[div_idx]
            # Should NOT drop by ~50% on dividend day
            rel_change = (on_day - before) / before
            assert abs(rel_change) < 0.05, (
                f"Equity jumped {rel_change*100:.1f}% on dividend day "
                f"({before:.0f} → {on_day:.0f})"
            )


class TestUseOpenPriceConsistency:
    """use_open_price=True and False should agree on dividend-free data."""

    def test_no_dividend_use_open_consistency(self):
        """Without dividends, use_open_price=True should give nearly identical
        results to use_open_price=False (differ only by open vs close price)."""
        # Plain random walk, no factor split
        rng = np.random.default_rng(42)
        n_days = 100
        dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
        prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.01, n_days))

        df = pd.DataFrame({
            "open": prices * 0.999,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices, "adj_close": prices,  # no dividend: adj == raw
            "volume": np.full(n_days, 1_000_000, dtype=np.int64),
        }, index=dates)
        data = {"S1": df}

        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(["S1"])

        r_close = run_portfolio_backtest(
            strategy=BuyAndHold("S1"),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[1].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            skip_terminal_liquidation=True, use_open_price=False,
        )
        r_open = run_portfolio_backtest(
            strategy=BuyAndHold("S1"),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[1].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            skip_terminal_liquidation=True, use_open_price=True,
        )

        # Final equity should match within 1% (only small difference from
        # open vs close price on entry day)
        final_close = r_close.equity_curve[-1]
        final_open = r_open.equity_curve[-1]
        rel_diff = abs(final_close - final_open) / final_close
        assert rel_diff < 0.01, (
            f"use_open_price=True differs from False by {rel_diff*100:.2f}%"
        )


class TestAdjOpenCalculation:
    """V2.18.1: verify adj_open = open × (adj_close / close)."""

    def test_adj_open_reflects_factor(self):
        """When adj_close/close ratio is not 1 (active factor), exec_price
        for use_open_price=True should be adj_open, not raw open."""
        # Synthetic ETF with factor = 0.5 throughout (active dividends before
        # the test period; no dividend events during test)
        n_days = 50
        dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
        rng = np.random.default_rng(0)
        raw = 10 * np.cumprod(1 + rng.normal(0.001, 0.01, n_days))
        adj = raw * 0.5  # factor = 0.5 (i.e. adj is half of raw)
        opens = raw * 0.998  # raw opens, ~0.2% lower than close

        df = pd.DataFrame({
            "open": opens,
            "high": raw * 1.005, "low": raw * 0.995,
            "close": raw, "adj_close": adj,
            "volume": np.full(n_days, 1_000_000, dtype=np.int64),
        }, index=dates)
        data = {"ETF1": df}

        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(["ETF1"])

        result = run_portfolio_backtest(
            strategy=BuyAndHold("ETF1"),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[1].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            skip_terminal_liquidation=True, use_open_price=True,
        )

        # Final equity must be in adj scale (not raw scale)
        # Expected return: adj[-1] / adj[1] ≈ raw[-1] / raw[1] (scale invariant)
        expected_ret = adj[-1] / adj[1]
        actual_ret = result.equity_curve[-1] / result.equity_curve[0]
        assert abs(actual_ret - expected_ret) / expected_ret < 0.03


class TestBenchmarkTracksAdjClose:
    """Benchmark curve must represent buy-and-hold total return (adj_close)."""

    def test_benchmark_uses_adj_not_raw(self):
        """On data with active factor (adj != raw), benchmark curve must
        follow adj_close returns, not raw_close returns."""
        n_days = 50
        dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
        rng = np.random.default_rng(1)
        # Make all series have a factor
        universe_data = {}
        for sym, noise_scale in [("BMK", 0.008), ("ASSET", 0.012)]:
            raw = 10 * np.cumprod(1 + rng.normal(0.001, noise_scale, n_days))
            df = pd.DataFrame({
                "open": raw * 0.999,
                "high": raw * 1.005, "low": raw * 0.995,
                "close": raw, "adj_close": raw * 0.6,  # factor=0.6
                "volume": np.full(n_days, 1_000_000, dtype=np.int64),
            }, index=dates)
            universe_data[sym] = df

        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(["ASSET"])

        result = run_portfolio_backtest(
            strategy=BuyAndHold("ASSET"),
            universe=universe, universe_data=universe_data, calendar=cal,
            start=dates[1].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            skip_terminal_liquidation=True,
            benchmark_symbol="BMK",
        )

        # benchmark_curve should exist and match adj_close returns of BMK
        assert result.benchmark_curve, "benchmark_curve should be populated"
        bmk_adj = universe_data["BMK"]["adj_close"]

        # First benchmark value / expected first adj_close ratio equals
        # last benchmark / last adj_close ratio (buy-and-hold)
        bmk_first = result.benchmark_curve[0]
        bmk_last = result.benchmark_curve[-1]
        # Match against adj_close in the tracked range
        first_date = result.dates[0]
        last_date = result.dates[-1]
        adj_first = bmk_adj.loc[bmk_adj.index >= pd.Timestamp(first_date)].iloc[0]
        adj_last = bmk_adj.loc[bmk_adj.index <= pd.Timestamp(last_date)].iloc[-1]

        expected_ret = float(adj_last / adj_first)
        actual_ret = bmk_last / bmk_first

        assert abs(actual_ret - expected_ret) / expected_ret < 0.02, (
            f"Benchmark tracks wrong price: expected ret {expected_ret:.4f}, "
            f"got {actual_ret:.4f}"
        )
