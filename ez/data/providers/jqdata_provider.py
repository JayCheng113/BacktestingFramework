"""JQData (聚宽) data provider for independent verification and fallback.

[EXTENSION] — new in V3.

Uses jqdatasdk package for A-share market data. Primary use case is cross-source
verification against Tushare, not replacement.

Priority: lowest in provider chain (below parquet cache / Tushare / AKShare).

Auth: JQDATA_USERNAME + JQDATA_PASSWORD env vars (or constructor params).
Free account window: approximately 15 months ago to 3 months ago.

Rate limit: 0.5s between API calls.
"""
from __future__ import annotations

import logging
import os
import time
import threading
from datetime import date, datetime

import pandas as pd

from ez.data.provider import DataProvider
from ez.types import Bar

logger = logging.getLogger(__name__)

_RATE_LIMIT_DELAY = 0.5

# Code suffix mapping: Tushare <-> JQData
_TS_TO_JQ_SUFFIX = {
    ".SZ": ".XSHE",
    ".SH": ".XSHG",
    ".BJ": ".XBJE",
}
_JQ_TO_TS_SUFFIX = {v: k for k, v in _TS_TO_JQ_SUFFIX.items()}


class JQDataProvider(DataProvider):
    """JQData (聚宽) data provider for independent verification and fallback.

    Priority: lower than Tushare/AKShare/parquet cache. Primary use case
    is cross-source verification, not replacement.
    """

    def __init__(self, username: str | None = None, password: str | None = None):
        """Read credentials from env or params. Auth is lazy (first call)."""
        self._username = username or os.environ.get("JQDATA_USERNAME", "")
        self._password = password or os.environ.get("JQDATA_PASSWORD", "")
        self._authenticated = False
        self._auth_failed = False
        self._last_call_time: float = 0.0
        self._throttle_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "jqdata"

    def close(self) -> None:
        """Logout from jqdatasdk if authenticated."""
        if self._authenticated:
            try:
                import jqdatasdk as jq
                jq.logout()
            except Exception:
                pass
            self._authenticated = False

    # ── Code mapping ─────────────────────────────────────────────────

    @staticmethod
    def to_jq_code(ts_code: str) -> str:
        """Convert Tushare code to JQData code.

        000001.SZ -> 000001.XSHE
        600000.SH -> 600000.XSHG
        830799.BJ -> 830799.XBJE
        """
        for ts_suffix, jq_suffix in _TS_TO_JQ_SUFFIX.items():
            if ts_code.endswith(ts_suffix):
                return ts_code[: -len(ts_suffix)] + jq_suffix
        return ts_code

    @staticmethod
    def from_jq_code(jq_code: str) -> str:
        """Convert JQData code to Tushare code.

        000001.XSHE -> 000001.SZ
        600000.XSHG -> 600000.SH
        830799.XBJE -> 830799.BJ
        """
        for jq_suffix, ts_suffix in _JQ_TO_TS_SUFFIX.items():
            if jq_code.endswith(jq_suffix):
                return jq_code[: -len(jq_suffix)] + ts_suffix
        return jq_code

    # ── DataProvider ABC ─────────────────────────────────────────────

    def get_kline(
        self,
        symbol: str,
        market: str,
        period: str,
        start_date: date,
        end_date: date,
    ) -> list[Bar]:
        """Fetch daily OHLCV bars via jqdatasdk.

        Only supports cn_stock market, daily period.
        Returns bars with raw close, forward-adjusted adj_close, and factor.
        """
        if market != "cn_stock":
            return []
        if period != "daily":
            return []

        df = self.get_daily(symbol, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        if df is None or df.empty:
            return []

        bars: list[Bar] = []
        for _, row in df.iterrows():
            try:
                dt = row["date"]
                if isinstance(dt, str):
                    dt = datetime.strptime(dt, "%Y-%m-%d")
                elif isinstance(dt, date) and not isinstance(dt, datetime):
                    dt = datetime(dt.year, dt.month, dt.day)

                bars.append(Bar(
                    time=dt,
                    symbol=symbol,
                    market=market,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["raw_close"]),
                    adj_close=round(float(row["adj_close"]), 4),
                    volume=int(row["volume"]),
                ))
            except (ValueError, KeyError, TypeError) as e:
                logger.debug("JQData skip bad row for %s: %s", symbol, e)
                continue

        bars.sort(key=lambda b: b.time)
        return bars

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        """JQData symbol search is not implemented. Other providers handle this."""
        return []

    # ── Extended APIs ────────────────────────────────────────────────

    def get_daily(
        self, ts_code: str, start_date: str, end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch daily OHLCV with both raw and adjusted prices.

        Returns DataFrame with columns:
            date, open, high, low, close (=adj_close), raw_close,
            adj_close, volume, amount, pre_close, factor

        Strategy:
            - Fetch fq='none' to get raw prices + factor
            - Compute adj_close = raw_close * factor / factor.iloc[-1]
              (forward adjustment, same semantics as Tushare)

        Returns None on any error (auth failure, network, no data, etc.).
        """
        if not self._ensure_auth():
            return None

        jq_code = self.to_jq_code(ts_code)

        try:
            import jqdatasdk as jq

            # Single fetch: raw prices + factor column
            self._throttle()
            df_raw = jq.get_price(
                jq_code,
                start_date=start_date,
                end_date=end_date,
                frequency="daily",
                fq=None,
                skip_paused=False,
                fill_paused=True,
            )

            if df_raw is None or df_raw.empty:
                return None

            # Build result DataFrame
            result = pd.DataFrame()
            result["date"] = df_raw.index
            result["open"] = df_raw["open"].values
            result["high"] = df_raw["high"].values
            result["low"] = df_raw["low"].values
            result["raw_close"] = df_raw["close"].values
            result["volume"] = df_raw["volume"].astype(int).values
            result["amount"] = df_raw["money"].values if "money" in df_raw.columns else 0.0
            result["pre_close"] = df_raw["pre_close"].values if "pre_close" in df_raw.columns else float("nan")

            # Factor and forward-adjusted close
            if "factor" in df_raw.columns:
                factor = df_raw["factor"].values
                result["factor"] = factor
                # Forward adjustment: adj_close = raw_close * factor / latest_factor
                latest_factor = factor[-1] if len(factor) > 0 and factor[-1] != 0 else 1.0
                result["adj_close"] = result["raw_close"] * factor / latest_factor
            else:
                result["factor"] = 1.0
                result["adj_close"] = result["raw_close"]

            # Alias: close = adj_close (convention for downstream consumers)
            result["close"] = result["adj_close"]

            result = result.reset_index(drop=True)
            return result

        except Exception as e:
            logger.warning("JQData get_daily failed for %s: %s", ts_code, e)
            return None

    def get_valuation(
        self, ts_code: str, start_date: str, end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch PE/PB/market cap/circulating market cap/turnover ratio.

        Returns DataFrame with columns:
            date, pe_ratio, pb_ratio, market_cap, circulating_market_cap, turnover_ratio
        """
        if not self._ensure_auth():
            return None

        jq_code = self.to_jq_code(ts_code)

        try:
            import jqdatasdk as jq

            self._throttle()
            df = jq.get_valuation(
                [jq_code],
                start_date=start_date,
                end_date=end_date,
                fields=[
                    "pe_ratio", "pb_ratio", "market_cap",
                    "circulating_market_cap", "turnover_ratio",
                ],
            )

            if df is None or df.empty:
                return None

            # Normalize: jqdata returns 'day' column for date
            if "day" in df.columns:
                df = df.rename(columns={"day": "date"})

            return df

        except Exception as e:
            logger.warning("JQData get_valuation failed for %s: %s", ts_code, e)
            return None

    def get_fundamentals(
        self, ts_code: str, watch_date: str, count: int = 4,
    ) -> pd.DataFrame | None:
        """Fetch multi-quarter financials (total_revenue, net_profit, etc.).

        Args:
            ts_code: Tushare-format stock code (e.g. '000001.SZ')
            watch_date: Reference date (YYYY-MM-DD)
            count: Number of quarters to look back

        Returns DataFrame or None on error.
        """
        if not self._ensure_auth():
            return None

        jq_code = self.to_jq_code(ts_code)

        try:
            import jqdatasdk as jq

            self._throttle()
            df = jq.get_history_fundamentals(
                jq_code,
                fields=["total_revenue", "net_profit"],
                watch_date=watch_date,
                count=count,
                interval="1q",
            )

            if df is None or df.empty:
                return None

            return df

        except Exception as e:
            logger.warning("JQData get_fundamentals failed for %s: %s", ts_code, e)
            return None

    def get_index_constituents(
        self, index_code: str, date_str: str,
    ) -> list[str] | None:
        """Get index constituent stocks, returned as Tushare-format codes.

        Args:
            index_code: Tushare-format index code (e.g. '000300.SH' for CSI300)
            date_str: Date string (YYYY-MM-DD)

        Returns list of Tushare-format stock codes, or None on error.
        """
        if not self._ensure_auth():
            return None

        jq_index = self.to_jq_code(index_code)

        try:
            import jqdatasdk as jq

            self._throttle()
            stocks = jq.get_index_stocks(jq_index, date=date_str)

            if not stocks:
                return None

            return [self.from_jq_code(s) for s in stocks]

        except Exception as e:
            logger.warning("JQData get_index_constituents failed for %s: %s", index_code, e)
            return None

    # ── Internal helpers ─────────────────────────────────────────────

    def _ensure_auth(self) -> bool:
        """Lazy auth: authenticate on first API call. Returns True if ok."""
        if self._authenticated:
            return True
        if self._auth_failed:
            return False
        if not self._username or not self._password:
            logger.debug("JQData credentials not configured, skipping")
            self._auth_failed = True
            return False

        try:
            import jqdatasdk as jq
            jq.auth(self._username, self._password)
            self._authenticated = True
            logger.info("JQData authenticated successfully")
            return True
        except ImportError:
            logger.debug("jqdatasdk not installed, skipping JQData provider")
            self._auth_failed = True
            return False
        except Exception as e:
            logger.warning("JQData auth failed: %s", e)
            self._auth_failed = True
            return False

    def _throttle(self) -> None:
        """Enforce minimum delay between API calls."""
        with self._throttle_lock:
            elapsed = time.monotonic() - self._last_call_time
            if elapsed < _RATE_LIMIT_DELAY:
                time.sleep(_RATE_LIMIT_DELAY - elapsed)
            self._last_call_time = time.monotonic()
