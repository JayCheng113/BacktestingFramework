"""Tests for V2.10 WF1+WF2: Portfolio Walk-Forward + Significance."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import MomentumRank
from ez.portfolio.engine import CostModel
from ez.portfolio.portfolio_strategy import TopNRotation, PortfolioStrategy
from ez.portfolio.universe import Universe
from ez.portfolio.walk_forward import (
    portfolio_walk_forward,
    portfolio_significance,
    PortfolioWFResult,
    PortfolioSignificanceResult,
)


def _make_data(n_stocks=10, n_days=500, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    data = {}
    for i in range(n_stocks):
        prices = 10 * np.cumprod(1 + rng.normal(0.0005 * (i + 1), 0.015, n_days))
        data[f"S{i:02d}"] = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
        }, index=dates)
    cal = TradingCalendar.from_dates([d.date() for d in dates])
    universe = Universe([f"S{i:02d}" for i in range(n_stocks)])
    return data, cal, universe, dates


class TestPortfolioWalkForward:

    def test_basic_wf(self):
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, train_ratio=0.7, freq="monthly",
        )
        assert isinstance(result, PortfolioWFResult)
        assert result.n_splits == 3
        assert len(result.is_sharpes) == 3
        assert len(result.oos_sharpes) == 3
        assert len(result.oos_equity_curve) > 0

    def test_degradation_computed(self):
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3,
        )
        assert np.isfinite(result.degradation)
        assert result.overfitting_score >= 0

    def test_oos_metrics(self):
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3,
        )
        assert "sharpe_ratio" in result.oos_metrics
        assert "total_return" in result.oos_metrics

    def test_too_few_splits_raises(self):
        data, cal, universe, dates = _make_data()
        with pytest.raises(ValueError, match="n_splits must be >= 2"):
            portfolio_walk_forward(
                strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
                universe=universe, universe_data=data, calendar=cal,
                start=dates[60].date(), end=dates[-1].date(),
                n_splits=1,
            )

    def test_invalid_train_ratio(self):
        data, cal, universe, dates = _make_data()
        with pytest.raises(ValueError, match="train_ratio"):
            portfolio_walk_forward(
                strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
                universe=universe, universe_data=data, calendar=cal,
                start=dates[60].date(), end=dates[-1].date(),
                train_ratio=0.0,
            )

    def test_too_many_splits_raises(self):
        data, cal, universe, dates = _make_data(n_days=100)
        with pytest.raises(ValueError, match="OOS window too short"):
            portfolio_walk_forward(
                strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
                universe=universe, universe_data=data, calendar=cal,
                start=dates[30].date(), end=dates[-1].date(),
                n_splits=20,
            )

    def test_fresh_strategy_per_fold(self):
        """Each fold must get a fresh strategy (no state leakage)."""
        call_count = [0]
        data, cal, universe, dates = _make_data()

        def factory():
            call_count[0] += 1
            return TopNRotation(MomentumRank(20), top_n=3)

        portfolio_walk_forward(
            strategy_factory=factory,
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3,
        )
        # 3 splits × 2 (IS + OOS) = 6 calls
        assert call_count[0] == 6


class TestPortfolioSignificance:

    def test_basic_significance(self):
        data, cal, universe, dates = _make_data()
        from ez.portfolio.engine import run_portfolio_backtest
        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            freq="monthly",
        )
        sig = portfolio_significance(result.equity_curve, seed=42)
        assert isinstance(sig, PortfolioSignificanceResult)
        assert sig.sharpe_ci_lower <= sig.sharpe_ci_upper
        assert 0 <= sig.monte_carlo_p_value <= 1
        assert np.isfinite(sig.observed_sharpe)

    def test_short_equity_curve(self):
        sig = portfolio_significance([100000, 100100, 100050], seed=42)
        assert sig.monte_carlo_p_value == 1.0
        assert not sig.is_significant

    def test_deterministic_with_seed(self):
        eq = list(np.cumprod(1 + np.random.default_rng(42).normal(0.001, 0.01, 200)) * 100000)
        s1 = portfolio_significance(eq, seed=99)
        s2 = portfolio_significance(eq, seed=99)
        assert s1.sharpe_ci_lower == s2.sharpe_ci_lower
        assert s1.monte_carlo_p_value == s2.monte_carlo_p_value
