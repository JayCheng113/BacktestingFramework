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
from datetime import datetime

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
    created_at: datetime = field(default_factory=datetime.now)
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


def _resolve_strategy(name: str, params: dict[str, float]) -> Strategy:
    """Find strategy by name in the registry and instantiate with params."""
    for key, cls in Strategy._registry.items():
        if cls.__name__ == name or key == name:
            schema = cls.get_parameters_schema()
            merged = {k: v["default"] for k, v in schema.items()}
            merged.update(params)
            return cls(**merged)
    raise ValueError(f"Strategy '{name}' not found in registry")


def _build_matcher(spec: RunSpec) -> Matcher:
    if spec.slippage_rate > 0:
        return SlippageMatcher(
            slippage_rate=spec.slippage_rate,
            commission_rate=spec.commission_rate,
            min_commission=spec.min_commission,
        )
    return SimpleMatcher(
        commission_rate=spec.commission_rate,
        min_commission=spec.min_commission,
    )


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
