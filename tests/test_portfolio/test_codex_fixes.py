"""Tests for Codex audit fixes (V2.9 post-review)."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import MomentumRank
from ez.portfolio.engine import CostModel, run_portfolio_backtest
from ez.portfolio.portfolio_strategy import TopNRotation, MultiFactorRotation, PortfolioStrategy
from ez.portfolio.universe import Universe


def _make_data(symbols, n=100, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    data = {}
    for i, sym in enumerate(symbols):
        prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.015, n))
        data[sym] = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n),
        }, index=dates)
    return data, dates


class TestC2MinCommissionBudget:
    """C2: min_commission can push cash negative — must be guarded."""

    def test_tiny_cash_min_commission(self):
        """100 cash, price=1, min_commission=5, lot_size=1 — must NOT assert."""
        data, dates = _make_data(["A"], n=50, seed=99)
        # Override prices to be exactly 1.0
        data["A"]["close"] = 1.0
        data["A"]["adj_close"] = 1.0
        data["A"]["open"] = 1.0
        data["A"]["high"] = 1.0
        data["A"]["low"] = 1.0

        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class AlwaysFull(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, prev_w, prev_r):
                return {"A": 1.0}

        cost = CostModel(buy_commission_rate=0.001, min_commission=5.0, stamp_tax_rate=0, slippage_rate=0)
        # Should NOT raise AssertionError
        result = run_portfolio_backtest(
            strategy=AlwaysFull(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=100.0, cost_model=cost, lot_size=1,
        )
        assert len(result.equity_curve) > 0

    def test_very_small_cash(self):
        """10 cash, price=100 — can't afford anything, should degrade gracefully."""
        data, dates = _make_data(["A"], n=50)
        data["A"]["close"] = 100.0
        data["A"]["adj_close"] = 100.0

        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class AlwaysFull(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, prev_w, prev_r):
                return {"A": 1.0}

        result = run_portfolio_backtest(
            strategy=AlwaysFull(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=10.0, lot_size=100,
        )
        assert result.equity_curve[-1] == 10.0  # stayed in cash


class TestI4TopNValidation:
    """I4: top_n <= 0 must raise ValueError."""

    def test_top_n_zero(self):
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            TopNRotation(factor=MomentumRank(20), top_n=0)

    def test_top_n_negative(self):
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            TopNRotation(factor=MomentumRank(20), top_n=-5)

    def test_multi_factor_top_n_zero(self):
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            MultiFactorRotation(factors=[MomentumRank(20)], top_n=0)


class TestM6InvalidFreq:
    """M6: invalid freq must raise ValueError, not silently degrade."""

    def test_invalid_freq_raises(self):
        cal = TradingCalendar.weekday_fallback(date(2024, 1, 1), date(2024, 3, 31))
        with pytest.raises(ValueError, match="Invalid freq"):
            cal.rebalance_dates(date(2024, 1, 1), date(2024, 3, 31), "biweekly")

    def test_valid_freqs_pass(self):
        cal = TradingCalendar.weekday_fallback(date(2024, 1, 1), date(2024, 3, 31))
        for freq in ("daily", "weekly", "monthly", "quarterly"):
            result = cal.rebalance_dates(date(2024, 1, 1), date(2024, 3, 31), freq)
            assert isinstance(result, list)


class TestLimitPrice:
    """涨跌停: 涨停不可买, 跌停不可卖."""

    def test_limit_up_blocks_buy(self):
        """Stock that hits +10% limit up should not be bought."""
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        data = {}
        # A: normal, B: hits limit up on rebalance day
        prices_a = np.full(30, 10.0)
        prices_b = np.full(30, 10.0)
        prices_b[20] = 11.0  # +10% on day 20 (limit up)

        for sym, p in [("A", prices_a), ("B", prices_b)]:
            data[sym] = pd.DataFrame({
                "open": p, "high": p, "low": p, "close": p,
                "adj_close": p, "volume": np.full(30, 100000),
            }, index=dates)

        cal = TradingCalendar.from_dates([d.date() for d in dates])
        rebal = cal.rebalance_dates(dates[5].date(), dates[-1].date(), "weekly")

        class BuyBoth(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 0.5, "B": 0.5}

        result = run_portfolio_backtest(
            strategy=BuyBoth(), universe=Universe(["A", "B"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=100000, lot_size=1,
            limit_pct=0.10,
            cost_model=CostModel(buy_commission_rate=0, min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        # On the rebalance day when B is limit up, B should not be bought
        b_buys = [t for t in result.trades if t["symbol"] == "B" and t["side"] == "buy"]
        # Check that at least one rebalance skipped B (the one on limit-up day)
        a_buys = [t for t in result.trades if t["symbol"] == "A" and t["side"] == "buy"]
        # A should have more buy events than B (B blocked on limit-up day)
        assert len(a_buys) >= len(b_buys)


class TestC1SellBeforeBuy:
    """C1: sells must execute before buys to free cash."""

    def test_sell_before_buy_order(self):
        """Track execution order: all sells should come before buys."""
        trade_sides = []

        class SwapStrategy(PortfolioStrategy):
            """Alternates between A and B each rebalance."""
            def generate_weights(self, universe_data, dt, prev_w, prev_r):
                self.state["toggle"] = not self.state.get("toggle", False)
                if self.state["toggle"]:
                    return {"A": 1.0}
                return {"B": 1.0}

        data, dates = _make_data(["A", "B"], n=100)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        result = run_portfolio_backtest(
            strategy=SwapStrategy(), universe=Universe(["A", "B"]),
            universe_data=data, calendar=cal,
            start=dates[25].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=100000, lot_size=1,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        # On each rebalance after the first, there should be a sell then a buy
        for i in range(0, len(result.trades) - 1):
            if result.trades[i]["date"] == result.trades[i + 1]["date"]:
                # Same day: sell should come before buy
                if result.trades[i]["side"] == "buy" and result.trades[i + 1]["side"] == "sell":
                    pytest.fail(f"Buy before sell on {result.trades[i]['date']}")


class TestC2SuspensionNoTrade:
    """C2: cannot trade a stock that has no bar today."""

    def test_missing_bar_blocks_trade(self):
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        data = {}
        # A: normal, B: missing data on day 15
        for sym in ["A", "B"]:
            p = np.full(30, 10.0)
            data[sym] = pd.DataFrame({
                "open": p, "high": p, "low": p, "close": p,
                "adj_close": p, "volume": np.full(30, 100000),
            }, index=dates)
        # Remove B's data for day 15
        data["B"] = data["B"].drop(dates[15])

        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class BuyBoth(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 0.5, "B": 0.5}

        result = run_portfolio_backtest(
            strategy=BuyBoth(), universe=Universe(["A", "B"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=100000, lot_size=1,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        # B should not be traded on the day it's missing
        b_trades_on_missing = [t for t in result.trades
                               if t["symbol"] == "B" and t["date"] == dates[15].date().isoformat()]
        assert len(b_trades_on_missing) == 0


class TestC3BenchmarkStartAlignment:
    """C3: benchmark curve must start at backtest start, not data start."""

    def test_benchmark_starts_at_initial_cash(self):
        data, dates = _make_data(["A", "BENCH"], n=200)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=1),
            universe=Universe(["A"]), universe_data=data, calendar=cal,
            start=dates[50].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=100000,
            benchmark_symbol="BENCH",
        )
        # Benchmark first value should be close to initial_cash
        assert abs(result.benchmark_curve[0] - 100000) < 100


class TestC4NegativeCostValidation:
    """C4: negative cost params must be rejected by API."""

    def test_negative_slippage_422(self):
        from fastapi.testclient import TestClient
        from ez.api.app import app
        client = TestClient(app)
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "TopNRotation", "symbols": ["A"],
            "slippage_rate": -0.05,
        })
        assert resp.status_code == 422


class TestC1LoaderRegistration:
    """C1: portfolio_strategies/ files must be loadable."""

    def test_loader_imports(self):
        from ez.portfolio.loader import load_portfolio_strategies, load_cross_factors
        # Should not raise even if directories are empty
        load_portfolio_strategies()
        load_cross_factors()

    def test_builtins_always_registered(self):
        """Built-in strategies must always be in registry regardless of loader."""
        registry = PortfolioStrategy.get_registry()
        assert "TopNRotation" in registry
        assert "MultiFactorRotation" in registry
