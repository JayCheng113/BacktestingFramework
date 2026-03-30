"""V2.9+V2.10: Portfolio API — run, list, detail, delete + factor evaluation."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ez.api.deps import get_chain
from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import MomentumRank, VolumeRank, ReverseVolatilityRank
from ez.portfolio.engine import CostModel, run_portfolio_backtest
from ez.portfolio.portfolio_strategy import PortfolioStrategy, TopNRotation, MultiFactorRotation
from ez.portfolio.builtin_strategies import EtfMacdRotation, EtfSectorSwitch, EtfStockEnhance  # noqa: F401
from ez.portfolio.universe import Universe

router = APIRouter()
logger = logging.getLogger(__name__)

# Factor name → factory
_FACTOR_MAP = {
    "momentum_rank_20": lambda: MomentumRank(20),
    "momentum_rank_10": lambda: MomentumRank(10),
    "momentum_rank_60": lambda: MomentumRank(60),
    "volume_rank_20": lambda: VolumeRank(20),
    "reverse_vol_rank_20": lambda: ReverseVolatilityRank(20),
}


class PortfolioRunRequest(BaseModel):
    strategy_name: str = "TopNRotation"
    symbols: list[str]
    market: str = "cn_stock"
    start_date: date | None = None
    end_date: date | None = None
    freq: str = Field(default="monthly", pattern="^(daily|weekly|monthly|quarterly)$")
    strategy_params: dict = {}
    initial_cash: float = Field(default=1_000_000, ge=10_000)
    buy_commission_rate: float = Field(default=0.0003, ge=0)
    sell_commission_rate: float = Field(default=0.0003, ge=0)
    commission_rate: float | None = Field(default=None, ge=0)  # backward compat: if set, overrides both
    min_commission: float = Field(default=5.0, ge=0)
    stamp_tax_rate: float = Field(default=0.0005, ge=0)
    slippage_rate: float = Field(default=0.0, ge=0)
    lot_size: int = Field(default=100, ge=1)
    limit_pct: float = Field(default=0.10, ge=0, le=0.30)  # 涨跌停比例 (10%=0.10, 科创板20%=0.20)
    benchmark_symbol: str = ""  # e.g. "510300.SH"


def _create_strategy(name: str, params: dict) -> PortfolioStrategy:
    """Instantiate strategy by name + params."""
    p = dict(params)  # don't mutate input
    if name == "TopNRotation":
        factor_name = p.pop("factor", "momentum_rank_20")
        factory = _FACTOR_MAP.get(factor_name)
        if not factory:
            raise HTTPException(400, f"Unknown factor: {factor_name}. Available: {list(_FACTOR_MAP.keys())}")
        top_n = p.pop("top_n", 10)
        return TopNRotation(factor=factory(), top_n=top_n, **p)
    elif name == "MultiFactorRotation":
        factor_names = p.pop("factors", ["momentum_rank_20"])
        factors = []
        for fn in factor_names:
            factory = _FACTOR_MAP.get(fn)
            if not factory:
                raise HTTPException(400, f"Unknown factor: {fn}")
            factors.append(factory())
        top_n = p.pop("top_n", 10)
        return MultiFactorRotation(factors=factors, top_n=top_n, **p)
    elif name in PortfolioStrategy.get_registry():
        cls = PortfolioStrategy.get_registry()[name]
        return cls(**p)
    else:
        raise HTTPException(404, f"Strategy '{name}' not found")


def _fetch_data(symbols: list[str], market: str, start: date, end: date, lookback_days: int = 252):
    """Fetch kline data for all symbols, build calendar from actual trading days."""
    chain = get_chain()
    # Add lookback buffer (1.6x to account for weekends/holidays)
    fetch_start = start - timedelta(days=int(lookback_days * 1.6))

    universe_data = {}
    all_dates = set()
    for sym in symbols:
        try:
            bars = chain.get_kline(sym, market, "daily", fetch_start, end)
            if not bars:
                continue
            df = pd.DataFrame([{
                "open": b.open, "high": b.high, "low": b.low,
                "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
            } for b in bars], index=pd.DatetimeIndex([b.time for b in bars]))
            universe_data[sym] = df
            all_dates.update(d.date() for d in df.index)
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", sym, e)

    if not universe_data:
        raise HTTPException(400, "No data available for any of the provided symbols")

    # Build calendar from actual trading days
    calendar = TradingCalendar.from_dates(sorted(all_dates))
    return universe_data, calendar


_portfolio_store = None


def _get_store():
    """Lazy singleton for PortfolioStore. Shares DuckDB connection via deps.get_store()."""
    global _portfolio_store
    if _portfolio_store is None:
        from ez.api.deps import get_store
        from ez.portfolio.portfolio_store import PortfolioStore
        # Share DuckDB connection with existing store (I2: avoid separate connection)
        _portfolio_store = PortfolioStore(get_store()._conn)
    return _portfolio_store


def reset_portfolio_store() -> None:
    """Reset singleton (called by deps.close_resources)."""
    global _portfolio_store
    _portfolio_store = None


@router.get("/strategies")
def list_portfolio_strategies():
    """List available portfolio strategies."""
    result = []
    for name, cls in PortfolioStrategy.get_registry().items():
        result.append({
            "name": name,
            "description": cls.get_description().strip()[:200] if hasattr(cls, 'get_description') else "",
            "parameters": cls.get_parameters_schema() if hasattr(cls, 'get_parameters_schema') else {},
        })
    # Add factor list for reference
    return {"strategies": result, "available_factors": list(_FACTOR_MAP.keys())}


@router.post("/run")
def run_portfolio(req: PortfolioRunRequest):
    """Run a portfolio backtest."""
    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    try:
        strategy = _create_strategy(req.strategy_name, req.strategy_params)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    universe = Universe(req.symbols)
    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, strategy.lookback_days)

    buy_rate = req.commission_rate if req.commission_rate is not None else req.buy_commission_rate
    sell_rate = req.commission_rate if req.commission_rate is not None else req.sell_commission_rate
    cost_model = CostModel(
        buy_commission_rate=buy_rate,
        sell_commission_rate=sell_rate,
        min_commission=req.min_commission,
        stamp_tax_rate=req.stamp_tax_rate,
        slippage_rate=req.slippage_rate,
    )

    # H4: count fetched symbols BEFORE adding benchmark
    fetched_count = len(universe_data)
    skipped = [s for s in req.symbols if s not in universe_data]

    # If benchmark not in symbols, fetch it separately
    if req.benchmark_symbol and req.benchmark_symbol not in universe_data:
        try:
            chain = get_chain()
            fetch_start = start - timedelta(days=int(strategy.lookback_days * 1.6))
            bars = chain.get_kline(req.benchmark_symbol, req.market, "daily", fetch_start, end)
            if bars:
                universe_data[req.benchmark_symbol] = pd.DataFrame([{
                    "open": b.open, "high": b.high, "low": b.low,
                    "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
                } for b in bars], index=pd.DatetimeIndex([b.time for b in bars]))
        except Exception:
            pass

    result = run_portfolio_backtest(
        strategy=strategy, universe=universe, universe_data=universe_data,
        calendar=calendar, start=start, end=end, freq=req.freq,
        initial_cash=req.initial_cash, cost_model=cost_model,
        lot_size=req.lot_size, limit_pct=req.limit_pct,
        benchmark_symbol=req.benchmark_symbol,
    )

    # Sanitize NaN/Inf in metrics
    metrics = {}
    for k, v in result.metrics.items():
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            metrics[k] = None
        else:
            metrics[k] = v

    # Persist
    store = _get_store()
    run_id = store.save_run({
        "strategy_name": req.strategy_name,
        "strategy_params": {
            **req.strategy_params,
            "_cost": {
                "buy_commission_rate": buy_rate, "sell_commission_rate": sell_rate,
                "min_commission": req.min_commission, "stamp_tax_rate": req.stamp_tax_rate,
                "slippage_rate": req.slippage_rate, "lot_size": req.lot_size,
                "limit_pct": req.limit_pct, "benchmark": req.benchmark_symbol,
                "market": req.market,
            },
        },
        "symbols": req.symbols,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "freq": req.freq,
        "initial_cash": req.initial_cash,
        "metrics": metrics,
        "equity_curve": [round(v, 2) for v in result.equity_curve],
        "trade_count": len(result.trades),
        "rebalance_count": len(result.rebalance_dates),
    })

    return {
        "run_id": run_id,
        "metrics": metrics,
        "equity_curve": [round(v, 2) for v in result.equity_curve],
        "benchmark_curve": [round(v, 2) for v in result.benchmark_curve],
        "dates": [d.isoformat() for d in result.dates],
        "trades": result.trades[:100],
        "rebalance_dates": [d.isoformat() for d in result.rebalance_dates],
        "symbols_fetched": fetched_count,
        "symbols_skipped": skipped,
    }


class PortfolioWFRequest(BaseModel):
    strategy_name: str = "TopNRotation"
    symbols: list[str]
    market: str = "cn_stock"
    start_date: date | None = None
    end_date: date | None = None
    freq: str = Field(default="monthly", pattern="^(daily|weekly|monthly|quarterly)$")
    strategy_params: dict = {}
    initial_cash: float = Field(default=1_000_000, ge=10_000)
    n_splits: int = Field(default=5, ge=2, le=20)
    train_ratio: float = Field(default=0.7, gt=0.0, lt=1.0)
    lot_size: int = Field(default=100, ge=1)
    limit_pct: float = Field(default=0.10, ge=0, le=0.30)


@router.post("/walk-forward")
def portfolio_walk_forward_api(req: PortfolioWFRequest):
    """Run walk-forward validation on a portfolio strategy."""
    from ez.portfolio.walk_forward import portfolio_walk_forward, portfolio_significance

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    def strategy_factory():
        return _create_strategy(req.strategy_name, req.strategy_params)

    universe = Universe(req.symbols)
    strategy_tmp = strategy_factory()
    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, strategy_tmp.lookback_days)

    try:
        wf_result = portfolio_walk_forward(
            strategy_factory=strategy_factory,
            universe=universe, universe_data=universe_data, calendar=calendar,
            start=start, end=end, n_splits=req.n_splits, train_ratio=req.train_ratio,
            freq=req.freq, initial_cash=req.initial_cash,
            lot_size=req.lot_size, limit_pct=req.limit_pct,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Significance on OOS equity curve
    sig = portfolio_significance(wf_result.oos_equity_curve, seed=42) if wf_result.oos_equity_curve else None

    return {
        "n_splits": wf_result.n_splits,
        "is_sharpes": wf_result.is_sharpes,
        "oos_sharpes": wf_result.oos_sharpes,
        "degradation": wf_result.degradation,
        "overfitting_score": wf_result.overfitting_score,
        "oos_metrics": wf_result.oos_metrics,
        "oos_equity_curve": [round(v, 2) for v in wf_result.oos_equity_curve],
        "oos_dates": wf_result.oos_dates,
        "significance": {
            "sharpe_ci_lower": sig.sharpe_ci_lower if sig else 0,
            "sharpe_ci_upper": sig.sharpe_ci_upper if sig else 0,
            "p_value": sig.monte_carlo_p_value if sig else 1,
            "is_significant": sig.is_significant if sig else False,
        } if sig else None,
    }


@router.get("/runs")
def list_portfolio_runs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return _get_store().list_runs(limit=limit, offset=offset)


@router.get("/runs/{run_id}")
def get_portfolio_run(run_id: str):
    run = _get_store().get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run '{run_id}' not found")
    return run


@router.delete("/runs/{run_id}")
def delete_portfolio_run(run_id: str):
    if _get_store().delete_run(run_id):
        return {"deleted": run_id}
    raise HTTPException(404, f"Run '{run_id}' not found")


# ─── V2.10: Factor Evaluation API ───

class FactorEvalRequest(BaseModel):
    symbols: list[str]
    market: str = "cn_stock"
    start_date: date | None = None
    end_date: date | None = None
    factor_names: list[str] = ["momentum_rank_20"]
    forward_days: int = Field(default=5, ge=1, le=60)
    eval_freq: str = Field(default="weekly", pattern="^(daily|weekly|monthly)$")
    n_quantiles: int = Field(default=5, ge=2, le=10)


class FactorCorrelationRequest(BaseModel):
    symbols: list[str]
    market: str = "cn_stock"
    start_date: date | None = None
    end_date: date | None = None
    factor_names: list[str] = ["momentum_rank_20", "volume_rank_20"]
    eval_freq: str = Field(default="monthly", pattern="^(daily|weekly|monthly)$")


def _resolve_factors(names: list[str]):
    """Resolve factor names to CrossSectionalFactor instances."""
    resolved = []
    for name in names:
        factory = _FACTOR_MAP.get(name)
        if not factory:
            raise HTTPException(400, f"Unknown factor: {name}. Available: {list(_FACTOR_MAP.keys())}")
        resolved.append(factory())
    return resolved


@router.post("/evaluate-factors")
def evaluate_factors(req: FactorEvalRequest):
    """Evaluate cross-sectional factors: IC, Rank IC, ICIR, IC decay, quintile returns."""
    from ez.portfolio.cross_evaluator import evaluate_cross_sectional_factor, evaluate_ic_decay

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))
    factors = _resolve_factors(req.factor_names)

    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, lookback_days=300)

    results = []
    for factor in factors:
        result = evaluate_cross_sectional_factor(
            factor=factor, universe_data=universe_data, calendar=calendar,
            start=start, end=end, forward_days=req.forward_days,
            eval_freq=req.eval_freq, n_quantiles=req.n_quantiles,
        )
        decay = evaluate_ic_decay(
            factor=factor, universe_data=universe_data, calendar=calendar,
            start=start, end=end, lags=[1, 5, 10, 20], eval_freq=req.eval_freq,
        )
        results.append({
            "factor_name": result.factor_name,
            "mean_ic": result.mean_ic,
            "mean_rank_ic": result.mean_rank_ic,
            "ic_std": result.ic_std,
            "icir": result.icir,
            "rank_icir": result.rank_icir,
            "n_eval_dates": result.n_eval_dates,
            "avg_stocks_per_date": result.avg_stocks_per_date,
            "ic_series": result.ic_series,
            "rank_ic_series": result.rank_ic_series,
            "eval_dates": result.eval_dates,
            "quintile_returns": result.quintile_returns,
            "ic_decay": decay,
        })

    return {"results": results, "symbols_count": len(universe_data)}


@router.post("/factor-correlation")
def factor_correlation(req: FactorCorrelationRequest):
    """Compute pairwise Spearman rank correlation between factors."""
    from ez.portfolio.cross_evaluator import compute_factor_correlation

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))
    factors = _resolve_factors(req.factor_names)

    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, lookback_days=300)

    corr_df = compute_factor_correlation(
        factors=factors, universe_data=universe_data, calendar=calendar,
        start=start, end=end, eval_freq=req.eval_freq,
    )

    return {
        "factor_names": list(corr_df.index),
        "correlation_matrix": corr_df.values.tolist(),
    }
