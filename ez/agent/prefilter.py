"""F3: Pre-filter rule engine — fast elimination of weak candidates.

Runs a quick backtest-only check (no WFO) against configurable thresholds.
Candidates that fail pre-filter are skipped from the expensive full pipeline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import pandas as pd

from ez.agent.run_spec import RunSpec
from ez.agent.runner import Runner


@dataclass
class PrefilterConfig:
    """Thresholds for quick elimination."""

    min_sharpe: float = 0.0
    max_drawdown: float = 0.5
    min_trades: int = 5


@dataclass
class PrefilterResult:
    """Result of pre-filtering a single candidate."""

    spec: RunSpec
    passed: bool
    sharpe: float | None = None
    max_drawdown: float | None = None
    trade_count: int = 0
    reason: str = ""


def prefilter(
    specs: list[RunSpec],
    data: pd.DataFrame,
    config: PrefilterConfig | None = None,
) -> list[PrefilterResult]:
    """Run quick backtest on each spec, return filter results.

    Uses backtest-only mode (no WFO) for speed.
    """
    if config is None:
        config = PrefilterConfig()

    runner = Runner()
    results = []

    for spec in specs:
        # V2.12.1 post-review fix (codex): previously this rebuilt RunSpec with a
        # hand-picked subset of fields, silently DROPPING use_market_rules /
        # t_plus_1 / price_limit_pct / lot_size. A candidate that passed prefilter
        # with no A-share rules could then fail the full experiment once lot-size
        # rounding, ±10% limits, and T+1 were enforced — making the gate verdicts
        # inconsistent with the actual trading environment.
        #
        # Use dataclasses.replace() so every current and future RunSpec field
        # is preserved, and only the run-mode flags are overridden.
        quick_spec = replace(spec, run_backtest=True, run_wfo=False)
        run_result = runner.run(quick_spec, data)

        if run_result.status != "completed" or run_result.backtest is None:
            results.append(PrefilterResult(
                spec=spec, passed=False, reason=f"backtest failed: {run_result.error or 'no result'}",
            ))
            continue

        metrics = run_result.backtest.metrics
        raw_sharpe = metrics.get("sharpe_ratio")
        raw_dd = metrics.get("max_drawdown")
        raw_trades = metrics.get("trade_count")

        # NaN/None → fail-safe defaults
        sharpe = raw_sharpe if isinstance(raw_sharpe, (int, float)) and math.isfinite(raw_sharpe) else float("-inf")
        dd = abs(raw_dd) if isinstance(raw_dd, (int, float)) and math.isfinite(raw_dd) else float("inf")
        trades = int(raw_trades) if isinstance(raw_trades, (int, float)) and math.isfinite(raw_trades) else 0

        reasons = []
        if sharpe < config.min_sharpe:
            reasons.append(f"sharpe {sharpe:.2f} < {config.min_sharpe}")
        if dd > config.max_drawdown:
            reasons.append(f"drawdown {dd:.1%} > {config.max_drawdown:.0%}")
        if trades < config.min_trades:
            reasons.append(f"trades {trades} < {config.min_trades}")

        results.append(PrefilterResult(
            spec=spec,
            passed=len(reasons) == 0,
            sharpe=sharpe,
            max_drawdown=dd,
            trade_count=trades,
            reason="; ".join(reasons),
        ))

    return results
