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
from ez.portfolio.cross_factor import CrossSectionalFactor, MomentumRank, VolumeRank, ReverseVolatilityRank
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
    # Ensure fundamental factors are imported (triggers auto-registration)
    try:
        import ez.factor.builtin.fundamental  # noqa: F401
    except ImportError:
        pass
    result = dict(_BUILTIN_FACTOR_MAP)
    # V2.11.1: AlphaCombiner placeholder (actual construction in _create_alpha_combiner)
    from ez.portfolio.alpha_combiner import AlphaCombiner
    result["alpha_combiner"] = AlphaCombiner  # placeholder, not callable without args
    for name, cls in CrossSectionalFactor.get_registry().items():
        # Skip builtin classes (already mapped with parameterized keys)
        if name in _BUILTIN_CLASS_NAMES:
            continue
        # Skip abstract base classes (Issue 2: can't be instantiated)
        if getattr(cls, '__abstractmethods__', None):
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


def _create_alpha_combiner(params: dict, symbols=None, start=None, end=None, market="cn_stock", skip_ensure=False):
    """Create AlphaCombiner from strategy params. Returns (factor, warnings)."""
    from ez.portfolio.alpha_combiner import AlphaCombiner
    from ez.factor.builtin.fundamental import NEEDS_FINA

    sub_names = params.pop("alpha_factors", [])
    method = params.pop("alpha_method", "equal")
    orthogonalize = params.pop("orthogonalize", False)
    _VALID_METHODS = ("equal", "ic", "icir")

    if method not in _VALID_METHODS:
        raise HTTPException(400, f"alpha_method 必须是 {_VALID_METHODS} 之一，收到: '{method}'")
    if not sub_names:
        raise HTTPException(400, "alpha_combiner 需要指定 alpha_factors (子因子列表)")

    # Issue 7: prevent alpha_combiner as own sub-factor
    if "alpha_combiner" in sub_names:
        raise HTTPException(400, "alpha_combiner 不能作为自身的子因子")

    # Resolve sub-factors (Issue 6: deduplicate by instance.name)
    sub_factors = []
    seen_names: set[str] = set()
    has_fundamental = False
    need_fina = False
    fmap = _get_factor_map()  # call once, not per sub-factor
    for fn in sub_names:
        factory = fmap.get(fn)
        if not factory:
            raise HTTPException(400, f"Unknown factor: {fn}")
        f = factory()
        if f.name in seen_names:
            continue  # skip duplicate (e.g., EP and ep both resolve to name="ep")
        seen_names.add(f.name)
        f = _inject_fundamental_store(f)
        if _is_fundamental_factor(type(f)):
            has_fundamental = True
            if type(f).__name__ in NEEDS_FINA:
                need_fina = True
        sub_factors.append(f)

    warnings: list[str] = []
    if not skip_ensure and has_fundamental and symbols and start and end:
        warnings = _ensure_fundamental_data(symbols, start, end, need_fina=need_fina)

    # Compute weights for ic/icir methods
    weights = None
    if method in ("ic", "icir") and symbols and start and end:
        weights = _compute_alpha_weights(sub_factors, symbols, market, start, end, method)
        if weights is None:
            warnings.append(f"IC/ICIR 权重计算失败，回退到等权")

    return AlphaCombiner(factors=sub_factors, weights=weights, orthogonalize=orthogonalize), warnings


def _compute_alpha_weights(factors, symbols, market, start, end, method,
                           forward_days: int = 5) -> dict[str, float] | None:
    """Pre-compute IC/ICIR weights from training data before backtest start.

    forward_days: horizon for IC computation (default 5, matches typical rebalance cycle).
    """
    from ez.portfolio.cross_evaluator import evaluate_cross_sectional_factor
    from ez.factor.builtin.fundamental import FundamentalCrossFactor, NEEDS_FINA

    train_end = start - timedelta(days=1)
    train_start = start - timedelta(days=365)

    try:
        universe_data, calendar = _fetch_data(symbols, market, train_start, train_end, lookback_days=300)
    except Exception as e:
        logger.warning("AlphaCombiner training data fetch failed: %s", e)
        return None

    # Ensure fundamental data is available for training window (Issue 1 fix)
    has_funda = any(_is_fundamental_factor(type(f)) for f in factors)
    if has_funda:
        need_fina = any(type(f).__name__ in NEEDS_FINA for f in factors if _is_fundamental_factor(type(f)))
        _ensure_fundamental_data(symbols, train_start, train_end, need_fina=need_fina)

    weights = {}
    for f in factors:
        try:
            result = evaluate_cross_sectional_factor(
                factor=f, universe_data=universe_data, calendar=calendar,
                start=train_start, end=train_end, forward_days=forward_days, eval_freq="weekly",
            )
            if method == "ic":
                # Use raw IC (preserve sign: negative IC = factor direction wrong, gets negative weight)
                weights[f.name] = result.mean_ic if result.mean_ic else 0.0
            elif method == "icir":
                weights[f.name] = result.icir if result.icir else 0.0
        except Exception as e:
            logger.warning("AlphaCombiner weight computation failed for %s: %s", f.name, e)
            weights[f.name] = 0.0

    if all(v == 0 for v in weights.values()):
        return None
    return weights


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
    # V2.12: Optimizer
    optimizer: str = Field(default="none", pattern="^(none|mean_variance|min_variance|risk_parity)$")
    risk_aversion: float = Field(default=1.0, gt=0)
    max_weight: float = Field(default=0.10, gt=0, le=1.0)
    max_industry_weight: float = Field(default=0.30, gt=0, le=1.0)
    cov_lookback: int = Field(default=60, ge=10, le=500)
    # V2.12: Risk control
    risk_control: bool = False
    max_drawdown: float = Field(default=0.20, gt=0, le=0.50)
    drawdown_reduce: float = Field(default=0.50, gt=0, le=1.0)
    drawdown_recovery: float = Field(default=0.10, gt=0, le=0.50)
    max_turnover: float = Field(default=0.50, gt=0, le=2.0)
    # V2.12.1: Index enhancement
    index_benchmark: str = Field(default="", pattern=r"^(|000300|000905|000852)$")
    max_tracking_error: float = Field(default=0.05, gt=0, le=0.20)


def _create_strategy(name: str, params: dict, symbols: list[str] | None = None,
                     start: date | None = None, end: date | None = None,
                     market: str = "cn_stock",
                     skip_ensure: bool = False) -> tuple[PortfolioStrategy, list[str]]:
    """Instantiate strategy by name + params. Auto-injects FundamentalStore if needed.

    Returns (strategy, warnings) tuple. Warnings are non-fatal messages about data availability.
    """
    from ez.factor.builtin.fundamental import NEEDS_FINA
    warnings: list[str] = []
    p = dict(params)  # don't mutate input
    if name == "TopNRotation":
        factor_name = p.pop("factor", "momentum_rank_20")

        # V2.11.1: AlphaCombiner special case
        if factor_name == "alpha_combiner":
            factor, warnings = _create_alpha_combiner(p, symbols, start, end, market=market, skip_ensure=skip_ensure)
        else:
            factory = _get_factor_map().get(factor_name)
            if not factory:
                raise HTTPException(400, f"Unknown factor: {factor_name}. Available: {list(_get_factor_map().keys())}")
            factor = factory()
            factor = _inject_fundamental_store(factor)
            if not skip_ensure and _is_fundamental_factor(type(factor)) and symbols and start and end:
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
        if not skip_ensure and has_fundamental and symbols and start and end:
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

    # V2.12.1: batch query (single SQL for cached, individual for missing)
    batch_result = chain.get_kline_batch(symbols, market, "daily", fetch_start, end)
    universe_data = {}
    all_dates = set()
    for sym, bars in batch_result.items():
        if not bars:
            continue
        df = pd.DataFrame([{
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
        } for b in bars], index=pd.DatetimeIndex([b.time for b in bars]))
        universe_data[sym] = df
        all_dates.update(d.date() for d in df.index)

    if not universe_data:
        raise HTTPException(400, "No data available for any of the provided symbols")

    # Build calendar from actual trading days
    calendar = TradingCalendar.from_dates(sorted(all_dates))
    return universe_data, calendar


def _ensure_benchmark(benchmark_symbol: str, universe_data: dict, market: str,
                      start: date, end: date, lookback_days: int = 252) -> str | None:
    """Fetch benchmark data into universe_data if not already present.

    Returns warning message if benchmark could not be fetched, None if OK.
    """
    if not benchmark_symbol:
        return None
    if benchmark_symbol in universe_data:
        return None
    try:
        chain = get_chain()
        fetch_start = start - timedelta(days=int(lookback_days * 1.6))
        bars = chain.get_kline(benchmark_symbol, market, "daily", fetch_start, end)
        if bars:
            universe_data[benchmark_symbol] = pd.DataFrame([{
                "open": b.open, "high": b.high, "low": b.low,
                "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
            } for b in bars], index=pd.DatetimeIndex([b.time for b in bars]))
            return None
        else:
            logger.warning("No benchmark data for %s", benchmark_symbol)
            return f"基准 {benchmark_symbol} 无数据，已退化为现金基准"
    except Exception as e:
        logger.warning("Failed to fetch benchmark %s: %s", benchmark_symbol, e)
        return f"基准 {benchmark_symbol} 获取失败，已退化为现金基准"


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
    # V2.11.1: Add alpha_combiner as special option (not in registry)
    if "alpha_combiner" not in factor_list:
        factor_list.append("alpha_combiner")

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
        # Exclude alpha_combiner (special construct, not evaluable as single factor)
        other_factors = [f for f in factor_list if f not in categorized_keys and f != "alpha_combiner"]
        if other_factors:
            categories.append({"key": "other", "label": "其他 (Other)", "factors": other_factors})

    except ImportError:
        categories = [{"key": "technical", "label": "量价 (Technical)", "factors": factor_list}]

    return {"strategies": result, "available_factors": factor_list, "factor_categories": categories}


def _build_active_weights(result, index_weights):
    """Build active weight deviation dict for index enhancement."""
    if not index_weights:
        return None
    latest_w = result.rebalance_weights[-1] if result.rebalance_weights else {}
    if not latest_w:
        return None
    active = {}
    for s in sorted(set(latest_w) | set(index_weights)):
        pw = latest_w.get(s, 0)
        bw = index_weights.get(s, 0)
        if abs(pw - bw) > 1e-6:
            active[s] = {"portfolio": round(pw, 6), "benchmark": round(bw, 6), "active": round(pw - bw, 6)}
    return active if active else None


def _compute_inline_attribution(result, universe_data, initial_cash,
                                 benchmark_type="equal", custom_benchmark=None):
    """Compute Brinson attribution inline after a run. Returns dict or None."""
    try:
        from ez.portfolio.attribution import compute_attribution
        industry_map = {}
        try:
            from ez.api.deps import get_fundamental_store
            fstore = get_fundamental_store()
            if fstore:
                industry_map = fstore.get_all_industries()
        except Exception:
            pass
        attr = compute_attribution(result, universe_data, industry_map,
                                   initial_cash=initial_cash,
                                   benchmark_type=benchmark_type,
                                   custom_benchmark=custom_benchmark)
        if attr.cumulative is None:
            return None
        return {
            "cumulative": {
                "allocation": round(attr.cumulative.allocation_effect, 6),
                "selection": round(attr.cumulative.selection_effect, 6),
                "interaction": round(attr.cumulative.interaction_effect, 6),
                "total_excess": round(attr.cumulative.total_excess, 6),
            },
            "cost_drag": round(attr.cost_drag, 6),
            "by_industry": {k: {kk: round(vv, 6) for kk, vv in v.items()}
                           for k, v in attr.by_industry.items()},
            "periods": [
                {"start": p.period_start, "end": p.period_end,
                 "allocation": round(p.allocation_effect, 6),
                 "selection": round(p.selection_effect, 6),
                 "interaction": round(p.interaction_effect, 6),
                 "total_excess": round(p.total_excess, 6)}
                for p in attr.periods
            ],
        }
    except Exception:
        return None


@router.post("/run")
def run_portfolio(req: PortfolioRunRequest):
    """Run a portfolio backtest."""
    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    try:
        strategy, fund_warnings = _create_strategy(req.strategy_name, req.strategy_params,
                                                   symbols=req.symbols, start=start, end=end,
                                                   market=req.market)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    universe = Universe(req.symbols)
    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, strategy.lookback_days)

    # Count fetched symbols BEFORE adding benchmark (so benchmark doesn't inflate count)
    fetched_count = len(universe_data)
    skipped = [s for s in req.symbols if s not in universe_data]

    bench_warn = _ensure_benchmark(req.benchmark_symbol, universe_data, req.market, start, end, strategy.lookback_days)
    if bench_warn:
        fund_warnings.append(bench_warn)

    buy_rate = req.commission_rate if req.commission_rate is not None else req.buy_commission_rate
    sell_rate = req.commission_rate if req.commission_rate is not None else req.sell_commission_rate
    cost_model = CostModel(
        buy_commission_rate=buy_rate,
        sell_commission_rate=sell_rate,
        min_commission=req.min_commission,
        stamp_tax_rate=req.stamp_tax_rate,
        slippage_rate=req.slippage_rate,
    )

    # V2.12.1: Index enhancement — fetch constituent weights
    index_weights: dict[str, float] = {}
    if req.index_benchmark:
        try:
            from ez.portfolio.index_data import IndexDataProvider
            idx_provider = IndexDataProvider()
            index_weights = idx_provider.get_weights(req.index_benchmark)
            if not index_weights:
                fund_warnings.append(f"无法获取指数 {req.index_benchmark} 成分数据")
        except Exception as e:
            fund_warnings.append(f"指数数据获取失败: {e}")

    # V2.12: Optimizer
    optimizer_instance = None
    if req.optimizer != "none":
        from ez.portfolio.optimizer import (
            MeanVarianceOptimizer, MinVarianceOptimizer,
            RiskParityOptimizer, OptimizationConstraints,
        )
        industry_map = {}
        try:
            from ez.api.deps import get_fundamental_store
            fstore = get_fundamental_store()
            if fstore:
                industry_map = fstore.get_all_industries()
        except Exception:
            pass
        if not industry_map and req.max_industry_weight < 1.0:
            fund_warnings.append("无行业分类数据，行业约束不生效。请先获取基本面数据。")
        constraints = OptimizationConstraints(
            max_weight=req.max_weight,
            max_industry_weight=req.max_industry_weight,
            industry_map=industry_map,
        )
        # V2.12.1: pass index weights + TE to optimizer
        opt_extra = {}
        if index_weights:
            opt_extra = {"benchmark_weights": index_weights, "max_tracking_error": req.max_tracking_error}
        opt_map = {
            "mean_variance": lambda: MeanVarianceOptimizer(
                risk_aversion=req.risk_aversion, constraints=constraints, cov_lookback=req.cov_lookback, **opt_extra),
            "min_variance": lambda: MinVarianceOptimizer(constraints=constraints, cov_lookback=req.cov_lookback, **opt_extra),
            "risk_parity": lambda: RiskParityOptimizer(constraints=constraints, cov_lookback=req.cov_lookback, **opt_extra),
        }
        optimizer_instance = opt_map[req.optimizer]()

    # V2.12: Risk control
    risk_mgr = None
    if req.risk_control:
        if req.drawdown_recovery >= req.max_drawdown:
            raise HTTPException(422, f"drawdown_recovery({req.drawdown_recovery}) must be < max_drawdown({req.max_drawdown})")
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        risk_mgr = RiskManager(RiskConfig(
            max_drawdown_threshold=req.max_drawdown,
            drawdown_reduce_ratio=req.drawdown_reduce,
            drawdown_recovery_ratio=req.drawdown_recovery,
            max_turnover=req.max_turnover,
        ))

    result = run_portfolio_backtest(
        strategy=strategy, universe=universe, universe_data=universe_data,
        calendar=calendar, start=start, end=end, freq=req.freq,
        initial_cash=req.initial_cash, cost_model=cost_model,
        lot_size=req.lot_size, limit_pct=req.limit_pct,
        benchmark_symbol=req.benchmark_symbol,
        optimizer=optimizer_instance, risk_manager=risk_mgr,
        t_plus_1=(req.market == "cn_stock"),
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
        "rebalance_weights": [
            {"date": d.isoformat(), "weights": w}
            for d, w in zip(result.rebalance_dates, result.rebalance_weights)
        ],
        "trades": result.trades,
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
        "risk_events": result.risk_events if result.risk_events else None,
        "attribution": _compute_inline_attribution(
            result, universe_data, req.initial_cash,
            benchmark_type="custom" if index_weights else "equal",
            custom_benchmark=index_weights or None,
        ),
        "active_weights": _build_active_weights(result, index_weights),
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

    wf_warnings: list[str] = []

    def strategy_factory():
        s, w = _create_strategy(req.strategy_name, req.strategy_params,
                                symbols=req.symbols, start=start, end=end,
                                market=req.market)
        if w:
            wf_warnings.extend(w)
        return s

    universe = Universe(req.symbols)
    strategy_tmp = strategy_factory()
    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, strategy_tmp.lookback_days)

    wf_bench_warn = _ensure_benchmark(req.benchmark_symbol, universe_data, req.market, start, end, strategy_tmp.lookback_days)

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
        "warnings": (wf_warnings + ([wf_bench_warn] if wf_bench_warn else [])) or None,
    }


# ─── V2.11.1: Portfolio Parameter Search ───

class PortfolioSearchRequest(BaseModel):
    strategy_name: str = "TopNRotation"
    symbols: list[str]
    market: str = "cn_stock"
    start_date: date | None = None
    end_date: date | None = None
    freq: str = Field(default="monthly", pattern="^(daily|weekly|monthly|quarterly)$")
    param_grid: dict[str, list] = {}
    max_combinations: int = Field(default=50, ge=1, le=200)
    initial_cash: float = Field(default=1_000_000, ge=10_000)
    buy_commission_rate: float = Field(default=0.0003, ge=0)
    sell_commission_rate: float = Field(default=0.0003, ge=0)
    min_commission: float = Field(default=5.0, ge=0)
    stamp_tax_rate: float = Field(default=0.0005, ge=0)
    slippage_rate: float = Field(default=0.0, ge=0)
    lot_size: int = Field(default=100, ge=1)
    limit_pct: float = Field(default=0.10, ge=0, le=0.30)
    benchmark_symbol: str = ""


def _generate_combinations(param_grid: dict[str, list], max_combos: int) -> tuple[list[dict], int]:
    """Expand parameter grid. Returns (combos, total_before_truncation)."""
    import itertools
    import random
    keys = list(param_grid.keys())
    if not keys:
        return [], 0
    values = [param_grid[k] for k in keys]
    all_combos = [dict(zip(keys, v)) for v in itertools.product(*values)]
    total = len(all_combos)
    if total > max_combos:
        random.seed(42)  # reproducible sampling
        random.shuffle(all_combos)
        all_combos = all_combos[:max_combos]
    return all_combos, total


@router.post("/search")
def portfolio_search(req: PortfolioSearchRequest):
    """Batch parameter search. Fetch data once, run N backtests, rank by Sharpe."""
    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    combos, total_before_truncation = _generate_combinations(req.param_grid, req.max_combinations)
    if not combos:
        raise HTTPException(400, "参数网格为空")
    search_funda_warn: list[str] = []

    # Fetch data once (E1: shared across all combinations)
    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, lookback_days=300)
    search_bench_warn = _ensure_benchmark(req.benchmark_symbol, universe_data, req.market, start, end, lookback_days=300)

    # Pre-load fundamental data once if any combo uses fundamental factors (E1+I2)
    from ez.factor.builtin.fundamental import FundamentalCrossFactor, NEEDS_FINA
    need_fina = False
    needs_preload = False
    factor_map = _get_factor_map()
    def _check_factor_name(fn):
        nonlocal needs_preload, need_fina
        factory = factor_map.get(fn)
        if factory and isinstance(factory, type) and issubclass(factory, FundamentalCrossFactor):
            needs_preload = True
            if factory.__name__ in NEEDS_FINA:
                need_fina = True

    for combo in combos:
        # TopNRotation: single "factor" key
        fn = combo.get("factor", "")
        _check_factor_name(fn)
        # AlphaCombiner sub-factors
        if fn == "alpha_combiner":
            for sf in combo.get("alpha_factors", []):
                _check_factor_name(sf)
        # MultiFactorRotation: "factors" list key
        for mfn in combo.get("factors", []):
            _check_factor_name(mfn)
    if needs_preload:
        search_funda_warn = _ensure_fundamental_data(req.symbols, start, end, need_fina=need_fina)

    cost_model = CostModel(
        buy_commission_rate=req.buy_commission_rate,
        sell_commission_rate=req.sell_commission_rate,
        min_commission=req.min_commission,
        stamp_tax_rate=req.stamp_tax_rate,
        slippage_rate=req.slippage_rate,
    )

    results = []
    for i, params in enumerate(combos):
        try:
            strategy, _ = _create_strategy(req.strategy_name, params,
                                           symbols=req.symbols, start=start, end=end,
                                           market=req.market, skip_ensure=True)
            result = run_portfolio_backtest(
                strategy=strategy, universe=Universe(req.symbols),
                universe_data=universe_data, calendar=calendar,
                start=start, end=end, freq=req.freq,
                initial_cash=req.initial_cash, cost_model=cost_model,
                lot_size=req.lot_size, limit_pct=req.limit_pct,
                benchmark_symbol=req.benchmark_symbol,
                t_plus_1=(req.market == "cn_stock"),
            )
            m = result.metrics
            results.append({
                "rank": 0,
                "params": params,
                "sharpe": m.get("sharpe_ratio"),
                "total_return": m.get("total_return"),
                "annualized_return": m.get("annualized_return"),
                "max_drawdown": m.get("max_drawdown"),
                "trade_count": m.get("trade_count", 0),
            })
        except Exception as e:
            logger.warning("Search combo %d/%d failed: %s", i + 1, len(combos), e)

    def _sort_key(r):
        v = r.get("sharpe")
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return -999.0
        return v
    results.sort(key=_sort_key, reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    resp = {"results": results, "total_combinations": total_before_truncation, "sampled": len(combos), "completed": len(results)}
    all_search_warns = (search_funda_warn or []) + ([search_bench_warn] if search_bench_warn else [])
    if all_search_warns:
        resp["warnings"] = all_search_warns
    return resp


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


@router.get("/runs/{run_id}/weights")
def get_run_weights(run_id: str):
    """Return full rebalance_weights for a run (V2.12.1 S3)."""
    run = _get_store().get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run '{run_id}' not found")
    return {"rebalance_weights": run.get("rebalance_weights", [])}


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
    neutralize: bool = Field(default=False, description="行业中性化")


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


# Note: _NeutralizedWrapper is defined at module level (not inside a function) to avoid
# repeated class creation. __init_subclass__ registers it momentarily; the pop on line ~820
# removes it immediately. This is import-order safe because pop executes at module load time.
class _NeutralizedWrapper(CrossSectionalFactor):
    """Transparent wrapper that neutralizes factor scores by industry."""
    def __init__(self, inner, ind_map):
        self._inner = inner
        self._ind_map = ind_map
        self.neutralize_warnings: list[str] = []

    @property
    def name(self):
        return self._inner.name

    @property
    def warmup_period(self):
        return self._inner.warmup_period

    def compute_raw(self, universe_data, date):
        from ez.portfolio.neutralization import neutralize_by_industry
        raw = self._inner.compute_raw(universe_data, date)
        if len(raw) == 0:
            return raw
        neutralized, warnings = neutralize_by_industry(raw, self._ind_map)
        for w in warnings:
            if w not in self.neutralize_warnings:
                self.neutralize_warnings.append(w)
        return neutralized

    def compute(self, universe_data, date):
        raw = self.compute_raw(universe_data, date)
        return raw.rank(pct=True) if len(raw) > 0 else raw

CrossSectionalFactor._registry.pop("_NeutralizedWrapper", None)


def _wrap_neutralized(factor, industry_map: dict):
    """Wrap a factor to apply industry neutralization on compute_raw()."""
    return _NeutralizedWrapper(factor, industry_map)


@router.post("/evaluate-factors")
def evaluate_factors(req: FactorEvalRequest):
    """Evaluate cross-sectional factors: IC, Rank IC, ICIR, IC decay, quintile returns."""
    from ez.portfolio.cross_evaluator import evaluate_cross_sectional_factor, evaluate_ic_decay

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))
    factors = _resolve_factors(req.factor_names, symbols=req.symbols, start=start, end=end)

    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, lookback_days=300)

    # V2.11.1: Apply industry neutralization if requested
    neutralize_warnings = []
    if req.neutralize:
        from ez.api.deps import get_fundamental_store
        store = get_fundamental_store()
        store.preload(req.symbols, start, end)
        industry_map = store.get_all_industries()
        factors = [_wrap_neutralized(f, industry_map) for f in factors]

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

    # Collect neutralization warnings
    if req.neutralize:
        for f in factors:
            if hasattr(f, 'neutralize_warnings') and f.neutralize_warnings:
                neutralize_warnings.extend(f.neutralize_warnings)

    resp = {"results": results, "symbols_count": len(universe_data)}
    if neutralize_warnings:
        resp["warnings"] = list(set(neutralize_warnings))
    return resp


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
