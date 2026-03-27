"""Financial Modeling Prep API data provider (US stocks primary).

[EXTENSION] — freely modifiable.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime

import httpx

from ez.data.provider import DataProvider
from ez.errors import ProviderError
from ez.types import Bar

logger = logging.getLogger(__name__)


class FMPDataProvider(DataProvider):
    """FMP API. Requires FMP_API_KEY env var. Free tier: 250 calls/day."""

    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: str | None = None, timeout: int = 10):
        self._api_key = api_key or os.environ.get("FMP_API_KEY", "")
        self._client = httpx.Client(timeout=timeout)

    @property
    def name(self) -> str:
        return "fmp"

    def close(self) -> None:
        self._client.close()

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        if not self._api_key:
            raise ProviderError("FMP_API_KEY not set")

        ticker = symbol.split(".")[0]
        url = f"{self.BASE_URL}/historical-price-full/{ticker}"

        try:
            resp = self._client.get(url, params={
                "apikey": self._api_key,
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
            })
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"FMP API error for {symbol}: {e}") from e

        historical = data.get("historical", [])
        bars = []
        for item in historical:
            try:
                dt = datetime.strptime(item["date"], "%Y-%m-%d")
                bars.append(Bar(
                    time=dt, symbol=symbol, market=market,
                    open=item["open"], high=item["high"],
                    low=item["low"], close=item["close"],
                    adj_close=item.get("adjClose", item["close"]),
                    volume=int(item["volume"]),
                ))
            except (KeyError, ValueError):
                continue

        bars.sort(key=lambda b: b.time)
        return bars

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        if not self._api_key:
            return []
        try:
            resp = self._client.get(
                f"{self.BASE_URL}/search",
                params={"query": keyword, "apikey": self._api_key, "limit": 20},
            )
            return [{"symbol": r["symbol"], "name": r.get("name", "")} for r in resp.json()]
        except Exception:
            return []
