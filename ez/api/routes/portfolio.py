"""V2.9+V2.10+V2.11: Portfolio API — run, list, detail, delete + factor evaluation + fundamental."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from ez.portfolio.cross_factor import CrossSectionalFactor
from ez.portfolio.engine import CostModel, run_portfolio_backtest
from ez.portfolio.portfolio_strategy import PortfolioStrategy
from ez.portfolio.builtin_strategies import EtfMacdRotation  # noqa: F401
from ez.portfolio.universe import Universe

# Extracted helpers (pure functions, no FastAPI route coupling)
from ez.api._portfolio_helpers import (
    _BUILTIN_FACTOR_MAP,
    _get_current_data_hash,
    _is_public_portfolio_strategy_cls,
    _is_fundamental_factor,
    _inject_fundamental_store,
    _ensure_fundamental_data,
    _get_factor_map,
    _create_alpha_combiner,
    _max_factor_warmup,
    _compute_alpha_weights,
    _build_optimizer_risk_factories,
    _create_strategy,
    _fetch_data,
    _ensure_benchmark,
    _build_active_weights,
    _compute_inline_attribution,
    _resolve_factors,
    _NeutralizedWrapper,
    _wrap_neutralized,
    _generate_combinations,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class PortfolioCommonConfig(BaseModel):
    """Shared optimizer/risk-control/index-enhancement/cost fields.

    V2.12.1 reviewer round 6 C1 fix: previously PortfolioRunRequest and
    PortfolioWFRequest each declared their own copies of these fields with
    DIFFERENT default values and Field constraints — running "/run" vs
    "/walk-forward" with identical user payloads produced different strategies
    because Pydantic fills in different defaults.

    Single source of truth for every field that affects optimizer / risk /
    index / cost behavior. Any endpoint that runs a portfolio backtest should
    inherit this mixin so defaults stay in lockstep.
    """

    # Cost model
    buy_commission_rate: float = Field(default=0.00008, ge=0)
    sell_commission_rate: float = Field(default=0.00008, ge=0)
    min_commission: float = Field(default=0.0, ge=0)
    stamp_tax_rate: float = Field(default=0.0005, ge=0)
    # V2.13.2 G1.2: stamp_tax is A-share specific (0.05% sell-side).
    # For non-CN markets, zero it unless user explicitly set it.
    # Uses model_fields_set to distinguish "user passed 0.0005" from
    # "Pydantic filled in the default 0.0005".

    @model_validator(mode="after")
    def _gate_stamp_tax_by_market(self):
        market = getattr(self, "market", "cn_stock")
        if market != "cn_stock" and "stamp_tax_rate" not in self.model_fields_set:
            self.stamp_tax_rate = 0.0
        return self
    slippage_rate: float = Field(default=0.001, ge=0)
    lot_size: int = Field(default=100, ge=1)
    limit_pct: float = Field(default=0.10, ge=0, le=0.30)

    @model_validator(mode="after")
    def _gate_lot_and_limit_by_market(self):
        """V2.16.2 round 3: lot_size / limit_pct are A-share specific.
        Default 100/0.10 silently applied to non-CN markets via API
        default-fill (Pydantic fills missing fields from Field default).
        A US backtest with all defaults would round to 100-share lots
        and reject moves > 10% — diverging from real US market rules.
        Use `model_fields_set` to distinguish explicit override from
        default fill (same pattern as stamp_tax)."""
        market = getattr(self, "market", "cn_stock")
        if market != "cn_stock":
            if "lot_size" not in self.model_fields_set:
                self.lot_size = 1
            if "limit_pct" not in self.model_fields_set:
                self.limit_pct = 0.0
        return self
    benchmark_symbol: str = ""

    # Optimizer (V2.12)
    optimizer: str = Field(default="none", pattern="^(none|mean_variance|min_variance|risk_parity)$")
    risk_aversion: float = Field(default=1.0, gt=0)
    max_weight: float = Field(default=0.10, gt=0, le=1.0)
    max_industry_weight: float = Field(default=0.30, gt=0, le=1.0)
    cov_lookback: int = Field(default=60, ge=10, le=500)

    # Risk control (V2.12)
    risk_control: bool = False
    max_drawdown: float = Field(default=0.20, gt=0, le=0.50)
    drawdown_reduce: float = Field(default=0.50, gt=0, le=1.0)
    drawdown_recovery: float = Field(default=0.10, gt=0, le=0.50)
    max_turnover: float = Field(default=0.50, gt=0, le=2.0)

    # Index enhancement (V2.12.1)
    index_benchmark: str = Field(default="", pattern=r"^(|000300|000905|000852)$")
    max_tracking_error: float = Field(default=0.05, gt=0, le=0.20)

    # Lookback validation (V2.13.2)
    strict_lookback: bool = Field(
        default=False,
        description="When True, raise ValueError if strategy.lookback_days "
                    "< max factor warmup_period (instead of just warning).",
    )
    # Weekly rebalance day (V2.16.2): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri
    rebal_weekday: int | None = Field(
        default=None, ge=0, le=4,
        description="Override weekly rebalance day (0=Mon..4=Fri). "
                    "None = last trading day of each week (default).",
    )
    skip_terminal_liquidation: bool = Field(
        default=False,
        description="Skip forced liquidation at backtest end (QMT compat).",
    )
    use_open_price: bool = Field(
        default=False,
        description="Execute trades at open price instead of close (QMT 5-min compat).",
    )


class PortfolioRunRequest(PortfolioCommonConfig):
    strategy_name: str = "TopNRotation"
    symbols: list[str]
    market: str = "cn_stock"
    start_date: date | None = None
    end_date: date | None = None
    freq: str = Field(default="monthly", pattern="^(daily|weekly|monthly|quarterly)$")
    strategy_params: dict = {}
    initial_cash: float = Field(default=1_000_000, ge=10_000)
    commission_rate: float | None = Field(default=None, ge=0)  # backward compat: if set, overrides both


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
        if not _is_public_portfolio_strategy_cls(cls):
            continue
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

        # V2.13.2 G2b: ML Alpha category — user-registered MLAlpha subclasses
        try:
            from ez.portfolio.ml.alpha import MLAlpha
            ml_alpha_factors = []
            for f in factor_list:
                if f in categorized_keys or f == "alpha_combiner":
                    continue
                cls = factor_map.get(f)
                if cls and isinstance(cls, type) and issubclass(cls, MLAlpha):
                    ml_alpha_factors.append(f)
                    categorized_keys.add(f)
            if ml_alpha_factors:
                categories.append({"key": "ml_alpha", "label": "ML Alpha", "factors": ml_alpha_factors})
        except ImportError:
            pass  # sklearn not installed — no ML alphas

        # "Other" category: user-registered factors not in any category above
        # Exclude alpha_combiner (special construct, not evaluable as single factor)
        other_factors = [f for f in factor_list if f not in categorized_keys and f != "alpha_combiner"]
        if other_factors:
            categories.append({"key": "other", "label": "其他 (Other)", "factors": other_factors})

    except ImportError:
        categories = [{"key": "technical", "label": "量价 (Technical)", "factors": factor_list}]

    # V2.14 B3: Append StrategyEnsemble metadata (not in registry)
    result.append({
        "name": "StrategyEnsemble",
        "description": "多策略组合: 等权/手动权重/收益加权/反向波动率",
        "parameters": {
            "mode": {"type": "select", "options": ["equal", "manual", "return_weighted", "inverse_vol"], "default": "equal", "label": "组合模式"},
            "sub_strategies": {"type": "ensemble_subs", "default": [], "label": "子策略列表"},
            "ensemble_weights": {"type": "weights", "default": None, "label": "手动权重 (mode=manual 时)"},
            "warmup_rebalances": {"type": "int", "default": 8, "min": 1, "max": 50, "label": "预热再平衡次数"},
            "correlation_threshold": {"type": "float", "default": 0.9, "min": 0.0, "max": 1.0, "label": "相关性警告阈值"},
        },
        "is_ensemble": True,
    })

    return {"strategies": result, "available_factors": factor_list, "factor_categories": categories}


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

    # V2.12.1 reviewer round 7 M1 + round 8: reuse _build_optimizer_risk_factories
    # helper. Helper now returns warnings list covering index fetch + industry
    # map issues, so /run, /walk-forward, /search all surface identical
    # configuration warnings.
    if req.risk_control and req.drawdown_recovery >= req.max_drawdown:
        raise HTTPException(
            422,
            f"drawdown_recovery({req.drawdown_recovery}) must be < "
            f"max_drawdown({req.max_drawdown})",
        )
    opt_factory, rm_factory, index_weights, helper_warnings = _build_optimizer_risk_factories(req)
    fund_warnings.extend(helper_warnings)
    # Single backtest — instantiate factories once
    optimizer_instance = opt_factory() if opt_factory else None
    risk_mgr = rm_factory() if rm_factory else None

    result = run_portfolio_backtest(
        strategy=strategy, universe=universe, universe_data=universe_data,
        calendar=calendar, start=start, end=end, freq=req.freq,
        initial_cash=req.initial_cash, cost_model=cost_model,
        lot_size=req.lot_size, limit_pct=req.limit_pct,
        benchmark_symbol=req.benchmark_symbol,
        optimizer=optimizer_instance, risk_manager=risk_mgr,
        t_plus_1=(req.market == "cn_stock"),
        strict_lookback=req.strict_lookback,
        rebal_weekday=req.rebal_weekday,
        skip_terminal_liquidation=req.skip_terminal_liquidation,
        use_open_price=req.use_open_price,
    )

    # Sanitize NaN/Inf in metrics
    metrics = {}
    for k, v in result.metrics.items():
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            metrics[k] = None
        else:
            metrics[k] = v

    # V2.12.1 codex follow-up: surface optimizer fallback events as user
    # warnings. Prior version only logger.warning'd them, so users saw a
    # "successful" run that silently used equal-weight instead of their
    # requested optimizer.
    if optimizer_instance is not None and optimizer_instance.fallback_events:
        n = len(optimizer_instance.fallback_events)
        reasons = {ev["reason"] for ev in optimizer_instance.fallback_events}
        fund_warnings.append(
            f"优化器在 {n} 次再平衡中退化为等权 (原因: {', '.join(sorted(reasons))[:200]})"
        )

    # Persist. V2.12.2 codex: the `config` column captures every non-default
    # run parameter so historical runs retain full context on reload —
    # optimizer choice, risk-control thresholds, index benchmark, tracking
    # error, market, and cost model. Prior version only packed `_cost` into
    # strategy_params and dropped everything else, making stored runs
    # un-reproducible.
    run_config = {
        "market": req.market,
        "freq": req.freq,
        "rebal_weekday": req.rebal_weekday,
        "_cost": {
            "buy_commission_rate": buy_rate, "sell_commission_rate": sell_rate,
            "min_commission": req.min_commission, "stamp_tax_rate": req.stamp_tax_rate,
            "slippage_rate": req.slippage_rate, "lot_size": req.lot_size,
            "limit_pct": req.limit_pct, "benchmark": req.benchmark_symbol,
        },
        "_optimizer": {
            "kind": req.optimizer,
            "risk_aversion": req.risk_aversion,
            "max_weight": req.max_weight,
            "max_industry_weight": req.max_industry_weight,
            "cov_lookback": req.cov_lookback,
        },
        "_risk": {
            "enabled": req.risk_control,
            "max_drawdown": req.max_drawdown,
            "drawdown_reduce": req.drawdown_reduce,
            "drawdown_recovery": req.drawdown_recovery,
            "max_turnover": req.max_turnover,
        },
        "_index": {
            "benchmark": req.index_benchmark,
            "max_tracking_error": req.max_tracking_error,
        },
        # V2.16.2 round 4: data reproducibility hash. If parquet cache is
        # rebuilt between runs (new build, ETF adj fix, etc.), the hash
        # changes and same-spec runs may produce different results.
        # Stored for cross-run comparison; a mismatch is a diagnostic
        # signal, not a blocker.
        "_data_hash": _get_current_data_hash(),
    }
    store = _get_store()
    run_id = store.save_run({
        "strategy_name": req.strategy_name,
        "strategy_params": {
            **req.strategy_params,
            "_cost": run_config["_cost"],  # backward-compat mirror
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
        "config": run_config,
        "warnings": list(fund_warnings) if fund_warnings else [],
        # V2.12.2 codex: persist per-bar dates so the compare-chart can
        # align runs by real trading days. Prior version stored only
        # equity_curve and the frontend fell back to index-based x-axis,
        # misleading users when compared runs had different date ranges.
        "dates": [d.isoformat() for d in result.dates],
        # V2.12.2 codex round 3: persist per-day actual post-execution
        # holdings. Previously we filtered out empty dict entries to save
        # space, but this dropped the POST-LIQUIDATION terminal marker
        # (engine appends {} after final sell-all). History page then
        # showed the last rebalance weights as "current holdings" even
        # when the backtest ended in all-cash. Now we preserve ALL entries
        # aligned 1:1 with `dates`, so `weights_history[-1] == {}` signals
        # terminal liquidation and history page can render "已清仓" state.
        # Pre-first-rebalance days (also empty) are included too — they
        # accurately represent "no position yet" which downstream
        # attribution and drawdown analysis benefit from knowing.
        "weights_history": [
            {"date": result.dates[i].isoformat(), "weights": result.weights_history[i]}
            for i in range(len(result.weights_history))
            if i < len(result.dates)
        ],
    })

    # V2.12.2 codex: return full trade list instead of truncating at 100.
    # Prior version lost half the trade history for long / frequent-
    # rebalancing strategies, and the frontend's "100+" indicator exposed
    # the bug without offering a drill-down. The persisted store always
    # held the full list; the truncation only existed in the API response.
    return {
        "run_id": run_id,
        "metrics": metrics,
        "equity_curve": [round(v, 2) for v in result.equity_curve],
        "benchmark_curve": [round(v, 2) for v in result.benchmark_curve],
        "dates": [d.isoformat() for d in result.dates],
        "trades": result.trades,
        "rebalance_dates": [d.isoformat() for d in result.rebalance_dates],
        "symbols_fetched": fetched_count,
        "symbols_skipped": skipped,
        # V2.12.1 codex follow-up: return the LAST NON-EMPTY weights entry,
        # not simply [-1]. The engine appends {} after final liquidation
        # so weights_history[-1] is usually empty for backtests whose
        # final period still held positions. Users want to see the last
        # actual held-position weights.
        #
        # V2.12.2 codex round 5+7: after the daily-drift fix, this is the
        # LAST DAILY ACTUAL holdings (drift-adjusted by end-of-day price),
        # NOT necessarily a rebalance target. Frontend labels it as "最新
        # 持仓分布" for normal runs and "期末前最后持仓 (次日已全部清仓)"
        # for terminal-liquidation runs, matching the true semantic.
        "latest_weights": next(
            (w for w in reversed(result.weights_history) if w),
            {}
        ),
        # V2.12.2 codex: flag terminal liquidation state so the UI can
        # distinguish "positions still held at period end" from "all
        # cash after final liquidation". When True, latest_weights is
        # the last pre-liquidation daily snapshot (not a rebalance
        # target, just the final held position before the T+1 force
        # close). UI uses this flag to adjust its pie chart label.
        "terminal_liquidated": (
            bool(result.weights_history)
            and not result.weights_history[-1]
        ),
        "weights_history": [
            {"date": result.dates[i].isoformat() if i < len(result.dates) else "",
             "weights": result.weights_history[i]}
            for i in range(max(0, len(result.weights_history) - 20), len(result.weights_history))
            # Skip empty weight entries in the display (pre-first-rebalance
            # warmup days and the post-liquidation {} marker). Live display
            # doesn't need these — `terminal_liquidated` flag above signals
            # terminal-cash state. History reload via /holdings returns the
            # full dense sequence including these entries.
            if result.weights_history[i]
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


class PortfolioWFRequest(PortfolioCommonConfig):
    """Walk-forward request. Inherits optimizer/risk/index/cost fields from
    PortfolioCommonConfig so /run and /walk-forward ALWAYS resolve to the
    same strategy for the same user payload (V2.12.1 reviewer round 6 C1).
    """

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
    # V2.15.1 S1: optional source_run_id — if provided, WF metrics are persisted
    # to portfolio_runs.wf_metrics for that run. This closes the trust boundary
    # in DeployGate (WF metrics read from DB, not client input).
    source_run_id: str | None = None


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

    # V2.12.1 reviewer round 7 M1 + round 8: shared helper returns helper
    # warnings covering index fetch + industry map issues.
    if req.risk_control and req.drawdown_recovery >= req.max_drawdown:
        raise HTTPException(
            422,
            f"drawdown_recovery({req.drawdown_recovery}) must be < "
            f"max_drawdown({req.max_drawdown})",
        )
    optimizer_factory, risk_manager_factory, index_weights, helper_warnings = _build_optimizer_risk_factories(req)
    wf_warnings.extend(helper_warnings)

    try:
        wf_result = portfolio_walk_forward(
            strategy_factory=strategy_factory,
            universe=universe, universe_data=universe_data, calendar=calendar,
            start=start, end=end, n_splits=req.n_splits, train_ratio=req.train_ratio,
            freq=req.freq, initial_cash=req.initial_cash, cost_model=cost_model,
            lot_size=req.lot_size, limit_pct=req.limit_pct,
            benchmark_symbol=req.benchmark_symbol,
            t_plus_1=(req.market == "cn_stock"),
            optimizer_factory=optimizer_factory,
            risk_manager_factory=risk_manager_factory,
            strict_lookback=req.strict_lookback,
            rebal_weekday=req.rebal_weekday,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Significance on OOS equity curve
    sig = portfolio_significance(wf_result.oos_equity_curve, seed=42) if wf_result.oos_equity_curve else None

    # V2.15.1 S1: persist WF metrics to portfolio_runs.wf_metrics for the source run.
    # This closes the trust boundary — DeployGate reads WF metrics from DB.
    if req.source_run_id:
        pf_store = _get_store()
        pf_store.update_wf_metrics(req.source_run_id, {
            "p_value": sig.monte_carlo_p_value if sig else 1.0,
            "overfitting_score": wf_result.overfitting_score,
            "oos_sharpe": wf_result.oos_metrics.get("sharpe_ratio") if wf_result.oos_metrics else None,
        })

    # V2.12.1 reviewer round 6 I1+I2: surface optimizer fallback and risk events
    # aggregated across all folds so WF users see the same warnings /run users do.
    all_warnings = wf_warnings + ([wf_bench_warn] if wf_bench_warn else [])
    if wf_result.optimizer_fallback_events:
        n = len(wf_result.optimizer_fallback_events)
        reasons = {ev["reason"] for ev in wf_result.optimizer_fallback_events}
        all_warnings.append(
            f"优化器在 walk-forward 中共 {n} 次退化为等权 (原因: {', '.join(sorted(reasons))[:200]})"
        )

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
        "warnings": all_warnings or None,
        "risk_events": wf_result.risk_events if wf_result.risk_events else None,
    }


# ─── V2.11.1: Portfolio Parameter Search ───

class PortfolioSearchRequest(PortfolioCommonConfig):
    """Parameter search request. Inherits optimizer/risk/index/cost fields
    from PortfolioCommonConfig (V2.12.1 reviewer round 6 I3) so searched
    candidates run under the SAME execution environment as /run."""

    strategy_name: str = "TopNRotation"
    symbols: list[str]
    market: str = "cn_stock"
    start_date: date | None = None
    end_date: date | None = None
    freq: str = Field(default="monthly", pattern="^(daily|weekly|monthly|quarterly)$")
    param_grid: dict[str, list] = {}
    max_combinations: int = Field(default=50, ge=1, le=200)
    initial_cash: float = Field(default=1_000_000, ge=10_000)


@router.post("/search")
def portfolio_search(req: PortfolioSearchRequest):
    """Batch parameter search. Fetch data once, run N backtests, rank by Sharpe."""
    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    combos, total_before_truncation = _generate_combinations(req.param_grid, req.max_combinations)
    if not combos:
        raise HTTPException(400, "参数网格为空")
    search_funda_warn: list[str] = []

    # Dynamic lookback: walk the combos and find the max factor warmup so
    # long-warmup factors don't get fed short history (codex #9).
    all_factor_names: list[str] = []
    for combo in combos:
        fn = combo.get("factor")
        if fn:
            all_factor_names.append(fn)
            if fn == "alpha_combiner":
                all_factor_names.extend(combo.get("alpha_factors", []))
        all_factor_names.extend(combo.get("factors", []))
    dynamic_lb = _max_factor_warmup(all_factor_names)

    # Fetch data once (E1: shared across all combinations)
    universe_data, calendar = _fetch_data(req.symbols, req.market, start, end, lookback_days=dynamic_lb)
    search_bench_warn = _ensure_benchmark(req.benchmark_symbol, universe_data, req.market, start, end, lookback_days=dynamic_lb)

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

    # V2.12.1 reviewer round 6 I3 + round 8: search uses the same helper as
    # /run and /walk-forward, collects helper warnings, creates fresh instance
    # per combo inside the loop.
    _opt_factory, _rm_factory, _index_weights, _helper_warns = _build_optimizer_risk_factories(req)

    results = []
    # V2.12.1 reviewer round 7: aggregate optimizer fallback events and engine
    # risk events across ALL combos so search users see the same warnings
    # /run and /walk-forward users do. Prior version discarded these per combo,
    # leaving users unaware when their optimizer silently degenerated to equal-
    # weight across many combos.
    search_optimizer_fallback_events: list[dict] = []
    search_risk_events: list[dict] = []
    # V2.12.2 codex: track failed combos with reasons. Prior version logged
    # a warning and silently dropped them, so the user saw fewer results
    # than combos tried without being told why. `failed_combos` surfaces
    # (combo_index, params, error) triples so the UI can display a clear
    # "N combos failed" banner with the specific parameter sets and error
    # messages that broke.
    failed_combos: list[dict] = []
    for i, params in enumerate(combos):
        # Allocate combo-scoped references outside try/except so finally can
        # reach them (V2.12.1 round 8 M3: prior version lost partial
        # fallback_events when run_portfolio_backtest raised mid-rebalance).
        combo_opt = None
        combo_result = None
        try:
            strategy, _ = _create_strategy(req.strategy_name, params,
                                           symbols=req.symbols, start=start, end=end,
                                           market=req.market, skip_ensure=True)
            combo_opt = _opt_factory() if _opt_factory else None
            combo_rm = _rm_factory() if _rm_factory else None
            combo_result = run_portfolio_backtest(
                strategy=strategy, universe=Universe(req.symbols),
                universe_data=universe_data, calendar=calendar,
                start=start, end=end, freq=req.freq,
                initial_cash=req.initial_cash, cost_model=cost_model,
                lot_size=req.lot_size, limit_pct=req.limit_pct,
                benchmark_symbol=req.benchmark_symbol,
                t_plus_1=(req.market == "cn_stock"),
                optimizer=combo_opt,
                risk_manager=combo_rm,
                strict_lookback=req.strict_lookback,
                rebal_weekday=req.rebal_weekday,
                skip_terminal_liquidation=req.skip_terminal_liquidation,
                use_open_price=req.use_open_price,
            )
            m = combo_result.metrics
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
            err_msg = str(e)[:300]
            logger.warning("Search combo %d/%d failed: %s", i + 1, len(combos), e)
            failed_combos.append({
                "combo_index": i,
                "params": params,
                "error": err_msg,
            })
        finally:
            # V2.12.1 round 8 M3: aggregate events in finally so partial
            # events from a crashed combo are still surfaced to users.
            if combo_opt is not None and combo_opt.fallback_events:
                for ev in combo_opt.fallback_events:
                    search_optimizer_fallback_events.append({**ev, "combo": i})
            if combo_result is not None:
                for ev in combo_result.risk_events:
                    search_risk_events.append({**ev, "combo": i})

    def _sort_key(r):
        v = r.get("sharpe")
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return -999.0
        return v
    results.sort(key=_sort_key, reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # V2.12.2 codex: expose failed count + detail so the UI can display a
    # clear "N combos failed" badge. Prior version silently dropped failed
    # combos from `results`, leaving users unable to distinguish "fewer
    # results because of failures" from "fewer because grid was smaller".
    resp = {
        "results": results,
        "total_combinations": total_before_truncation,
        "sampled": len(combos),
        "completed": len(results),
        "failed": len(failed_combos),
        "failed_combos": failed_combos,
    }
    all_search_warns = (
        (search_funda_warn or [])
        + ([search_bench_warn] if search_bench_warn else [])
        + _helper_warns  # V2.12.1 round 8: index fetch + industry warnings from helper
    )
    # Surface optimizer fallback events as a search-wide warning
    if search_optimizer_fallback_events:
        n = len(search_optimizer_fallback_events)
        reasons = {ev["reason"] for ev in search_optimizer_fallback_events}
        all_search_warns.append(
            f"优化器在参数搜索中共 {n} 次退化为等权 "
            f"(原因: {', '.join(sorted(reasons))[:200]})"
        )
    # V2.12.2 codex: add a prominent warning summarizing how many combos
    # failed so even users who don't inspect `failed_combos` detail see it.
    if failed_combos:
        unique_errors = list({fc["error"] for fc in failed_combos})[:3]
        all_search_warns.append(
            f"⚠️ {len(failed_combos)}/{len(combos)} 个参数组合执行失败 "
            f"(示例错误: {'; '.join(unique_errors)[:300]})"
        )
    if all_search_warns:
        resp["warnings"] = all_search_warns
    if search_risk_events:
        resp["risk_events"] = search_risk_events
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


@router.get("/runs/{run_id}/trades")
def get_run_trades(run_id: str):
    """Return the full trade list for a persisted run (V2.12.2).

    History page uses this to drill into a past run's full trade record.
    Prior to V2.12.2 the /run response truncated trades to 100 and there
    was no drill-down endpoint, so history runs could not display their
    post-rebalance execution detail.
    """
    run = _get_store().get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run '{run_id}' not found")
    return {"trades": run.get("trades", [])}


@router.get("/runs/{run_id}/holdings")
def get_run_holdings(run_id: str):
    """Return per-day actual post-execution holdings for a persisted run.

    V2.12.2 codex: distinct from /runs/{run_id}/weights which returns the
    per-rebalance target weights. This endpoint returns the realized
    holdings after lot rounding and risk-manager turnover caps. Prior to
    V2.12.2 this data was only available in the /run response and was
    lost on reload from history.

    V2.12.2 codex round 3: also returns `terminal_liquidated` flag derived
    from whether the last entry's weights dict is empty. Matches the /run
    response's flag so history-reload and live-response render the same
    UI label ("最后一次调仓目标 (期末已清仓)" vs "最新持仓分布").
    """
    run = _get_store().get_run(run_id)
    if not run:
        raise HTTPException(404, f"Run '{run_id}' not found")
    weights_history = run.get("weights_history") or []
    latest = next(
        (w for w in reversed(weights_history) if isinstance(w, dict) and w.get("weights")),
        {},
    )
    # Terminal liquidation: last entry exists and its weights dict is empty.
    terminal_liquidated = (
        bool(weights_history)
        and isinstance(weights_history[-1], dict)
        and not weights_history[-1].get("weights")
    )
    return {
        "weights_history": weights_history,
        "latest_weights": latest.get("weights", {}) if isinstance(latest, dict) else {},
        "terminal_liquidated": terminal_liquidated,
    }


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


@router.post("/evaluate-factors")
def evaluate_factors(req: FactorEvalRequest):
    """Evaluate cross-sectional factors: IC, Rank IC, ICIR, IC decay, quintile returns."""
    from ez.portfolio.cross_evaluator import evaluate_cross_sectional_factor, evaluate_ic_decay

    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))
    factors = _resolve_factors(req.factor_names, symbols=req.symbols, start=start, end=end)

    # Dynamic lookback: adapt to the factors actually being evaluated (codex #9).
    # V2.12.1 reviewer round 5 follow-up: propagate lookback_days to the
    # evaluator functions too — prior version only lengthened the data fetch
    # but evaluate_cross_sectional_factor() / compute_factor_correlation()
    # internally default to 252 and then `slice_universe_data(..., 252)`, so
    # long-warmup factors were still silently truncated.
    dynamic_lb = _max_factor_warmup(factors)
    universe_data, calendar = _fetch_data(
        req.symbols, req.market, start, end, lookback_days=dynamic_lb,
    )

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
            lookback_days=dynamic_lb,
        )
        decay = evaluate_ic_decay(
            factor=factor, universe_data=universe_data, calendar=calendar,
            start=start, end=end, lags=[1, 5, 10, 20], eval_freq=req.eval_freq,
            lookback_days=dynamic_lb,
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

    # Dynamic lookback: adapt to the factors actually being evaluated (codex #9).
    # Must be passed to compute_factor_correlation() too, not just to the fetch.
    dynamic_lb = _max_factor_warmup(factors)
    universe_data, calendar = _fetch_data(
        req.symbols, req.market, start, end, lookback_days=dynamic_lb,
    )

    corr_df = compute_factor_correlation(
        factors=factors, universe_data=universe_data, calendar=calendar,
        start=start, end=end, eval_freq=req.eval_freq,
        lookback_days=dynamic_lb,
    )

    return {
        "factor_names": list(corr_df.index),
        "correlation_matrix": corr_df.values.tolist(),
    }


# ─── V2.13.1 Phase 5: ML Alpha Diagnostics ───────────────────────

class MLDiagnosticsRequest(BaseModel):
    """Request model for POST /ml-alpha/diagnostics."""

    ml_alpha_name: str = Field(
        ...,
        description="Class name or module.class key of the MLAlpha subclass. "
                    "Resolved via CrossSectionalFactor.resolve_class().",
    )
    symbols: list[str] = Field(..., min_length=1)
    market: str = Field(default="cn_stock", pattern=r"^(cn_stock|us_stock|hk_stock)$")
    start_date: date | None = None
    end_date: date | None = None
    eval_freq: str = Field(
        default="weekly",
        pattern=r"^(daily|weekly|monthly|quarterly)$",
    )
    # DiagnosticsConfig overrides
    forward_horizon: int = Field(default=5, ge=1, le=60)
    severe_overfit_threshold: float = Field(default=0.5, gt=0, le=2.0)
    mild_overfit_threshold: float = Field(default=0.2, gt=0, le=2.0)
    high_turnover_threshold: float = Field(default=0.6, gt=0, le=1.0)
    top_n_for_turnover: int = Field(default=10, ge=1, le=100)


@router.post("/ml-alpha/diagnostics")
def ml_alpha_diagnostics(req: MLDiagnosticsRequest):
    """Run MLDiagnostics on a user-defined MLAlpha and return the
    overfitting assessment.

    V2.13.1 Phase 5: exposes Phase 2 Python API (MLDiagnostics)
    as a REST endpoint.
    """
    # Lazy imports — sklearn is optional, avoid circular deps
    try:
        from ez.portfolio.ml.alpha import MLAlpha
    except ImportError as e:
        # Narrow the message: only claim "sklearn required" if the error
        # actually mentions sklearn. Otherwise surface the real error.
        msg = str(e).lower()
        if "sklearn" in msg or "scikit" in msg:
            detail = (f"scikit-learn>=1.5 is required for MLAlpha diagnostics. "
                      f"Install with: pip install -e '.[ml]'. Error: {e}")
        else:
            detail = f"Failed to import MLAlpha: {type(e).__name__}: {e}"
        raise HTTPException(status_code=422, detail=detail)
    from ez.portfolio.ml.diagnostics import MLDiagnostics, DiagnosticsConfig

    # 1. Resolve class
    try:
        cls = CrossSectionalFactor.resolve_class(req.ml_alpha_name)
    except (KeyError, ValueError) as e:
        raise HTTPException(
            status_code=404,
            detail=f"MLAlpha '{req.ml_alpha_name}' not found: {e}",
        )

    if not issubclass(cls, MLAlpha):
        raise HTTPException(
            status_code=422,
            detail=f"'{req.ml_alpha_name}' is {cls.__name__} "
                   f"(CrossSectionalFactor), not an MLAlpha subclass. "
                   f"Use /evaluate-factors for non-ML factors.",
        )

    # 2. Instantiate (may raise UnsupportedEstimatorError)
    try:
        alpha = cls()
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to instantiate {req.ml_alpha_name}: "
                   f"{type(e).__name__}: {e}",
        )

    # 3. Fetch data (warmup-aware lookback)
    end_dt = req.end_date or date.today()
    start_dt = req.start_date or (end_dt - timedelta(days=730))
    try:
        universe_data, calendar = _fetch_data(
            req.symbols, req.market, start_dt, end_dt,
            lookback_days=alpha.warmup_period + 50,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Data fetch failed: {type(e).__name__}: {e}",
        )

    # 4. Run diagnostics (wrap to prevent raw 500 on user code errors)
    config = DiagnosticsConfig(
        forward_horizon=req.forward_horizon,
        severe_overfit_threshold=req.severe_overfit_threshold,
        mild_overfit_threshold=req.mild_overfit_threshold,
        high_turnover_threshold=req.high_turnover_threshold,
        top_n_for_turnover=req.top_n_for_turnover,
    )
    diag = MLDiagnostics(alpha, config=config)
    try:
        result = diag.run(universe_data, calendar, start_dt, end_dt, req.eval_freq)
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Diagnostics run failed: {type(e).__name__}: {e}",
        )

    return result.to_dict()
