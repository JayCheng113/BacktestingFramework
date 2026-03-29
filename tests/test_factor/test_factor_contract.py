"""Auto-discover and validate all Factor subclasses."""
import pandas as pd
import pytest

from ez.factor.base import Factor
from ez.factor.builtin.technical import MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR


def all_factors() -> list[Factor]:
    return [MA(period=5), EMA(period=12), RSI(period=14), MACD(), BOLL(period=20), Momentum(period=20),
            VWAP(period=10), OBV(), ATR(period=14)]


@pytest.fixture(params=all_factors(), ids=lambda f: f.name)
def factor(request):
    return request.param


class TestFactorContract:
    def test_has_name(self, factor):
        assert isinstance(factor.name, str)
        assert len(factor.name) > 0

    def test_has_warmup_period(self, factor):
        assert isinstance(factor.warmup_period, int)
        assert factor.warmup_period > 0

    def test_compute_returns_dataframe(self, factor, sample_df):
        result = factor.compute(sample_df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(sample_df)

    def test_compute_preserves_original_columns(self, factor, sample_df):
        original_cols = set(sample_df.columns)
        result = factor.compute(sample_df)
        assert original_cols.issubset(set(result.columns))

    def test_compute_adds_at_least_one_column(self, factor, sample_df):
        original_cols = set(sample_df.columns)
        result = factor.compute(sample_df)
        new_cols = set(result.columns) - original_cols
        assert len(new_cols) >= 1

    def test_warmup_period_rows_may_be_nan(self, factor, sample_df):
        result = factor.compute(sample_df)
        new_cols = set(result.columns) - set(sample_df.columns)
        for col in new_cols:
            if factor.warmup_period > 1 and len(sample_df) > factor.warmup_period:
                assert result[col].iloc[: factor.warmup_period - 1].isna().any()
