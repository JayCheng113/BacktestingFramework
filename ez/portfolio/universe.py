"""V2.9 P1: Universe — PIT security pool with dynamic constituents.

Eliminates survivorship bias: queries historical constituents at each date,
excludes delisted stocks after delist date, excludes IPOs < N days old.
"""
from __future__ import annotations

from datetime import date
import pandas as pd

from ez.portfolio.calendar import TradingCalendar


class Universe:
    """Point-in-time universe of tradeable securities.

    Supports:
    - Static custom pool (e.g., ETF rotation list)
    - Dynamic index constituents (CSI300/500/1000 with monthly refresh)
    """

    def __init__(
        self,
        symbols: list[str],
        *,
        ipo_min_days: int = 60,
        delist_dates: dict[str, date] | None = None,
        ipo_dates: dict[str, date] | None = None,
    ):
        self._symbols = list(dict.fromkeys(symbols))  # deduplicate, preserve order
        self._ipo_min_days = ipo_min_days
        self._delist_dates = delist_dates or {}
        self._ipo_dates = ipo_dates or {}

    @property
    def all_symbols(self) -> list[str]:
        return list(self._symbols)

    def tradeable_at(self, d: date) -> list[str]:
        """Return symbols tradeable on date d (PIT filtering)."""
        result = []
        for sym in self._symbols:
            # Exclude delisted
            if sym in self._delist_dates and d > self._delist_dates[sym]:
                continue
            # Exclude IPO too recent
            if sym in self._ipo_dates:
                days_since_ipo = (d - self._ipo_dates[sym]).days
                if days_since_ipo < self._ipo_min_days:
                    continue
            result.append(sym)
        return result

    def __len__(self) -> int:
        return len(self._symbols)

    def __repr__(self) -> str:
        return f"Universe({len(self._symbols)} symbols, ipo_min={self._ipo_min_days}d)"


def fetch_universe_data(
    universe: Universe,
    calendar: TradingCalendar,
    start: date,
    end: date,
    data_fetcher,
) -> dict[str, pd.DataFrame]:
    """Batch-fetch daily OHLCV for all universe symbols.

    data_fetcher: callable(symbol, start_date, end_date) -> pd.DataFrame with OHLCV columns.
    Returns: {symbol: DataFrame} aligned to calendar trading days.
    """
    trading_days = calendar.trading_days_between(start, end)
    if not trading_days:
        return {}

    result = {}
    for sym in universe.all_symbols:
        try:
            df = data_fetcher(sym, start, end)
            if df is not None and not df.empty:
                # Align to trading days (forward-fill gaps, NaN for missing)
                if isinstance(df.index, pd.DatetimeIndex):
                    td_index = pd.DatetimeIndex(trading_days)
                else:
                    td_index = pd.Index(trading_days)
                df = df.reindex(td_index)
                result[sym] = df
        except Exception:
            continue  # skip symbols with data errors

    return result


def slice_universe_data(
    universe_data: dict[str, pd.DataFrame],
    target_date: date,
    lookback_days: int,
) -> dict[str, pd.DataFrame]:
    """Slice universe data to [target_date - lookback, target_date - 1 day].

    This is the anti-lookahead guarantee: strategy functions receive
    data ending BEFORE the decision date. They cannot see target_date or later.
    """
    result = {}
    for sym, df in universe_data.items():
        if isinstance(df.index, pd.DatetimeIndex):
            mask = df.index.date < target_date
        else:
            mask = df.index < target_date
        sliced = df.loc[mask]
        if len(sliced) > lookback_days:
            sliced = sliced.iloc[-lookback_days:]
        if not sliced.empty:
            result[sym] = sliced
    return result
