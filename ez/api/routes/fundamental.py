"""V2.11: Fundamental data API — fetch, status, quality report."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ez.api.deps import get_fundamental_store, get_tushare_provider

router = APIRouter()
logger = logging.getLogger(__name__)


class FetchRequest(BaseModel):
    symbols: list[str] = Field(min_length=1)
    market: str = "cn_stock"
    start_date: date | None = None
    end_date: date | None = None
    include_fina: bool = Field(default=True, description="Fetch fina_indicator (may need paid Tushare)")


class DataQualityRequest(BaseModel):
    symbols: list[str] = Field(min_length=1)
    start_date: date | None = None
    end_date: date | None = None


@router.post("/fetch")
def fetch_fundamental_data(req: FetchRequest):
    """Fetch and cache fundamental data for symbols. Idempotent (skips existing data)."""
    provider = get_tushare_provider()
    if provider is None:
        raise HTTPException(400, "Tushare Token 未设置，无法获取基本面数据")

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))
    store = get_fundamental_store()

    status = store.ensure_data(
        symbols=req.symbols,
        start=start,
        end=end,
        provider=provider,
        need_fina=req.include_fina,
    )

    return {
        "daily_fetched": status["daily_fetched"],
        "fina_fetched": status["fina_fetched"],
        "errors": status["errors"],
        "message": f"已获取 {status['daily_fetched']} 条日度数据, {status['fina_fetched']} 条财务指标",
    }


@router.post("/quality")
def data_quality_report(req: DataQualityRequest):
    """Check data quality for given symbols."""
    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))
    store = get_fundamental_store()
    store.preload(req.symbols, start, end)

    report = store.data_quality_report(req.symbols, start, end)
    return {"report": report}


@router.get("/factors")
def list_fundamental_factors():
    """List available fundamental factors with categories and descriptions."""
    from ez.factor.builtin.fundamental import (
        FACTOR_CATEGORIES, CATEGORY_LABELS, NEEDS_FINA, get_fundamental_factors,
    )
    factors = get_fundamental_factors()
    result = []
    for cat_key, factor_names in FACTOR_CATEGORIES.items():
        cat_factors = []
        for fname in factor_names:
            cls = factors.get(fname)
            if cls is None:
                continue
            instance = cls()
            cat_factors.append({
                "name": instance.name,
                "class_name": fname,
                "description": instance.description,
                "needs_fina": fname in NEEDS_FINA,
            })
        result.append({
            "category": cat_key,
            "label": CATEGORY_LABELS.get(cat_key, cat_key),
            "factors": cat_factors,
        })
    return {"categories": result}
