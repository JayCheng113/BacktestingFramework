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
        assert abs(attr.cumulative.allocation_effect -
                   sum(p.allocation_effect for p in attr.periods)) < 1e-10

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
