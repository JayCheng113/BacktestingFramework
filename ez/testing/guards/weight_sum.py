"""WeightSumGuard: portfolio weights must be in [-0.001, 1.001] across dates."""
from __future__ import annotations
import time

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at

CHECK_INDICES = (50, 100, 150, 175, 199)
UPPER = 1.001
LOWER = -0.001


class WeightSumGuard(Guard):
    name = "WeightSumGuard"
    tier = "block"
    applies_to = ("portfolio_strategy",)

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"WeightSumGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )
        try:
            inst = context.user_class()
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=f"WeightSumGuard: instantiation failed: {type(e).__name__}: {e}",
            )
        violations: list[dict] = []
        for idx in CHECK_INDICES:
            target = target_date_at(idx)
            # Codex round-2 finding P2 #2: fresh panel per date so user
            # code mutating the panel in place doesn't bleed across calls.
            panel = build_mock_panel()
            try:
                w = inst.generate_weights(panel, target, {}, {})
            except Exception as e:
                return GuardResult(
                    guard_name=self.name,
                    severity=GuardSeverity.BLOCK,
                    tier=self.tier,
                    message=(
                        f"WeightSumGuard: user code raised at date {target.date()}: "
                        f"{type(e).__name__}: {e}"
                    ),
                    runtime_ms=(time.perf_counter() - t0) * 1000,
                )
            if w is None:
                continue
            s = sum(float(v) for v in w.values())
            if s > UPPER or s < LOWER:
                violations.append({
                    "date": str(target.date()),
                    "sum": s,
                    "weights_preview": {str(k): round(float(v), 6) for k, v in list(w.items())[:5]},
                })
        runtime = (time.perf_counter() - t0) * 1000
        if violations:
            first = violations[0]
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"WeightSumGuard failed: weight sum out of [{LOWER}, {UPPER}] "
                    f"at {len(violations)} date(s). First: date={first['date']}, "
                    f"sum={first['sum']:.6f}. A-share long-only strategies must "
                    f"have 0 <= sum(w) <= 1 (over-leverage or net short blocks save)."
                ),
                details={"violations": violations},
                runtime_ms=runtime,
            )
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            details={"n_dates_checked": len(CHECK_INDICES)},
            runtime_ms=runtime,
        )
