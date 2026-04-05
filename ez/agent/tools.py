"""V2.7: Tool registration framework for the AI assistant.

Tools are plain functions decorated with @tool(). The decorator captures
metadata (name, description, parameter schema) used to:
1. Generate OpenAI-format tool definitions for the LLM
2. Dispatch tool calls from the agent loop

Security boundaries are enforced per-tool, not at the framework level.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Global tool registry
_TOOLS: dict[str, dict] = {}  # name -> {"fn": callable, "schema": dict}


def tool(name: str, description: str, params: dict | None = None):
    """Decorator to register a function as an agent tool.

    Args:
        name: Tool name (unique).
        description: Human-readable description for the LLM.
        params: JSON Schema for parameters. If None, auto-generated from
                function signature (simple types only).
    """

    def decorator(fn: Callable) -> Callable:
        schema = params
        if schema is None:
            schema = _schema_from_sig(fn)
        _TOOLS[name] = {
            "fn": fn,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": schema,
                },
            },
        }
        return fn

    return decorator


def _schema_from_sig(fn: Callable) -> dict:
    """Auto-generate JSON Schema from function signature."""
    sig = inspect.signature(fn)
    properties: dict[str, dict] = {}
    required: list[str] = []
    type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}

    for pname, param in sig.parameters.items():
        annotation = param.annotation
        json_type = type_map.get(annotation, "string")
        prop: dict[str, Any] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
        else:
            prop["default"] = param.default
        properties[pname] = prop

    return {"type": "object", "properties": properties, "required": required}


def get_all_tool_schemas() -> list[dict]:
    """Return OpenAI-format tool definitions for all registered tools."""
    return [entry["schema"] for entry in _TOOLS.values()]


def execute_tool(name: str, arguments: dict) -> str:
    """Execute a registered tool and return the result as a string."""
    entry = _TOOLS.get(name)
    if not entry:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        result = entry["fn"](**arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str, ensure_ascii=False)
    except Exception as e:
        logger.warning("Tool %s failed: %s", name, e)
        return json.dumps({"error": str(e)})


# ── Built-in tool implementations ────────────────────────────────────


@tool(
    name="list_strategies",
    description="List all registered trading strategies with their parameter schemas.",
    params={"type": "object", "properties": {}, "required": []},
)
def _list_strategies() -> list[dict]:
    from ez.strategy.base import Strategy
    from ez.strategy.loader import load_all_strategies

    load_all_strategies()
    return [
        {
            "name": cls.__name__,
            "key": key,
            "parameters": cls.get_parameters_schema(),
            "description": cls.get_description() if hasattr(cls, "get_description") else "",
        }
        for key, cls in Strategy._registry.items()
    ]


@tool(
    name="list_factors",
    description="List all available technical factors (indicators).",
    params={"type": "object", "properties": {}, "required": []},
)
def _list_factors() -> list[str]:
    import ez.factor.builtin.technical  # noqa: F401 — ensure loaded
    from ez.factor.base import Factor

    # Include both builtin (ez.*) and user factors (factors.*)
    return [cls.__name__ for cls in Factor.__subclasses__()
            if cls.__module__.startswith("ez.") or cls.__module__.startswith("factors.")]


@tool(
    name="read_source",
    description="Read the source code of a strategy or factor file. Only files in strategies/ and ez/strategy/builtin/ and ez/factor/builtin/ are accessible.",
    params={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Relative file path, e.g. 'strategies/my_strategy.py' or 'ez/strategy/builtin/ma_cross.py'"}},
        "required": ["path"],
    },
)
def _read_source(path: str) -> str:
    from pathlib import Path

    from ez.config import get_project_root
    _PROJECT_ROOT = get_project_root()
    allowed_dirs = [
        (_PROJECT_ROOT / "strategies").resolve(),
        (_PROJECT_ROOT / "ez" / "strategy" / "builtin").resolve(),
        (_PROJECT_ROOT / "ez" / "factor" / "builtin").resolve(),
    ]
    full = (_PROJECT_ROOT / path).resolve()
    # Must resolve to inside one of the allowed directories
    if not any(full.is_relative_to(d) for d in allowed_dirs):
        return json.dumps({"error": "Access denied: path resolves outside allowed directories"})
    if not full.exists() or not full.is_file():
        return json.dumps({"error": f"File not found: {path}"})
    return full.read_text(encoding="utf-8")


@tool(
    name="create_strategy",
    description="Create a new strategy Python file in the strategies/ directory. Runs contract test automatically. Returns test result.",
    params={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "File name, e.g. 'rsi_reversal.py'"},
            "code": {"type": "string", "description": "Complete Python source code for the strategy"},
        },
        "required": ["filename", "code"],
    },
)
def _create_strategy(filename: str, code: str) -> dict:
    from ez.agent.sandbox import save_and_validate_strategy

    return save_and_validate_strategy(filename, code)


@tool(
    name="update_strategy",
    description="Update an existing strategy file in strategies/ directory. Runs contract test automatically.",
    params={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "File name, e.g. 'rsi_reversal.py'"},
            "code": {"type": "string", "description": "Complete updated Python source code"},
        },
        "required": ["filename", "code"],
    },
)
def _update_strategy(filename: str, code: str) -> dict:
    from ez.agent.sandbox import save_and_validate_strategy

    return save_and_validate_strategy(filename, code, overwrite=True)


@tool(
    name="run_backtest",
    description="Run a single backtest for a strategy. Returns metrics (Sharpe, max drawdown, trade count, etc.).",
    params={
        "type": "object",
        "properties": {
            "strategy_name": {"type": "string", "description": "Strategy class name, e.g. 'MACrossStrategy'"},
            "symbol": {"type": "string", "description": "Stock symbol, e.g. '000001.SZ'"},
            "market": {"type": "string", "default": "cn_stock"},
            "period": {"type": "string", "default": "daily"},
            "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            "params": {"type": "object", "description": "Strategy parameters", "default": {}},
        },
        "required": ["strategy_name", "symbol", "start_date", "end_date"],
    },
)
def _run_backtest(
    strategy_name: str,
    symbol: str,
    start_date: str,
    end_date: str,
    market: str = "cn_stock",
    period: str = "daily",
    params: dict | None = None,
) -> dict:
    from datetime import date

    from ez.agent.run_spec import RunSpec
    from ez.agent.runner import Runner
    from ez.agent.data_access import get_chain

    chain = get_chain()
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)
    bars = chain.get_kline(symbol, market, period, sd, ed)
    if not bars:
        return {"error": f"No data for {symbol}"}

    import pandas as pd

    df = pd.DataFrame(
        [
            {
                "time": b.time, "open": b.open, "high": b.high, "low": b.low,
                "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
            }
            for b in bars
        ]
    ).set_index("time")

    # V2.12.1 post-review (codex): propagate A-share market rules so chat/
    # research backtest runs use the SAME execution environment as the web
    # ExperimentPanel (which sets use_market_rules=True for cn_stock). Prior
    # version inherited RunSpec defaults (use_market_rules=False), producing
    # systematically different results between the AI assistant and the UI.
    is_cn = market == "cn_stock"
    spec = RunSpec(
        strategy_name=strategy_name,
        strategy_params=params or {},
        symbol=symbol,
        market=market,
        period=period,
        start_date=start_date,
        end_date=end_date,
        run_wfo=False,
        use_market_rules=is_cn,
        t_plus_1=is_cn,
        price_limit_pct=0.10 if is_cn else 0.0,
        lot_size=100 if is_cn else 1,
    )
    result = _run_with_timeout(lambda: Runner().run(spec, df), timeout=300)
    if isinstance(result, dict) and "error" in result:
        return result
    if result.status == "failed":
        return {"error": result.error}
    metrics = result.backtest.metrics if result.backtest else {}
    return {
        "status": "completed",
        "metrics": metrics,
        "trade_count": metrics.get("trade_count", 0),
        "sharpe_ratio": metrics.get("sharpe_ratio"),
        "max_drawdown": metrics.get("max_drawdown"),
        "total_return": metrics.get("total_return"),
    }


def _run_with_timeout(fn, timeout: int = 300):
    """Run a function with timeout. Returns error dict on timeout.

    Note: Python threads cannot be force-killed. On timeout, the caller returns
    immediately but the underlying thread continues until it finishes naturally.
    cancel_futures=True prevents queued (not running) futures from starting.

    Resource safety: pool.shutdown() is called in BOTH success and timeout paths
    via try/finally — prior versions only cleaned up on timeout, leaking one
    ThreadPoolExecutor per successful run_backtest/run_experiment call.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    timed_out = False
    try:
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            timed_out = True
            future.cancel()
            return {"error": f"执行超时 ({timeout}秒)，请缩短回测区间或减少股票数量"}
    finally:
        # Success path: wait for worker to finish (it already did — result() returned)
        # Timeout path: don't wait; let orphaned thread die naturally (Python can't kill threads)
        pool.shutdown(wait=not timed_out, cancel_futures=True)


@tool(
    name="run_experiment",
    description="Run a full experiment (backtest + walk-forward + significance + gate) and persist results.",
    params={
        "type": "object",
        "properties": {
            "strategy_name": {"type": "string"},
            "symbol": {"type": "string"},
            "market": {"type": "string", "default": "cn_stock"},
            "period": {"type": "string", "default": "daily"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "params": {"type": "object", "default": {}},
        },
        "required": ["strategy_name", "symbol", "start_date", "end_date"],
    },
)
def _run_experiment(
    strategy_name: str,
    symbol: str,
    start_date: str,
    end_date: str,
    market: str = "cn_stock",
    period: str = "daily",
    params: dict | None = None,
) -> dict:
    from datetime import date

    from ez.agent.gates import ResearchGate
    from ez.agent.report import ExperimentReport
    from ez.agent.run_spec import RunSpec
    from ez.agent.runner import Runner
    from ez.agent.data_access import get_chain, get_experiment_store

    chain = get_chain()
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)
    bars = chain.get_kline(symbol, market, period, sd, ed)
    if not bars:
        return {"error": f"No data for {symbol}"}

    import pandas as pd

    df = pd.DataFrame(
        [
            {
                "time": b.time, "open": b.open, "high": b.high, "low": b.low,
                "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
            }
            for b in bars
        ]
    ).set_index("time")

    # Market rules aligned with web ExperimentPanel (codex fix, see _run_backtest)
    is_cn = market == "cn_stock"
    spec = RunSpec(
        strategy_name=strategy_name,
        strategy_params=params or {},
        symbol=symbol,
        market=market,
        period=period,
        start_date=start_date,
        end_date=end_date,
        use_market_rules=is_cn,
        t_plus_1=is_cn,
        price_limit_pct=0.10 if is_cn else 0.0,
        lot_size=100 if is_cn else 1,
    )

    store = get_experiment_store()
    existing = store.get_completed_run_id(spec.spec_id)
    if existing:
        return {"status": "duplicate", "run_id": existing, "spec_id": spec.spec_id}

    result = _run_with_timeout(lambda: Runner().run(spec, df), timeout=600)  # experiments get 10 min (WFO is slow)
    if isinstance(result, dict) and "error" in result:
        return result
    gate = ResearchGate()
    verdict = gate.evaluate(result)
    report = ExperimentReport.from_result(result, verdict)
    report_dict = report.to_dict()

    # spec.to_dict() — NOT spec.__dict__ — because spec_id is a @property
    # computed from the other fields, and save_spec() reads spec_dict["spec_id"].
    # Prior version passed __dict__ which lacks the property → KeyError crash.
    store.save_spec(spec.to_dict())
    store.save_completed_run(report_dict)

    return {
        "status": report_dict["status"],
        "run_id": report_dict["run_id"],
        "gate_passed": report_dict["gate_passed"],
        "gate_summary": report_dict["gate_summary"],
        "sharpe_ratio": report_dict["sharpe_ratio"],
        "max_drawdown": report_dict["max_drawdown"],
        "total_return": report_dict["total_return"],
    }


@tool(
    name="list_experiments",
    description="List recent experiment runs with metrics and gate results.",
    params={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 20},
        },
        "required": [],
    },
)
def _list_experiments(limit: int = 20) -> list[dict]:
    from ez.agent.data_access import get_experiment_store

    store = get_experiment_store()
    return store.list_runs(limit=limit)


@tool(
    name="explain_metrics",
    description="Get detailed metrics and gate reasons for a specific experiment run.",
    params={
        "type": "object",
        "properties": {"run_id": {"type": "string", "description": "The run_id to look up"}},
        "required": ["run_id"],
    },
)
def _explain_metrics(run_id: str) -> dict:
    from ez.agent.data_access import get_experiment_store

    store = get_experiment_store()
    run = store.get_run(run_id)
    if not run:
        return {"error": f"Run {run_id} not found"}
    return run


# ── V2.9 Portfolio tools ─────────────────────────────────────────────


@tool(
    name="list_portfolio_strategies",
    description="List all registered portfolio strategies with their parameter schemas.",
    params={"type": "object", "properties": {}, "required": []},
)
def list_portfolio_strategies() -> list[dict]:
    from ez.portfolio.portfolio_strategy import PortfolioStrategy
    return [
        {"name": name, "description": cls.get_description().strip()[:200] if hasattr(cls, 'get_description') else ""}
        for name, cls in PortfolioStrategy.get_registry().items()
    ]


@tool(
    name="create_portfolio_strategy",
    description="Create a new portfolio strategy file in portfolio_strategies/. Runs contract test (weights>=0, sum<=1). Returns test result.",
    params={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Filename like 'my_rotation.py'"},
            "code": {"type": "string", "description": "Python code defining a PortfolioStrategy subclass"},
        },
        "required": ["filename", "code"],
    },
)
def create_portfolio_strategy(filename: str, code: str) -> dict:
    from ez.agent.sandbox import save_and_validate_code
    return save_and_validate_code(filename, code, kind="portfolio_strategy")


@tool(
    name="create_cross_factor",
    description="Create a new cross-sectional factor file in cross_factors/. Runs contract test (returns Series, not all NaN). Returns test result.",
    params={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Filename like 'my_factor.py'"},
            "code": {"type": "string", "description": "Python code defining a CrossSectionalFactor subclass"},
        },
        "required": ["filename", "code"],
    },
)
def create_cross_factor(filename: str, code: str) -> dict:
    from ez.agent.sandbox import save_and_validate_code
    return save_and_validate_code(filename, code, kind="cross_factor")


@tool(
    name="run_portfolio_backtest",
    description="Run a portfolio backtest with multiple stocks. Returns metrics (Sharpe, return, drawdown, turnover).",
    params={
        "type": "object",
        "properties": {
            "strategy_name": {"type": "string", "description": "Registered strategy name (e.g. 'TopNRotation')"},
            "symbols": {"type": "array", "items": {"type": "string"}, "description": "Stock/ETF codes"},
            "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            "freq": {"type": "string", "description": "Rebalance frequency: daily/weekly/monthly/quarterly", "default": "monthly"},
            "strategy_params": {"type": "object", "description": "Strategy parameters (e.g. {top_n: 10, factor: 'momentum_rank_20'})", "default": {}},
        },
        "required": ["strategy_name", "symbols", "start_date", "end_date"],
    },
)
def run_portfolio_backtest_tool(
    strategy_name: str, symbols: list[str],
    start_date: str, end_date: str,
    freq: str = "monthly", strategy_params: dict | None = None,
) -> dict:
    valid_freqs = {"daily", "weekly", "monthly", "quarterly"}
    if freq not in valid_freqs:
        return {"error": f"Invalid freq '{freq}'. Must be one of: {sorted(valid_freqs)}"}

    from datetime import date, timedelta
    import numpy as np
    import pandas as pd
    from ez.agent.data_access import get_chain
    from ez.portfolio.calendar import TradingCalendar
    from ez.portfolio.cross_factor import MomentumRank, VolumeRank, ReverseVolatilityRank
    from ez.portfolio.engine import run_portfolio_backtest
    from ez.portfolio.portfolio_strategy import PortfolioStrategy, TopNRotation, MultiFactorRotation
    from ez.portfolio.universe import Universe

    _factor_map = {
        "momentum_rank_20": lambda: MomentumRank(20),
        "momentum_rank_10": lambda: MomentumRank(10),
        "volume_rank_20": lambda: VolumeRank(20),
        "reverse_vol_rank_20": lambda: ReverseVolatilityRank(20),
    }

    params = dict(strategy_params or {})
    registry = PortfolioStrategy.get_registry()

    # Create strategy instance
    if strategy_name == "TopNRotation":
        factor_name = params.pop("factor", "momentum_rank_20")
        factory = _factor_map.get(factor_name)
        if not factory:
            return {"error": f"Unknown factor: {factor_name}"}
        strategy = TopNRotation(factor=factory(), top_n=params.pop("top_n", 10))
    elif strategy_name == "MultiFactorRotation":
        factor_names = params.pop("factors", ["momentum_rank_20"])
        factors = [_factor_map[f]() for f in factor_names if f in _factor_map]
        strategy = MultiFactorRotation(factors=factors, top_n=params.pop("top_n", 10))
    elif strategy_name in registry:
        strategy = registry[strategy_name](**params)
    else:
        return {"error": f"Strategy '{strategy_name}' not found"}

    # Fetch data
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    chain = get_chain()
    fetch_start = start - timedelta(days=int(strategy.lookback_days * 1.6))

    universe_data = {}
    all_dates = set()
    for sym in symbols:
        try:
            bars = chain.get_kline(sym, "cn_stock", "daily", fetch_start, end)
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
            continue

    if not universe_data:
        return {"error": "No data available for any symbol"}

    calendar = TradingCalendar.from_dates(sorted(all_dates))
    universe = Universe(symbols)

    result = run_portfolio_backtest(
        strategy=strategy, universe=universe, universe_data=universe_data,
        calendar=calendar, start=start, end=end, freq=freq,
    )

    # Sanitize metrics
    metrics = {}
    for k, v in result.metrics.items():
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            metrics[k] = None
        else:
            metrics[k] = v

    return {
        "metrics": metrics,
        "trade_count": len(result.trades),
        "rebalance_count": len(result.rebalance_dates),
        "equity_start": result.equity_curve[0] if result.equity_curve else 0,
        "equity_end": result.equity_curve[-1] if result.equity_curve else 0,
    }
