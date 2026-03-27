"""Market data endpoints."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ez.config import load_config
from ez.data.providers.tencent_provider import TencentDataProvider
from ez.data.provider import DataProviderChain
from ez.data.store import DuckDBStore

router = APIRouter()


def _get_chain() -> DataProviderChain:
    config = load_config()
    store = DuckDBStore(config.database.path)
    providers = [TencentDataProvider()]
    return DataProviderChain(providers=providers, store=store)


@router.get("/kline")
def get_kline(
    symbol: str = Query(..., description="Stock symbol, e.g. 000001.SZ"),
    market: str = Query("cn_stock"),
    period: str = Query("daily"),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    chain = _get_chain()
    bars = chain.get_kline(symbol, market, period, start_date, end_date)
    return [
        {
            "date": b.time.strftime("%Y-%m-%d"),
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
        }
        for b in bars
    ]


@router.get("/symbols")
def search_symbols(keyword: str = Query(...), market: str = Query("")):
    chain = _get_chain()
    return chain.search_symbols(keyword, market)
