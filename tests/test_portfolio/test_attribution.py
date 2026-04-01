"""Tests for V2.12 Brinson attribution."""
from datetime import date

import numpy as np
import pandas as pd
import pytest


def _make_attribution_data(symbols, n_days=60, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    data = {}
    for sym in symbols:
        prices = 10 * np.cumprod(1 + rng.normal(0.002, 0.02, n_days))
        data[sym] = pd.DataFrame({"close": prices, "adj_close": prices}, index=dates)
    return data


class TestBrinsonIdentity:
    """allocation + selection + interaction must equal total excess return."""

    def test_single_period_identity(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult

        symbols = ["A", "B", "C"]
        data = _make_attribution_data(symbols)

        result = PortfolioResult(
            rebalance_dates=[date(2023, 1, 2), date(2023, 2, 1), date(2023, 3, 1)],
            weights_history=[
                {"A": 0.5, "B": 0.3, "C": 0.2},
                {"A": 0.4, "B": 0.4, "C": 0.2},
            ],
            trades=[{"cost": 100}, {"cost": 50}],
        )
        industry_map = {"A": "银行", "B": "银行", "C": "食品饮料"}
        attr = compute_attribution(result, data, industry_map, initial_cash=1_000_000)

        for period in attr.periods:
            recon = period.allocation_effect + period.selection_effect + period.interaction_effect
            assert abs(recon - period.total_excess) < 1e-10, \
                f"Brinson identity failed: {recon} != {period.total_excess}"

    def test_cumulative_is_sum_of_periods(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult

        symbols = ["A", "B"]
        data = _make_attribution_data(symbols)

        result = PortfolioResult(
            rebalance_dates=[date(2023, 1, 2), date(2023, 2, 1), date(2023, 3, 1)],
            weights_history=[{"A": 0.6, "B": 0.4}, {"A": 0.4, "B": 0.6}],
            trades=[],
        )
        attr = compute_attribution(result, data, {"A": "银行", "B": "食品饮料"})
        assert attr.cumulative is not None
        # Carino linking: cumulative ≠ simple sum, but should be close
        simple_sum = sum(p.allocation_effect for p in attr.periods)
        assert abs(attr.cumulative.allocation_effect - simple_sum) < 0.01  # within 1%
        # Carino identity: alloc + select + interact = total_excess
        recon = attr.cumulative.allocation_effect + attr.cumulative.selection_effect + attr.cumulative.interaction_effect
        assert abs(recon - attr.cumulative.total_excess) < 1e-10

    def test_cost_drag_positive(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult

        symbols = ["A", "B"]
        data = _make_attribution_data(symbols)

        result = PortfolioResult(
            rebalance_dates=[date(2023, 1, 2), date(2023, 2, 1)],
            weights_history=[{"A": 0.5, "B": 0.5}],
            trades=[{"cost": 500}, {"cost": 300}],
        )
        attr = compute_attribution(result, data, {"A": "银行", "B": "银行"}, initial_cash=1_000_000)
        assert attr.cost_drag > 0
        assert abs(attr.cost_drag - 800 / 1_000_000) < 1e-10

    def test_empty_weights_no_crash(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult
        result = PortfolioResult(rebalance_dates=[], weights_history=[], trades=[])
        attr = compute_attribution(result, {}, {}, initial_cash=1_000_000)
        assert len(attr.periods) == 0
        assert attr.cumulative is None

    def test_by_industry_populated(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult

        symbols = ["A", "B", "C"]
        data = _make_attribution_data(symbols)
        result = PortfolioResult(
            rebalance_dates=[date(2023, 1, 2), date(2023, 2, 1)],
            weights_history=[{"A": 0.5, "B": 0.3, "C": 0.2}],
            trades=[],
        )
        attr = compute_attribution(result, data, {"A": "银行", "B": "银行", "C": "食品饮料"})
        assert "银行" in attr.by_industry
        assert "食品饮料" in attr.by_industry
        assert "allocation" in attr.by_industry["银行"]


class TestAttributionEngineIntegration:
    """CRITICAL-1 regression: attribution must work with real engine output."""

    def test_attribution_with_engine_output(self):
        """rebalance_weights must align with rebalance_dates (not per-day weights_history)."""
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import run_portfolio_backtest, CostModel
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.cross_factor import MomentumRank
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.universe import Universe

        symbols = [f"S{i}" for i in range(5)]
        data = _make_attribution_data(symbols, n_days=120, seed=42)
        dates = data[symbols[0]].index
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
        )

        # rebalance_weights must be aligned with rebalance_dates
        assert len(result.rebalance_weights) == len(result.rebalance_dates)
        # weights_history has one entry per trading day (much longer)
        assert len(result.weights_history) > len(result.rebalance_weights)

        industry_map = {f"S{i}": f"ind{i % 2}" for i in range(5)}
        attr = compute_attribution(result, data, industry_map, initial_cash=1_000_000)

        # Must have periods matching rebalance intervals
        assert len(attr.periods) == len(result.rebalance_dates) - 1
        # Brinson identity must hold
        for p in attr.periods:
            recon = p.allocation_effect + p.selection_effect + p.interaction_effect
            assert abs(recon - p.total_excess) < 1e-10
