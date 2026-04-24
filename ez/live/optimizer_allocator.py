"""Deterministic constrained allocator for live target-weight rebalancing.

This module intentionally serves the current live path only:

    strategy target weights -> allocator -> OMS -> execution

It does not try to solve manual orders, broker-native order types,
split-order algorithms, or partial-fill semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(slots=True)
class ConstrainedAllocatorResult:
    adjusted_weights: dict[str, float]
    details: dict[str, Any]


def optimize_target_weights(
    *,
    requested_weights: dict[str, float],
    current_weights: dict[str, float],
    budget: float,
    max_position_weight: float | None,
    max_daily_turnover: float | None,
    covariance_symbols: tuple[str, ...],
    covariance_matrix: Any | None,
    covariance_risk_aversion: float,
    risk_budget_strength: float,
    volatility_by_symbol: dict[str, float],
) -> ConstrainedAllocatorResult:
    """Project requested weights into a constrained long-only target.

    Objective:
    - stay as close as possible to the requested target weights
    - satisfy budget and per-symbol caps deterministically
    - compress turnover toward the feasible target when a turnover cap exists

    Notes:
    - `sum(weights) <= budget`
    - `0 <= weight_i <= max_position_weight` when configured
    - turnover compression is best-effort; if budget/cap constraints already
      require more turnover than the cap allows, the allocator prefers the
      hard feasibility constraints and reports that the turnover cap was
      infeasible.
    """
    requested = {
        symbol: max(0.0, float(weight))
        for symbol, weight in requested_weights.items()
        if float(weight) > 0
    }
    current = {
        symbol: max(0.0, float(weight))
        for symbol, weight in current_weights.items()
        if float(weight) > 0
    }
    all_symbols = sorted(set(requested) | set(current))
    if not all_symbols or budget <= 0:
        return ConstrainedAllocatorResult(
            adjusted_weights={},
            details={
                "requested_turnover": _turnover(current, {}),
                "effective_turnover": _turnover(current, {}),
                "turnover_scale": 0.0,
                "turnover_cap_infeasible": False,
                "covariance_used": False,
                "covariance_degenerate": False,
                "feasibility_clamped": False,
                "feasibility_original_budget": float(budget),
                "hard_constraints": {
                    "budget": budget,
                    "max_position_weight": max_position_weight,
                },
            },
        )

    caps = {
        symbol: (max_position_weight if max_position_weight is not None else budget)
        for symbol in all_symbols
    }
    budget = max(0.0, float(budget))

    # #2b: If the feasible region is empty (sum of caps < budget), clamp the
    # budget down to the cap total so the allocator can still produce a
    # feasible projection. Record the clamp so monitor/API can surface it.
    sum_caps = float(sum(max(0.0, caps.get(symbol, 0.0)) for symbol in all_symbols))
    feasibility_clamped = False
    feasibility_original_budget = budget
    if sum_caps + 1e-9 < budget:
        feasibility_clamped = True
        budget = sum_caps

    feasible_target = _project_capped_simplex(
        values={symbol: requested.get(symbol, 0.0) for symbol in all_symbols},
        caps=caps,
        budget=budget,
    )
    covariance_used = False
    covariance_degenerate = False
    risk_budget_target: dict[str, float] = {}
    optimized_target = dict(feasible_target)
    sigma = _align_covariance(
        all_symbols=all_symbols,
        covariance_symbols=covariance_symbols,
        covariance_matrix=covariance_matrix,
        volatility_by_symbol=volatility_by_symbol,
    )
    # #2a: Validate positive-definiteness. If the aligned covariance is
    # degenerate (rank-deficient / NaN / all-zero) even after the eigenvalue
    # lift, fall back to pro-rata-on-caps and record the event.
    if sigma is not None:
        sigma, covariance_degenerate = _ensure_positive_definite(sigma)
    if covariance_degenerate:
        sigma = None

    if sigma is not None and (covariance_risk_aversion > 0 or risk_budget_strength > 0):
        covariance_used = True
        if risk_budget_strength > 0:
            risk_budget_target = _risk_budget_target(
                requested=requested,
                all_symbols=all_symbols,
                budget=budget,
                caps=caps,
                volatility_by_symbol=volatility_by_symbol,
                sigma=sigma,
            )
            optimized_target = _blend_targets(
                base=optimized_target,
                alternative=risk_budget_target,
                strength=risk_budget_strength,
                caps=caps,
                budget=budget,
            )
        optimized_target = _covariance_projected_gradient(
            initial=optimized_target,
            target=optimized_target,
            sigma=sigma,
            caps=caps,
            budget=budget,
            risk_aversion=covariance_risk_aversion,
        )
    elif covariance_degenerate and (covariance_risk_aversion > 0 or risk_budget_strength > 0):
        # Fail-closed fallback: honor the budget/cap feasible target without
        # risk shaping; projection is already pro-rata-like under the capped
        # simplex, so the caller still gets a deterministic long-only weight.
        optimized_target = dict(feasible_target)
    requested_turnover = _turnover(current, feasible_target)
    optimized_turnover = _turnover(current, optimized_target)

    if max_daily_turnover is None or optimized_turnover <= max_daily_turnover + 1e-12:
        return ConstrainedAllocatorResult(
            adjusted_weights=optimized_target,
            details={
                "requested_turnover": requested_turnover,
                "optimized_turnover": optimized_turnover,
                "effective_turnover": optimized_turnover,
                "turnover_scale": 1.0,
                "turnover_cap_infeasible": False,
                "covariance_used": covariance_used,
                "covariance_degenerate": covariance_degenerate,
                "risk_budget_target": risk_budget_target,
                "feasibility_clamped": feasibility_clamped,
                "feasibility_original_budget": feasibility_original_budget,
                "hard_constraints": {
                    "budget": budget,
                    "max_position_weight": max_position_weight,
                },
            },
        )

    compressed = _compress_turnover(
        current=current,
        target=optimized_target,
        turnover_cap=max_daily_turnover,
    )
    compressed = _project_capped_simplex(values=compressed, caps=caps, budget=budget)
    effective_turnover = _turnover(current, compressed)
    infeasible = effective_turnover > max_daily_turnover + 1e-9
    if infeasible:
        compressed = optimized_target
        effective_turnover = optimized_turnover

    return ConstrainedAllocatorResult(
        adjusted_weights=compressed,
        details={
            "requested_turnover": requested_turnover,
            "optimized_turnover": optimized_turnover,
            "effective_turnover": effective_turnover,
            "turnover_scale": (
                min(1.0, max_daily_turnover / optimized_turnover)
                if optimized_turnover > 0
                else 1.0
            ),
            "turnover_cap": max_daily_turnover,
            "turnover_cap_infeasible": infeasible,
            "covariance_used": covariance_used,
            "covariance_degenerate": covariance_degenerate,
            "risk_budget_target": risk_budget_target,
            "feasibility_clamped": feasibility_clamped,
            "feasibility_original_budget": feasibility_original_budget,
            "hard_constraints": {
                "budget": budget,
                "max_position_weight": max_position_weight,
            },
        },
    )


def _compress_turnover(
    *,
    current: dict[str, float],
    target: dict[str, float],
    turnover_cap: float,
) -> dict[str, float]:
    requested_turnover = _turnover(current, target)
    if requested_turnover <= 0:
        return dict(target)
    scale = min(1.0, turnover_cap / requested_turnover)
    adjusted: dict[str, float] = {}
    for symbol in sorted(set(current) | set(target)):
        current_weight = current.get(symbol, 0.0)
        target_weight = target.get(symbol, 0.0)
        weight = current_weight + scale * (target_weight - current_weight)
        if weight > 1e-12:
            adjusted[symbol] = weight
    return adjusted


def _project_capped_simplex(
    *,
    values: dict[str, float],
    caps: dict[str, float],
    budget: float,
) -> dict[str, float]:
    symbols = sorted(values)
    if not symbols or budget <= 0:
        return {}

    clipped = {
        symbol: min(max(0.0, values.get(symbol, 0.0)), max(0.0, caps.get(symbol, budget)))
        for symbol in symbols
    }
    clipped_sum = sum(clipped.values())
    if clipped_sum <= budget + 1e-12:
        return {symbol: weight for symbol, weight in clipped.items() if weight > 1e-12}

    lower = min(values.get(symbol, 0.0) - caps.get(symbol, budget) for symbol in symbols)
    upper = max(values.get(symbol, 0.0) for symbol in symbols)
    for _ in range(80):
        midpoint = (lower + upper) / 2.0
        total = 0.0
        for symbol in symbols:
            capped = max(0.0, min(caps.get(symbol, budget), values.get(symbol, 0.0) - midpoint))
            total += capped
        if total > budget:
            lower = midpoint
        else:
            upper = midpoint

    projected = {}
    for symbol in symbols:
        weight = max(0.0, min(caps.get(symbol, budget), values.get(symbol, 0.0) - upper))
        if weight > 1e-12:
            projected[symbol] = weight
    return projected


def _turnover(current: dict[str, float], target: dict[str, float]) -> float:
    return sum(
        abs(target.get(symbol, 0.0) - current.get(symbol, 0.0))
        for symbol in set(current) | set(target)
    )


def _align_covariance(
    *,
    all_symbols: list[str],
    covariance_symbols: tuple[str, ...],
    covariance_matrix: Any | None,
    volatility_by_symbol: dict[str, float],
) -> np.ndarray | None:
    if covariance_matrix is None:
        if not volatility_by_symbol:
            return None
        diag = [
            max(_safe_positive(volatility_by_symbol.get(symbol, 0.20)), 1e-8) ** 2
            for symbol in all_symbols
        ]
        return np.diag(diag)

    try:
        sigma_full = np.asarray(covariance_matrix, dtype=float)
    except (TypeError, ValueError):
        return None
    if sigma_full.ndim != 2 or sigma_full.shape[0] != sigma_full.shape[1]:
        return None
    # NaN/Inf in the full covariance poison downstream eigvalsh and gradient
    # math. Replace non-finite entries with zero; diagonal fallback fills in
    # replacement variances from `volatility_by_symbol` just below.
    if not np.all(np.isfinite(sigma_full)):
        sigma_full = np.where(np.isfinite(sigma_full), sigma_full, 0.0)
    index = {symbol: i for i, symbol in enumerate(covariance_symbols)}
    sigma = np.zeros((len(all_symbols), len(all_symbols)), dtype=float)
    for i, sym_i in enumerate(all_symbols):
        ii = index.get(sym_i)
        for j, sym_j in enumerate(all_symbols):
            jj = index.get(sym_j)
            if ii is not None and jj is not None:
                sigma[i, j] = sigma_full[ii, jj]
    for i, symbol in enumerate(all_symbols):
        if not np.isfinite(sigma[i, i]) or sigma[i, i] <= 0:
            sigma[i, i] = max(_safe_positive(volatility_by_symbol.get(symbol, 0.20)), 1e-8) ** 2
    # Symmetrize defensively so floating-point noise does not produce
    # imaginary eigenvalues in the PSD check.
    sigma = 0.5 * (sigma + sigma.T)
    sigma += 1e-10 * np.eye(len(all_symbols))
    return sigma


def _ensure_positive_definite(sigma: np.ndarray) -> tuple[np.ndarray, bool]:
    """Try to lift a near-singular covariance to strictly PSD.

    Returns the (possibly lifted) matrix plus a `degenerate` flag.

    Semantics:
    - If eigvalsh fails or returns non-finite values -> degenerate.
    - If the matrix is truly rank-deficient (smallest eigenvalue <= 0 in
      the sense of strict non-PSD) -> degenerate. A small ridge can make
      the math workable, but a non-PSD covariance is a modeling red flag
      and the caller explicitly asked for fail-closed behavior.
    - If the matrix is PSD but merely near-singular (0 < min_eig < 1e-8)
      -> lift the diagonal so min eigenvalue reaches ~1e-8 and proceed.
    """
    if sigma.size == 0:
        return sigma, True
    try:
        eigvals = np.linalg.eigvalsh(sigma)
    except np.linalg.LinAlgError:
        return sigma, True
    if not np.all(np.isfinite(eigvals)):
        return sigma, True
    min_eig = float(np.min(eigvals))
    max_eig = float(np.max(eigvals))
    # Conditioning check: treat the matrix as degenerate whenever the
    # smallest eigenvalue is far below the largest one. Rank-1 / all-zero
    # / negative-eigenvalue matrices all trip this and fall back instead
    # of silently being rescued by a ridge. The 1e-6 relative floor is
    # generous enough that realistic daily equity covariances pass.
    if max_eig <= 0 or not np.isfinite(max_eig):
        return sigma, True
    # Rank-deficient: smallest eigenvalue is essentially zero (or negative)
    # relative to the largest. `_align_covariance` already adds a 1e-10
    # ridge, so a truly rank-1 input still lands below the relative floor
    # and is correctly flagged here.
    if min_eig <= max(1e-8, 1e-6 * max_eig):
        # Try a rescue lift before declaring degeneracy: a PSD matrix that
        # is merely near-singular can be rescued by a small diagonal shift.
        if min_eig > 0:
            shift = (1e-8 - min_eig) + 1e-8
            lifted = sigma + shift * np.eye(sigma.shape[0])
            try:
                eigvals_lifted = np.linalg.eigvalsh(lifted)
            except np.linalg.LinAlgError:
                return sigma, True
            if not np.all(np.isfinite(eigvals_lifted)):
                return sigma, True
            lifted_min = float(np.min(eigvals_lifted))
            lifted_max = float(np.max(eigvals_lifted))
            if lifted_max > 0 and lifted_min > max(1e-10, 1e-6 * lifted_max):
                return lifted, False
        return sigma, True
    return sigma, False


def _safe_positive(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(numeric) or numeric < 0:
        return 0.0
    return numeric


def _risk_budget_target(
    *,
    requested: dict[str, float],
    all_symbols: list[str],
    budget: float,
    caps: dict[str, float],
    volatility_by_symbol: dict[str, float],
    sigma: np.ndarray,
) -> dict[str, float]:
    inv_vol_scores: dict[str, float] = {}
    for idx, symbol in enumerate(all_symbols):
        if requested.get(symbol, 0.0) <= 0:
            continue
        vol = volatility_by_symbol.get(symbol)
        if vol is None or vol <= 0:
            vol = float(np.sqrt(max(sigma[idx, idx], 1e-10)))
        inv_vol_scores[symbol] = 1.0 / max(vol, 1e-8)
    if not inv_vol_scores:
        return {}
    total = sum(inv_vol_scores.values())
    raw = {
        symbol: budget * score / total
        for symbol, score in inv_vol_scores.items()
    }
    return _project_capped_simplex(values=raw, caps=caps, budget=budget)


def _blend_targets(
    *,
    base: dict[str, float],
    alternative: dict[str, float],
    strength: float,
    caps: dict[str, float],
    budget: float,
) -> dict[str, float]:
    if strength <= 0 or not alternative:
        return dict(base)
    symbols = sorted(set(base) | set(alternative))
    blended = {
        symbol: (1.0 - strength) * base.get(symbol, 0.0) + strength * alternative.get(symbol, 0.0)
        for symbol in symbols
    }
    return _project_capped_simplex(values=blended, caps=caps, budget=budget)


def _covariance_projected_gradient(
    *,
    initial: dict[str, float],
    target: dict[str, float],
    sigma: np.ndarray,
    caps: dict[str, float],
    budget: float,
    risk_aversion: float,
) -> dict[str, float]:
    if risk_aversion <= 0:
        return dict(initial)
    symbols = sorted(target)
    if not symbols:
        return {}
    w = np.array([initial.get(symbol, 0.0) for symbol in symbols], dtype=float)
    t = np.array([target.get(symbol, 0.0) for symbol in symbols], dtype=float)
    cap_vec = {symbol: caps[symbol] for symbol in symbols}
    max_diag = float(np.max(np.diag(sigma))) if sigma.size else 0.0
    step = 1.0 / max(1.0, 1.0 + risk_aversion * max_diag * 4.0)
    for _ in range(120):
        grad = (w - t) + risk_aversion * (sigma @ w)
        candidate = {
            symbol: float(weight)
            for symbol, weight in zip(symbols, (w - step * grad), strict=False)
        }
        projected = _project_capped_simplex(values=candidate, caps=cap_vec, budget=budget)
        projected = _fill_to_budget(
            weights=projected,
            caps=cap_vec,
            budget=budget,
            priority={symbol: max(target.get(symbol, 0.0), 1e-8) for symbol in symbols},
        )
        next_w = np.array([projected.get(symbol, 0.0) for symbol in symbols], dtype=float)
        if float(np.max(np.abs(next_w - w))) <= 1e-9:
            w = next_w
            break
        w = next_w
    return {symbol: float(weight) for symbol, weight in zip(symbols, w, strict=False) if weight > 1e-12}


def _fill_to_budget(
    *,
    weights: dict[str, float],
    caps: dict[str, float],
    budget: float,
    priority: dict[str, float],
) -> dict[str, float]:
    total = sum(weights.values())
    if total >= budget - 1e-12:
        return dict(weights)

    adjusted = dict(weights)
    residual = budget - total
    for _ in range(12):
        expandable = [
            symbol for symbol, cap in caps.items()
            if cap - adjusted.get(symbol, 0.0) > 1e-12
        ]
        if not expandable or residual <= 1e-12:
            break
        denom = sum(max(priority.get(symbol, 0.0), 1e-8) for symbol in expandable)
        if denom <= 0:
            break
        consumed = 0.0
        for symbol in expandable:
            room = caps[symbol] - adjusted.get(symbol, 0.0)
            share = residual * max(priority.get(symbol, 0.0), 1e-8) / denom
            add = min(room, share)
            if add > 0:
                adjusted[symbol] = adjusted.get(symbol, 0.0) + add
                consumed += add
        if consumed <= 1e-12:
            break
        residual -= consumed
    return {symbol: weight for symbol, weight in adjusted.items() if weight > 1e-12}
