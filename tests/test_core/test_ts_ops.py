"""Tests for ez.core.ts_ops — time series primitives."""
import numpy as np
import pandas as pd
import pytest
from ez.core import ts_ops


@pytest.fixture
def prices():
    return pd.Series([10.0, 11.0, 12.0, 11.5, 13.0, 12.5, 14.0, 13.5, 15.0, 14.5])


class TestRollingMean:
    def test_basic(self, prices):
        result = ts_ops.rolling_mean(prices, window=3)
        assert len(result) == len(prices)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(11.0)  # (10+11+12)/3
        assert result.iloc[3] == pytest.approx(11.5)  # (11+12+11.5)/3

    def test_matches_pandas(self, prices):
        result = ts_ops.rolling_mean(prices, window=5)
        expected = prices.rolling(window=5, min_periods=5).mean()
        pd.testing.assert_series_equal(result, expected)

    def test_window_1(self, prices):
        result = ts_ops.rolling_mean(prices, window=1)
        pd.testing.assert_series_equal(result, prices)

    def test_all_nan_when_window_exceeds_data(self):
        s = pd.Series([1.0, 2.0])
        result = ts_ops.rolling_mean(s, window=5)
        assert result.isna().all()


class TestRollingStd:
    def test_basic(self, prices):
        result = ts_ops.rolling_std(prices, window=3)
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] > 0  # non-zero std

    def test_matches_pandas(self, prices):
        result = ts_ops.rolling_std(prices, window=5)
        expected = prices.rolling(window=5, min_periods=5).std(ddof=1)
        pd.testing.assert_series_equal(result, expected)

    def test_ddof_0(self, prices):
        r1 = ts_ops.rolling_std(prices, window=3, ddof=1)
        r0 = ts_ops.rolling_std(prices, window=3, ddof=0)
        # population std < sample std
        valid = r1.dropna().index
        assert (r0[valid] <= r1[valid]).all()

    def test_constant_series(self):
        s = pd.Series([5.0] * 10)
        result = ts_ops.rolling_std(s, window=3)
        assert result.dropna().eq(0).all()


class TestEwmMean:
    def test_basic(self, prices):
        result = ts_ops.ewm_mean(prices, span=3)
        assert len(result) == len(prices)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert not pd.isna(result.iloc[2])

    def test_matches_pandas(self, prices):
        result = ts_ops.ewm_mean(prices, span=5)
        expected = prices.ewm(span=5, min_periods=5).mean()
        pd.testing.assert_series_equal(result, expected)


class TestDiff:
    def test_basic(self, prices):
        result = ts_ops.diff(prices)
        assert pd.isna(result.iloc[0])
        assert result.iloc[1] == pytest.approx(1.0)  # 11 - 10

    def test_matches_pandas(self, prices):
        result = ts_ops.diff(prices, periods=3)
        expected = prices.diff(periods=3)
        pd.testing.assert_series_equal(result, expected)

    def test_periods_2(self, prices):
        result = ts_ops.diff(prices, periods=2)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)  # 12 - 10


class TestPctChange:
    def test_basic(self, prices):
        result = ts_ops.pct_change(prices)
        assert pd.isna(result.iloc[0])
        assert result.iloc[1] == pytest.approx(0.1)  # (11-10)/10

    def test_matches_pandas(self, prices):
        result = ts_ops.pct_change(prices, periods=3)
        expected = prices.pct_change(periods=3)
        pd.testing.assert_series_equal(result, expected)

    def test_periods(self, prices):
        result = ts_ops.pct_change(prices, periods=2)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(0.2)  # (12-10)/10
