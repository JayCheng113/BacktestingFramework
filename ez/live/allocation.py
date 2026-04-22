"""Runtime allocation helpers for live OMS target weights."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from ez.live._utils import (
    positive_or_none as _positive_or_none,
    fraction_or_none as _fraction_or_none,
)
from ez.live.optimizer_allocator import (
    _project_capped_simplex,
    optimize_target_weights,
)


@dataclass(slots=True)
class RuntimeAllocatorConfig:
    allocation_mode: str = "pro_rata_cap"
    runtime_allocation_cap: float | None = None
    max_names: int | None = None
    max_position_weight: float | None = None
    max_daily_turnover: float | None = None
    covariance_lookback_days: int = 60
    covariance_risk_aversion: float = 0.0
    risk_budget_strength: float = 0.0
    target_portfolio_vol: float | None = None
    vol_lookback_days: int = 20
    volatility_fallback: float = 0.20

    @classmethod
    def from_params(cls, params: dict[str, Any] | None) -> "RuntimeAllocatorConfig":
        params = params or {}
        mode = str(params.get("allocation_mode", "pro_rata_cap") or "pro_rata_cap")
        if mode not in {"pro_rata_cap", "equal_weight_cap", "risk_budget_cap", "constrained_opt"}:
            mode = "pro_rata_cap"
        return cls(
            allocation_mode=mode,
            runtime_allocation_cap=_fraction_or_none(params.get("runtime_allocation_cap")),
            max_names=_positive_int_or_none(params.get("max_names")),
            max_position_weight=_fraction_or_none(params.get("max_position_weight")),
            max_daily_turnover=_fraction_or_none(params.get("max_daily_turnover")),
            covariance_lookback_days=_positive_int_or_default(params.get("covariance_lookback_days"), 60),
            covariance_risk_aversion=_positive_or_default(params.get("covariance_risk_aversion"), 0.0),
            risk_budget_strength=_fraction_or_none(params.get("risk_budget_strength")) or 0.0,
            target_portfolio_vol=_positive_or_none(params.get("target_portfolio_vol")),
            vol_lookback_days=_positive_int_or_default(params.get("vol_lookback_days"), 20),
            volatility_fallback=_positive_or_default(params.get("volatility_fallback"), 0.20),
        )


@dataclass(slots=True)
class AllocationDecision:
    adjusted_weights: dict[str, float]
    allocation_events: list[dict[str, Any]]


@dataclass(slots=True)
class AllocationContext:
    volatility_by_symbol: dict[str, float] = field(default_factory=dict)
    current_weights: dict[str, float] = field(default_factory=dict)
    covariance_symbols: tuple[str, ...] = ()
    covariance_matrix: Any | None = None


class RuntimeAllocator:
    """Adjust target weights before OMS order generation."""

    def __init__(self, config: RuntimeAllocatorConfig | None = None):
        self.config = config or RuntimeAllocatorConfig()

    def allocate(
        self,
        *,
        business_date: date,
        target_weights: dict[str, float],
        context: AllocationContext | None = None,
    ) -> AllocationDecision:
        requested = {
            symbol: float(weight)
            for symbol, weight in target_weights.items()
            if float(weight) > 0
        }
        if not requested:
            return AllocationDecision(adjusted_weights={}, allocation_events=[])

        requested_allocation = sum(requested.values())
        selected, dropped = self._select_symbols(requested)
        allocation_budget = self._allocation_budget(requested_allocation)

        constrained_details: dict[str, Any] = {}
        risk_budget_fallback = False
        if self.config.allocation_mode == "equal_weight_cap":
            adjusted = self._equal_weight(selected, allocation_budget)
        elif self.config.allocation_mode == "risk_budget_cap":
            adjusted, risk_budget_fallback = self._risk_budget(
                selected, allocation_budget, context
            )
        elif self.config.allocation_mode == "constrained_opt":
            constrained = optimize_target_weights(
                requested_weights=selected,
                current_weights=(context.current_weights if context else {}) or {},
                budget=allocation_budget,
                max_position_weight=self.config.max_position_weight,
                max_daily_turnover=self.config.max_daily_turnover,
                covariance_symbols=(context.covariance_symbols if context else ()) or (),
                covariance_matrix=(context.covariance_matrix if context else None),
                covariance_risk_aversion=self.config.covariance_risk_aversion,
                risk_budget_strength=self.config.risk_budget_strength,
                volatility_by_symbol=(context.volatility_by_symbol if context else {}) or {},
            )
            adjusted = constrained.adjusted_weights
            constrained_details = constrained.details
        else:
            adjusted = self._pro_rata(selected, allocation_budget)
        adjusted, vol_details = self._apply_vol_target(adjusted, context)

        # Important #2: surface underfill when max_names + caps made the
        # resulting allocation fall materially below the budget.
        underfill_details = self._underfill_details(
            adjusted=adjusted,
            allocation_budget=allocation_budget,
            dropped=dropped,
        )

        extra_details: dict[str, Any] = {}
        if risk_budget_fallback:
            extra_details["risk_budget_fallback"] = True

        if (
            not self._changed(requested, adjusted, dropped)
            and not vol_details
            and not constrained_details
            and not underfill_details
            and not extra_details
        ):
            return AllocationDecision(adjusted_weights=adjusted, allocation_events=[])

        event_type = (
            "runtime_allocator"
            if (
                dropped
                or self.config.allocation_mode != "pro_rata_cap"
                or vol_details
                or constrained_details
                or underfill_details
            )
            else "runtime_allocation_gate"
        )
        return AllocationDecision(
            adjusted_weights=adjusted,
            allocation_events=[
                {
                    "date": business_date.isoformat(),
                    "event": event_type,
                    "rule": "runtime_allocator",
                    "reason": "risk:runtime_allocator",
                    "message": self._message(dropped=dropped, adjusted=adjusted, requested=requested),
                    "details": {
                        "allocation_mode": self.config.allocation_mode,
                        "requested_allocation": sum(requested.values()),
                        "effective_allocation": sum(adjusted.values()),
                        "runtime_allocation_cap": self.config.runtime_allocation_cap,
                        "max_names": self.config.max_names,
                        "max_position_weight": self.config.max_position_weight,
                        "max_daily_turnover": self.config.max_daily_turnover,
                        "covariance_lookback_days": self.config.covariance_lookback_days,
                        "covariance_risk_aversion": self.config.covariance_risk_aversion,
                        "risk_budget_strength": self.config.risk_budget_strength,
                        "requested_weights": requested,
                        "adjusted_weights": adjusted,
                        "dropped_symbols": dropped,
                        **constrained_details,
                        **vol_details,
                        **underfill_details,
                        **extra_details,
                    },
                }
            ],
        )

    def _select_symbols(
        self,
        requested: dict[str, float],
    ) -> tuple[dict[str, float], list[str]]:
        max_names = self.config.max_names
        ranked = sorted(requested.items(), key=lambda item: (-item[1], item[0]))
        if max_names is None or max_names >= len(ranked):
            return dict(ranked), []
        selected = dict(ranked[:max_names])
        dropped = [symbol for symbol, _ in ranked[max_names:]]
        return selected, dropped

    def _allocation_budget(self, requested_allocation: float) -> float:
        if requested_allocation <= 0:
            return 0.0
        cap = self.config.runtime_allocation_cap
        if cap is None:
            return requested_allocation
        return min(requested_allocation, cap)

    @staticmethod
    def _pro_rata(selected: dict[str, float], budget: float) -> dict[str, float]:
        total = sum(selected.values())
        if total <= 0 or budget <= 0:
            return {}
        scale = budget / total
        return {
            symbol: weight * scale
            for symbol, weight in selected.items()
            if weight * scale > 0
        }

    @staticmethod
    def _equal_weight(selected: dict[str, float], budget: float) -> dict[str, float]:
        if not selected or budget <= 0:
            return {}
        per_name = budget / len(selected)
        return {symbol: per_name for symbol in selected}

    def _risk_budget(
        self,
        selected: dict[str, float],
        budget: float,
        context: AllocationContext | None,
    ) -> tuple[dict[str, float], bool]:
        """Allocate using inverse-vol risk budgets.

        Returns `(weights, fallback_used)`. Numerical stability notes:
        - Invalid vols (negative/NaN/Inf) are sanitized to 0 before use.
        - The divisor uses a small absolute floor (`1e-6`) rather than
          `1e-8`, so a single dust-level vol cannot explode into an
          arbitrarily dominant score.
        - When all valid vols are below that floor (the uniformly tiny /
          degenerate case), fall back to pro-rata-on-caps instead of
          pretending the inverse-vol ranking still carries signal.
        """
        if not selected or budget <= 0:
            return {}, False
        vols = (context.volatility_by_symbol if context else {}) or {}
        sanitized: dict[str, float] = {}
        for symbol in selected:
            raw = vols.get(symbol, self.config.volatility_fallback)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                v = 0.0
            if not np.isfinite(v) or v < 0:
                v = 0.0
            sanitized[symbol] = v

        valid_positive = [v for v in sanitized.values() if v > 0]
        # Absolute safety floor: prevents a single 1e-8 vol from exploding
        # inverse-vol scores far above the rest. This is intentionally
        # wider than the old 1e-8 to keep the math numerically stable
        # for A-share daily vols (which never legitimately fall below 1e-6).
        epsilon = 1e-6

        # Degenerate case: every observed vol is below the safety floor, so
        # the ranking is informationless. Fall back deterministically.
        if valid_positive and max(valid_positive) < epsilon:
            return self._pro_rata(selected, budget), True

        scores: dict[str, float] = {}
        for symbol, weight in selected.items():
            v = sanitized[symbol]
            denom = max(v, epsilon)
            scores[symbol] = weight / denom

        total = sum(scores.values())
        if total <= 0 or not np.isfinite(total):
            return self._pro_rata(selected, budget), True
        return {
            symbol: budget * score / total
            for symbol, score in scores.items()
            if score > 0
        }, False

    def _apply_vol_target(
        self,
        adjusted: dict[str, float],
        context: AllocationContext | None,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        target = self.config.target_portfolio_vol
        if target is None or not adjusted:
            return adjusted, {}
        vols = (context.volatility_by_symbol if context else {}) or {}
        if not vols:
            return adjusted, {}

        # Minor: sanitize non-finite / negative vols to 0 before squaring.
        def _safe_vol(symbol: str) -> float:
            raw = vols.get(symbol, self.config.volatility_fallback)
            try:
                v = float(raw)
            except (TypeError, ValueError):
                return 0.0
            if not np.isfinite(v) or v < 0:
                return 0.0
            return v

        variance_sum = sum(
            (weight * _safe_vol(symbol)) ** 2 for symbol, weight in adjusted.items()
        )
        if not np.isfinite(variance_sum) or variance_sum < 0:
            return adjusted, {}
        estimated = math.sqrt(variance_sum)
        if estimated <= 0:
            return adjusted, {}
        vol_report = {symbol: _safe_vol(symbol) for symbol in adjusted}
        if estimated <= target + 1e-9:
            return adjusted, {
                "estimated_portfolio_vol": estimated,
                "target_portfolio_vol": target,
                "vol_target_scale": 1.0,
                "volatility_by_symbol": vol_report,
            }
        scale = target / estimated
        scaled = {
            symbol: weight * scale
            for symbol, weight in adjusted.items()
            if weight * scale > 0
        }

        # Important #1: uniform scaling cannot raise any weight above what
        # it already was, so only *retightening* is needed when the caller
        # configured a `max_position_weight`. Re-project onto the cap
        # simplex so the vol-target path cannot leak a single symbol above
        # the configured per-symbol cap.
        reproject_cap_hit = False
        if self.config.max_position_weight is not None and scaled:
            cap = float(self.config.max_position_weight)
            budget = sum(scaled.values())
            # If any scaled weight exceeds the per-symbol cap, re-project.
            if any(weight - cap > 1e-12 for weight in scaled.values()):
                reproject_cap_hit = True
                scaled = _project_capped_simplex(
                    values=scaled,
                    caps={symbol: cap for symbol in scaled},
                    budget=budget,
                )
        return scaled, {
            "estimated_portfolio_vol": estimated,
            "target_portfolio_vol": target,
            "vol_target_scale": scale,
            "volatility_by_symbol": vol_report,
            "vol_target_reproject_cap_hit": reproject_cap_hit,
        }

    def _underfill_details(
        self,
        *,
        adjusted: dict[str, float],
        allocation_budget: float,
        dropped: list[str],
    ) -> dict[str, Any]:
        """Surface underfill when caps + max_names leave budget unused.

        Returns `{}` when the allocation is within 10% of the budget. Only
        reports when `max_names` (or equivalent trimming) was active, since
        pro-rata / equal-weight without trimming naturally fills the budget.
        """
        if allocation_budget <= 0:
            return {}
        effective_allocation = sum(adjusted.values())
        underfill_ratio = 1.0 - (effective_allocation / allocation_budget)
        if underfill_ratio <= 0.1:
            return {}
        # Only flag when caps and trimming are the plausible cause.
        reason_bits: list[str] = []
        if self.config.max_names is not None and dropped:
            reason_bits.append("max_names")
        if self.config.max_position_weight is not None:
            reason_bits.append("max_position_weight")
        if not reason_bits:
            return {}
        reason = "_plus_".join(reason_bits) + "_too_tight"
        return {
            "underfill_ratio": float(underfill_ratio),
            "underfill_reason": reason,
        }

    @staticmethod
    def _changed(
        requested: dict[str, float],
        adjusted: dict[str, float],
        dropped: list[str],
    ) -> bool:
        if dropped:
            return True
        if requested.keys() != adjusted.keys():
            return True
        for symbol, requested_weight in requested.items():
            if abs(requested_weight - adjusted.get(symbol, 0.0)) > 1e-9:
                return True
        return False

    def _message(
        self,
        *,
        dropped: list[str],
        adjusted: dict[str, float],
        requested: dict[str, float],
    ) -> str:
        if dropped:
            return "Runtime allocator trimmed the symbol set before order generation."
        if self.config.allocation_mode == "equal_weight_cap":
            return "Runtime allocator rebalanced selected symbols to equal weights before order generation."
        if self.config.allocation_mode == "risk_budget_cap":
            return "Runtime allocator redistributed target weights using realized volatility budgets before order generation."
        if self.config.allocation_mode == "constrained_opt":
            return "Runtime allocator projected target weights into the live constraint set with covariance-aware risk shaping before order generation."
        if sum(adjusted.values()) < sum(requested.values()) - 1e-9:
            return "Runtime allocator scaled target weights before order generation to enforce the allocation cap."
        return "Runtime allocator adjusted target weights before order generation."


def _positive_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _positive_or_default(value: Any, default: float) -> float:
    parsed = _positive_or_none(value)
    return parsed if parsed is not None else default


def _positive_int_or_default(value: Any, default: int) -> int:
    parsed = _positive_int_or_none(value)
    return parsed if parsed is not None else default
