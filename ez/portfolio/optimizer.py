"""V2.12: Portfolio optimization — constrained weight optimization.

Three optimizers: MeanVariance, MinVariance, RiskParity.
Uses Ledoit-Wolf shrinkage covariance (numpy, no sklearn dependency).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy import optimize


@dataclass
class OptimizationConstraints:
    """Constraints for portfolio optimization."""
    max_weight: float = 0.10
    max_industry_weight: float = 0.30
    industry_map: dict[str, str] = field(default_factory=dict)


def ledoit_wolf_shrinkage(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage covariance estimator (OAS variant, numpy only).

    Reference: Chen, Wiesel, Eldar, Hero (2010).

    Args:
        returns: T×N matrix of daily returns.
    Returns:
        N×N shrunk covariance, guaranteed positive definite.
    """
    T, N = returns.shape
    if T < 2:
        return np.eye(N) * 0.04  # fallback: 20% vol identity

    S = np.cov(returns, rowvar=False, ddof=1)
    if N == 1:
        return S.reshape(1, 1) + 1e-8 * np.eye(1)

    trace_S = np.trace(S)
    trace_S2 = np.sum(S ** 2)

    mu = trace_S / N
    F = mu * np.eye(N)

    rho_num = (1 - 2.0 / N) * trace_S2 + trace_S ** 2
    rho_den = (T + 1 - 2.0 / N) * (trace_S2 - trace_S ** 2 / N)
    rho = min(1.0, max(0.0, rho_num / rho_den)) if abs(rho_den) > 1e-20 else 1.0

    sigma = (1 - rho) * S + rho * F
    sigma += 1e-8 * np.eye(N)
    return sigma
