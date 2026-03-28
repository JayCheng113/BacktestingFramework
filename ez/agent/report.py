"""B4: Report — structured experiment output.

Combines RunResult + GateVerdict into a single ExperimentReport
suitable for storage, API response, and human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ez.agent.gates import GateVerdict
from ez.agent.runner import RunResult


@dataclass
class ExperimentReport:
    """Complete experiment output: run result + gate verdict."""

    run_id: str
    spec_id: str
    status: str
    created_at: datetime
    duration_ms: float
    code_commit: str

    # Backtest summary (flattened for easy querying)
    sharpe_ratio: float | None = None
    total_return: float | None = None
    max_drawdown: float | None = None
    trade_count: int = 0
    win_rate: float | None = None
    profit_factor: float | None = None

    # Significance
    p_value: float | None = None
    is_significant: bool = False

    # Walk-forward
    oos_sharpe: float | None = None
    overfitting_score: float | None = None

    # Gate
    gate_passed: bool = False
    gate_summary: str = ""
    gate_reasons: list[dict] = field(default_factory=list)

    # Error
    error: str | None = None

    @classmethod
    def from_result(cls, result: RunResult, verdict: GateVerdict) -> ExperimentReport:
        """Build report from RunResult + GateVerdict."""
        report = cls(
            run_id=result.run_id,
            spec_id=result.spec_id,
            status=result.status,
            created_at=result.created_at,
            duration_ms=result.duration_ms,
            code_commit=result.code_commit,
            error=result.error,
            gate_passed=verdict.passed,
            gate_summary=verdict.summary,
            gate_reasons=[
                {"rule": r.rule, "passed": r.passed, "value": r.value,
                 "threshold": r.threshold, "message": r.message}
                for r in verdict.reasons
            ],
        )

        if result.backtest:
            m = result.backtest.metrics
            report.sharpe_ratio = m.get("sharpe_ratio")
            report.total_return = m.get("total_return")
            report.max_drawdown = m.get("max_drawdown")
            report.trade_count = int(m.get("trade_count", 0))
            report.win_rate = m.get("win_rate")
            report.profit_factor = m.get("profit_factor")
            report.p_value = result.backtest.significance.monte_carlo_p_value
            report.is_significant = result.backtest.significance.is_significant

        if result.walk_forward:
            report.oos_sharpe = result.walk_forward.oos_metrics.get("sharpe_ratio")
            report.overfitting_score = result.walk_forward.overfitting_score

        return report

    def to_dict(self) -> dict:
        """Serialize for JSON/DuckDB."""
        return {
            "run_id": self.run_id,
            "spec_id": self.spec_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "duration_ms": self.duration_ms,
            "code_commit": self.code_commit,
            "sharpe_ratio": self.sharpe_ratio,
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "p_value": self.p_value,
            "is_significant": self.is_significant,
            "oos_sharpe": self.oos_sharpe,
            "overfitting_score": self.overfitting_score,
            "gate_passed": self.gate_passed,
            "gate_summary": self.gate_summary,
            "gate_reasons": self.gate_reasons,
            "error": self.error,
        }
