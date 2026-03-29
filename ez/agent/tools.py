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

    return [cls.__name__ for cls in Factor.__subclasses__() if cls.__module__.startswith("ez.")]


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

    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    allowed_dirs = [
        (_PROJECT_ROOT / "strategies").resolve(),
        (_PROJECT_ROOT / "ez" / "strategy" / "builtin").resolve(),
        (_PROJECT_ROOT / "ez" / "factor" / "builtin").resolve(),
    ]
    full = (_PROJECT_ROOT / path).resolve()
    # Must resolve to inside one of the allowed directories
    if not any(str(full).startswith(str(d) + "/") or str(full) == str(d) for d in allowed_dirs):
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

    spec = RunSpec(
        strategy_name=strategy_name,
        strategy_params=params or {},
        symbol=symbol,
        market=market,
        period=period,
        start_date=start_date,
        end_date=end_date,
        run_wfo=False,
    )
    result = Runner().run(spec, df)
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

    spec = RunSpec(
        strategy_name=strategy_name,
        strategy_params=params or {},
        symbol=symbol,
        market=market,
        period=period,
        start_date=start_date,
        end_date=end_date,
    )

    store = get_experiment_store()
    existing = store.get_completed_run_id(spec.spec_id)
    if existing:
        return {"status": "duplicate", "run_id": existing, "spec_id": spec.spec_id}

    result = Runner().run(spec, df)
    gate = ResearchGate()
    verdict = gate.evaluate(result)
    report = ExperimentReport.from_result(result, verdict)
    report_dict = report.to_dict()

    store.save_spec(spec.__dict__)
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
