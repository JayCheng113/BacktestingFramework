"""V2.9+V2.10+V2.11: Portfolio API — run, list, detail, delete + factor evaluation + fundamental."""
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

# Factor name → factory (builtin defaults + dynamic from registry)
_BUILTIN_FACTOR_MAP = {
    "momentum_rank_20": lambda: MomentumRank(20),
    "momentum_rank_10": lambda: MomentumRank(10),
    "momentum_rank_60": lambda: MomentumRank(60),
    "volume_rank_20": lambda: VolumeRank(20),
    "reverse_vol_rank_20": lambda: ReverseVolatilityRank(20),
}


_BUILTIN_CLASS_NAMES = {"MomentumRank", "VolumeRank", "ReverseVolatilityRank"}


def _is_fundamental_factor(cls_or_factory) -> bool:
    """Check if a factor class/factory is a FundamentalCrossFactor."""
    from ez.factor.builtin.fundamental import FundamentalCrossFactor
    if isinstance(cls_or_factory, type):
        return issubclass(cls_or_factory, FundamentalCrossFactor)
    return False


def _inject_fundamental_store(factor):
    """If factor is a FundamentalCrossFactor, inject the singleton store."""
    from ez.factor.builtin.fundamental import FundamentalCrossFactor
    if isinstance(factor, FundamentalCrossFactor) and factor._store is None:
        from ez.api.deps import get_fundamental_store
        factor.set_store(get_fundamental_store())
    return factor


def _ensure_fundamental_data(symbols: list[str], start: date, end: date, need_fina: bool) -> list[str]:
    """Pre-fetch fundamental data if a fundamental factor is being used.

    Returns list of warning messages (empty if all OK).
    Raises HTTPException 400 if Tushare provider unavailable.
    """
    from ez.api.deps import get_fundamental_store, get_tushare_provider
    store = get_fundamental_store()
    provider = get_tushare_provider()
    warnings = []

    if provider is None:
        raise HTTPException(400, "基本面因子需要 Tushare Token，请在设置中配置")

    # Extend fina fetch window: need reports announced BEFORE backtest start
    fina_start = start - timedelta(days=540)  # 18 months lookback for PIT
    status = store.ensure_data(symbols, fina_start, end, provider, need_fina=need_fina)

    if status["errors"]:
        err_count = len(status["errors"])
        warnings.append(f"基本面数据获取部分失败 ({err_count} 个错误): {status['errors'][:3]}")
        logger.warning("Fundamental data fetch errors: %s", status["errors"])

    if status["daily_fetched"] == 0 and not any(
        store.has_daily_basic(s, start, end) for s in symbols[:3]
    ):
        warnings.append("警告: 未获取到日度基本面数据, 基本面因子可能返回空信号")

    store.preload(symbols, start, end)
    return warnings


def _get_factor_map() -> dict:
    """Build factor map: builtins + dynamically registered CrossSectionalFactor subclasses.

    Fundamental factors are registered by BOTH class name (EP) and instance.name (ep),
    ensuring frontend can resolve using either convention.
    """
    from ez.portfolio.cross_factor import CrossSectionalFactor
    # Ensure fundamental factors are imported (triggers auto-registration)
    try:
        import ez.factor.builtin.fundamental  # noqa: F401
    except ImportError:
        pass
    result = dict(_BUILTIN_FACTOR_MAP)
    for name, cls in CrossSectionalFactor.get_registry().items():
        # Skip builtin classes (already mapped with parameterized keys)
        if name in _BUILTIN_CLASS_NAMES:
            continue
        if name not in result:
            result[name] = cls
        # Also register by instance.name (e.g., "ep" for EP) to match frontend convention
        try:
            inst = cls()
            iname = inst.name
            if iname and iname != name and iname not in result:
                result[iname] = cls
        except (TypeError, Exception):
            pass
    return result


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


def _create_strategy(name: str, params: dict, symbols: list[str] | None = None,
                     start: date | None = None, end: date | None = None) -> tuple[PortfolioStrategy, list[str]]:
    """Instantiate strategy by name + params. Auto-injects FundamentalStore if needed.

    Returns (strategy, warnings) tuple. Warnings are non-fatal messages about data availability.
    """
    from ez.factor.builtin.fundamental import NEEDS_FINA
    warnings: list[str] = []
    p = dict(params)  # don't mutate input
    if name == "TopNRotation":
        factor_name = p.pop("factor", "momentum_rank_20")
        factory = _get_factor_map().get(factor_name)
        if not factory:
            raise HTTPException(400, f"Unknown factor: {factor_name}. Available: {list(_get_factor_map().keys())}")
        factor = factory()
        factor = _inject_fundamental_store(factor)
        if _is_fundamental_factor(type(factor)) and symbols and start and end:
            warnings = _ensure_fundamental_data(symbols, start, end, need_fina=type(factor).__name__ in NEEDS_FINA)
        top_n = p.pop("top_n", 10)
        return TopNRotation(factor=factor, top_n=top_n, **p), warnings
    elif name == "MultiFactorRotation":
        factor_names = p.pop("factors", ["momentum_rank_20"])
        factors = []
        has_fundamental = False
        need_fina = False
        for fn in factor_names:
            factory = _get_factor_map().get(fn)
            if not factory:
                raise HTTPException(400, f"Unknown factor: {fn}")
            f = factory()
            f = _inject_fundamental_store(f)
            if _is_fundamental_factor(type(f)):
                has_fundamental = True
                if type(f).__name__ in NEEDS_FINA:
                    need_fina = True
            factors.append(f)
        if has_fundamental and symbols and start and end:
            warnings = _ensure_fundamental_data(symbols, start, end, need_fina=need_fina)
        top_n = p.pop("top_n", 10)
        return MultiFactorRotation(factors=factors, top_n=top_n, **p), warnings
    elif name in PortfolioStrategy.get_registry():
        cls = PortfolioStrategy.get_registry()[name]
        return cls(**p), []
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
    """List available portfolio strategies with factor categories."""
    result = []
    for name, cls in PortfolioStrategy.get_registry().items():
        result.append({
            "name": name,
            "description": cls.get_description().strip()[:200] if hasattr(cls, 'get_description') else "",
            "parameters": cls.get_parameters_schema() if hasattr(cls, 'get_parameters_schema') else {},
        })

    # Build categorized factor list
    factor_map = _get_factor_map()
    factor_list = list(factor_map.keys())

    # Factor categories for frontend grouping
    try:
        from ez.factor.builtin.fundamental import FACTOR_CATEGORIES, CATEGORY_LABELS, NEEDS_FINA, get_fundamental_factors
        categories = []
        categorized_keys: set[str] = set()

        # Technical factors (built-in, not fundamental)
        tech_factors = [f for f in factor_list if f in _BUILTIN_FACTOR_MAP]
        if tech_factors:
            categories.append({"key": "technical", "label": "量价 (Technical)", "factors": tech_factors})
            categorized_keys.update(tech_factors)

        # Fundamental factor categories — use instance.name as key (matches factor_map dual registration)
        fundamental_names = {cls.__name__ for cls in get_fundamental_factors().values()}
        for cat_key, class_names in FACTOR_CATEGORIES.items():
            cat_factors = []
            for cname in class_names:
                cls = factor_map.get(cname)
                if cls and isinstance(cls, type):
                    try:
                        instance = cls()
                    except (TypeError, Exception):
                        continue
                    fkey = instance.name  # e.g., "ep" — matches factor_map dual registration
                    cat_factors.append({
                        "key": fkey,
                        "class_name": cname,
                        "description": getattr(instance, 'description', ''),
                        "needs_fina": cname in NEEDS_FINA,
                    })
                    categorized_keys.add(fkey)
                    categorized_keys.add(cname)  # also mark class name as categorized
            if cat_factors:
                categories.append({"key": cat_key, "label": CATEGORY_LABELS.get(cat_key, cat_key), "factors": cat_factors})

        # "Other" category: user-registered factors not in any category above
        other_factors = [f for f in factor_list if f not in categorized_keys]
        if other_factors:
            categories.append({"key": "other", "label": "其他 (Other)", "factors": other_factors})

    except ImportError:
        categories = [{"key": "technical", "label": "量价 (Technical)", "factors": factor_list}]

    return {"strategies": result, "available_factors": factor_list, "factor_categories": categories}


@router.post("/run")
def run_portfolio(req: PortfolioRunRequest):
    """Run a portfolio backtest."""
    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    try:
        strategy, fund_warnings = _create_strategy(req.strategy_name, req.strategy_params,
                                                   symbols=req.symbols, start=start, end=end)
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
        "latest_weights": result.weights_history[-1] if result.weights_history else {},
        "weights_history": [
            {"date": result.dates[i].isoformat() if i < len(result.dates) else "",
             "weights": result.weights_history[i]}
            for i in range(max(0, len(result.weights_history) - 20), len(result.weights_history))
            if result.weights_history[i]  # skip empty weight entries (non-rebalance days)
        ] if result.weights_history else [],
        "warnings": fund_warnings if fund_warnings else None,
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
    buy_commission_rate: float = Field(default=0.0003, ge=0)
    sell_commission_rate: float = Field(default=0.0003, ge=0)
    min_commission: float = Field(default=5.0, ge=0)
    stamp_tax_rate: float = Field(default=0.0005, ge=0)
    slippage_rate: float = Field(default=0.0, ge=0)
    lot_size: int = Field(default=100, ge=1)
    limit_pct: float = Field(default=0.10, ge=0, le=0.30)
    benchmark_symbol: str = ""


@router.post("/walk-forward")
def portfolio_walk_forward_api(req: PortfolioWFRequest):
    """Run walk-forward validation on a portfolio strategy."""
    from ez.portfolio.walk_forward import portfolio_walk_forward, portfolio_significance

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    def strategy_factory():
        s, _w = _create_strategy(req.strategy_name, req.strategy_params,
                                 symbols=req.symbols, start=start, end=end)
        return s

    universe = Universe(req.symbols)
    strategy_tmp = strategy_factory()
    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, strategy_tmp.lookback_days)

    cost_model = CostModel(
        buy_commission_rate=req.buy_commission_rate,
        sell_commission_rate=req.sell_commission_rate,
        min_commission=req.min_commission,
        stamp_tax_rate=req.stamp_tax_rate,
        slippage_rate=req.slippage_rate,
    )

    try:
        wf_result = portfolio_walk_forward(
            strategy_factory=strategy_factory,
            universe=universe, universe_data=universe_data, calendar=calendar,
            start=start, end=end, n_splits=req.n_splits, train_ratio=req.train_ratio,
            freq=req.freq, initial_cash=req.initial_cash, cost_model=cost_model,
            lot_size=req.lot_size, limit_pct=req.limit_pct,
            benchmark_symbol=req.benchmark_symbol,
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


def _resolve_factors(names: list[str], symbols: list[str] | None = None,
                     start: date | None = None, end: date | None = None):
    """Resolve factor names to CrossSectionalFactor instances. Injects store for fundamental factors."""
    from ez.factor.builtin.fundamental import NEEDS_FINA
    resolved = []
    has_fundamental = False
    need_fina = False
    for name in names:
        factory = _get_factor_map().get(name)
        if not factory:
            raise HTTPException(400, f"Unknown factor: {name}. Available: {list(_get_factor_map().keys())}")
        try:
            f = factory()
        except TypeError as e:
            raise HTTPException(400, f"Factor '{name}' requires constructor arguments: {e}") from e
        f = _inject_fundamental_store(f)
        if _is_fundamental_factor(type(f)):
            has_fundamental = True
            if type(f).__name__ in NEEDS_FINA:
                need_fina = True
        resolved.append(f)
    # Pre-fetch fundamental data if any fundamental factor is used
    if has_fundamental and symbols and start and end:
        _ensure_fundamental_data(symbols, start, end, need_fina=need_fina)
    return resolved


@router.post("/evaluate-factors")
def evaluate_factors(req: FactorEvalRequest):
    """Evaluate cross-sectional factors: IC, Rank IC, ICIR, IC decay, quintile returns."""
    from ez.portfolio.cross_evaluator import evaluate_cross_sectional_factor, evaluate_ic_decay

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))
    factors = _resolve_factors(req.factor_names, symbols=req.symbols, start=start, end=end)

    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, lookback_days=300)

    def _safe(v):
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            return None
        return v

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
            "mean_ic": _safe(result.mean_ic),
            "mean_rank_ic": _safe(result.mean_rank_ic),
            "ic_std": _safe(result.ic_std),
            "icir": _safe(result.icir),
            "rank_icir": _safe(result.rank_icir),
            "n_eval_dates": result.n_eval_dates,
            "avg_stocks_per_date": _safe(result.avg_stocks_per_date),
            "ic_series": [_safe(v) for v in result.ic_series],
            "rank_ic_series": [_safe(v) for v in result.rank_ic_series],
            "eval_dates": result.eval_dates,
            "quintile_returns": {k: _safe(v) for k, v in result.quintile_returns.items()},
            "ic_decay": {k: _safe(v) for k, v in decay.items()},
        })

    return {"results": results, "symbols_count": len(universe_data)}


@router.post("/factor-correlation")
def factor_correlation(req: FactorCorrelationRequest):
    """Compute pairwise Spearman rank correlation between factors."""
    from ez.portfolio.cross_evaluator import compute_factor_correlation

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))
    factors = _resolve_factors(req.factor_names, symbols=req.symbols, start=start, end=end)

    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, lookback_days=300)

    corr_df = compute_factor_correlation(
        factors=factors, universe_data=universe_data, calendar=calendar,
        start=start, end=end, eval_freq=req.eval_freq,
    )

    return {
        "factor_names": list(corr_df.index),
        "correlation_matrix": corr_df.values.tolist(),
    }
