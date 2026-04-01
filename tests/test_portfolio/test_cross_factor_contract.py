"""Contract test for CrossSectionalFactor ABC — any implementation must pass these.

Add new factor implementations to `all_factors()` — contract tests auto-validate.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.cross_factor import (
    CrossSectionalFactor, MomentumRank, VolumeRank, ReverseVolatilityRank,
)


def _make_universe(n_symbols=10, n_days=60, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    data = {}
    for i in range(n_symbols):
        sym = f"S{i:03d}"
        prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.02, n_days))
        data[sym] = pd.DataFrame({
            "open": prices * (1 + rng.normal(0, 0.002, n_days)),
            "high": prices * (1 + abs(rng.normal(0, 0.005, n_days))),
            "low": prices * (1 - abs(rng.normal(0, 0.005, n_days))),
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n_days),
        }, index=dates)
    return data


def all_factors() -> list[CrossSectionalFactor]:
    return [
        MomentumRank(20),
        MomentumRank(10),
        VolumeRank(20),
        ReverseVolatilityRank(20),
    ]


@pytest.fixture(params=all_factors(), ids=lambda f: f.name)
def factor(request):
    return request.param


@pytest.fixture
def universe():
    return _make_universe()


@pytest.fixture
def eval_date():
    return datetime(2023, 3, 15)


class TestCrossSectionalFactorContract:
    """Invariants that ANY CrossSectionalFactor implementation must satisfy."""

    def test_has_name(self, factor):
        assert isinstance(factor.name, str)
        assert len(factor.name) > 0

    def test_has_warmup_period(self, factor):
        assert isinstance(factor.warmup_period, int)
        assert factor.warmup_period >= 0

    def test_compute_returns_series(self, factor, universe, eval_date):
        result = factor.compute(universe, eval_date)
        assert isinstance(result, pd.Series)

    def test_compute_values_in_0_1(self, factor, universe, eval_date):
        """compute() returns percentile ranks in [0, 1]."""
        result = factor.compute(universe, eval_date)
        if len(result) > 0:
            assert result.min() >= -1e-9, f"Min {result.min()} < 0"
            assert result.max() <= 1.0 + 1e-9, f"Max {result.max()} > 1"

    def test_compute_raw_returns_series(self, factor, universe, eval_date):
        result = factor.compute_raw(universe, eval_date)
        assert isinstance(result, pd.Series)

    def test_compute_raw_not_empty(self, factor, universe, eval_date):
        result = factor.compute_raw(universe, eval_date)
        assert len(result) > 0, "compute_raw returned empty series"

    def test_compute_and_raw_same_index(self, factor, universe, eval_date):
        """compute() and compute_raw() should cover the same symbols."""
        ranked = factor.compute(universe, eval_date)
        raw = factor.compute_raw(universe, eval_date)
        assert set(ranked.index) == set(raw.index)

    def test_compute_no_nan(self, factor, universe, eval_date):
        """compute() should not contain NaN (dropna expected)."""
        result = factor.compute(universe, eval_date)
        assert not result.isna().any(), f"NaN found in compute(): {result[result.isna()].index.tolist()}"

    def test_empty_universe_returns_empty(self, factor, eval_date):
        result = factor.compute({}, eval_date)
        assert len(result) == 0

    def test_registered_in_registry(self, factor):
        registry = CrossSectionalFactor.get_registry()
        assert factor.name in registry or type(factor).__name__ in registry
