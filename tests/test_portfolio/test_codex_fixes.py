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

        cost = CostModel(commission_rate=0.001, min_commission=5.0, stamp_tax_rate=0, slippage_rate=0)
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
