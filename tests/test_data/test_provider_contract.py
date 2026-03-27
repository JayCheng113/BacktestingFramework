"""Contract tests auto-verifying any DataProvider subclass."""
from __future__ import annotations

import os
from datetime import date

import pytest

from ez.data.provider import DataProvider
from ez.types import Bar
from tests.mocks.mock_provider import MockDataProvider


# Each entry: (ProviderClass, test_symbol, test_market, unknown_symbol)
# This allows market-specific providers (e.g. Tushare = cn_stock only) to be tested
# with appropriate symbols.
def discover_providers() -> list[tuple]:
    providers = [
        (MockDataProvider, "TEST.US", "us_stock", "ZZZZZZZ.XX"),
    ]

    # Include TushareProvider only when a real token is available (integration)
    if os.environ.get("TUSHARE_TOKEN"):
        from ez.data.providers.tushare_provider import TushareDataProvider
        providers.append(
            (TushareDataProvider, "000001.SZ", "cn_stock", "999999.SZ"),
        )

    return providers


_PROVIDER_PARAMS = discover_providers()


@pytest.fixture(params=_PROVIDER_PARAMS, ids=lambda t: t[0].__name__)
def provider_ctx(request):
    """Return (provider_instance, symbol, market, unknown_symbol) tuple."""
    cls, symbol, market, unknown = request.param
    return cls(), symbol, market, unknown


class TestDataProviderContract:
    def test_has_name(self, provider_ctx):
        provider, *_ = provider_ctx
        assert isinstance(provider.name, str)
        assert len(provider.name) > 0

    def test_get_kline_returns_list_of_bars(self, provider_ctx):
        provider, symbol, market, _ = provider_ctx
        bars = provider.get_kline(symbol, market, "daily", date(2023, 1, 1), date(2023, 6, 30))
        assert isinstance(bars, list)
        if bars:
            assert isinstance(bars[0], Bar)

    def test_get_kline_empty_for_unknown_symbol(self, provider_ctx):
        provider, _, market, unknown = provider_ctx
        bars = provider.get_kline(unknown, market, "daily", date(2023, 1, 1), date(2023, 1, 31))
        assert isinstance(bars, list)
        assert len(bars) == 0

    def test_get_kline_sorted_by_time(self, provider_ctx):
        provider, symbol, market, _ = provider_ctx
        bars = provider.get_kline(symbol, market, "daily", date(2023, 1, 1), date(2023, 6, 30))
        if len(bars) > 1:
            times = [b.time for b in bars]
            assert times == sorted(times)

    def test_search_symbols_returns_list(self, provider_ctx):
        provider, *_ = provider_ctx
        results = provider.search_symbols("TEST")
        assert isinstance(results, list)
        if results:
            assert "symbol" in results[0]
