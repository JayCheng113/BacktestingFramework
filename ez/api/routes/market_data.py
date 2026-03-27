"""Market data endpoints."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ez.api.deps import get_chain, get_tushare_provider

router = APIRouter()


@router.get("/kline")
def get_kline(
    symbol: str = Query(..., description="Stock symbol, e.g. 000001.SZ"),
    market: str = Query("cn_stock"),
    period: str = Query("daily"),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    chain = get_chain()
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
    chain = get_chain()
    return chain.search_symbols(keyword, market)


def _require_tushare():
    """Return shared TushareDataProvider or raise 503."""
    provider = get_tushare_provider()
    if not provider:
        raise HTTPException(status_code=503, detail="Tushare not configured (set TUSHARE_TOKEN)")
    return provider


@router.get("/daily-basic")
def get_daily_basic(
    symbol: str = Query(..., description="Stock symbol, e.g. 000001.SZ"),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Daily fundamental indicators: PE, PB, turnover rate, market cap, etc."""
    provider = _require_tushare()
    data = provider.get_daily_basic(symbol, start_date, end_date)
    for row in data:
        if "trade_date" in row and hasattr(row["trade_date"], "isoformat"):
            row["trade_date"] = row["trade_date"].isoformat()
    return data


@router.get("/index-kline")
def get_index_kline(
    index_code: str = Query("000300.SH", description="Index code, e.g. 000300.SH (CSI 300)"),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Index daily K-line for benchmark. 000001.SH=Shanghai, 000300.SH=CSI300, 399006.SZ=ChiNext."""
    provider = _require_tushare()
    bars = provider.get_index_kline(index_code, start_date, end_date)
    return [
        {
            "date": b.time.strftime("%Y-%m-%d"),
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume,
        }
        for b in bars
    ]


@router.get("/trade-cal")
def get_trade_cal(
    start_date: date = Query(...),
    end_date: date = Query(...),
    exchange: str = Query("SSE"),
):
    """Return trading days in the date range."""
    provider = _require_tushare()
    days = provider.get_trade_cal(exchange, start_date, end_date)
    return [d.isoformat() for d in days]
