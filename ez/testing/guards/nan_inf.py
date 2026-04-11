"""NaNInfGuard: detect NaN/Inf in output past the warmup region."""
from __future__ import annotations
import math
import time

import numpy as np
import pandas as pd

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at, MOCK_N_DAYS


def _scan_dataframe_new_cols(
    input_cols: frozenset,
    result_df: pd.DataFrame,
    warmup: int,
) -> list[tuple[str, int]]:
    """Return [(column, position)] of NaN/Inf beyond warmup in newly-added cols.

    **input_cols MUST be captured BEFORE user compute() was called**
    (V2.19.0 post-review C1). If you pass the post-mutation frame's
    columns, in-place mutation by user code will make ``new_cols`` empty
    and the guard will silently pass.
    """
    if result_df is None or len(result_df) == 0:
        return []
    new_cols = [c for c in result_df.columns if c not in input_cols]
    if not new_cols:
        return []
    bad: list[tuple[str, int]] = []
    for col in new_cols:
        values = np.asarray(result_df[col].values, dtype=float)
        # Vectorized scan: find non-finite positions beyond warmup.
        if warmup >= len(values):
            continue
        scan_slice = values[warmup:]
        bad_offsets = np.flatnonzero(~np.isfinite(scan_slice))
        for off in bad_offsets:
            bad.append((str(col), int(off + warmup)))
    return bad


def _scan_series(series: pd.Series, warmup: int) -> list[int]:
    if series is None or len(series) == 0:
        return []
    values = np.asarray(series.values, dtype=float)
    bad = []
    for i in range(len(values)):
        if i < warmup:
            continue
        v = values[i]
        if math.isnan(v) or math.isinf(v):
            bad.append(i)
    return bad


def _scan_dict(d: dict) -> list[str]:
    if not d:
        return []
    bad = []
    for k, v in d.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            bad.append(str(k))
            continue
        if math.isnan(fv) or math.isinf(fv):
            bad.append(str(k))
    return bad


def _scan_pandas_series_values(series: pd.Series) -> list[str]:
    """For cross-sectional outputs (Series indexed by symbol)."""
    if series is None or len(series) == 0:
        return []
    bad = []
    for k, v in series.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            bad.append(str(k))
            continue
        if math.isnan(fv) or math.isinf(fv):
            bad.append(str(k))
    return bad


class NaNInfGuard(Guard):
    name = "NaNInfGuard"
    tier = "block"
    applies_to = ("factor", "cross_factor", "strategy", "portfolio_strategy", "ml_alpha")

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"NaNInfGuard: could not load user class. "
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
                message=f"NaNInfGuard: instantiation failed: {type(e).__name__}: {e}",
            )
        warmup = int(getattr(inst, "warmup_period", 0) or 0)
        panel = build_mock_panel()
        target = target_date_at(MOCK_N_DAYS - 1)
        bad_desc: list[str] = []
        try:
            if context.kind == "factor":
                sym = next(iter(panel))
                original_df = panel[sym]
                # Defensive copy + pre-capture input_cols so that user
                # in-place mutation cannot defeat the new-column diff.
                # See V2.19.0 post-review C1.
                input_cols = frozenset(original_df.columns)
                df_for_user = original_df.copy()
                out = inst.compute(df_for_user)
                bad = _scan_dataframe_new_cols(input_cols, out, warmup)
                bad_desc = [f"{col}@{i}" for col, i in bad]
            elif context.kind == "cross_factor":
                out = inst.compute(panel, target)
                if isinstance(out, pd.Series):
                    bad_desc = _scan_pandas_series_values(out)
                else:
                    bad_desc = _scan_dict(dict(out) if out is not None else {})
            elif context.kind == "strategy":
                sym = next(iter(panel))
                df = panel[sym].copy()
                required = inst.required_factors() or []
                for factor in required:
                    df = factor.compute(df)
                out = inst.generate_signals(df)
                if isinstance(out, pd.Series):
                    bad_desc = [str(i) for i in _scan_series(out, warmup)]
                else:
                    bad_desc = []
            elif context.kind == "portfolio_strategy":
                out = inst.generate_weights(panel, target, {}, {})
                bad_desc = _scan_dict(dict(out) if out is not None else {})
            elif context.kind == "ml_alpha":
                out = inst.compute(panel, target)
                if isinstance(out, pd.Series):
                    bad_desc = _scan_pandas_series_values(out)
                else:
                    bad_desc = _scan_dict(dict(out) if out is not None else {})
            else:
                return GuardResult(
                    guard_name=self.name,
                    severity=GuardSeverity.PASS,
                    tier=self.tier,
                    message=f"NaNInfGuard: kind '{context.kind}' not covered",
                )
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=f"NaNInfGuard: user code raised: {type(e).__name__}: {e}",
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )
        runtime = (time.perf_counter() - t0) * 1000
        if bad_desc:
            sample = bad_desc[:10]
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"NaNInfGuard failed: output contains NaN/Inf at "
                    f"{len(bad_desc)} position(s) beyond warmup={warmup}. "
                    f"First: {sample}. Common causes: division by zero, "
                    f"log of negative, unpropagated intermediate NaN, or "
                    f"warmup_period under-declared vs. actual rolling window."
                ),
                details={"bad_positions": bad_desc, "warmup": warmup},
                runtime_ms=runtime,
            )
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            details={"warmup": warmup},
            runtime_ms=runtime,
        )
