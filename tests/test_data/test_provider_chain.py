"""DataProviderChain unit tests — cache logic, failover, validation."""
from datetime import date, datetime
from unittest.mock import MagicMock, patch
import pytest
from ez.data.provider import DataProviderChain, DataProvider
from ez.data.store import DuckDBStore
from ez.errors import ProviderError
from ez.types import Bar


def _bar(day=2, **kw):
    defaults = dict(
        time=datetime(2024, 1, day), symbol="TEST.SZ", market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000,
    )
    defaults.update(kw)
    return Bar(**defaults)


@pytest.fixture
def mock_store():
    store = MagicMock(spec=DuckDBStore)
    store.query_kline.return_value = []
    store.save_kline.return_value = 0
    return store


@pytest.fixture
def mock_provider():
    p = MagicMock(spec=DataProvider)
    p.name = "mock1"
    p.get_kline.return_value = [_bar(2), _bar(3)]
    p.search_symbols.return_value = [{"symbol": "TEST.SZ", "name": "Test"}]
    return p


class TestCacheLogic:
    def test_cache_hit_full_coverage(self, mock_store, mock_provider):
        """Cache covering full date range returns cached data, no provider call."""
        cached = [_bar(2), _bar(3), _bar(6)]
        mock_store.query_kline.return_value = cached
        chain = DataProviderChain([mock_provider], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 1, 6))
        assert result == cached
        mock_provider.get_kline.assert_not_called()

    def test_cache_partial_fetches_fresh(self, mock_store, mock_provider):
        """Cache only covering partial range should fetch from provider."""
        # Use Jan 10-11, request Jan 2-15 so the gap exceeds the 3-day tolerance
        cached = [_bar(10), _bar(11)]
        mock_store.query_kline.return_value = cached
        chain = DataProviderChain([mock_provider], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 1, 15))
        mock_provider.get_kline.assert_called_once()

    def test_cache_empty_fetches_from_provider(self, mock_store, mock_provider):
        mock_store.query_kline.return_value = []
        chain = DataProviderChain([mock_provider], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        assert len(result) > 0

    def test_cache_with_middle_gap_refetches(self, mock_store, mock_provider):
        """Regression test for codex finding: cache with first/last bars covering
        the range but massive middle gap should NOT be considered a hit.

        Previously only boundary dates were checked — a cache with 3 bars at
        Jan 2, Jan 30, Jun 28 covering a 6-month request would be accepted,
        silently returning data with huge middle holes.
        """
        # Request 6 months (Jan 2 - Jun 28, 2024) = ~178 days.
        # Expected bars: 178 * 245/365 ≈ 119. 75% threshold: 89.
        # Provide only 3 bars: one at start, one at Jan 30, one at end. Boundary passes (first/last within 3 days),
        # but bar count (3) is way below 89 → should refetch.
        cached = [
            _bar(2),  # Jan 2
            _bar(30),  # Jan 30
            Bar(time=datetime(2024, 6, 28), symbol="TEST.SZ", market="cn_stock",
                open=11.0, high=11.5, low=10.8, close=11.2, adj_close=11.15, volume=1000000),
        ]
        mock_store.query_kline.return_value = cached
        chain = DataProviderChain([mock_provider], mock_store)
        chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 6, 28))
        # Middle gap detected → provider must have been called
        mock_provider.get_kline.assert_called_once()

    def test_cache_short_range_skips_density_check(self, mock_store, mock_provider):
        """For ranges <= 14 days, density check is skipped (sample too small
        to reliably estimate coverage — boundary check is sufficient)."""
        # 5-day request, cached has 2 boundary bars only
        cached = [_bar(2), _bar(6)]
        mock_store.query_kline.return_value = cached
        chain = DataProviderChain([mock_provider], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 1, 6))
        # Short range → cache accepted, no provider call
        assert result == cached
        mock_provider.get_kline.assert_not_called()

    def test_cache_complete_coverage_accepts(self, mock_store, mock_provider):
        """Cache with ~trading-day density should be accepted.

        30-day request → expected ~20 bars → 75% = 15.
        Provide 22 bars (close to realistic trading-day count, above threshold).
        """
        # Mon-Fri only, 30 days → roughly 22 trading days
        cached = [_bar(day=d) for d in range(2, 32) if datetime(2024, 1, d).weekday() < 5][:22]
        mock_store.query_kline.return_value = cached
        chain = DataProviderChain([mock_provider], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 1, 31))
        # Dense cache → accepted, no provider call
        assert result == cached
        mock_provider.get_kline.assert_not_called()

    def test_is_cache_complete_helper_direct(self):
        """Direct test of _is_cache_complete static method (no DB/provider)."""
        # Empty
        complete, reason = DataProviderChain._is_cache_complete([], date(2024, 1, 1), date(2024, 1, 31))
        assert not complete and reason == "empty"
        # Start gap
        bars = [_bar(15)]
        complete, reason = DataProviderChain._is_cache_complete(bars, date(2024, 1, 1), date(2024, 1, 15))
        assert not complete and "start gap" in reason
        # End gap
        complete, reason = DataProviderChain._is_cache_complete(bars, date(2024, 1, 15), date(2024, 1, 31))
        assert not complete and "end gap" in reason
        # Middle gap (3 bars over 90 days, expected ~60, 3 << 75%)
        bars = [
            _bar(2),
            Bar(time=datetime(2024, 2, 15), symbol="TEST.SZ", market="cn_stock",
                open=10, high=10, low=10, close=10, adj_close=10, volume=100),
            Bar(time=datetime(2024, 3, 31), symbol="TEST.SZ", market="cn_stock",
                open=10, high=10, low=10, close=10, adj_close=10, volume=100),
        ]
        complete, reason = DataProviderChain._is_cache_complete(bars, date(2024, 1, 2), date(2024, 3, 31))
        assert not complete and "middle gap" in reason


class TestFailover:
    def test_first_provider_fails_second_succeeds(self, mock_store):
        p1 = MagicMock(spec=DataProvider)
        p1.name = "fail"
        p1.get_kline.side_effect = Exception("timeout")
        p2 = MagicMock(spec=DataProvider)
        p2.name = "ok"
        p2.get_kline.return_value = [_bar()]
        chain = DataProviderChain([p1, p2], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        assert len(result) > 0

    def test_all_providers_fail_raises(self, mock_store):
        p1 = MagicMock(spec=DataProvider)
        p1.name = "fail1"
        p1.get_kline.side_effect = Exception("err1")
        p2 = MagicMock(spec=DataProvider)
        p2.name = "fail2"
        p2.get_kline.side_effect = Exception("err2")
        chain = DataProviderChain([p1, p2], mock_store)
        with pytest.raises(ProviderError, match="All providers failed"):
            chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))

    def test_empty_providers_returns_empty(self, mock_store):
        chain = DataProviderChain([], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        assert result == []


class TestValidation:
    def test_invalid_bars_filtered(self, mock_store):
        """Bars with bad OHLC should be filtered by DataValidator."""
        p = MagicMock(spec=DataProvider)
        p.name = "test"
        good = _bar(2)
        bad = _bar(3, low=999.0, high=1.0)  # invalid: low > high
        p.get_kline.return_value = [good, bad]
        chain = DataProviderChain([p], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        assert len(result) == 1  # bad bar filtered
        assert result[0].time == good.time


class TestSearchSymbols:
    def test_search_returns_first_success(self, mock_store, mock_provider):
        chain = DataProviderChain([mock_provider], mock_store)
        result = chain.search_symbols("TEST")
        assert len(result) == 1

    def test_search_failover(self, mock_store):
        p1 = MagicMock(spec=DataProvider)
        p1.search_symbols.side_effect = Exception("err")
        p2 = MagicMock(spec=DataProvider)
        p2.search_symbols.return_value = [{"symbol": "X"}]
        chain = DataProviderChain([p1, p2], mock_store)
        result = chain.search_symbols("X")
        assert len(result) == 1

    def test_search_all_fail(self, mock_store):
        p = MagicMock(spec=DataProvider)
        p.search_symbols.side_effect = Exception("err")
        chain = DataProviderChain([p], mock_store)
        assert chain.search_symbols("X") == []
