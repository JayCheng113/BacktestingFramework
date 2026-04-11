"""NonNegativeWeightsGuard: individual weights must be >= 0 (A-share long-only)."""
from __future__ import annotations
import time

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at

CHECK_INDICES = (50, 100, 150, 175, 199)
NEG_TOLERANCE = -1e-9


class NonNegativeWeightsGuard(Guard):
    name = "NonNegativeWeightsGuard"
    tier = "warn"
    applies_to = ("portfolio_strategy",)

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"NonNegativeWeightsGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )
        try:
            inst = context.user_class()
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=f"NonNegativeWeightsGuard: instantiation failed: {type(e).__name__}: {e}",
            )
        panel = build_mock_panel()
        violations: list[dict] = []
        for idx in CHECK_INDICES:
            target = target_date_at(idx)
            try:
                w = inst.generate_weights(panel, target, {}, {})
            except Exception:
                continue
            if not w:
                continue
            for sym, val in w.items():
                fv = float(val)
                if fv < NEG_TOLERANCE:
                    violations.append({
                        "date": str(target.date()),
                        "symbol": str(sym),
                        "weight": fv,
                    })
                    break
        runtime = (time.perf_counter() - t0) * 1000
        if violations:
            first = violations[0]
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"NonNegativeWeightsGuard warning: individual weight < 0 at "
                    f"{len(violations)} date(s). First: date={first['date']}, "
                    f"symbol={first['symbol']}, weight={first['weight']:.6f}. "
                    f"A-share long-only requires all individual weights >= 0. "
                    f"If this is intentional (e.g., raw alphas), ignore this warning."
                ),
                details={"violations": violations},
                runtime_ms=runtime,
            )
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            runtime_ms=runtime,
        )
