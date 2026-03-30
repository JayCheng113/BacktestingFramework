"""V2.9 P4: Allocator — weight adjustment with constraints.

Applied after strategy generates raw weights. Enforces long-only, max position, normalization.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Allocator(ABC):
    """Weight allocator: adjust raw weights to satisfy constraints."""

    @abstractmethod
    def allocate(self, raw_weights: dict[str, float]) -> dict[str, float]:
        """Adjust weights. Output must satisfy: all >= 0, sum <= 1.0."""
        ...


class EqualWeightAllocator(Allocator):
    """Equal weight across all selected assets."""

    def allocate(self, raw_weights: dict[str, float]) -> dict[str, float]:
        selected = {k: v for k, v in raw_weights.items() if v > 0}
        if not selected:
            return {}
        w = 1.0 / len(selected)
        return {k: w for k in selected}


class MaxWeightAllocator(Allocator):
    """Clip individual weights to max_weight, redistribute excess proportionally."""

    def __init__(self, max_weight: float = 0.05):
        self._max_weight = max_weight

    def allocate(self, raw_weights: dict[str, float]) -> dict[str, float]:
        if not raw_weights:
            return {}
        # Clip negative to 0
        w = {k: max(0.0, v) for k, v in raw_weights.items()}
        total = sum(w.values())
        if total <= 0:
            return {}
        # Normalize to sum=1
        w = {k: v / total for k, v in w.items()}
        # Iterative clipping
        for _ in range(10):
            excess = 0.0
            under = {}
            for k, v in w.items():
                if v > self._max_weight:
                    excess += v - self._max_weight
                    w[k] = self._max_weight
                else:
                    under[k] = v
            if excess <= 1e-10 or not under:
                break
            under_total = sum(under.values())
            if under_total > 0:
                for k in under:
                    w[k] += excess * (under[k] / under_total)
        return {k: v for k, v in w.items() if v > 1e-10}


class RiskParityAllocator(Allocator):
    """Inverse-volatility weighting (simplified risk parity)."""

    def __init__(self, volatilities: dict[str, float] | None = None):
        self._vols = volatilities or {}

    def set_volatilities(self, vols: dict[str, float]) -> None:
        self._vols = vols

    def allocate(self, raw_weights: dict[str, float]) -> dict[str, float]:
        selected = {k for k, v in raw_weights.items() if v > 0}
        if not selected:
            return {}
        inv_vols = {}
        for sym in selected:
            vol = self._vols.get(sym, 0.2)  # default 20% vol if unknown
            inv_vols[sym] = 1.0 / max(vol, 1e-8)
        total = sum(inv_vols.values())
        if total <= 0:
            return {}
        return {k: v / total for k, v in inv_vols.items()}
