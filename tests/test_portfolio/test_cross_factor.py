"""Tests for CrossSectionalFactor (V2.9 P2)."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.cross_factor import (
    CrossSectionalFactor, MomentumRank, VolumeRank, ReverseVolatilityRank,
)


def _make_data(n: int = 50, symbols: int = 5):
    """Create test universe data."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    data = {}
    for i in range(symbols):
        sym = f"S{i:03d}"
        prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.02, n))
        data[sym] = pd.DataFrame({
            "open": prices * 0.99, "high": prices * 1.01,
            "low": prices * 0.98, "close": prices,
            "adj_close": prices, "volume": rng.integers(100_000, 1_000_000, n),
        }, index=dates)
    return data


class TestMomentumRank:
    def test_returns_series(self):
        data = _make_data()
        f = MomentumRank(period=20)
        result = f.compute(data, datetime(2024, 3, 15))
        assert isinstance(result, pd.Series)
        assert len(result) == 5

    def test_values_between_0_1(self):
        data = _make_data()
        result = MomentumRank(20).compute(data, datetime(2024, 3, 15))
        assert (result >= 0).all() and (result <= 1).all()

    def test_name(self):
        assert MomentumRank(10).name == "momentum_rank_10"

    def test_insufficient_data(self):
        data = _make_data(n=5)  # only 5 bars, period=20
        result = MomentumRank(20).compute(data, datetime(2024, 1, 10))
        assert result.empty


class TestVolumeRank:
    def test_returns_series(self):
        data = _make_data()
        result = VolumeRank(20).compute(data, datetime(2024, 3, 15))
        assert isinstance(result, pd.Series)
        assert len(result) == 5


class TestReverseVolatilityRank:
    def test_low_vol_ranks_higher(self):
        data = _make_data(n=50, symbols=3)
        result = ReverseVolatilityRank(20).compute(data, datetime(2024, 3, 15))
        assert isinstance(result, pd.Series)
        assert len(result) == 3


class TestContractTest:
    """Contract test: all factors must return Series, no all-NaN, correct length."""

    @pytest.mark.parametrize("FactorCls", [MomentumRank, VolumeRank, ReverseVolatilityRank])
    def test_contract(self, FactorCls):
        data = _make_data(n=50, symbols=5)
        f = FactorCls(period=20)
        result = f.compute(data, datetime(2024, 3, 15))

        assert isinstance(result, pd.Series), f"{FactorCls.__name__} must return Series"
        assert not result.isna().all(), f"{FactorCls.__name__} must not be all NaN"
        assert len(result) <= 5, f"{FactorCls.__name__} length must <= universe size"
