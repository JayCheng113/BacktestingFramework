"""Tests for TradingCalendar (V2.9 P0)."""
from datetime import date

import pytest

from ez.portfolio.calendar import TradingCalendar


@pytest.fixture
def cal_2024():
    """A realistic 2024 Q1 calendar (Mon-Fri, skip some holidays)."""
    return TradingCalendar.weekday_fallback(date(2024, 1, 1), date(2024, 3, 31))


class TestConstruction:
    def test_from_dates(self):
        cal = TradingCalendar.from_dates([date(2024, 1, 2), date(2024, 1, 3)])
        assert len(cal) == 2

    def test_dedup_and_sort(self):
        cal = TradingCalendar.from_dates([date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 2)])
        assert len(cal) == 2
        assert cal.start == date(2024, 1, 2)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            TradingCalendar.from_dates([])

    def test_weekday_fallback(self):
        cal = TradingCalendar.weekday_fallback(date(2024, 1, 1), date(2024, 1, 7))
        # Jan 1 Mon, Jan 2 Tue, Jan 3 Wed, Jan 4 Thu, Jan 5 Fri, Jan 6 Sat(skip), Jan 7 Sun(skip)
        assert len(cal) == 5


class TestQuery:
    def test_is_trading_day(self, cal_2024):
        assert cal_2024.is_trading_day(date(2024, 1, 2))  # Tuesday
        assert not cal_2024.is_trading_day(date(2024, 1, 6))  # Saturday

    def test_trading_days_between(self, cal_2024):
        days = cal_2024.trading_days_between(date(2024, 1, 1), date(2024, 1, 5))
        assert len(days) == 5  # Mon-Fri

    def test_prev_trading_day(self, cal_2024):
        prev = cal_2024.prev_trading_day(date(2024, 1, 8))  # Monday
        assert prev == date(2024, 1, 5)  # previous Friday

    def test_prev_trading_day_none(self, cal_2024):
        assert cal_2024.prev_trading_day(date(2024, 1, 1)) is None

    def test_next_trading_day(self, cal_2024):
        nxt = cal_2024.next_trading_day(date(2024, 1, 6))  # Saturday
        assert nxt == date(2024, 1, 8)  # next Monday


class TestRebalanceDates:
    def test_daily(self, cal_2024):
        days = cal_2024.rebalance_dates(date(2024, 1, 1), date(2024, 1, 5), "daily")
        assert len(days) == 5

    def test_weekly_last_trading_day(self):
        cal = TradingCalendar.weekday_fallback(date(2024, 1, 1), date(2024, 1, 31))
        rebal = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 31), "weekly")
        # Each rebalance date should be the last trading day of its week
        for d in rebal[:-1]:  # last one might be end of range
            assert d.weekday() == 4  # Friday (last weekday)

    def test_monthly_last_trading_day(self):
        cal = TradingCalendar.weekday_fallback(date(2024, 1, 1), date(2024, 3, 31))
        rebal = cal.rebalance_dates(date(2024, 1, 1), date(2024, 3, 31), "monthly")
        assert len(rebal) == 3  # Jan, Feb, Mar
        # Jan 31 is Wednesday (trading day), should be the rebalance date
        assert rebal[0].month == 1
        assert rebal[1].month == 2
        assert rebal[2].month == 3

    def test_quarterly(self):
        cal = TradingCalendar.weekday_fallback(date(2024, 1, 1), date(2024, 12, 31))
        rebal = cal.rebalance_dates(date(2024, 1, 1), date(2024, 12, 31), "quarterly")
        assert len(rebal) == 4  # Q1, Q2, Q3, Q4

    def test_empty_range(self, cal_2024):
        rebal = cal_2024.rebalance_dates(date(2025, 1, 1), date(2025, 1, 31), "monthly")
        assert rebal == []

    def test_no_weekday_hardcode(self):
        """Calendar with a holiday (skip Friday Jan 5) — weekly rebalance uses Thursday."""
        days = [date(2024, 1, d) for d in [2, 3, 4, 8, 9, 10, 11, 12]]
        # Week 1: Tue-Thu (no Fri), Week 2: Mon-Fri
        cal = TradingCalendar.from_dates(days)
        rebal = cal.rebalance_dates(date(2024, 1, 2), date(2024, 1, 12), "weekly")
        assert rebal[0] == date(2024, 1, 4)  # Thursday (last day of week 1)
        assert rebal[1] == date(2024, 1, 12)  # Friday (last day of week 2)
