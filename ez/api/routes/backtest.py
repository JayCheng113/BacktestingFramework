"""Backtest endpoints."""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ez.api.deps import get_chain
from ez.backtest.engine import VectorizedBacktestEngine
from ez.backtest.walk_forward import WalkForwardValidator
from ez.strategy.base import Strategy

router = APIRouter()


class BacktestRequest(BaseModel):
    symbol: str
    market: str = "cn_stock"
    period: str = "daily"
    strategy_name: str
    strategy_params: dict = {}
    start_date: date
    end_date: date
    initial_capital: float = 100000.0
    commission_rate: float = 0.0003


class WalkForwardRequest(BacktestRequest):
    n_splits: int = 5
    train_ratio: float = 0.7


def _get_strategy(name: str, params: dict) -> Strategy:
    for key, cls in Strategy._registry.items():
        if cls.__name__ == name or key == name:
            schema = cls.get_parameters_schema()
            p = {k: v["default"] for k, v in schema.items()}
            p.update(params)
            return cls(**p)
    raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")


def _fetch_data(req: BacktestRequest) -> pd.DataFrame:
    chain = get_chain()
    bars = chain.get_kline(req.symbol, req.market, req.period, req.start_date, req.end_date)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No data for {req.symbol}")
    return pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")


@router.post("/run")
def run_backtest(req: BacktestRequest):
    strategy = _get_strategy(req.strategy_name, req.strategy_params)
    df = _fetch_data(req)
    engine = VectorizedBacktestEngine(commission_rate=req.commission_rate)
    result = engine.run(df, strategy, req.initial_capital)
    return {
        "metrics": result.metrics,
        "benchmark_info": f"Buy & Hold {req.symbol}",
        "equity_curve": result.equity_curve.tolist(),
        "benchmark_curve": result.benchmark_curve.tolist(),
        "trades": [
            {"entry_time": t.entry_time.isoformat(), "exit_time": t.exit_time.isoformat(),
             "entry_price": t.entry_price, "exit_price": t.exit_price,
             "pnl": t.pnl, "pnl_pct": t.pnl_pct, "commission": t.commission}
            for t in result.trades
        ],
        "significance": {
            "sharpe_ci_lower": result.significance.sharpe_ci_lower,
            "sharpe_ci_upper": result.significance.sharpe_ci_upper,
            "p_value": result.significance.monte_carlo_p_value,
            "is_significant": result.significance.is_significant,
        },
    }


@router.post("/walk-forward")
def run_walk_forward(req: WalkForwardRequest):
    strategy = _get_strategy(req.strategy_name, req.strategy_params)
    df = _fetch_data(req)
    validator = WalkForwardValidator(
        VectorizedBacktestEngine(commission_rate=req.commission_rate)
    )
    try:
        result = validator.validate(df, strategy, req.n_splits, req.train_ratio, req.initial_capital)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "oos_metrics": result.oos_metrics,
        "overfitting_score": result.overfitting_score,
        "is_vs_oos_degradation": result.is_vs_oos_degradation,
        "n_splits": len(result.splits),
        "oos_equity_curve": result.oos_equity_curve.tolist(),
    }


@router.get("/strategies")
def list_strategies():
    return [
        {
            "name": cls.__name__,
            "key": key,
            "parameters": cls.get_parameters_schema(),
        }
        for key, cls in Strategy._registry.items()
    ]
