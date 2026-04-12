"""Validation verdict engine.

Takes the full validation results (WF + significance + annual breakdown
+ paired comparison) and produces a pass/warn/fail verdict with
human-readable reasons.

Design:
- 6 standard checks, configurable thresholds
- Each check returns "pass" / "warn" / "fail" with a reason string
- Overall verdict: fail if any check fails, warn if any warn, else pass
- Returns structured result so the UI can render per-check badges
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VerdictThresholds:
    """Configurable thresholds for each validation check.

    Defaults tuned for A-share quant research (daily bar, long-only,
    typical 3-10 year backtests). Override per-deployment if needed.
    """
    # Walk-Forward
    max_degradation: float = 0.40         # warn if >0.40, fail if >0.70
    max_degradation_fail: float = 0.70
    max_overfitting_score: float = 0.30   # warn if >0.30, fail if >0.60
    max_overfitting_fail: float = 0.60

    # Significance
    max_p_value: float = 0.05              # warn if >0.05, fail if >0.10
    max_p_value_fail: float = 0.10
    min_ci_lower_sharpe: float = 0.0       # CI should exclude 0 (pass)
    min_deflated_sharpe: float = 0.50      # warn if <0.50, fail if <0.30
    min_deflated_sharpe_fail: float = 0.30

    # Sample size
    require_min_btl_pass: bool = True      # fail if backtest < MinBTL

    # Annual stability
    min_profitable_ratio: float = 0.60     # warn if <0.60, fail if <0.40
    min_profitable_ratio_fail: float = 0.40


@dataclass
class CheckResult:
    name: str
    status: str  # "pass" / "warn" / "fail"
    reason: str
    value: Any = None


@dataclass
class Verdict:
    result: str                            # overall: pass/warn/fail
    passed: int
    warned: int
    failed: int
    total: int
    checks: list[CheckResult] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "passed": self.passed,
            "warned": self.warned,
            "failed": self.failed,
            "total": self.total,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "reason": c.reason,
                    "value": c.value,
                }
                for c in self.checks
            ],
            "summary": self.summary,
        }


def _classify(
    value: float,
    warn_threshold: float,
    fail_threshold: float,
    higher_is_worse: bool = True,
) -> str:
    """Classify a value as pass/warn/fail relative to thresholds."""
    if higher_is_worse:
        if value > fail_threshold:
            return "fail"
        if value > warn_threshold:
            return "warn"
        return "pass"
    else:
        if value < fail_threshold:
            return "fail"
        if value < warn_threshold:
            return "warn"
        return "pass"


def compute_verdict(
    *,
    wf_aggregate: dict[str, Any] | None = None,
    bootstrap: dict[str, Any] | None = None,
    deflated: dict[str, Any] | None = None,
    min_btl_result: dict[str, Any] | None = None,
    annual: dict[str, Any] | None = None,
    thresholds: VerdictThresholds | None = None,
) -> Verdict:
    """Run all validation checks and produce a verdict.

    Parameters
    ----------
    wf_aggregate : dict
        WalkForwardStep aggregate dict with keys:
        ``degradation``, ``oos_sharpe``, ``avg_is_sharpe``.
    bootstrap : dict
        Bootstrap result with ``ci_lower``, ``ci_upper``, ``p_value``.
    deflated : dict
        Output of ``deflated_sharpe_ratio()``.
    min_btl_result : dict
        ``{actual_years: float, min_btl_years: float}``.
    annual : dict
        Output of ``annual_breakdown()``.
    thresholds : VerdictThresholds
        Optional threshold overrides.

    Returns
    -------
    Verdict object with per-check results and overall rating.
    """
    t = thresholds or VerdictThresholds()
    checks: list[CheckResult] = []

    # 1. Walk-Forward degradation
    if wf_aggregate is not None and "degradation" in wf_aggregate:
        deg = float(wf_aggregate["degradation"])
        status = _classify(deg, t.max_degradation, t.max_degradation_fail)
        checks.append(CheckResult(
            name="Walk-Forward degradation",
            status=status,
            reason=(
                f"OOS Sharpe drops {deg * 100:.0f}% from IS. "
                + (
                    "Robust." if status == "pass"
                    else "Mild overfitting concern." if status == "warn"
                    else "Severe overfitting."
                )
            ),
            value=deg,
        ))

    # 2. Overfitting score (if separately computed)
    if wf_aggregate is not None and "overfitting_score" in wf_aggregate:
        score = float(wf_aggregate["overfitting_score"])
        status = _classify(score, t.max_overfitting_score, t.max_overfitting_fail)
        checks.append(CheckResult(
            name="Overfitting score",
            status=status,
            reason=f"Score = {score:.2f}",
            value=score,
        ))

    # 3. Bootstrap CI excludes zero
    if bootstrap is not None and "ci_lower" in bootstrap:
        ci_lower = float(bootstrap["ci_lower"])
        ci_upper = float(bootstrap["ci_upper"])
        ci_excludes_zero = ci_lower > 0 or ci_upper < 0
        status = "pass" if ci_excludes_zero else "fail"
        checks.append(CheckResult(
            name="Bootstrap 95% CI",
            status=status,
            reason=(
                f"CI = [{ci_lower:.3f}, {ci_upper:.3f}]. "
                + ("Excludes zero." if ci_excludes_zero else "Includes zero — not significant.")
            ),
            value={"lower": ci_lower, "upper": ci_upper},
        ))

    # 4. Monte Carlo / bootstrap p-value
    if bootstrap is not None and "p_value" in bootstrap:
        p = float(bootstrap["p_value"])
        status = _classify(p, t.max_p_value, t.max_p_value_fail)
        checks.append(CheckResult(
            name="Statistical significance (p-value)",
            status=status,
            reason=f"p = {p:.4f}. " + (
                "Significant." if status == "pass"
                else "Marginal." if status == "warn"
                else "Not significant."
            ),
            value=p,
        ))

    # 5. Deflated Sharpe Ratio
    if deflated is not None and "deflated_sharpe" in deflated:
        dsr = float(deflated["deflated_sharpe"])
        status = _classify(
            dsr, t.min_deflated_sharpe, t.min_deflated_sharpe_fail,
            higher_is_worse=False,
        )
        checks.append(CheckResult(
            name="Deflated Sharpe Ratio",
            status=status,
            reason=(
                f"DSR = {dsr:.2f}. "
                + (
                    "High confidence in true alpha."
                    if status == "pass"
                    else "Moderate confidence (possible luck)."
                    if status == "warn"
                    else "Low confidence — likely noise."
                )
            ),
            value=dsr,
        ))

    # 6. Minimum backtest length
    if min_btl_result is not None:
        actual = min_btl_result.get("actual_years", 0)
        required = min_btl_result.get("min_btl_years")
        if required is None:
            status = "fail"
            reason = "MinBTL undefined (Sharpe ≤ 0)."
        elif actual >= required:
            status = "pass"
            reason = f"Backtest {actual:.1f}y ≥ MinBTL {required:.1f}y."
        else:
            status = "warn" if actual >= required * 0.7 else "fail"
            reason = f"Backtest {actual:.1f}y < MinBTL {required:.1f}y."
        if t.require_min_btl_pass:
            checks.append(CheckResult(
                name="Minimum backtest length",
                status=status,
                reason=reason,
                value={"actual": actual, "required": required},
            ))

    # 7. Annual stability
    if annual is not None and annual.get("per_year"):
        pr = float(annual.get("profitable_ratio", 0))
        status = _classify(
            pr, t.min_profitable_ratio, t.min_profitable_ratio_fail,
            higher_is_worse=False,
        )
        n_years = len(annual["per_year"])
        n_profitable = int(pr * n_years)
        checks.append(CheckResult(
            name="Annual stability",
            status=status,
            reason=f"{n_profitable}/{n_years} years profitable ({pr * 100:.0f}%).",
            value=pr,
        ))

    # Aggregate
    passed = sum(1 for c in checks if c.status == "pass")
    warned = sum(1 for c in checks if c.status == "warn")
    failed = sum(1 for c in checks if c.status == "fail")
    total = len(checks)

    if failed > 0:
        result = "fail"
        emoji = "🔴"
    elif warned > 0:
        result = "warn"
        emoji = "🟡"
    else:
        result = "pass"
        emoji = "🟢"

    if result == "pass":
        summary = (
            f"{emoji} 策略通过 {passed}/{total} 项检验, 无警告. "
            f"建议推进到模拟盘阶段."
        )
    elif result == "warn":
        warn_names = [c.name for c in checks if c.status == "warn"]
        summary = (
            f"{emoji} 策略通过主要检验 ({passed}/{total}), 但有 {warned} 项警告: "
            f"{', '.join(warn_names)}. 建议复核后谨慎推进."
        )
    else:
        fail_names = [c.name for c in checks if c.status == "fail"]
        summary = (
            f"{emoji} 策略未通过 {failed} 项关键检验: {', '.join(fail_names)}. "
            f"不建议部署, 需重新设计."
        )

    return Verdict(
        result=result,
        passed=passed,
        warned=warned,
        failed=failed,
        total=total,
        checks=checks,
        summary=summary,
    )
