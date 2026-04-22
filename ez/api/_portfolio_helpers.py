"""Extracted helper functions for portfolio route handlers.

Pure helpers that don't depend on FastAPI request/response objects.
Moved from routes/portfolio.py to reduce route file size.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from fastapi import HTTPException

from ez.api.deps import get_chain
from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import CrossSectionalFactor, MomentumRank, VolumeRank, ReverseVolatilityRank
from ez.portfolio.portfolio_strategy import PortfolioStrategy, TopNRotation, MultiFactorRotation

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


def _get_current_data_hash() -> str | None:
    """V2.16.2 round 4: fetch parquet cache hash from the DuckDBStore
    singleton. Falls back to None if the store / cache manifest is
    unavailable (dev env without cache, or fresh install)."""
    try:
        from ez.api.deps import get_store
        store = get_store()
        return store.get_data_hash() if store else None
    except Exception:
        return None


def _is_public_portfolio_strategy_cls(cls: type[PortfolioStrategy]) -> bool:
    """Hide test-only portfolio strategies from public API surfaces."""
    return getattr(cls, "PUBLIC_API", True) is not False


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


def _max_factor_warmup(items, default: int = 300) -> int:
    """Compute max warmup_period across factor instances or names, with 50-day buffer.

    V2.12.1 post-review (codex #9): portfolio search / factor eval / factor
    correlation / alpha training previously hardcoded lookback_days=300. For
    long-warmup custom factors (e.g., 250-day momentum, annualized stats) this
    fed the compute() routine insufficient history, silently biasing the
    result. This helper is called at each fetch site to adapt the lookback
    to the actual factors in use.
    """
    factor_map = _get_factor_map()
    max_w = int(default)
    for item in items or []:
        w = 0
        if hasattr(item, 'warmup_period'):
            w = int(getattr(item, 'warmup_period', 0) or 0)
        elif isinstance(item, str):
            factory = factor_map.get(item)
            if factory:
                try:
                    inst = factory() if callable(factory) else factory
                    w = int(getattr(inst, 'warmup_period', 0) or 0)
                except Exception:
                    continue
        if w > 0:
            # +50 days so rolling-window factors have clean post-warmup data
            max_w = max(max_w, w + 50)
    return max_w


def _compute_alpha_weights(factors, symbols, market, start, end, method,
                           forward_days: int = 5) -> dict[str, float] | None:
    """Pre-compute IC/ICIR weights from training data before backtest start.

    forward_days: horizon for IC computation (default 5, matches typical rebalance cycle).
    """
    from ez.portfolio.cross_evaluator import evaluate_cross_sectional_factor
    from ez.factor.builtin.fundamental import FundamentalCrossFactor, NEEDS_FINA

    train_end = start - timedelta(days=1)
    # V2.13.2 G1.3: dynamic training window. Previously fixed at 365 days,
    # which starved long-warmup factors (e.g., MLAlpha(train_window=400)).
    max_warmup = _max_factor_warmup(factors)
    train_days = max(365, max_warmup * 2)
    train_start = start - timedelta(days=train_days)

    try:
        # Dynamic lookback: respect factor warmup instead of hardcoded 300
        dynamic_lb = _max_factor_warmup(factors)
        universe_data, calendar = _fetch_data(symbols, market, train_start, train_end, lookback_days=dynamic_lb)
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
            # V2.12.2 codex: propagate dynamic_lb to the evaluator — prior
            # version only lengthened the data fetch, but
            # evaluate_cross_sectional_factor() internally defaults to
            # lookback_days=252 and then slice_universe_data() truncates,
            # so long-warmup custom factors (e.g. 250-day momentum,
            # annualized stats) were silently starved of history.
            result = evaluate_cross_sectional_factor(
                factor=f, universe_data=universe_data, calendar=calendar,
                start=train_start, end=train_end, forward_days=forward_days, eval_freq="weekly",
                lookback_days=dynamic_lb,
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


def _build_optimizer_risk_factories(req):
    """Build optimizer_factory / risk_manager_factory from a PortfolioCommonConfig request.

    V2.12.1 reviewer round 6 C1+I3 + round 7 M1 + round 8 M1+M2:
    shared helper used by /run, /walk-forward, and /search so all three
    endpoints construct optimizer and risk manager the same way from the
    same config fields — AND surface the same configuration warnings.

    Returns (opt_factory, rm_factory, index_weights, warnings):
    - opt_factory: callable returning a fresh PortfolioOptimizer (or None)
    - rm_factory: callable returning a fresh RiskManager (or None)
    - index_weights: pre-fetched constituent weights for the index benchmark
      (empty dict if req.index_benchmark is unset or fetch failed)
    - warnings: list[str] of configuration warnings ready to append to the
      endpoint's response. Covers:
      * Index fetch failure (with exception detail when available) — round 8 M1
      * Missing industry-map data when max_industry_weight < 1.0 — round 8 M2

    Factories (not instances) because both classes carry state across days
    and need fresh copies for each backtest/fold/combo:
    - /run: single call, instantiate once via opt_factory() / rm_factory()
    - /walk-forward: per fold, n_splits × 2 (IS + OOS) instances
    - /search: per combo, one optimizer + one risk_manager per parameter combo
    """
    warnings_out: list[str] = []

    index_weights: dict[str, float] = {}
    if getattr(req, "index_benchmark", "") and req.index_benchmark:
        try:
            from ez.portfolio.index_data import IndexDataProvider
            idx_provider = IndexDataProvider()
            fetched = idx_provider.get_weights(req.index_benchmark) or {}
            if fetched:
                index_weights = fetched
            else:
                warnings_out.append(f"无法获取指数 {req.index_benchmark} 成分数据")
        except Exception as e:
            # V2.12.1 round 8 M1: preserve exception detail. Prior version
            # silently swallowed the exception and only emitted the generic
            # "empty result" warning at the endpoint layer.
            logger.warning("Index data fetch failed for %s: %s", req.index_benchmark, e)
            warnings_out.append(f"指数数据获取失败: {e}")
            index_weights = {}

    opt_factory = None
    if getattr(req, "optimizer", "none") != "none":
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
        # V2.12.1 round 8 M2: industry-map warning now lives in the helper
        # so /run, /walk-forward, and /search all emit it consistently.
        # Prior version only /run had this check.
        if not industry_map and getattr(req, "max_industry_weight", 1.0) < 1.0:
            warnings_out.append("无行业分类数据，行业约束不生效。请先获取基本面数据。")
        constraints = OptimizationConstraints(
            max_weight=req.max_weight,
            max_industry_weight=req.max_industry_weight,
            industry_map=industry_map,
        )
        opt_extra = {}
        if index_weights:
            opt_extra = {
                "benchmark_weights": index_weights,
                "max_tracking_error": req.max_tracking_error,
            }

        def _make_optimizer():
            if req.optimizer == "mean_variance":
                return MeanVarianceOptimizer(
                    risk_aversion=req.risk_aversion, constraints=constraints,
                    cov_lookback=req.cov_lookback, **opt_extra)
            elif req.optimizer == "min_variance":
                return MinVarianceOptimizer(
                    constraints=constraints, cov_lookback=req.cov_lookback, **opt_extra)
            else:
                return RiskParityOptimizer(
                    constraints=constraints, cov_lookback=req.cov_lookback, **opt_extra)
        opt_factory = _make_optimizer

    rm_factory = None
    if getattr(req, "risk_control", False):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig

        def _make_rm():
            return RiskManager(RiskConfig(
                max_drawdown_threshold=req.max_drawdown,
                drawdown_reduce_ratio=req.drawdown_reduce,
                drawdown_recovery_ratio=req.drawdown_recovery,
                max_turnover=req.max_turnover,
            ))
        rm_factory = _make_rm

    return opt_factory, rm_factory, index_weights, warnings_out


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
    elif name == "StrategyEnsemble":
        from ez.portfolio.ensemble import StrategyEnsemble
        sub_defs: list[dict] = p.pop("sub_strategies", [])
        if not sub_defs or len(sub_defs) < 2:
            raise HTTPException(400, "StrategyEnsemble 需要至少 2 个子策略")
        mode = p.pop("mode", "equal")
        ensemble_weights = p.pop("ensemble_weights", None)
        warmup_rebalances = p.pop("warmup_rebalances", 8)
        correlation_threshold = p.pop("correlation_threshold", 0.9)

        sub_strategies = []
        all_warnings: list[str] = []
        for sub_def in sub_defs:
            sub_name = sub_def.get("name", "")
            sp = dict(sub_def.get("params", {}))
            if not sub_name:
                raise HTTPException(400, "子策略 name 不能为空")
            sub_strat, sub_warn = _create_strategy(
                sub_name, sp, symbols=symbols, start=start, end=end,
                market=market, skip_ensure=skip_ensure,
            )
            sub_strategies.append(sub_strat)
            all_warnings.extend(sub_warn)

        try:
            ensemble = StrategyEnsemble(
                strategies=sub_strategies,
                mode=mode,
                ensemble_weights=ensemble_weights,
                warmup_rebalances=warmup_rebalances,
                correlation_threshold=correlation_threshold,
            )
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        return ensemble, all_warnings
    elif name in PortfolioStrategy.get_registry():
        cls = PortfolioStrategy.get_registry()[name]
        if not _is_public_portfolio_strategy_cls(cls):
            raise HTTPException(404, f"Strategy '{name}' not found")
        # Auto-inject broad/sector classification from strategy's own DEFAULT_BROAD_ETFS
        # EtfRotateCombo has its own DEFAULT_SECTOR_ETFS and handles classification
        # internally — don't override with route's "symbols - broad" heuristic.
        has_own_sector = hasattr(cls, "DEFAULT_SECTOR_ETFS")
        if symbols and "broad_symbols" not in p and "sector_symbols" not in p and not has_own_sector:
            default_broad = getattr(cls, "DEFAULT_BROAD_ETFS", None)
            if default_broad:
                broad = [s for s in symbols if s in default_broad]
                sector = [s for s in symbols if s not in default_broad]
                if broad and sector:
                    p["broad_symbols"] = broad
                    p["sector_symbols"] = sector
        # Auto-inject rotate_symbols for EtfRotateCombo
        if symbols and "rotate_symbols" not in p:
            default_rotate = getattr(cls, "DEFAULT_ROTATE_SYMBOLS", None)
            if default_rotate:
                p["rotate_symbols"] = [s for s in symbols if s in default_rotate]
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

# V2.12.2 codex: dual-dict registry — pop from both dicts.
CrossSectionalFactor._registry.pop("_NeutralizedWrapper", None)
_nw_key = f"{_NeutralizedWrapper.__module__}._NeutralizedWrapper"
CrossSectionalFactor._registry_by_key.pop(_nw_key, None)


def _wrap_neutralized(factor, industry_map: dict):
    """Wrap a factor to apply industry neutralization on compute_raw()."""
    return _NeutralizedWrapper(factor, industry_map)


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
