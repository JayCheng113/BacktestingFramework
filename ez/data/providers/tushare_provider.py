"""Tushare Pro API data provider (A-share primary source).

[EXTENSION] — freely modifiable.

Tushare Pro provides comprehensive Chinese A-share market data.
Uses direct HTTP calls to the Tushare Pro API (no SDK dependency).
Requires a token: set TUSHARE_TOKEN env var or pass to constructor.
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

# Map our period names to Tushare API names
_PERIOD_API_MAP = {
    "daily": "daily",
    "weekly": "weekly",
    "monthly": "monthly",
}

# Tushare only serves Chinese A-shares
_SUPPORTED_MARKETS = {"cn_stock"}

# Minimum delay between consecutive API calls to respect rate limits (seconds)
_RATE_LIMIT_DELAY = 0.3


def _date_to_tushare(d: date) -> str:
    """Convert date(2024, 1, 2) to Tushare format '20240102'."""
    return d.strftime("%Y%m%d")


def _tushare_to_datetime(s: str) -> datetime:
    """Convert Tushare date string '20240102' to datetime."""
    return datetime.strptime(s, "%Y%m%d")


class TushareDataProvider(DataProvider):
    """Tushare Pro API. Requires TUSHARE_TOKEN env var. A-share market only."""

    API_URL = "http://api.tushare.pro"

    def __init__(self, token: str | None = None, timeout: int = 10):
        self._token = token or os.environ.get("TUSHARE_TOKEN", "")
        self._client = httpx.Client(timeout=timeout)
        self._last_call_time: float = 0.0

    @property
    def name(self) -> str:
        return "tushare"

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        if not self._token:
            raise ProviderError("TUSHARE_TOKEN not set")

        if market not in _SUPPORTED_MARKETS:
            raise ProviderError(
                f"Tushare only supports markets: {_SUPPORTED_MARKETS}, got '{market}'"
            )

        api_name = _PERIOD_API_MAP.get(period)
        if not api_name:
            raise ProviderError(
                f"Unsupported period '{period}'. Supported: {list(_PERIOD_API_MAP)}"
            )

        ts_start = _date_to_tushare(start_date)
        ts_end = _date_to_tushare(end_date)

        # Fetch OHLCV data
        kline_data = self._call_api(
            api_name=api_name,
            params={"ts_code": symbol, "start_date": ts_start, "end_date": ts_end},
            fields="ts_code,trade_date,open,high,low,close,vol",
        )

        if not kline_data:
            return []

        # For daily data, also fetch adjustment factors for forward-adjusted close
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

        # Use stock_basic API to search by name or code
        data = self._call_api(
            api_name="stock_basic",
            params={"list_status": "L"},  # listed stocks only
            fields="ts_code,symbol,name,area,industry,market,list_date",
        )

        if not data:
            return []

        fields = data["fields"]
        items = data["items"]
        keyword_upper = keyword.upper()

        results = []
        for row in items:
            record = dict(zip(fields, row))
            ts_code = record.get("ts_code", "")
            name = record.get("name", "")
            # Match on code or name (case-insensitive)
            if keyword_upper in ts_code.upper() or keyword.lower() in name.lower():
                results.append({
                    "symbol": ts_code,
                    "name": name,
                    "area": record.get("area", ""),
                    "industry": record.get("industry", ""),
                })
        return results[:50]  # cap results

    # ── Internal helpers ──────────────────────────────────────────────

    def _call_api(
        self, api_name: str, params: dict, fields: str,
    ) -> dict | None:
        """Make a single Tushare Pro API call with rate-limit throttling.

        Returns the 'data' dict from response (with 'fields' and 'items'),
        or None if the response is empty.
        """
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

        # Check Tushare error codes
        code = body.get("code", -1)
        if code != 0:
            msg = body.get("msg", "unknown error")
            if code == 2002:
                raise ProviderError(f"Tushare auth error: {msg}")
            raise ProviderError(f"Tushare API error (code={code}): {msg}")

        data = body.get("data")
        if not data or not data.get("items"):
            return None
        return data

    def _throttle(self) -> None:
        """Enforce minimum delay between API calls to respect rate limits."""
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < _RATE_LIMIT_DELAY:
            time.sleep(_RATE_LIMIT_DELAY - elapsed)
        self._last_call_time = time.monotonic()

    @staticmethod
    def _build_adj_map(adj_data: dict | None) -> dict[str, float]:
        """Build {trade_date: adj_factor} map from adj_factor API response.

        Returns empty dict if adj_data is None (caller should use close as adj_close).
        """
        if not adj_data:
            return {}

        fields = adj_data["fields"]
        items = adj_data["items"]
        date_idx = fields.index("trade_date")
        factor_idx = fields.index("adj_factor")

        return {row[date_idx]: float(row[factor_idx]) for row in items}

    @staticmethod
    def _parse_kline(
        data: dict, symbol: str, market: str,
        adj_map: dict[str, float],
    ) -> list[Bar]:
        """Convert Tushare API response into sorted list of Bar objects.

        If adj_map is provided, compute forward-adjusted close:
            adj_close = close * adj_factor_today / adj_factor_latest
        where adj_factor_latest is the maximum adj_factor in the range
        (most recent trading day has the highest factor in forward adjustment).
        """
        fields = data["fields"]
        items = data["items"]

        # Build field index lookup
        idx = {f: i for i, f in enumerate(fields)}

        # Find latest adj_factor for forward adjustment normalization
        latest_adj = max(adj_map.values()) if adj_map else 1.0

        bars = []
        for row in items:
            try:
                trade_date = row[idx["trade_date"]]
                dt = _tushare_to_datetime(trade_date)
                close = float(row[idx["close"]])

                # Compute forward-adjusted close price
                if adj_map and trade_date in adj_map:
                    adj_close = close * adj_map[trade_date] / latest_adj
                else:
                    adj_close = close

                vol_raw = row[idx["vol"]]
                # Tushare vol is in units of 手 (100 shares); convert to shares
                volume = int(float(vol_raw) * 100) if vol_raw is not None else 0

                bars.append(Bar(
                    time=dt,
                    symbol=symbol,
                    market=market,
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
