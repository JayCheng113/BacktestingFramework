"""V2.9 P0: TradingCalendar — unified trading calendar for rebalancing and date alignment.

All rebalance date calculations and date alignment go through this module.
No weekday/week-number hardcoding — uses actual exchange trading days.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

import pandas as pd


RebalanceFreq = Literal["daily", "weekly", "monthly", "quarterly"]


class TradingCalendar:
    """Trading calendar backed by a sorted list of trading days.

    Can be constructed from:
    - A list of dates (e.g., from Tushare trade_cal)
    - A date range with weekday-only fallback (for testing without Tushare)
    """

    def __init__(self, trading_days: list[date]):
        if not trading_days:
            raise ValueError("trading_days must not be empty")
        self._days = sorted(set(trading_days))
        self._day_set = set(self._days)

    # ── Factory methods ────────────────────────────────────────────────

    @classmethod
    def from_dates(cls, trading_days: list[date]) -> TradingCalendar:
        return cls(trading_days)

    _MARKET_TO_EXCHANGE = {"cn_stock": "SSE", "us_stock": "NYSE", "hk_stock": "HKEX"}

    @classmethod
    def from_market(cls, market: str) -> TradingCalendar:
        """V2.15: Factory from market code.
        Tries Tushare trade_cal first, falls back to weekday calendar."""
        from datetime import date, timedelta
        end = date.today()
        start = end - timedelta(days=365 * 5)
        exchange = cls._MARKET_TO_EXCHANGE.get(market, "SSE")
        try:
            from ez.data.providers.tushare_provider import TushareProvider
            tp = TushareProvider()
            days = tp.get_trade_cal(exchange, start, end)
            if days:
                return cls.from_dates(days)
        except Exception:
            pass
        return cls.weekday_fallback(start, end)

    @classmethod
    def weekday_fallback(cls, start: date, end: date) -> TradingCalendar:
        """Generate Mon-Fri calendar (no holidays). For testing only."""
        days = []
        d = start
        while d <= end:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return cls(days)

    # ── Query ──────────────────────────────────────────────────────────

    def is_trading_day(self, d: date) -> bool:
        return d in self._day_set

    def trading_days_between(self, start: date, end: date) -> list[date]:
        """Return trading days in [start, end] inclusive."""
        return [d for d in self._days if start <= d <= end]

    @property
    def all_days(self) -> list[date]:
        return list(self._days)

    @property
    def start(self) -> date:
        return self._days[0]

    @property
    def end(self) -> date:
        return self._days[-1]

    def prev_trading_day(self, d: date) -> date | None:
        """Return the trading day strictly before d, or None."""
        idx = self._bisect_left(d)
        return self._days[idx - 1] if idx > 0 else None

    def next_trading_day(self, d: date) -> date | None:
        """Return the trading day on or after d, or None."""
        idx = self._bisect_left(d)
        return self._days[idx] if idx < len(self._days) else None

    # ── Rebalance dates ────────────────────────────────────────────────

    _VALID_FREQS = {"daily", "weekly", "monthly", "quarterly"}

    def rebalance_dates(
        self, start: date, end: date, freq: RebalanceFreq,
        rebal_weekday: int | None = None,
    ) -> list[date]:
        """Compute rebalance dates within [start, end].

        Raises ValueError for unrecognized freq.

        - daily: every trading day
        - weekly: last trading day of each calendar week (default), or the
          trading day closest to ``rebal_weekday`` (0=Mon .. 4=Fri) within
          each week.  If that weekday is not a trading day, the nearest
          **preceding** trading day in the same week is used.
        - monthly: last trading day of each calendar month
        - quarterly: last trading day of each calendar quarter
        """
        if freq not in self._VALID_FREQS:
            raise ValueError(f"Invalid freq '{freq}'. Must be one of: {sorted(self._VALID_FREQS)}")

        days = self.trading_days_between(start, end)
        if not days:
            return []

        if freq == "daily":
            return days

        # Weekly with specific weekday: group by ISO week, pick target day
        if freq == "weekly" and rebal_weekday is not None:
            if not (0 <= rebal_weekday <= 4):
                raise ValueError(f"rebal_weekday must be 0-4 (Mon-Fri), got {rebal_weekday}")
            from itertools import groupby
            result = []
            for _, week_days in groupby(days, key=lambda d: d.isocalendar()[:2]):
                week_list = list(week_days)
                # Find target weekday or nearest trading day:
                # 1. Exact match
                # 2. Nearest FOLLOWING trading day in the same week (e.g. Mon holiday → Tue)
                # 3. Nearest PRECEDING trading day (e.g. Fri holiday → Thu)
                best = None
                best_after = None
                for d in week_list:
                    if d.weekday() == rebal_weekday:
                        best = d
                        break
                    if d.weekday() < rebal_weekday:
                        best = d  # last one before target
                    elif best_after is None and d.weekday() > rebal_weekday:
                        best_after = d  # first one after target
                # Prefer exact > next-after > last-before > last-of-week
                if best is None and best_after is not None:
                    best = best_after
                if best is None:
                    best = week_list[-1]
                result.append(best)
            return result

        result = []
        for i, d in enumerate(days):
            is_last = (i == len(days) - 1)
            if not is_last:
                next_d = days[i + 1]
                if freq == "weekly" and next_d.isocalendar()[1] != d.isocalendar()[1]:
                    result.append(d)
                elif freq == "monthly" and next_d.month != d.month:
                    result.append(d)
                elif freq == "quarterly":
                    q_cur = (d.month - 1) // 3
                    q_next = (next_d.month - 1) // 3
                    if q_cur != q_next or next_d.year != d.year:
                        result.append(d)
            else:
                result.append(d)

        return result

    # ── Internal ───────────────────────────────────────────────────────

    def _bisect_left(self, d: date) -> int:
        lo, hi = 0, len(self._days)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._days[mid] < d:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def __len__(self) -> int:
        return len(self._days)

    def __repr__(self) -> str:
        return f"TradingCalendar({self._days[0]}..{self._days[-1]}, {len(self._days)} days)"
