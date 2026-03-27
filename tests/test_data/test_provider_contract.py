"""Contract tests auto-verifying any DataProvider subclass."""
from __future__ import annotations

from datetime import date

import pytest

from ez.data.provider import DataProvider
from ez.types import Bar
from tests.mocks.mock_provider import MockDataProvider


def discover_providers() -> list[type[DataProvider]]:
    return [MockDataProvider]


@pytest.fixture(params=discover_providers(), ids=lambda cls: cls.__name__)
def provider(request):
    return request.param()


class TestDataProviderContract:
    def test_has_name(self, provider):
        assert isinstance(provider.name, str)
        assert len(provider.name) > 0

    def test_get_kline_returns_list_of_bars(self, provider):
        bars = provider.get_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2023, 6, 30))
        assert isinstance(bars, list)
        if bars:
            assert isinstance(bars[0], Bar)

    def test_get_kline_empty_for_unknown_symbol(self, provider):
        bars = provider.get_kline("ZZZZZZZ.XX", "us_stock", "daily", date(2023, 1, 1), date(2023, 1, 31))
        assert isinstance(bars, list)
        assert len(bars) == 0

    def test_get_kline_sorted_by_time(self, provider):
        bars = provider.get_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2023, 6, 30))
        if len(bars) > 1:
            times = [b.time for b in bars]
            assert times == sorted(times)

    def test_search_symbols_returns_list(self, provider):
        results = provider.search_symbols("TEST")
        assert isinstance(results, list)
        if results:
            assert "symbol" in results[0]
