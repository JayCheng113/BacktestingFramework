"""单股回测 REST 路由。

本模块把前端/Agent 的回测请求转换为数据拉取、策略实例化、撮合器构建和
walk-forward 验证调用。依赖 `ez.api.deps` 提供的数据链，以及
`ez.backtest` / `ez.strategy` 的公开接口。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from ez.backtest.engine import VectorizedBacktestEngine
from ez.config import load_config
from ez.core.matcher import Matcher, SimpleMatcher, SlippageMatcher
from ez.core.market_rules import MarketRulesMatcher
from ez.backtest.walk_forward import WalkForwardValidator
from ez.strategy.base import Strategy

router = APIRouter()


# SellSideTaxMatcher moved to ez/core/matcher.py (V3 debt cleanup).
# Re-import for backward compatibility with this module's usage.
from ez.core.matcher import SellSideTaxMatcher as _SellSideTaxMatcher


class BacktestRequest(BaseModel):
    """单股向量化回测请求体。

    字段覆盖标的、市场、周期、策略参数、日期窗口和交易成本。A 股请求在
    未显式传入时会自动补齐印花税、最小交易单位和涨跌停规则。
    """

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
    slippage_rate: float = Field(default=0.001, ge=0, le=0.1, description="Slippage rate (万1 = 0.1%)")
    stamp_tax_rate: float = Field(default=0.0, ge=0, description="Sell-side stamp tax (A-share: 0.0005)")
    lot_size: int = Field(default=0, ge=0, description="Lot size (A-share: 100, 0=disabled)")
    limit_pct: float = Field(default=0.0, ge=0, le=0.3, description="Limit up/down pct (A-share: 0.10, 0=disabled)")

    @model_validator(mode="after")
    def _apply_market_defaults(self):
        """V2.16.2 round 3: auto-fill A-share rules for cn_stock when the
        caller did not explicitly set them. Prior behaviour: defaults
        are 0/0/0 regardless of market, so an external script / AI
        client running a CN backtest without specifying these silently
        skipped stamp tax, lot rounding, and limit checks — systematically
        inflating P&L.

        Use `model_fields_set` to distinguish "user passed 0" (intentional
        disable) from "Pydantic filled default" (caller didn't specify).

        Frontend always sends full cost settings via BacktestSettings.tsx
        `getDefaultSettings(market)`, so this affects only backend-direct
        callers (scripts, AI tools, tests)."""
        if self.market == "cn_stock":
            if "stamp_tax_rate" not in self.model_fields_set:
                self.stamp_tax_rate = 0.0005
            if "lot_size" not in self.model_fields_set:
                self.lot_size = 100
            if "limit_pct" not in self.model_fields_set:
                self.limit_pct = 0.10
        return self


class WalkForwardRequest(BacktestRequest):
    """单股 walk-forward 验证请求体。

    继承基础回测参数，并增加折数和训练区间比例；路由层直接传给
    `WalkForwardValidator`，不会修改策略公开 API。
    """

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
    """按请求窗口获取回测所需 K 线数据。

    Args:
        req: 已通过 Pydantic 校验的回测请求。

    Returns:
        以日期为索引的行情 DataFrame；字段由 `fetch_kline_df` 保证。

    Side Effects:
        可能触发数据源链路读取远端或缓存数据。
    """
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
    """运行单股回测并返回前端所需的序列化结果。

    Args:
        req: 回测请求体，包含策略名称、参数、行情窗口和交易规则。

    Returns:
        指标、净值曲线、交易列表和显著性检验结果组成的 JSON 兼容字典。

    Side Effects:
        读取行情数据源；不写入实验库或组合回测历史。
    """
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
    """运行单股 walk-forward 验证。

    Args:
        req: 继承基础回测参数的 walk-forward 请求体。

    Returns:
        样本外指标、过拟合评分、折数和样本外净值曲线。

    Raises:
        HTTPException: 当验证窗口无法切分或数据不足时返回 400。
    """
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
    """列出已注册的单股策略。

    Returns:
        每个策略的显示名、注册 key、参数 schema 和描述，供前端下拉框使用。
    """
    return [
        {
            "name": cls.__name__,
            "key": key,
            "parameters": cls.get_parameters_schema(),
            "description": cls.get_description() if hasattr(cls, 'get_description') else "",
        }
        for key, cls in Strategy._registry.items()
    ]
