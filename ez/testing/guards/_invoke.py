"""Shared helpers for running user code across guards.

Contract per kind:
  - factor: `compute(df) -> df` (appends new columns). We extract the
    newly-added columns at position `target_date` as a dict {col: value}.
  - strategy: `generate_signals(df) -> pd.Series`. Engine runs
    `required_factors()` first to enrich df with factor columns. We
    mirror that here, then take the signal at position `target_date`.
  - cross_factor: `compute(universe_data, date) -> pd.Series`. Returned
    Series index = symbols. Convert to dict for comparison.
  - portfolio_strategy: `generate_weights(universe_data, date, prev_w,
    prev_r) -> dict[str, float] | None`. Return None mapped to {}.
  - ml_alpha: same as cross_factor (MLAlpha IS-A CrossSectionalFactor).
"""
from __future__ import annotations
import math
from datetime import datetime
from typing import Any

import pandas as pd


def _factor_output_at(
    original_df: pd.DataFrame,
    result: pd.DataFrame,
    target_date: datetime,
) -> dict[str, float]:
    """Extract newly-added column values at the row ≤ target_date.

    Factor.compute returns a DataFrame with new columns appended. The
    guard compares only those new columns (ignoring OHLCV passthroughs)
    so that shuffle-future tests stay deterministic.
    """
    if result is None or len(result) == 0:
        return {}
    input_cols = set(original_df.columns)
    new_cols = [c for c in result.columns if c not in input_cols]
    if not new_cols:
        return {}
    mask = result.index <= target_date
    target_slice = result.loc[mask, new_cols]
    if len(target_slice) == 0:
        return {}
    last = target_slice.iloc[-1]
    out: dict[str, float] = {}
    for col, val in last.items():
        try:
            fv = float(val)
        except (TypeError, ValueError):
            out[str(col)] = math.nan
            continue
        out[str(col)] = fv
    return out


def invoke_user_code(
    cls: type,
    kind: str,
    panel: dict[str, pd.DataFrame],
    target_date: datetime,
) -> Any:
    """Run the user class with the canonical signature for its kind.

    Returns one of:
      - dict[str, float] (factor / cross_factor / portfolio_strategy / ml_alpha)
      - float (strategy — signal at target_date position)
      - None (empty output)

    Raises whatever the user code raises — callers should wrap.
    """
    inst = cls()
    if kind == "factor":
        sym = next(iter(panel))
        df = panel[sym]
        result = inst.compute(df)
        return _factor_output_at(df, result, target_date)
    if kind == "strategy":
        sym = next(iter(panel))
        df = panel[sym].copy()
        # Mirror engine: compute required_factors() first so the strategy
        # sees the enriched DataFrame.
        required = inst.required_factors() or []
        for factor in required:
            df = factor.compute(df)
        signals = inst.generate_signals(df)
        if signals is None or len(signals) == 0:
            return None
        mask = signals.index <= target_date
        truncated = signals.loc[mask]
        if len(truncated) == 0:
            return None
        return float(truncated.iloc[-1])
    if kind == "cross_factor":
        result = inst.compute(panel, target_date)
        if result is None:
            return {}
        if isinstance(result, pd.Series):
            return {str(k): float(v) for k, v in result.items() if pd.notna(v)}
        # User might return a dict-like. Normalize.
        return {str(k): float(v) for k, v in dict(result).items() if v is not None}
    if kind == "portfolio_strategy":
        result = inst.generate_weights(panel, target_date, {}, {})
        if result is None:
            return {}
        return {str(k): float(v) for k, v in result.items()}
    if kind == "ml_alpha":
        result = inst.compute(panel, target_date)
        if result is None:
            return {}
        if isinstance(result, pd.Series):
            return {str(k): float(v) for k, v in result.items() if pd.notna(v)}
        return {str(k): float(v) for k, v in dict(result).items() if v is not None}
    raise ValueError(f"Unknown kind: {kind}")
