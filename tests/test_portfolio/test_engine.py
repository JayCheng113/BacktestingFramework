"""Tests for PortfolioEngine (V2.9 P5) — accounting invariant, anti-lookahead, discrete shares."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.allocator import EqualWeightAllocator, MaxWeightAllocator
from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import MomentumRank
from ez.portfolio.engine import CostModel, PortfolioResult, run_portfolio_backtest
from ez.portfolio.portfolio_strategy import TopNRotation, PortfolioStrategy
from ez.portfolio.universe import Universe


def _make_universe_data(symbols: list[str], n_days: int = 300, seed: int = 42):
    """Generate synthetic OHLCV for testing."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    data = {}
    for i, sym in enumerate(symbols):
        prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.015, n_days))
        data[sym] = pd.DataFrame({
            "open": prices * (1 + rng.normal(0, 0.002, n_days)),
            "high": prices * (1 + abs(rng.normal(0, 0.005, n_days))),
            "low": prices * (1 - abs(rng.normal(0, 0.005, n_days))),
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n_days),
        }, index=dates)
    return data, dates


class TestAccountingInvariant:
    """Codex #4: cash + Σ(shares × price) == equity must hold every day."""

    def test_basic_rotation(self):
        symbols = [f"S{i}" for i in range(10)]
        data, dates = _make_universe_data(symbols)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
        )
        # If invariant violated, engine would have raised AssertionError
        assert len(result.equity_curve) > 0
        assert result.equity_curve[0] == 1_000_000  # no trades on first day if not rebalance

    def test_with_costs(self):
        symbols = [f"S{i}" for i in range(5)]
        data, dates = _make_universe_data(symbols)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        cost = CostModel(commission_rate=0.001, min_commission=5, stamp_tax_rate=0.001, slippage_rate=0.002)
        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=2),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            freq="monthly", cost_model=cost,
        )
        assert len(result.trades) > 0
        total_cost = sum(t["cost"] for t in result.trades)
        assert total_cost > 0  # costs were charged


class TestAntiLookahead:
    """Codex #2: strategy must not see data on or after decision date."""

    def test_strategy_receives_sliced_data(self):
        """A spy strategy records the max date it sees per call."""
        violations = []

        class SpyStrategy(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, prev_w, prev_r):
                rebal_date = dt.date() if hasattr(dt, 'date') else dt
                for sym, df in universe_data.items():
                    if len(df) > 0:
                        if isinstance(df.index, pd.DatetimeIndex):
                            last = df.index[-1].date()
                        else:
                            last = df.index[-1]
                        if last >= rebal_date:
                            violations.append((sym, last, rebal_date))
                return {}

        symbols = ["A", "B"]
        data, dates = _make_universe_data(symbols, n_days=100)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        run_portfolio_backtest(
            strategy=SpyStrategy(), universe=universe, universe_data=data,
            calendar=cal, start=dates[30].date(), end=dates[-1].date(), freq="monthly",
        )

        assert len(violations) == 0, f"Strategy saw future data: {violations[:3]}"


class TestDiscreteShares:
    """Codex #4: weights → shares (lot-size rounded), remainder to cash."""

    def test_shares_are_lot_multiples(self):
        symbols = [f"S{i}" for i in range(5)]
        data, dates = _make_universe_data(symbols)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            freq="monthly", lot_size=100,
        )
        for trade in result.trades:
            assert trade["shares"] % 100 == 0, f"Trade shares {trade['shares']} not lot-aligned"


class TestStatefulStrategy:
    """P3: prev_weights/prev_returns correctly passed, self.state persists."""

    def test_state_persists(self):
        call_count = [0]

        class CountingStrategy(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, prev_w, prev_r):
                self.state["calls"] = self.state.get("calls", 0) + 1
                call_count[0] = self.state["calls"]
                # Simple: equal weight top 2
                syms = list(universe_data.keys())[:2]
                return {s: 0.5 for s in syms} if syms else {}

        symbols = ["A", "B", "C"]
        data, dates = _make_universe_data(symbols, n_days=200)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        result = run_portfolio_backtest(
            strategy=CountingStrategy(), universe=universe, universe_data=data,
            calendar=cal, start=dates[30].date(), end=dates[-1].date(), freq="monthly",
        )
        assert call_count[0] > 1  # called multiple times
        assert call_count[0] == len(result.rebalance_dates)


class TestAllocator:
    def test_max_weight_allocator(self):
        symbols = [f"S{i}" for i in range(20)]
        data, dates = _make_universe_data(symbols)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=10),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            freq="monthly", allocator=MaxWeightAllocator(max_weight=0.15),
        )
        # All weights in history should respect max
        for w_dict in result.weights_history:
            for sym, w in w_dict.items():
                assert w <= 0.16  # small tolerance for price drift


class TestDegradation:
    """Single-stock portfolio ≈ single-stock backtest (退化验证)."""

    def test_single_stock_equity_positive(self):
        """With one stock, portfolio should track that stock's performance."""
        data, dates = _make_universe_data(["ONLY"], n_days=200, seed=99)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(["ONLY"])

        class AlwaysFull(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, prev_w, prev_r):
                return {"ONLY": 1.0}

        result = run_portfolio_backtest(
            strategy=AlwaysFull(), universe=universe, universe_data=data,
            calendar=cal, start=dates[1].date(), end=dates[-1].date(),
            freq="daily", cost_model=CostModel(commission_rate=0, min_commission=0,
                                                stamp_tax_rate=0, slippage_rate=0),
            lot_size=1,  # no lot rounding for degradation test
        )
        assert len(result.equity_curve) > 100
        # Final equity should be close to stock performance
        stock_ret = float(data["ONLY"]["close"].iloc[-1] / data["ONLY"]["close"].iloc[1])
        portfolio_ret = result.equity_curve[-1] / result.equity_curve[0]
        assert abs(portfolio_ret - stock_ret) / stock_ret < 0.02  # within 2%


class TestMetrics:
    def test_metrics_computed(self):
        symbols = [f"S{i}" for i in range(5)]
        data, dates = _make_universe_data(symbols)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(), freq="monthly",
        )
        assert "sharpe_ratio" in result.metrics
        assert "max_drawdown" in result.metrics
        assert "total_return" in result.metrics
        assert "trade_count" in result.metrics
        assert result.metrics["trade_count"] == len(result.trades)
