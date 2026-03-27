"""Tencent Finance API data provider (free, no auth, backup source).

[EXTENSION] — freely modifiable.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime

import httpx

from ez.data.provider import DataProvider
from ez.errors import ProviderError
from ez.types import Bar

logger = logging.getLogger(__name__)

_PERIOD_MAP = {"daily": "day", "weekly": "week", "monthly": "month"}


class TencentDataProvider(DataProvider):
    """Tencent Finance undocumented API. Free, no auth. Use as backup only."""

    BASE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    HK_URL = "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"

    def __init__(self, timeout: int = 10):
        self._client = httpx.Client(timeout=timeout)

    @property
    def name(self) -> str:
        return "tencent"

    def close(self) -> None:
        self._client.close()

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        code = self._to_tencent_code(symbol, market)
        tc_period = _PERIOD_MAP.get(period, "day")
        url = self.HK_URL if market == "hk_stock" else self.BASE_URL

        try:
            # Tencent API param format: code,period,start,end,count,qfq
            resp = self._client.get(url, params={
                "param": f"{code},{tc_period},{start_date},{end_date},800,qfq",
            })
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"Tencent API error for {symbol}: {e}") from e

        return self._parse_response(resp.text, symbol, market, tc_period, start_date, end_date)

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        return []

    def _to_tencent_code(self, symbol: str, market: str) -> str:
        if market == "cn_stock":
            code = symbol.split(".")[0]
            suffix = symbol.split(".")[-1].lower() if "." in symbol else ""
            if suffix == "sh" or code.startswith("6"):
                return f"sh{code}"
            return f"sz{code}"
        elif market == "us_stock":
            return f"us{symbol.split('.')[0]}"
        elif market == "hk_stock":
            code = symbol.split(".")[0]
            return f"hk{code}"
        return symbol

    def _parse_response(
        self, text: str, symbol: str, market: str, tc_period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        text = re.sub(r"^[^{]*", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []

        if data.get("code") != 0:
            return []

        inner = data.get("data", {})
        # Guard: API may return empty list instead of dict on error
        if not isinstance(inner, dict):
            return []

        stock_data = None
        for v in inner.values():
            if isinstance(v, dict):
                stock_data = v
                break
        if not stock_data:
            return []

        # Tencent uses "qfqday", "qfqweek", etc. for adjusted data
        kline_key = f"qfq{tc_period}"
        rows = stock_data.get(kline_key) or stock_data.get(tc_period, [])

        bars = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                dt = datetime.strptime(str(row[0]), "%Y-%m-%d")
                # Tencent format: [date, open, close, high, low, volume]
                o, c, h, l = float(row[1]), float(row[2]), float(row[3]), float(row[4])
                vol = int(float(row[5]))
                if not (start_date <= dt.date() <= end_date):
                    continue
                bars.append(Bar(
                    time=dt, symbol=symbol, market=market,
                    open=o, high=h, low=l, close=c, adj_close=c,
                    volume=vol,
                ))
            except (ValueError, IndexError):
                continue

        bars.sort(key=lambda b: b.time)
        return bars
