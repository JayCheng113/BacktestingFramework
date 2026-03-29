"""Factor endpoints."""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ez.api.deps import get_chain
from ez.factor.builtin.technical import MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR
from ez.factor.evaluator import FactorEvaluator

router = APIRouter()

_FACTOR_MAP = {
    "ma": MA, "ema": EMA, "rsi": RSI, "macd": MACD, "boll": BOLL, "momentum": Momentum,
    "vwap": VWAP, "obv": OBV, "atr": ATR,
}


class FactorEvalRequest(BaseModel):
    symbol: str
    market: str = "cn_stock"
    factor_name: str
    factor_params: dict = {}
    start_date: date
    end_date: date
    periods: list[int] = [1, 5, 10, 20]


@router.get("")
def list_factors():
    return [
        {"name": name, "class": cls.__name__}
        for name, cls in _FACTOR_MAP.items()
    ]


@router.post("/evaluate")
def evaluate_factor(req: FactorEvalRequest):
    factory = _FACTOR_MAP.get(req.factor_name.lower())
    if not factory:
        raise HTTPException(status_code=404, detail=f"Factor '{req.factor_name}' not found")

    factor = factory(**req.factor_params) if req.factor_params else factory()

    chain = get_chain()
    bars = chain.get_kline(req.symbol, req.market, "daily", req.start_date, req.end_date)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No data for {req.symbol}")

    df = pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")

    computed = factor.compute(df)
    factor_col = [c for c in computed.columns if c not in df.columns]
    if not factor_col:
        raise HTTPException(status_code=500, detail="Factor produced no new columns")

    factor_values = computed[factor_col[0]].dropna()
    forward_returns = df["adj_close"].pct_change().shift(-1).dropna()

    if len(factor_values) < 10:
        raise HTTPException(status_code=400, detail=f"Not enough data for evaluation (need 10+ bars after warmup, got {len(factor_values)})")

    evaluator = FactorEvaluator()
    analysis = evaluator.evaluate(factor_values, forward_returns, req.periods)

    return {
        "ic_mean": analysis.ic_mean,
        "rank_ic_mean": analysis.rank_ic_mean,
        "icir": analysis.icir,
        "rank_icir": analysis.rank_icir,
        "ic_decay": analysis.ic_decay,
        "turnover": analysis.turnover,
        "ic_series": analysis.ic_series.tolist(),
        "rank_ic_series": analysis.rank_ic_series.tolist(),
    }
