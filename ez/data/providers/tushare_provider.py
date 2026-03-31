"""Tushare Pro API data provider (A-share primary source).

[EXTENSION] — freely modifiable.

Tushare Pro provides comprehensive Chinese A-share market data.
Uses direct HTTP calls to the Tushare Pro API (no SDK dependency).
Requires a token: set TUSHARE_TOKEN env var or pass to constructor.

Extended APIs beyond DataProvider ABC:
- get_daily_basic(): PE/PB/turnover/market_cap fundamentals
- get_trade_cal(): Trading calendar with local cache
- get_index_kline(): Index K-line for benchmark comparison
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime

import httpx

from ez.data.provider import DataProvider
from ez.errors import ProviderError
from ez.types import Bar

logger = logging.getLogger(__name__)

_PERIOD_API_MAP = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
_SUPPORTED_MARKETS = {"cn_stock"}

# 2000 points = 200 calls/min → 0.35s interval (with safety margin)
_RATE_LIMIT_DELAY = 0.35
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds, doubles on each retry


def _date_to_tushare(d: date) -> str:
    """Convert date(2024, 1, 2) to Tushare format '20240102'."""
    return d.strftime("%Y%m%d")


def _tushare_to_datetime(s: str) -> datetime:
    """Convert Tushare date string '20240102' to datetime. Raises ProviderError on bad format."""
    try:
        return datetime.strptime(s, "%Y%m%d")
    except (ValueError, TypeError) as e:
        raise ProviderError(f"Invalid Tushare date format: '{s}'") from e


def _tushare_to_date(s: str) -> date:
    """Convert Tushare date string '20240102' to date. Raises ProviderError on bad format."""
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except (ValueError, TypeError) as e:
        raise ProviderError(f"Invalid Tushare date format: '{s}'") from e


class TushareDataProvider(DataProvider):
    """Tushare Pro API. Requires TUSHARE_TOKEN env var. A-share market only.

    Implements DataProvider ABC (get_kline, search_symbols) plus extended methods
    for trading calendar, fundamental data, and index benchmarks.
    """

    API_URL = "https://api.tushare.pro"

    def __init__(self, token: str | None = None, timeout: int = 10, store=None):
        self._token = token or os.environ.get("TUSHARE_TOKEN", "")
        self._client = httpx.Client(timeout=timeout)
        self._last_call_time: float = 0.0
        self._trade_cal_cache: dict[str, set[str]] = {}  # exchange -> set of YYYYMMDD
        self._symbol_cache: list[dict] | None = None  # cached stock + ETF list
        self._store = store  # optional DuckDBStore for persistent symbol cache

    @property
    def name(self) -> str:
        return "tushare"

    def close(self) -> None:
        """Close the HTTP client connection pool."""
        self._client.close()

    # ── DataProvider ABC implementation ───────────────────────────────

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        if not self._token:
            raise ProviderError("TUSHARE_TOKEN not set")
        if market not in _SUPPORTED_MARKETS:
            raise ProviderError(f"Tushare only supports markets: {_SUPPORTED_MARKETS}, got '{market}'")

        api_name = _PERIOD_API_MAP.get(period)
        if not api_name:
            raise ProviderError(f"Unsupported period '{period}'. Supported: {list(_PERIOD_API_MAP)}")

        ts_start = _date_to_tushare(start_date)
        ts_end = _date_to_tushare(end_date)

        kline_data = self._call_api(
            api_name=api_name,
            params={"ts_code": symbol, "start_date": ts_start, "end_date": ts_end},
            fields="ts_code,trade_date,open,high,low,close,vol",
        )
        if not kline_data:
            return []

        # For daily data, fetch adjustment factors for forward-adjusted close
        adj_map: dict[str, float] = {}
        if period == "daily":
            adj_data = self._call_api(
                api_name="adj_factor",
                params={"ts_code": symbol, "start_date": ts_start, "end_date": ts_end},
                fields="ts_code,trade_date,adj_factor",
            )
            adj_map = self._build_adj_map(adj_data)

        return self._parse_kline(kline_data, symbol, market, adj_map)

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        if not self._token:
            return []
        if market and market not in _SUPPORTED_MARKETS:
            return []

        self._ensure_symbol_cache()

        keyword_upper = keyword.upper()
        keyword_lower = keyword.lower()
        results = []
        for record in self._symbol_cache or []:
            ts_code = record.get("ts_code", "")
            name = record.get("name", "")
            if keyword_upper in ts_code.upper() or keyword_lower in name.lower():
                results.append({
                    "symbol": ts_code,
                    "name": name,
                    "area": record.get("area", ""),
                    "industry": record.get("industry", ""),
                })
        return results[:50]

    def _ensure_symbol_cache(self) -> None:
        """Load stock + ETF lists. Priority: memory → DuckDB → Tushare API."""
        if self._symbol_cache is not None:
            return

        # Try DuckDB cache first
        if self._store and hasattr(self._store, "symbols_count") and self._store.symbols_count() > 0:
            self._symbol_cache = self._store.query_symbols("", limit=99999)
            logger.info("Symbol cache loaded from DB: %d symbols", len(self._symbol_cache))
            return

        # Fetch from API
        all_symbols: list[dict] = []

        # 1. Stocks
        stock_data = self._call_api(
            api_name="stock_basic",
            params={"list_status": "L"},
            fields="ts_code,symbol,name,area,industry,market,list_date",
        )
        if stock_data:
            for row in stock_data["items"]:
                record = dict(zip(stock_data["fields"], row))
                all_symbols.append({
                    "ts_code": record.get("ts_code", ""),
                    "name": record.get("name", ""),
                    "area": record.get("area", ""),
                    "industry": record.get("industry", ""),
                })

        # 2. ETFs
        etf_data = self._call_api(
            api_name="fund_basic",
            params={"market": "E", "status": "L"},
            fields="ts_code,name,management,type,fund_type,market",
        )
        if etf_data:
            for row in etf_data["items"]:
                record = dict(zip(etf_data["fields"], row))
                all_symbols.append({
                    "ts_code": record.get("ts_code", ""),
                    "name": record.get("name", ""),
                    "area": record.get("management", ""),
                    "industry": "ETF",
                })

        logger.info("Symbol cache from API: %d stocks + %d ETFs",
                     len(stock_data["items"]) if stock_data else 0,
                     len(etf_data["items"]) if etf_data else 0)

        # Persist to DuckDB for next startup
        if self._store and hasattr(self._store, "save_symbols") and all_symbols:
            saved = self._store.save_symbols(all_symbols)
            logger.info("Saved %d symbols to DB", saved)

        self._symbol_cache = all_symbols

    # ── Extended APIs (not part of DataProvider ABC) ──────────────────

    def get_trade_cal(
        self, exchange: str = "SSE",
        start_date: date | None = None, end_date: date | None = None,
    ) -> list[date]:
        """Return list of trading days for the given exchange and date range.

        Results are cached in memory per exchange (calendar rarely changes).
        """
        if not self._token:
            raise ProviderError("TUSHARE_TOKEN not set")

        cache_key = exchange
        if cache_key not in self._trade_cal_cache:
            # Fetch full calendar for this exchange (cheap, do once)
            data = self._call_api(
                api_name="trade_cal",
                params={"exchange": exchange, "is_open": "1"},
                fields="cal_date",
            )
            if data:
                self._trade_cal_cache[cache_key] = {
                    row[0] for row in data["items"]
                }
            else:
                self._trade_cal_cache[cache_key] = set()

        all_dates = self._trade_cal_cache[cache_key]
        ts_start = _date_to_tushare(start_date) if start_date else "00000000"
        ts_end = _date_to_tushare(end_date) if end_date else "99999999"

        trading_days = sorted(d for d in all_dates if ts_start <= d <= ts_end)
        return [_tushare_to_date(d) for d in trading_days]

    def is_trading_day(self, d: date, exchange: str = "SSE") -> bool:
        """Check if a given date is a trading day."""
        cal = self.get_trade_cal(exchange)  # uses cache after first call
        return d in {td for td in cal} if cal else True  # default True if no calendar

    def get_daily_basic(
        self, symbol: str, start_date: date, end_date: date,
    ) -> list[dict]:
        """Fetch daily fundamental indicators: PE, PB, turnover, market cap, etc.

        Returns list of dicts with keys:
        ts_code, trade_date, close, turnover_rate, turnover_rate_f,
        volume_ratio, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio,
        total_share, float_share, total_mv, circ_mv
        """
        if not self._token:
            raise ProviderError("TUSHARE_TOKEN not set")

        data = self._call_api(
            api_name="daily_basic",
            params={
                "ts_code": symbol,
                "start_date": _date_to_tushare(start_date),
                "end_date": _date_to_tushare(end_date),
            },
            fields="ts_code,trade_date,close,turnover_rate,turnover_rate_f,"
                   "volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,"
                   "total_share,float_share,total_mv,circ_mv",
        )
        if not data:
            return []

        fields = data["fields"]
        results = []
        for row in data["items"]:
            record = dict(zip(fields, row))
            if "trade_date" in record and record["trade_date"]:
                record["trade_date"] = _tushare_to_date(record["trade_date"])
            results.append(record)

        results.sort(key=lambda r: r.get("trade_date", date.min))
        return results

    def get_fina_indicator(
        self, symbol: str, start_date: date, end_date: date,
    ) -> list[dict]:
        """Fetch pre-computed financial indicators with ann_date for PIT alignment.

        Returns list of dicts with keys including:
        ts_code, ann_date, end_date, roe, roe_waa, roa, grossprofit_margin,
        netprofit_margin, debt_to_assets, current_ratio, quick_ratio,
        q_revenue_yoy, q_profit_yoy, roe_yoy, eps, dt_eps

        Note: This API may require Tushare paid subscription (2000+ points).
        """
        if not self._token:
            raise ProviderError("TUSHARE_TOKEN not set")

        data = self._call_api(
            api_name="fina_indicator",
            params={
                "ts_code": symbol,
                "start_date": _date_to_tushare(start_date),
                "end_date": _date_to_tushare(end_date),
            },
            fields="ts_code,ann_date,end_date,"
                   "roe,roe_waa,roa,grossprofit_margin,netprofit_margin,"
                   "debt_to_assets,current_ratio,quick_ratio,"
                   "q_revenue_yoy,q_profit_yoy,roe_yoy,eps,dt_eps",
        )
        if not data:
            return []

        fields = data["fields"]
        results = []
        for row in data["items"]:
            record = dict(zip(fields, row))
            for date_field in ("ann_date", "end_date"):
                if date_field in record and record[date_field]:
                    record[date_field] = _tushare_to_date(record[date_field])
            # Normalize field names for FundamentalStore
            record["revenue_yoy"] = record.pop("q_revenue_yoy", None)
            record["profit_yoy"] = record.pop("q_profit_yoy", None)
            results.append(record)

        results.sort(key=lambda r: r.get("end_date", date.min))
        return results

    def get_index_kline(
        self, index_code: str, start_date: date, end_date: date,
    ) -> list[Bar]:
        """Fetch index daily K-line (e.g., '000001.SH' for Shanghai Composite).

        Common index codes:
        - 000001.SH: Shanghai Composite
        - 399001.SZ: Shenzhen Component
        - 399006.SZ: ChiNext
        - 000300.SH: CSI 300
        - 000905.SH: CSI 500
        """
        if not self._token:
            raise ProviderError("TUSHARE_TOKEN not set")

        data = self._call_api(
            api_name="index_daily",
            params={
                "ts_code": index_code,
                "start_date": _date_to_tushare(start_date),
                "end_date": _date_to_tushare(end_date),
            },
            fields="ts_code,trade_date,open,high,low,close,vol",
        )
        if not data:
            return []

        fields = data["fields"]
        idx = {f: i for i, f in enumerate(fields)}
        bars = []
        for row in data["items"]:
            try:
                dt = _tushare_to_datetime(row[idx["trade_date"]])
                vol_raw = row[idx["vol"]]
                volume = int(float(vol_raw) * 100) if vol_raw is not None else 0
                close = float(row[idx["close"]])
                bars.append(Bar(
                    time=dt, symbol=index_code, market="cn_index",
                    open=float(row[idx["open"]]),
                    high=float(row[idx["high"]]),
                    low=float(row[idx["low"]]),
                    close=close, adj_close=close,
                    volume=volume,
                ))
            except (ValueError, KeyError, IndexError, TypeError):
                continue

        bars.sort(key=lambda b: b.time)
        return bars

    # ── Internal helpers ──────────────────────────────────────────────

    def _call_api(
        self, api_name: str, params: dict, fields: str,
    ) -> dict | None:
        """Make a Tushare API call with rate-limit throttling and exponential backoff retry.

        Retries up to _MAX_RETRIES times on rate-limit errors (code 2002) with
        exponential backoff (1s, 2s, 4s). Non-rate-limit errors raise immediately.
        """
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            self._throttle()

            payload = {
                "api_name": api_name,
                "token": self._token,
                "params": params,
                "fields": fields,
            }

            try:
                resp = self._client.post(self.API_URL, json=payload)
                resp.raise_for_status()
                body = resp.json()
            except httpx.HTTPError as e:
                raise ProviderError(f"Tushare HTTP error ({api_name}): {e}") from e

            code = body.get("code", -1)
            if code == 0:
                data = body.get("data")
                if not data or not data.get("items"):
                    return None
                return data

            msg = body.get("msg", "unknown error")

            # Rate limit / permission error → retry with backoff
            if code == 2002 and attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                logger.warning("Tushare rate limited (%s), retry %d/%d in %.1fs: %s",
                               api_name, attempt + 1, _MAX_RETRIES, wait, msg)
                time.sleep(wait)
                last_error = ProviderError(f"Tushare rate limited ({api_name}): {msg}")
                continue

            # Auth error on final attempt or non-retryable error
            if code == 2002:
                raise ProviderError(f"Tushare auth/rate-limit error ({api_name}): {msg}")
            raise ProviderError(f"Tushare API error ({api_name}, code={code}): {msg}")

        # Should not reach here, but safety
        if last_error:
            raise last_error
        return None

    def _throttle(self) -> None:
        """Enforce minimum delay between API calls."""
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)
        self._last_call_time = time.monotonic()

    @staticmethod
    def _build_adj_map(adj_data: dict | None) -> dict[str, float]:
        """Build {trade_date: adj_factor} map from adj_factor API response."""
        if not adj_data:
            return {}
        fields = adj_data["fields"]
        date_idx = fields.index("trade_date")
        factor_idx = fields.index("adj_factor")
        return {row[date_idx]: float(row[factor_idx]) for row in adj_data["items"]}

    @staticmethod
    def _parse_kline(
        data: dict, symbol: str, market: str,
        adj_map: dict[str, float],
    ) -> list[Bar]:
        """Convert Tushare API response into sorted list of Bar objects.

        Forward-adjusted close: adj_close = close * adj_factor / latest_adj_factor
        """
        fields = data["fields"]
        idx = {f: i for i, f in enumerate(fields)}
        latest_adj = max(adj_map.values()) if adj_map else 1.0

        bars = []
        for row in data["items"]:
            try:
                trade_date = row[idx["trade_date"]]
                dt = _tushare_to_datetime(trade_date)
                close = float(row[idx["close"]])

                if adj_map and trade_date in adj_map:
                    adj_close = close * adj_map[trade_date] / latest_adj
                else:
                    adj_close = close

                vol_raw = row[idx["vol"]]
                volume = int(float(vol_raw) * 100) if vol_raw is not None else 0

                bars.append(Bar(
                    time=dt, symbol=symbol, market=market,
                    open=float(row[idx["open"]]),
                    high=float(row[idx["high"]]),
                    low=float(row[idx["low"]]),
                    close=close,
                    adj_close=round(adj_close, 4),
                    volume=volume,
                ))
            except (ValueError, KeyError, IndexError, TypeError) as e:
                logger.warning("Skipping bad row for %s: %s — %s", symbol, row, e)
                continue

        bars.sort(key=lambda b: b.time)
        return bars
