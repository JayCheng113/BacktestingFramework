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


class TestRebalWeekday:
    """V2.16.2: rebal_weekday parameter for weekly rebalancing."""

    def _make_cal(self):
        # Week 1 (Jan 1-5): Mon-Fri all trading
        # Week 2 (Jan 8-12): Mon is holiday, Tue-Fri trading
        days = [date(2024, 1, d) for d in [1, 2, 3, 4, 5, 9, 10, 11, 12]]
        return TradingCalendar.from_dates(days)

    def test_exact_match(self):
        """Target weekday is a trading day — pick it."""
        cal = self._make_cal()
        # Thursday = weekday 3
        rebal = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 12), "weekly", rebal_weekday=3)
        assert rebal[0] == date(2024, 1, 4)  # Week 1 Thu
        assert rebal[1] == date(2024, 1, 11)  # Week 2 Thu

    def test_friday_exact(self):
        """Friday (weekday=4) — exact match both weeks."""
        cal = self._make_cal()
        rebal = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 12), "weekly", rebal_weekday=4)
        assert rebal[0] == date(2024, 1, 5)  # Week 1 Fri
        assert rebal[1] == date(2024, 1, 12)  # Week 2 Fri

    def test_next_after_fallback(self):
        """Monday is holiday in week 2 — should fall back to Tuesday (next-after)."""
        cal = self._make_cal()
        # Monday = weekday 0; week 2 has no Mon
        rebal = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 12), "weekly", rebal_weekday=0)
        assert rebal[0] == date(2024, 1, 1)   # Week 1 Mon (exact)
        assert rebal[1] == date(2024, 1, 9)   # Week 2 Tue (next-after, NOT Fri!)

    def test_last_before_fallback(self):
        """Target is Fri but Fri is holiday — fall back to Thu (last-before)."""
        # Week with only Mon-Thu
        days = [date(2024, 1, d) for d in [1, 2, 3, 4]]
        cal = TradingCalendar.from_dates(days)
        rebal = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 4), "weekly", rebal_weekday=4)
        assert rebal[0] == date(2024, 1, 4)  # Thu (last-before Fri)

    def test_none_preserves_default(self):
        """rebal_weekday=None uses last-of-week (original behavior)."""
        cal = self._make_cal()
        default = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 12), "weekly")
        explicit_none = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 12), "weekly", rebal_weekday=None)
        assert default == explicit_none

    def test_invalid_weekday_raises(self):
        cal = self._make_cal()
        with pytest.raises(ValueError, match="rebal_weekday must be 0-4"):
            cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 12), "weekly", rebal_weekday=5)

    def test_non_weekly_ignores(self):
        """rebal_weekday is ignored for non-weekly freq."""
        cal = self._make_cal()
        daily = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 12), "daily", rebal_weekday=3)
        daily_no = cal.rebalance_dates(date(2024, 1, 1), date(2024, 1, 12), "daily")
        assert daily == daily_no
