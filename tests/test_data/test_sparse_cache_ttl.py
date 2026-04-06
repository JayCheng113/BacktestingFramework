"""Tests for sparse cache TTL expiry in DataProviderChain."""
import time
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from ez.data.provider import DataProviderChain, DataProvider
from ez.types import Bar


@pytest.fixture(autouse=True)
def _clear_sparse_cache():
    """Reset sparse symbol cache before and after each test."""
    DataProviderChain._known_sparse_symbols.clear()
    yield
    DataProviderChain._known_sparse_symbols.clear()


def _sparse_bars():
    """10 bars spread over 90 days — below 75% density threshold."""
    dates = [
        datetime(2024, 1, 2), datetime(2024, 1, 15), datetime(2024, 1, 29),
        datetime(2024, 2, 5), datetime(2024, 2, 19),
        datetime(2024, 3, 4), datetime(2024, 3, 11), datetime(2024, 3, 18),
        datetime(2024, 3, 25), datetime(2024, 3, 28),
    ]
    return [
        Bar(time=d, symbol="THIN.SZ", market="cn_stock",
            open=10, high=10, low=10, close=10, adj_close=10, volume=100)
        for d in dates
    ]


class _FakeStore:
    def __init__(self, bars=None):
        self._data = list(bars) if bars else []

    def query_kline(self, sym, mkt, p, s, e):
        return list(self._data)

    def query_kline_batch(self, syms, mkt, p, s, e):
        return {sym: list(self._data) for sym in syms}

    def save_kline(self, bars, period):
        self._data = list(bars)
        return len(bars)

    def has_data(self, *args, **kwargs):
        return len(self._data) > 0


def test_sparse_cache_expires_after_ttl():
    """After TTL expires, provider should be called again for a sparse symbol."""
    bars = _sparse_bars()
    store = _FakeStore(bars)  # pre-populate cache with sparse data

    provider = MagicMock(spec=DataProvider)
    provider.name = "ttl_mock"
    provider.get_kline.return_value = bars

    chain = DataProviderChain([provider], store)
    key = ("THIN.SZ", "cn_stock", "daily")

    # Manually mark sparse with a timestamp > 24h ago
    DataProviderChain._known_sparse_symbols[key] = time.monotonic() - 90000.0

    # Call get_kline — TTL expired, so skip_density=False, density check fails,
    # provider should be called
    chain.get_kline("THIN.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 3, 31))
    assert provider.get_kline.call_count == 1, (
        "Provider should be called after sparse cache TTL expires"
    )
    # Key should be re-populated with a fresh timestamp
    assert key in DataProviderChain._known_sparse_symbols


def test_sparse_cache_within_ttl_skips_density():
    """Within TTL window, sparse symbol should use cache without calling provider."""
    bars = _sparse_bars()
    store = _FakeStore(bars)  # pre-populate cache with sparse data

    provider = MagicMock(spec=DataProvider)
    provider.name = "ttl_mock"
    provider.get_kline.return_value = bars

    chain = DataProviderChain([provider], store)
    key = ("THIN.SZ", "cn_stock", "daily")

    # Mark sparse with a fresh timestamp (just now)
    DataProviderChain._known_sparse_symbols[key] = time.monotonic()

    # Call get_kline — TTL not expired, skip_density=True, cache should be accepted
    result = chain.get_kline("THIN.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 3, 31))
    assert provider.get_kline.call_count == 0, (
        "Provider should NOT be called when sparse cache is within TTL"
    )
    assert len(result) == 10
