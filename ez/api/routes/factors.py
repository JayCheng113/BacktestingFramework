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

_BUILTIN_FACTOR_MAP = {
    "ma": MA, "ema": EMA, "rsi": RSI, "macd": MACD, "boll": BOLL, "momentum": Momentum,
    "vwap": VWAP, "obv": OBV, "atr": ATR,
}


def _get_factor_map() -> dict:
    """Build factor map: builtins + dynamically registered Factor subclasses."""
    from ez.factor.base import Factor
    result = dict(_BUILTIN_FACTOR_MAP)
    for name, cls in Factor.get_registry().items():
        lower = name.lower()
        if lower not in result:
            result[lower] = cls
    return result


class FactorEvalRequest(BaseModel):
    symbol: str
    market: str = "cn_stock"
    factor_name: str
    factor_params: dict = {}
    start_date: date
    end_date: date
    periods: list[int] = [1, 5, 10, 20]
    column: str = ""  # V2.7.1: specify which column for multi-column factors


@router.get("")
def list_factors():
    return [
        {"name": name, "class": cls.__name__}
        for name, cls in _get_factor_map().items()
    ]


@router.post("/evaluate")
def evaluate_factor(req: FactorEvalRequest):
    factory = _get_factor_map().get(req.factor_name.lower())
    if not factory:
        raise HTTPException(status_code=404, detail=f"Factor '{req.factor_name}' not found")

    try:
        factor = factory(**req.factor_params) if req.factor_params else factory()
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"Factor '{req.factor_name}' constructor error: {e}")

    chain = get_chain()
    bars = chain.get_kline(req.symbol, req.market, "daily", req.start_date, req.end_date)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No data for {req.symbol}")

    df = pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")

    computed = factor.compute(df)
    factor_cols = [c for c in computed.columns if c not in df.columns]
    if not factor_cols:
        raise HTTPException(status_code=500, detail="Factor produced no new columns")

    # V2.7.1: support multi-column factors (MACD, BOLL, etc.)
    # If column specified, use it; otherwise evaluate all columns
    if req.column:
        if req.column not in factor_cols:
            raise HTTPException(
                status_code=400,
                detail=f"Column '{req.column}' not found. Available: {factor_cols}",
            )
        eval_cols = [req.column]
    else:
        eval_cols = factor_cols

    forward_returns = df["adj_close"].pct_change().shift(-1).dropna()
    evaluator = FactorEvaluator()
    results: dict = {"columns": eval_cols}

    for col in eval_cols:
        factor_values = computed[col].dropna()
        if len(factor_values) < 10:
            results[col] = {"error": f"Not enough data ({len(factor_values)} bars)"}
            continue
        analysis = evaluator.evaluate(factor_values, forward_returns, req.periods)
        results[col] = {
            "ic_mean": analysis.ic_mean,
            "rank_ic_mean": analysis.rank_ic_mean,
            "icir": analysis.icir,
            "rank_icir": analysis.rank_icir,
            "ic_decay": analysis.ic_decay,
            "turnover": analysis.turnover,
            "ic_series": analysis.ic_series.tolist(),
            "rank_ic_series": analysis.rank_ic_series.tolist(),
        }

    # Always flatten the first column to top level for backward compatibility.
    # Multi-column results go in per_column for advanced use.
    primary_col = eval_cols[0]
    primary = results.get(primary_col, {})
    if "error" in primary:
        raise HTTPException(status_code=400, detail=primary["error"])
    response = {**primary, "columns": eval_cols}
    if len(eval_cols) > 1:
        response["per_column"] = {col: results[col] for col in eval_cols}
    return response
