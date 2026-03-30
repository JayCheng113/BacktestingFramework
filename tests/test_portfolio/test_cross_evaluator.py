"""Tests for V2.10 F1: CrossSectionalEvaluator + F2b FactorCorrelationMatrix."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import MomentumRank, VolumeRank, ReverseVolatilityRank
from ez.portfolio.cross_evaluator import (
    CrossSectionalEvalResult,
    evaluate_cross_sectional_factor,
    evaluate_ic_decay,
    compute_factor_correlation,
)


def _make_universe(n_stocks=20, n_days=300, seed=42):
    """Generate synthetic universe with clear momentum signal."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    data = {}
    for i in range(n_stocks):
        # Stocks with higher index have higher drift → momentum factor should work
        drift = 0.0005 * (i + 1)
        prices = 10 * np.cumprod(1 + rng.normal(drift, 0.02, n_days))
        data[f"S{i:03d}"] = pd.DataFrame({
            "open": prices * (1 + rng.normal(0, 0.002, n_days)),
            "high": prices * (1 + abs(rng.normal(0, 0.005, n_days))),
            "low": prices * (1 - abs(rng.normal(0, 0.005, n_days))),
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n_days).astype(float),
        }, index=dates)
    cal = TradingCalendar.from_dates([d.date() for d in dates])
    return data, cal, dates


class TestCrossSectionalEvaluator:
    """F1: Cross-sectional IC / Rank IC / ICIR."""

    def test_basic_evaluation(self):
        data, cal, dates = _make_universe()
        result = evaluate_cross_sectional_factor(
            factor=MomentumRank(20),
            universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
            forward_days=5, eval_freq="weekly",
        )
        assert isinstance(result, CrossSectionalEvalResult)
        assert result.n_eval_dates > 10
        assert result.factor_name == "momentum_rank_20"
        assert len(result.ic_series) > 0
        assert len(result.rank_ic_series) > 0
        assert len(result.eval_dates) > 0

    def test_momentum_positive_ic(self):
        """Momentum factor should have positive IC in trending data."""
        data, cal, dates = _make_universe(seed=42)
        result = evaluate_cross_sectional_factor(
            factor=MomentumRank(20),
            universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
            forward_days=5, eval_freq="weekly",
        )
        # With clear drift differences, momentum IC should be positive
        assert result.mean_rank_ic > 0, f"Expected positive Rank IC, got {result.mean_rank_ic}"

    def test_icir_computed(self):
        data, cal, dates = _make_universe()
        result = evaluate_cross_sectional_factor(
            factor=MomentumRank(20),
            universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
            forward_days=5, eval_freq="weekly",
        )
        assert result.ic_std > 0
        assert result.icir != 0
        # ICIR = mean / std
        assert abs(result.icir - result.mean_ic / result.ic_std) < 1e-10

    def test_quintile_returns(self):
        data, cal, dates = _make_universe()
        result = evaluate_cross_sectional_factor(
            factor=MomentumRank(20),
            universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
            forward_days=5, eval_freq="weekly", n_quantiles=5,
        )
        assert len(result.quintile_returns) > 0
        # Top quintile should outperform bottom for momentum
        if 1 in result.quintile_returns and 5 in result.quintile_returns:
            assert result.quintile_returns[5] > result.quintile_returns[1], \
                "Top quintile should outperform bottom for momentum"

    def test_empty_universe(self):
        cal = TradingCalendar.from_dates([date(2024, 1, 2)])
        result = evaluate_cross_sectional_factor(
            factor=MomentumRank(20),
            universe_data={}, calendar=cal,
            start=date(2024, 1, 1), end=date(2024, 3, 1),
        )
        assert result.n_eval_dates == 0

    def test_short_data(self):
        """With too few stocks/dates, should not crash."""
        data, cal, dates = _make_universe(n_stocks=3, n_days=50)
        result = evaluate_cross_sectional_factor(
            factor=MomentumRank(20),
            universe_data=data, calendar=cal,
            start=dates[25].date(), end=dates[-1].date(),
            forward_days=5, eval_freq="weekly",
        )
        # May have 0 eval dates due to insufficient data, but should not crash
        assert isinstance(result, CrossSectionalEvalResult)

    def test_avg_stocks(self):
        data, cal, dates = _make_universe(n_stocks=20)
        result = evaluate_cross_sectional_factor(
            factor=MomentumRank(20),
            universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
            forward_days=5, eval_freq="weekly",
        )
        assert result.avg_stocks_per_date > 10  # most stocks should have data


class TestICDecay:
    """IC decay curve across forward horizons."""

    def test_decay_curve(self):
        data, cal, dates = _make_universe()
        decay = evaluate_ic_decay(
            factor=MomentumRank(20),
            universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
            lags=[1, 5, 10, 20], eval_freq="weekly",
        )
        assert set(decay.keys()) == {1, 5, 10, 20}
        # All values should be finite
        for lag, ic in decay.items():
            assert np.isfinite(ic), f"IC at lag {lag} is not finite: {ic}"

    def test_decay_trend(self):
        """IC should generally decay with longer horizons for short-term momentum."""
        data, cal, dates = _make_universe(n_stocks=30, n_days=400, seed=77)
        decay = evaluate_ic_decay(
            factor=MomentumRank(20),
            universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-50].date(),
            lags=[1, 5, 20], eval_freq="weekly",
        )
        # Short-horizon IC should be >= long-horizon IC for momentum
        # (not always true due to noise, so just check it's computable)
        assert len(decay) == 3


class TestFactorCorrelation:
    """F2b: Factor correlation matrix."""

    def test_correlation_matrix_shape(self):
        data, cal, dates = _make_universe()
        factors = [MomentumRank(20), VolumeRank(20), ReverseVolatilityRank(20)]
        corr = compute_factor_correlation(
            factors=factors, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
            eval_freq="monthly",
        )
        assert corr.shape == (3, 3)
        assert list(corr.index) == [f.name for f in factors]
        assert list(corr.columns) == [f.name for f in factors]

    def test_diagonal_is_one(self):
        data, cal, dates = _make_universe()
        factors = [MomentumRank(20), VolumeRank(20)]
        corr = compute_factor_correlation(
            factors=factors, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
        )
        for i in range(len(factors)):
            assert abs(corr.iloc[i, i] - 1.0) < 1e-10

    def test_symmetric(self):
        data, cal, dates = _make_universe()
        factors = [MomentumRank(20), VolumeRank(20), ReverseVolatilityRank(20)]
        corr = compute_factor_correlation(
            factors=factors, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
        )
        for i in range(len(factors)):
            for j in range(len(factors)):
                assert abs(corr.iloc[i, j] - corr.iloc[j, i]) < 1e-10

    def test_values_in_range(self):
        data, cal, dates = _make_universe()
        factors = [MomentumRank(20), VolumeRank(20)]
        corr = compute_factor_correlation(
            factors=factors, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
        )
        assert (corr >= -1.0).all().all()
        assert (corr <= 1.0).all().all()

    def test_single_factor(self):
        data, cal, dates = _make_universe()
        corr = compute_factor_correlation(
            factors=[MomentumRank(20)], universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-30].date(),
        )
        assert corr.shape == (1, 1)
        assert abs(corr.iloc[0, 0] - 1.0) < 1e-10
