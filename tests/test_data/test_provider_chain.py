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

    def test_cache_short_range_refetches_when_weekday_tail_missing(self, mock_store, mock_provider):
        """A 1-business-day trailing gap on a short range is stale data, not a cache hit."""
        cached = [_bar(2), _bar(5)]
        mock_store.query_kline.return_value = cached
        chain = DataProviderChain([mock_provider], mock_store)
        chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 1, 8))
        mock_provider.get_kline.assert_called_once()

    def test_cache_short_range_accepts_weekend_only_tail_gap(self, mock_store, mock_provider):
        """Weekend-only gaps should still be treated as complete on short ranges."""
        cached = [_bar(2), _bar(5)]
        mock_store.query_kline.return_value = cached
        chain = DataProviderChain([mock_provider], mock_store)
        result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 1, 7))
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

    def test_skip_density_accepts_sparse_cache(self):
        """skip_density=True bypasses the density check for known-sparse symbols."""
        # Same middle-gap scenario as above
        bars = [
            _bar(2),
            Bar(time=datetime(2024, 2, 15), symbol="TEST.SZ", market="cn_stock",
                open=10, high=10, low=10, close=10, adj_close=10, volume=100),
            Bar(time=datetime(2024, 3, 31), symbol="TEST.SZ", market="cn_stock",
                open=10, high=10, low=10, close=10, adj_close=10, volume=100),
        ]
        complete, reason = DataProviderChain._is_cache_complete(
            bars, date(2024, 1, 2), date(2024, 3, 31), skip_density=True,
        )
        assert complete is True
        assert "known-sparse" in reason

    def test_short_range_weekday_tail_gap_fails_helper(self):
        bars = [_bar(2), _bar(5)]
        complete, reason = DataProviderChain._is_cache_complete(
            bars, date(2024, 1, 2), date(2024, 1, 8),
        )
        assert complete is False
        assert "trailing gap" in reason

    def test_short_range_weekend_tail_gap_passes_helper(self):
        bars = [_bar(2), _bar(5)]
        complete, reason = DataProviderChain._is_cache_complete(
            bars, date(2024, 1, 2), date(2024, 1, 7),
        )
        assert complete is True
        assert "short range" in reason

    def test_thinly_traded_symbol_no_infinite_refetch(self):
        """Regression test for reviewer finding I2: a legitimately thin symbol
        (e.g., niche ETF with ~10 trading days/month) should not cause repeated
        refetches on every call. After the first provider confirms the data is
        sparse, subsequent cache hits must be accepted.
        """
        # Scenario: 90-day range, provider returns only 10 bars (way below 75% of
        # expected ~60). For a realistic thinly-traded symbol, the last bar is
        # close to end_date (provider already gave everything it has).
        # First call: cache empty → fetch → save + mark sparse. Second call:
        # cache has 10 bars → density check would fail → must be bypassed via
        # the _known_sparse_symbols set.
        from ez.data.provider import DataProviderChain

        # 10 bars spread over Jan 2 - Mar 28 (90 days). Last bar at Mar 28 is
        # within 3-day boundary of end_date Mar 31.
        sparse_dates = [
            datetime(2024, 1, 2), datetime(2024, 1, 15), datetime(2024, 1, 29),
            datetime(2024, 2, 5), datetime(2024, 2, 19),
            datetime(2024, 3, 4), datetime(2024, 3, 11), datetime(2024, 3, 18),
            datetime(2024, 3, 25), datetime(2024, 3, 28),
        ]
        sparse_bars = [
            Bar(time=d, symbol="THIN.SZ", market="cn_stock",
                open=10, high=10, low=10, close=10, adj_close=10, volume=100)
            for d in sparse_dates
        ]  # 10 bars over 90 days — well below 75% of ~60 expected

        class _FakeStore:
            def __init__(self):
                self._data = []
            def query_kline(self, sym, mkt, p, s, e):
                return list(self._data)
            def query_kline_batch(self, syms, mkt, p, s, e):
                return {sym: list(self._data) for sym in syms}
            def save_kline(self, bars, period):
                self._data = list(bars)
                return len(bars)
            def has_data(self, *args, **kwargs):
                return len(self._data) > 0

        store = _FakeStore()
        provider = MagicMock(spec=DataProvider)
        provider.name = "sparse_mock"
        provider.get_kline.return_value = sparse_bars
        chain = DataProviderChain([provider], store)

        # First call: cache empty → fetch from provider → save → return
        chain.get_kline("THIN.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 3, 31))
        assert provider.get_kline.call_count == 1
        # The thin symbol should now be marked as known-sparse
        assert ("THIN.SZ", "cn_stock", "daily") in chain._known_sparse_symbols

        # Second call: cache has 10 bars → density would fail → but skip_density=True
        # bypasses it → cache accepted → NO provider refetch
        chain.get_kline("THIN.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 3, 31))
        assert provider.get_kline.call_count == 1, (
            f"Provider was called {provider.get_kline.call_count} times for thin symbol — "
            f"refetch loop not prevented"
        )

        # Third, fourth, fifth calls: still cached
        for _ in range(3):
            chain.get_kline("THIN.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 3, 31))
        assert provider.get_kline.call_count == 1, "Refetch triggered after sparse mark"

        # Batch path also uses the sparse flag
        chain.get_kline_batch(["THIN.SZ"], "cn_stock", "daily", date(2024, 1, 2), date(2024, 3, 31))
        assert provider.get_kline.call_count == 1, "get_kline_batch refetched sparse symbol"

        # I5: sparse cache is instance-level now, no class-level cleanup needed


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
