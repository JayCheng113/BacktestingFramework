"""V2.12 F6: Brinson performance attribution.

Decomposes portfolio excess return into:
  - Allocation effect: industry weight deviation * benchmark industry return
  - Selection effect: benchmark industry weight * (portfolio - benchmark) industry return
  - Interaction effect: weight deviation * return deviation
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ez.portfolio.engine import PortfolioResult


@dataclass
class BrinsonAttribution:
    period_start: str
    period_end: str
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_excess: float


@dataclass
class AttributionResult:
    periods: list[BrinsonAttribution] = field(default_factory=list)
    cumulative: BrinsonAttribution | None = None
    cost_drag: float = 0.0
    by_industry: dict[str, dict] = field(default_factory=dict)


def _period_return(df: pd.DataFrame | None, start: date, end: date) -> float:
    """Compute return for a single symbol over [start, end]."""
    if df is None or df.empty:
        return 0.0
    col = "adj_close" if "adj_close" in df.columns else "close"
    if hasattr(df.index, 'date'):
        mask_start = df.index.date >= start
        mask_end = df.index.date <= end
    else:
        mask_start = df.index >= pd.Timestamp(start)
        mask_end = df.index <= pd.Timestamp(end)
    subset = df[col][mask_start & mask_end]
    if len(subset) < 2:
        return 0.0
    p0, p1 = float(subset.iloc[0]), float(subset.iloc[-1])
    return (p1 - p0) / p0 if p0 > 0 else 0.0


def _has_data(df: pd.DataFrame | None, start: date, end: date) -> bool:
    if df is None or df.empty:
        return False
    if hasattr(df.index, 'date'):
        dates = df.index.date
    else:
        dates = df.index
    return any(start <= d <= end for d in dates)


def _weighted_return(symbols: list[str], weights: dict[str, float],
                     returns: dict[str, float]) -> float:
    """Weighted average return for a group of symbols."""
    total_w = sum(weights.get(s, 0) for s in symbols)
    if total_w <= 0:
        return 0.0
    return sum(weights.get(s, 0) * returns.get(s, 0) for s in symbols) / total_w


def compute_attribution(
    result: PortfolioResult,
    universe_data: dict[str, pd.DataFrame],
    industry_map: dict[str, str],
    initial_cash: float = 1_000_000.0,
    benchmark_type: str = "equal",
    custom_benchmark: dict[str, float] | None = None,
) -> AttributionResult:
    """Compute Brinson attribution from backtest result + universe data.

    Per-stock returns computed from universe_data (not stored in DB).
    Equal-weight benchmark dynamically computed per period.
    """
    rebalance_dates = result.rebalance_dates
    # Use rebalance_weights (1:1 aligned with rebalance_dates) if available,
    # otherwise fall back to weights_history (for manually constructed results in tests)
    weights_history = (
        result.rebalance_weights if hasattr(result, 'rebalance_weights') and result.rebalance_weights
        else result.weights_history
    )

    if len(rebalance_dates) < 2 or len(weights_history) < 1:
        return AttributionResult()

    periods: list[BrinsonAttribution] = []
    industry_accum: dict[str, dict[str, float]] = {}

    for i in range(min(len(rebalance_dates) - 1, len(weights_history))):
        t_start = rebalance_dates[i]
        t_end = rebalance_dates[i + 1]
        w_p = weights_history[i]

        # Dynamic benchmark per period
        if benchmark_type == "equal":
            active = [s for s in universe_data
                      if _has_data(universe_data.get(s), t_start, t_end)]
            n = len(active)
            w_b = {s: 1.0 / n for s in active} if n > 0 else {}
        else:
            w_b = custom_benchmark or {}

        # Per-stock returns
        all_syms = set(w_p) | set(w_b)
        stock_returns = {
            s: _period_return(universe_data.get(s), t_start, t_end)
            for s in all_syms
        }

        # Brinson decomposition by industry
        industries = set(industry_map.get(s, "_other") for s in all_syms)
        alloc, select, interact = 0.0, 0.0, 0.0

        for ind in industries:
            syms = [s for s in all_syms if industry_map.get(s, "_other") == ind]
            w_p_j = sum(w_p.get(s, 0) for s in syms)
            w_b_j = sum(w_b.get(s, 0) for s in syms)
            r_p_j = _weighted_return(syms, w_p, stock_returns) if w_p_j > 0 else 0
            r_b_j = _weighted_return(syms, w_b, stock_returns) if w_b_j > 0 else 0

            a = (w_p_j - w_b_j) * r_b_j
            s_eff = w_b_j * (r_p_j - r_b_j)
            ix = (w_p_j - w_b_j) * (r_p_j - r_b_j)
            alloc += a
            select += s_eff
            interact += ix

            if ind not in industry_accum:
                industry_accum[ind] = {"allocation": 0.0, "selection": 0.0, "interaction": 0.0}
            industry_accum[ind]["allocation"] += a
            industry_accum[ind]["selection"] += s_eff
            industry_accum[ind]["interaction"] += ix

        total = alloc + select + interact
        periods.append(BrinsonAttribution(
            period_start=t_start.isoformat(), period_end=t_end.isoformat(),
            allocation_effect=alloc, selection_effect=select,
            interaction_effect=interact, total_excess=total,
        ))

    cumulative = BrinsonAttribution(
        period_start=periods[0].period_start if periods else "",
        period_end=periods[-1].period_end if periods else "",
        allocation_effect=sum(p.allocation_effect for p in periods),
        selection_effect=sum(p.selection_effect for p in periods),
        interaction_effect=sum(p.interaction_effect for p in periods),
        total_excess=sum(p.total_excess for p in periods),
    ) if periods else None

    cost_drag = (
        sum(float(t.get("cost", 0)) for t in result.trades) / initial_cash
        if initial_cash > 0 else 0.0
    )

    return AttributionResult(
        periods=periods, cumulative=cumulative,
        cost_drag=cost_drag, by_industry=industry_accum,
    )
