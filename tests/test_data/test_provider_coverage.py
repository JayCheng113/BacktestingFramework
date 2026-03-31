"""Tests for DataProviderChain coverage check (CORE change)."""
from datetime import date, datetime

import pytest

from ez.data.provider import DataProviderChain, DataProvider
from ez.data.store import DuckDBStore
from ez.types import Bar


class _PartialProvider(DataProvider):
    """Returns data for only a narrow window."""
    def __init__(self, name_: str, start: date, end: date):
        self._name = name_
        self._start = start
        self._end = end

    @property
    def name(self):
        return self._name

    def close(self):
        pass

    def get_kline(self, symbol, market, period, start_date, end_date):
        bars = []
        d = self._start
        while d <= min(self._end, end_date):
            if d >= start_date and d.weekday() < 5:
                bars.append(Bar(
                    time=datetime.combine(d, datetime.min.time()),
                    symbol=symbol, market=market,
                    open=10.0, high=10.5, low=9.5, close=10.0, adj_close=10.0, volume=1000,
                ))
            d += __import__('datetime').timedelta(days=1)
        return bars

    def search_symbols(self, keyword, market=""):
        return []


class TestProviderCoverageCheck:
    def test_partial_provider_skipped_when_next_exists(self, tmp_path):
        """Provider returning <50% coverage should be skipped if another provider exists."""
        # Provider 1: only 2023 data (1 year out of 3 requested)
        p1 = _PartialProvider("short", date(2023, 1, 1), date(2023, 12, 31))
        # Provider 2: full 3 years
        p2 = _PartialProvider("full", date(2021, 1, 1), date(2024, 12, 31))
        store = DuckDBStore(str(tmp_path / "test.db"))
        chain = DataProviderChain(providers=[p1, p2], store=store)

        bars = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2021, 1, 1), date(2024, 1, 1))
        # Should get full data from p2, not partial from p1
        first = bars[0].time.date()
        assert first.year == 2021, f"Expected 2021 start, got {first}"

    def test_last_provider_returns_partial(self, tmp_path):
        """Last provider should return whatever it has, even if <50%."""
        p1 = _PartialProvider("only", date(2023, 6, 1), date(2023, 12, 31))
        store = DuckDBStore(str(tmp_path / "test.db"))
        chain = DataProviderChain(providers=[p1], store=store)

        bars = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2021, 1, 1), date(2024, 1, 1))
        assert len(bars) > 0, "Last provider should return partial data"

    def test_short_request_skips_coverage_check(self, tmp_path):
        """Requests shorter than 30 days skip coverage check."""
        p1 = _PartialProvider("short", date(2024, 1, 10), date(2024, 1, 15))  # 5 days of 20 requested
        store = DuckDBStore(str(tmp_path / "test.db"))
        chain = DataProviderChain(providers=[p1], store=store)

        bars = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 20))
        assert len(bars) > 0, "Short request should accept partial data"
