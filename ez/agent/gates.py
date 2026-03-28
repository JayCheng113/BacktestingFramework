"""B3: Gate — automated promotion rules with reason codes.

The ResearchGate evaluates a RunResult against configurable thresholds.
Each rule produces a pass/fail verdict with the actual value, threshold,
and human-readable message. The overall verdict is AND of all rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ez.agent.runner import RunResult


@dataclass
class GateReason:
    """Single rule evaluation result."""

    rule: str
    passed: bool
    value: float
    threshold: float
    message: str


@dataclass
class GateVerdict:
    """Overall gate evaluation result."""

    passed: bool
    reasons: list[GateReason] = field(default_factory=list)

    @property
    def failed_reasons(self) -> list[GateReason]:
        return [r for r in self.reasons if not r.passed]

    @property
    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        n_pass = sum(1 for r in self.reasons if r.passed)
        return f"{status} ({n_pass}/{len(self.reasons)} rules passed)"


@dataclass
class GateConfig:
    """Configurable thresholds for research gate."""

    min_sharpe: float = 0.5
    max_drawdown: float = 0.3  # 30%
    min_trades: int = 10
    max_p_value: float = 0.05  # significance threshold
    max_overfitting_score: float = 0.5
    require_wfo: bool = True


class ResearchGate:
    """Evaluate whether a run result passes the research gate."""

    def __init__(self, config: GateConfig | None = None):
        self._config = config or GateConfig()

    def evaluate(self, result: RunResult) -> GateVerdict:
        if result.status != "completed":
            return GateVerdict(
                passed=False,
                reasons=[GateReason(
                    rule="run_status",
                    passed=False,
                    value=0,
                    threshold=0,
                    message=f"Run failed: {result.error}",
                )],
            )

        reasons: list[GateReason] = []
        cfg = self._config

        # Rule 1: Sharpe ratio
        if result.backtest:
            sharpe = result.backtest.metrics.get("sharpe_ratio", 0.0)
            reasons.append(GateReason(
                rule="min_sharpe",
                passed=sharpe >= cfg.min_sharpe,
                value=sharpe,
                threshold=cfg.min_sharpe,
                message=f"Sharpe {sharpe:.2f} {'>=':} {cfg.min_sharpe}",
            ))

            # Rule 2: Max drawdown
            dd = result.backtest.metrics.get("max_drawdown", 1.0)
            reasons.append(GateReason(
                rule="max_drawdown",
                passed=dd <= cfg.max_drawdown,
                value=dd,
                threshold=cfg.max_drawdown,
                message=f"MaxDD {dd:.1%} {'<=':} {cfg.max_drawdown:.0%}",
            ))

            # Rule 3: Min trades
            trades = result.backtest.metrics.get("trade_count", 0)
            reasons.append(GateReason(
                rule="min_trades",
                passed=trades >= cfg.min_trades,
                value=trades,
                threshold=cfg.min_trades,
                message=f"Trades {int(trades)} {'>=':} {cfg.min_trades}",
            ))

            # Rule 4: Significance
            sig = result.backtest.significance
            reasons.append(GateReason(
                rule="significance",
                passed=sig.monte_carlo_p_value <= cfg.max_p_value,
                value=sig.monte_carlo_p_value,
                threshold=cfg.max_p_value,
                message=f"p-value {sig.monte_carlo_p_value:.3f} {'<=':} {cfg.max_p_value}",
            ))

        # Rule 5: Walk-forward overfitting
        if cfg.require_wfo:
            if result.walk_forward is None:
                reasons.append(GateReason(
                    rule="wfo_required",
                    passed=False,
                    value=0,
                    threshold=0,
                    message="Walk-forward validation required but not run",
                ))
            else:
                ofs = result.walk_forward.overfitting_score
                reasons.append(GateReason(
                    rule="max_overfitting",
                    passed=ofs <= cfg.max_overfitting_score,
                    value=ofs,
                    threshold=cfg.max_overfitting_score,
                    message=f"Overfitting {ofs:.2f} {'<=':} {cfg.max_overfitting_score}",
                ))

        passed = all(r.passed for r in reasons)
        return GateVerdict(passed=passed, reasons=reasons)
