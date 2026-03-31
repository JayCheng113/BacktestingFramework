"""AKShare data provider — free, no registration, full history for stocks + ETFs.

[EXTENSION] — new in V2.11.1.

Uses akshare package which wraps East Money / Sina / Tencent public APIs.
Covers A-share stocks, ETFs, and indexes with data back to listing date.

Rate limit: ~0.5s between calls to avoid East Money IP bans.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

from ez.data.provider import DataProvider
from ez.errors import ProviderError
from ez.types import Bar

logger = logging.getLogger(__name__)

_RATE_LIMIT_DELAY = 0.6  # seconds between API calls (East Money is aggressive)

# ETF code prefixes (Shanghai 51xxxx, Shenzhen 15xxxx/16xxxx)
_ETF_PREFIXES = ("51", "15", "16")


class AKShareDataProvider(DataProvider):
    """AKShare provider — free A-share stocks + ETFs + indexes.

    No token needed. Data sourced from East Money (dongcai) via akshare.
    Supports daily/weekly/monthly OHLCV with forward-adjusted close.
    """

    def __init__(self):
        self._last_call_time: float = 0.0

    @property
    def name(self) -> str:
        return "akshare"

    def close(self) -> None:
        pass  # no persistent connections

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        if market != "cn_stock":
            return []

        try:
            import akshare as ak
        except ImportError:
            raise ProviderError("akshare package not installed: pip install akshare")

        self._throttle()

        code = symbol.split(".")[0] if "." in symbol else symbol
        is_etf = code.startswith(_ETF_PREFIXES)

        # Map period to akshare period names
        ak_period = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}.get(period)
        if not ak_period:
            return []

        ts_start = start_date.strftime("%Y%m%d")
        ts_end = end_date.strftime("%Y%m%d")

        # Fetch qfq (forward-adjusted) — required
        try:
            if is_etf:
                df_adj = ak.fund_etf_hist_em(
                    symbol=code, period=ak_period,
                    start_date=ts_start, end_date=ts_end, adjust="qfq",
                )
            else:
                df_adj = ak.stock_zh_a_hist(
                    symbol=code, period=ak_period,
                    start_date=ts_start, end_date=ts_end, adjust="qfq",
                )
        except Exception as e:
            logger.warning("AKShare qfq fetch failed for %s: %s", symbol, e)
            return []

        # Fetch raw (unadjusted) — best-effort, degrade gracefully
        df_raw = None
        try:
            self._throttle()
            if is_etf:
                df_raw = ak.fund_etf_hist_em(
                    symbol=code, period=ak_period,
                    start_date=ts_start, end_date=ts_end, adjust="",
                )
            else:
                df_raw = ak.stock_zh_a_hist(
                    symbol=code, period=ak_period,
                    start_date=ts_start, end_date=ts_end, adjust="",
                )
        except Exception as e:
            logger.warning("AKShare raw fetch failed for %s, using qfq for all fields: %s", symbol, e)
            # df_raw stays None → raw_map empty → fallback to adj values in bar construction

        if df_adj is None or df_adj.empty:
            return []

        # Build raw OHLCV lookup for correct close/adj_close + raw open/high/low
        raw_map: dict[str, dict] = {}
        if df_raw is not None and not df_raw.empty:
            for _, row in df_raw.iterrows():
                raw_map[str(row["日期"])[:10]] = {
                    "open": float(row["开盘"]), "high": float(row["最高"]),
                    "low": float(row["最低"]), "close": float(row["收盘"]),
                    "volume": int(float(row["成交量"])),
                }

        bars = []
        for _, row in df_adj.iterrows():
            try:
                date_str = str(row["日期"])[:10]
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                adj_close = float(row["收盘"])
                raw = raw_map.get(date_str)
                bars.append(Bar(
                    time=dt, symbol=symbol, market=market,
                    open=raw["open"] if raw else float(row["开盘"]),
                    high=raw["high"] if raw else float(row["最高"]),
                    low=raw["low"] if raw else float(row["最低"]),
                    close=raw["close"] if raw else adj_close,   # raw price for limit checks
                    adj_close=adj_close,                         # forward-adjusted for returns
                    volume=raw["volume"] if raw else int(float(row["成交量"])),
                ))
            except (ValueError, KeyError, TypeError) as e:
                logger.debug("AKShare skip bad row for %s: %s", symbol, e)
                continue

        bars.sort(key=lambda b: b.time)
        return bars

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        """AKShare doesn't have a fast symbol search. Return empty (other providers handle this)."""
        return []

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)
        self._last_call_time = time.monotonic()
