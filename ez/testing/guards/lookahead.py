"""LookaheadGuard: detect future data access via 3-run test.

Design:
  Run the user code three times on the same ``target_date``:
    1. clean panel A  → output clean_a
    2. clean panel A  → output clean_b
    3. shuffled panel (rows after cutoff permuted) → output shuffled

  Interpretation:
    - clean_a ≠ clean_b (beyond tolerance) → user code is non-deterministic.
      We cannot detect lookahead on non-deterministic code, so we **warn
      without blocking** (lookahead_inconclusive). DeterminismGuard handles
      the non-determinism finding separately.
    - clean_a == clean_b but clean_a ≠ shuffled → user code reads rows
      after ``target_date``; shuffling them changed the output → **block**.
    - All three equal → pass.

Scope (``applies_to``):
  Only ``factor`` and ``strategy``. For the engine-sliced kinds
  (``cross_factor``/``portfolio_strategy``/``ml_alpha``), the engine pre-slices
  ``universe_data`` to ``[date-lookback, date-1]`` before calling user code.
  A clean user function may legitimately call ``df.iloc[-1]`` assuming that
  pre-slice — the guard, which passes an un-sliced panel, would see that
  call return a shuffled row and mis-diagnose the clean code as lookahead.
  V1 therefore leaves those kinds uncovered; a future ``FeatureShiftGuard``
  can target MLAlpha ``feature_fn``/``target_fn`` directly.
"""
from __future__ import annotations
import math
import time

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, build_shuffled_panel, target_date_at
from ._invoke import invoke_user_code

TOLERANCE = 1e-9
CUTOFF_IDX = 150
# Multi-probe indices for strategy kind (V2.19.0 post-review I1).
# Boolean-signal lookahead bugs only affect 1 row per (cutoff, target)
# probe — 50% coincidental match per probe. 7 probes → 0.5^7 ≈ 0.8% FP.
# Each probe uses cutoff=target=idx so the shuffle covers rows strictly
# after the target; clean strategies are unaffected.
STRATEGY_PROBE_INDICES = (130, 140, 150, 160, 170, 180, 190)


def _compare_scalar(a: float | None, b: float | None) -> float:
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return math.inf
    if math.isnan(a) and math.isnan(b):
        return 0.0
    if math.isnan(a) or math.isnan(b):
        return math.inf
    return abs(a - b)


def _compare_dict(a: dict, b: dict) -> tuple[float, str]:
    if not a and not b:
        return 0.0, ""
    all_keys = set(a) | set(b)
    max_diff = 0.0
    max_key = ""
    for k in all_keys:
        va = a.get(k)
        vb = b.get(k)
        fa = float(va) if va is not None else None
        fb = float(vb) if vb is not None else None
        d = _compare_scalar(fa, fb)
        if d > max_diff:
            max_diff = d
            max_key = k
    return max_diff, max_key


def _compare_tuple(a: tuple, b: tuple) -> tuple[float, str]:
    """Element-wise scalar comparison over the longer of the two tuples."""
    n = max(len(a), len(b))
    if n == 0:
        return 0.0, "<empty>"
    max_diff = 0.0
    max_key = ""
    for i in range(n):
        va = a[i] if i < len(a) else None
        vb = b[i] if i < len(b) else None
        fa = float(va) if va is not None else None
        fb = float(vb) if vb is not None else None
        d = _compare_scalar(fa, fb)
        if d > max_diff:
            max_diff = d
            max_key = f"[{i}]"
    return max_diff, max_key


def _compare_outputs(a, b) -> tuple[float, str]:
    """Canonical comparison across tuple / dict / scalar / None."""
    if isinstance(a, tuple) or isinstance(b, tuple):
        ta = a if isinstance(a, tuple) else ()
        tb = b if isinstance(b, tuple) else ()
        return _compare_tuple(ta, tb)
    if isinstance(a, dict) or isinstance(b, dict):
        da = a if isinstance(a, dict) else {}
        db = b if isinstance(b, dict) else {}
        return _compare_dict(da, db)
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        fa = float(a) if a is not None else None
        fb = float(b) if b is not None else None
        return _compare_scalar(fa, fb), "<scalar>"
    if a is None and b is None:
        return 0.0, "<none>"
    return (0.0 if a == b else math.inf), "<value>"


class LookaheadGuard(Guard):
    name = "LookaheadGuard"
    tier = "block"
    applies_to = ("factor", "strategy")

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"LookaheadGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )

        # Step 1: non-determinism check (single probe at CUTOFF_IDX).
        # We do this once up-front so that if user code is non-deterministic,
        # we return WARN immediately and skip the (noisy) multi-probe step.
        target = target_date_at(CUTOFF_IDX)
        panel_clean = build_mock_panel()
        try:
            clean_a = invoke_user_code(context.user_class, context.kind, panel_clean, target)
            clean_b = invoke_user_code(
                context.user_class, context.kind, build_mock_panel(), target
            )
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=f"LookaheadGuard: user code raised: {type(e).__name__}: {e}",
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )
        nondet_diff, _ = _compare_outputs(clean_a, clean_b)
        if nondet_diff > TOLERANCE:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"LookaheadGuard: code is non-deterministic "
                    f"(two runs on identical input differ by {nondet_diff:.3e}); "
                    f"cannot run shuffle-future test. Seed your RNG and re-save."
                ),
                details={
                    "target_date": str(target.date()),
                    "nondet_diff": nondet_diff,
                    "tolerance": TOLERANCE,
                },
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )

        # Step 2: shuffle-future test. Factor = single probe (continuous
        # output, 1 position is enough). Strategy = multi-probe (boolean
        # signal collapse, 1-bit channel → need ≥7 independent probes to
        # push false-pass rate below 1%).
        probes = (
            STRATEGY_PROBE_INDICES if context.kind == "strategy" else (CUTOFF_IDX,)
        )
        try:
            for probe_idx in probes:
                probe_target = target_date_at(probe_idx)
                panel_shuffled = build_shuffled_panel(probe_idx)
                clean_out = invoke_user_code(
                    context.user_class, context.kind, build_mock_panel(), probe_target
                )
                shuffled_out = invoke_user_code(
                    context.user_class, context.kind, panel_shuffled, probe_target
                )
                diff, key = _compare_outputs(clean_out, shuffled_out)
                if diff > TOLERANCE:
                    runtime = (time.perf_counter() - t0) * 1000
                    return GuardResult(
                        guard_name=self.name,
                        severity=GuardSeverity.BLOCK,
                        tier=self.tier,
                        message=(
                            f"LookaheadGuard failed: output at t={probe_target.date()} "
                            f"differs when future data (rows after t) is shuffled. "
                            f"Max delta at '{key}' = {diff:.3e}. "
                            f"Strong signal that the code reads future data."
                        ),
                        details={
                            "target_date": str(probe_target.date()),
                            "probe_idx": probe_idx,
                            "max_abs_diff": diff,
                            "max_diff_key": key,
                            "tolerance": TOLERANCE,
                            "output_clean_sample": str(clean_out)[:300],
                            "output_shuffled_sample": str(shuffled_out)[:300],
                        },
                        runtime_ms=runtime,
                    )
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=f"LookaheadGuard: user code raised during probe: {type(e).__name__}: {e}",
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )

        runtime = (time.perf_counter() - t0) * 1000
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            details={"target_date": str(target.date()), "n_probes": len(probes)},
            runtime_ms=runtime,
        )
