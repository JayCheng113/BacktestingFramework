"""B2: Runner — orchestrates backtest + WFO + significance for a RunSpec.

The Runner is the central execution unit: given a RunSpec, it fetches data,
instantiates strategy/matcher/engine, runs the requested analyses, and
returns a structured RunResult.
"""
from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from ez.agent.run_spec import RunSpec
from ez.backtest.engine import VectorizedBacktestEngine
from ez.backtest.walk_forward import WalkForwardValidator
from ez.core.matcher import Matcher, SimpleMatcher, SlippageMatcher
from ez.strategy.base import Strategy
from ez.types import BacktestResult, WalkForwardResult


@dataclass
class RunResult:
    """Complete output of a single experiment run."""

    run_id: str
    spec: RunSpec
    spec_id: str
    status: str  # "completed" | "failed"
    backtest: BacktestResult | None = None
    walk_forward: WalkForwardResult | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0
    code_commit: str = ""
    error: str | None = None


def _get_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _resolve_strategy(name: str, params: dict) -> Strategy:
    """Find strategy by name in the registry and instantiate with params.

    V2.12.1 codex post-review fix: delegates class resolution to the shared
    `Strategy.resolve_class()` three-stage helper (exact key → unique name →
    ambiguous). Previously this function had its own first-match scan that
    silently picked wrong classes when two files registered the same
    `__name__` — the exact scenario `promote_research_strategy()` makes
    likely (ResearchFoo → Foo colliding with builtin Foo). The API route
    `_get_strategy` was hardened earlier, but this function wasn't, leaving
    the research pipeline (`run_batch` → `Runner().run`), the experiment
    tool, and the chat assistant backtest tool still vulnerable.

    After resolution, applies schema-driven type coercion that the API
    route doesn't need (JSON/Pydantic may send float for int params).
    """
    from ez.strategy.base import AmbiguousStrategyName
    try:
        cls = Strategy.resolve_class(name)
    except KeyError:
        raise ValueError(f"Strategy '{name}' not found in registry")
    except AmbiguousStrategyName as e:
        # Re-raise as ValueError so Runner can record it in RunResult.error
        raise ValueError(str(e)) from e
    schema = cls.get_parameters_schema()
    merged = {k: v["default"] for k, v in schema.items()}
    merged.update(params)
    # Coerce types per schema (JSON/Pydantic may send float for int params)
    for k, v in merged.items():
        if k in schema:
            expected = schema[k].get("type", "float")
            if expected == "int":
                if isinstance(v, float) and v != int(v):
                    raise ValueError(
                        f"Parameter '{k}' expects int but got {v} "
                        f"(non-integer float would be silently truncated)"
                    )
                merged[k] = int(v)
            elif expected == "float":
                merged[k] = float(v)
            elif expected == "bool":
                if isinstance(v, str):
                    merged[k] = v.lower() not in ("false", "0", "no", "")
                else:
                    merged[k] = bool(v)
    return cls(**merged)


def _build_matcher(spec: RunSpec) -> Matcher:
    if spec.slippage_rate > 0:
        inner: Matcher = SlippageMatcher(
            slippage_rate=spec.slippage_rate,
            commission_rate=spec.commission_rate,
            min_commission=spec.min_commission,
        )
    else:
        inner = SimpleMatcher(
            commission_rate=spec.commission_rate,
            min_commission=spec.min_commission,
        )
    # V2.6: wrap with market rules if enabled
    if spec.use_market_rules:
        from ez.core.market_rules import MarketRulesMatcher
        inner = MarketRulesMatcher(
            inner,
            t_plus_1=spec.t_plus_1,
            price_limit_pct=spec.price_limit_pct,
            lot_size=spec.lot_size,
        )
    return inner


class Runner:
    """Execute a RunSpec and return a RunResult.

    The Runner does NOT fetch data — the caller provides the DataFrame.
    This keeps the Runner pure (no I/O) and testable.
    """

    def run(self, spec: RunSpec, data: pd.DataFrame) -> RunResult:
        run_id = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()

        try:
            strategy = _resolve_strategy(spec.strategy_name, spec.strategy_params)
            matcher = _build_matcher(spec)
            engine = VectorizedBacktestEngine(matcher=matcher)

            bt_result: BacktestResult | None = None
            wf_result: WalkForwardResult | None = None

            if spec.run_backtest:
                bt_result = engine.run(data, strategy, spec.initial_capital)

            if spec.run_wfo:
                validator = WalkForwardValidator(engine)
                wf_result = validator.validate(
                    data, strategy,
                    n_splits=spec.wfo_n_splits,
                    train_ratio=spec.wfo_train_ratio,
                    initial_capital=spec.initial_capital,
                )

            duration = (time.perf_counter() - t0) * 1000
            return RunResult(
                run_id=run_id,
                spec=spec,
                spec_id=spec.spec_id,
                status="completed",
                backtest=bt_result,
                walk_forward=wf_result,
                duration_ms=duration,
                code_commit=_get_git_sha(),
            )

        except Exception as e:
            duration = (time.perf_counter() - t0) * 1000
            return RunResult(
                run_id=run_id,
                spec=spec,
                spec_id=spec.spec_id,
                status="failed",
                duration_ms=duration,
                code_commit=_get_git_sha(),
                error=str(e),
            )
