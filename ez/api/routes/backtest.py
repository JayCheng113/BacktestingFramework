"""Backtest endpoints."""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ez.api.deps import get_chain
from ez.backtest.engine import VectorizedBacktestEngine
from ez.config import load_config
from ez.core.matcher import FillResult, Matcher, SimpleMatcher, SlippageMatcher
from ez.core.market_rules import MarketRulesMatcher
from ez.backtest.walk_forward import WalkForwardValidator
from ez.strategy.base import Strategy

router = APIRouter()


class _SellSideTaxMatcher(Matcher):
    """Wrapper: adds sell-side stamp tax without modifying core Matcher.

    A-share stamp tax is charged only on sells (0.05%).
    """

    def __init__(self, inner: Matcher, stamp_tax_rate: float):
        self._inner = inner
        self._tax = stamp_tax_rate

    def fill_buy(self, price: float, amount: float) -> FillResult:
        return self._inner.fill_buy(price, amount)

    def fill_sell(self, price: float, shares: float) -> FillResult:
        result = self._inner.fill_sell(price, shares)
        if result.shares <= 0:
            return result
        tax = result.shares * result.fill_price * self._tax
        # Cap tax so net_amount stays >= 0 (inner may have already capped commission)
        max_tax = max(result.net_amount, 0.0)
        tax = min(tax, max_tax)
        return FillResult(
            shares=result.shares,
            fill_price=result.fill_price,
            commission=result.commission + tax,
            net_amount=result.net_amount - tax,
        )


class BacktestRequest(BaseModel):
    symbol: str = Field(min_length=1)
    market: str = "cn_stock"
    period: str = "daily"
    strategy_name: str
    strategy_params: dict = {}
    start_date: date
    end_date: date
    initial_capital: float = 1000000.0
    # V2.12.2 codex: prior version had only `commission_rate` (single value),
    # silently dropping the frontend's "sell commission" UI input.
    # New fields `buy_commission_rate` and `sell_commission_rate` allow
    # asymmetric rates; when both are None, falls back to `commission_rate`
    # for backward compat with external callers.
    commission_rate: float | None = Field(default=None, ge=0, description="Legacy single commission rate; overridden by buy/sell rates when set")
    buy_commission_rate: float | None = Field(default=None, ge=0, description="Buy-side commission rate; None = use commission_rate")
    sell_commission_rate: float | None = Field(default=None, ge=0, description="Sell-side commission rate; None = use commission_rate")
    min_commission: float | None = Field(default=None, ge=0, description="Min commission per trade; None = use config default")
    slippage_rate: float = Field(default=0.0, ge=0, le=0.1, description="Slippage rate (e.g., 0.001 = 0.1%)")
    stamp_tax_rate: float = Field(default=0.0, ge=0, description="Sell-side stamp tax (A-share: 0.0005)")
    lot_size: int = Field(default=0, ge=0, description="Lot size (A-share: 100, 0=disabled)")
    limit_pct: float = Field(default=0.0, ge=0, le=0.3, description="Limit up/down pct (A-share: 0.10, 0=disabled)")


class WalkForwardRequest(BacktestRequest):
    n_splits: int = Field(default=5, ge=2, le=50)
    train_ratio: float = Field(default=0.7, gt=0.0, lt=1.0)


def _get_strategy(name: str, params: dict) -> Strategy:
    """Resolve a strategy identifier to an instantiated Strategy.

    Delegates to Strategy.resolve_class() for the three-stage resolution
    (exact key → unique name → ambiguous), then instantiates with params
    merged over the schema defaults. Converts KeyError → 404 and
    AmbiguousStrategyName → 409 for the REST layer.
    """
    from ez.strategy.base import AmbiguousStrategyName
    try:
        cls = Strategy.resolve_class(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    except AmbiguousStrategyName as e:
        raise HTTPException(status_code=409, detail=str(e))
    schema = cls.get_parameters_schema()
    p = {k: v["default"] for k, v in schema.items()}
    p.update(params)
    return cls(**p)


def _fetch_data(req: BacktestRequest) -> pd.DataFrame:
    from ez.api.deps import fetch_kline_df
    return fetch_kline_df(req.symbol, req.market, req.period, req.start_date, req.end_date)


def _build_matcher(req: BacktestRequest) -> Matcher:
    """Build matcher from request params. Falls back to config defaults for None values.

    V2.12.1 post-review (codex): MarketRulesMatcher previously wrapped any
    request with lot_size > 0, but the frontend passes lot_size=1 for US/HK
    (to disable lot-size rounding) — and MarketRulesMatcher defaults to
    t_plus_1=True, silently enforcing A-share T+1 on foreign markets.
    Fix:
    - lot_size=1 is treated as "no constraint" (was > 0, now > 1)
    - t_plus_1 is gated on market == cn_stock, so HK/US future lot_size>1
      cases will NOT incorrectly apply T+1
    """
    config = load_config()
    # V2.12.2 codex: resolve buy + sell rates independently. Order of
    # precedence per side: explicit buy/sell field → legacy commission_rate
    # → config default. Prior version only used `commission_rate` and
    # dropped the frontend's sell-side input.
    default_rate = config.backtest.default_commission_rate
    legacy_rate = req.commission_rate if req.commission_rate is not None else default_rate
    buy_rate = req.buy_commission_rate if req.buy_commission_rate is not None else legacy_rate
    sell_rate = req.sell_commission_rate if req.sell_commission_rate is not None else legacy_rate
    min_comm = req.min_commission if req.min_commission is not None else config.backtest.default_min_commission
    if req.slippage_rate > 0:
        inner: Matcher = SlippageMatcher(
            slippage_rate=req.slippage_rate,
            commission_rate=buy_rate,
            sell_commission_rate=sell_rate,
            min_commission=min_comm,
        )
    else:
        inner = SimpleMatcher(
            commission_rate=buy_rate,
            sell_commission_rate=sell_rate,
            min_commission=min_comm,
        )
    # Sell-side stamp tax wrapper (A-share: 0.05%)
    if req.stamp_tax_rate > 0:
        inner = _SellSideTaxMatcher(inner, stamp_tax_rate=req.stamp_tax_rate)
    # Wrap with MarketRulesMatcher if A-share rules requested.
    # lot_size=1 is treated as "no constraint" (avoids the US/HK T+1 leak).
    if req.lot_size > 1 or req.limit_pct > 0:
        inner = MarketRulesMatcher(
            inner=inner,
            t_plus_1=(req.market == "cn_stock"),  # T+1 is A-share only
            lot_size=req.lot_size if req.lot_size > 1 else 0,
            price_limit_pct=req.limit_pct if req.limit_pct > 0 else 0,
        )
    return inner


@router.post("/run")
def run_backtest(req: BacktestRequest):
    strategy = _get_strategy(req.strategy_name, req.strategy_params)
    df = _fetch_data(req)
    config = load_config()
    engine = VectorizedBacktestEngine(
        matcher=_build_matcher(req),
        risk_free_rate=config.backtest.risk_free_rate,
    )
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
    config = load_config()
    validator = WalkForwardValidator(
        VectorizedBacktestEngine(
            matcher=_build_matcher(req),
            risk_free_rate=config.backtest.risk_free_rate,
        )
    )
    try:
        result = validator.validate(df, strategy, req.n_splits, req.train_ratio, req.initial_capital)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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
            "description": cls.get_description() if hasattr(cls, 'get_description') else "",
        }
        for key, cls in Strategy._registry.items()
    ]
