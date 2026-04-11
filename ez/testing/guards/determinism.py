"""DeterminismGuard: two runs on identical input must produce identical output."""
from __future__ import annotations
import math
import time

import pandas as pd

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at, MOCK_N_DAYS
from ._invoke import invoke_user_code


def _canonicalize(output) -> str:
    """Produce a deterministic string for comparison."""
    if output is None:
        return "<None>"
    if isinstance(output, float):
        if math.isnan(output):
            return "<NaN>"
        return f"{output:.15e}"
    if isinstance(output, int):
        return f"{float(output):.15e}"
    if isinstance(output, tuple):
        items = []
        for v in output:
            if v is None:
                items.append("<None>")
                continue
            try:
                fv = float(v)
                items.append("<NaN>" if math.isnan(fv) else f"{fv:.15e}")
            except (TypeError, ValueError):
                items.append(str(v))
        return "(" + ",".join(items) + ")"
    if isinstance(output, pd.Series):
        return output.to_json()
    if isinstance(output, dict):
        items = []
        for k in sorted(output.keys()):
            v = output[k]
            if v is None:
                items.append((str(k), "<None>"))
            else:
                try:
                    fv = float(v)
                    if math.isnan(fv):
                        items.append((str(k), "<NaN>"))
                    else:
                        items.append((str(k), f"{fv:.15e}"))
                except (TypeError, ValueError):
                    items.append((str(k), str(v)))
        return str(items)
    return str(output)


class DeterminismGuard(Guard):
    name = "DeterminismGuard"
    tier = "warn"
    applies_to = ("factor", "cross_factor", "strategy", "portfolio_strategy", "ml_alpha")

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"DeterminismGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )
        target = target_date_at(MOCK_N_DAYS - 1)
        try:
            # Codex round-2 finding P2 #2: each invoke gets a FRESH panel
            # so that user code mutating the panel in place cannot make the
            # second run see polluted data — which would falsely surface
            # as "non-determinism" when the user code itself is fine.
            out_a = invoke_user_code(context.user_class, context.kind, build_mock_panel(), target)
            out_b = invoke_user_code(context.user_class, context.kind, build_mock_panel(), target)
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=f"DeterminismGuard: user code raised: {type(e).__name__}: {e}",
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )
        ca = _canonicalize(out_a)
        cb = _canonicalize(out_b)
        runtime = (time.perf_counter() - t0) * 1000
        if ca != cb:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"DeterminismGuard warning: two runs on identical input produced "
                    f"different output. Common causes: unseeded RNG "
                    f"(use np.random.default_rng(seed)), uncontrolled set() iteration, "
                    f"BLAS threading non-determinism (set OMP_NUM_THREADS=1 for ML)."
                ),
                details={"canonical_a": ca[:200], "canonical_b": cb[:200]},
                runtime_ms=runtime,
            )
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            runtime_ms=runtime,
        )
