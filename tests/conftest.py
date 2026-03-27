"""Shared test fixtures for ez-trading."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ez.config import reset_config


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config cache between tests."""
    reset_config()
    yield
    reset_config()


@pytest.fixture
def sample_bars():
    """Load sample bars from CSV as list[Bar]."""
    from tests.mocks.mock_provider import MockDataProvider
    provider = MockDataProvider()
    return provider.get_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2025, 12, 31))


@pytest.fixture
def sample_df(sample_bars):
    """Convert sample bars to DataFrame (the format factors/strategies expect)."""
    from ez.types import Bar
    data = [
        {
            "time": b.time, "symbol": b.symbol, "market": b.market,
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
        }
        for b in sample_bars
    ]
    return pd.DataFrame(data).set_index("time")
