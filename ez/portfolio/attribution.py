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
    # Per-period per-industry effects (for Carino-linked industry accumulation)
    period_industry_effects: list[dict[str, dict[str, float]]] = []

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
        period_ind: dict[str, dict[str, float]] = {}

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
            period_ind.setdefault(ind, {"allocation": 0.0, "selection": 0.0, "interaction": 0.0})
            period_ind[ind]["allocation"] += a
            period_ind[ind]["selection"] += s_eff
            period_ind[ind]["interaction"] += ix

        total = alloc + select + interact
        period_industry_effects.append(period_ind)
        periods.append(BrinsonAttribution(
            period_start=t_start.isoformat(), period_end=t_end.isoformat(),
            allocation_effect=alloc, selection_effect=select,
            interaction_effect=interact, total_excess=total,
        ))

    # Carino (1999) geometric linking for multi-period attribution
    # k_t = ln(1+R_t) / R_t for each period, K = ln(1+R_total) / R_total
    # adj_effect = effect_t * k_t / K, then sum across periods
    cumulative: BrinsonAttribution | None = None
    if periods:
        # Compute per-period portfolio returns for linking factors
        period_returns = [p.total_excess for p in periods]
        total_return = 1.0
        for r in period_returns:
            total_return *= (1 + r)
        total_return -= 1.0

        def _carino_k(r: float) -> float:
            if abs(r) < 1e-10:
                return 1.0
            if r <= -1.0:
                return 1.0  # degenerate: total wipeout, fall back to arithmetic
            return float(np.log(1 + r) / r)

        K = _carino_k(total_return)
        if abs(K) < 1e-15:
            K = 1.0  # degenerate: total return ≈ 0

        cum_alloc, cum_select, cum_interact = 0.0, 0.0, 0.0
        for p, r_t in zip(periods, period_returns):
            k_t = _carino_k(r_t)
            factor = k_t / K
            cum_alloc += p.allocation_effect * factor
            cum_select += p.selection_effect * factor
            cum_interact += p.interaction_effect * factor

        cumulative = BrinsonAttribution(
            period_start=periods[0].period_start,
            period_end=periods[-1].period_end,
            allocation_effect=cum_alloc,
            selection_effect=cum_select,
            interaction_effect=cum_interact,
            total_excess=cum_alloc + cum_select + cum_interact,
        )

        # Rebuild industry_accum with Carino linking
        industry_accum: dict[str, dict[str, float]] = {}
        for pie, r_t in zip(period_industry_effects, period_returns):
            k_t = _carino_k(r_t)
            factor = k_t / K
            for ind, effects in pie.items():
                if ind not in industry_accum:
                    industry_accum[ind] = {"allocation": 0.0, "selection": 0.0, "interaction": 0.0}
                industry_accum[ind]["allocation"] += effects["allocation"] * factor
                industry_accum[ind]["selection"] += effects["selection"] * factor
                industry_accum[ind]["interaction"] += effects["interaction"] * factor
    else:
        industry_accum = {}

    # Exclude liquidation trades from cost_drag (they occur after last rebalance period)
    cost_drag = (
        sum(float(t.get("cost", 0)) for t in result.trades if not t.get("liquidation"))
        / initial_cash if initial_cash > 0 else 0.0
    )

    return AttributionResult(
        periods=periods, cumulative=cumulative,
        cost_drag=cost_drag, by_industry=industry_accum,
    )
