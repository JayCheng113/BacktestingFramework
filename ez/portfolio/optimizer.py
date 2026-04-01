"""V2.12: Portfolio optimization — constrained weight optimization.

Three optimizers: MeanVariance, MinVariance, RiskParity.
Uses Ledoit-Wolf shrinkage covariance (numpy, no sklearn dependency).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy import optimize

logger = logging.getLogger(__name__)


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


# ── PortfolioOptimizer ABC ────────────────────────────────────────────

class PortfolioOptimizer(ABC):
    """Portfolio optimizer: alpha signal + risk model → optimal weights.

    Unlike Allocator, receives date context via set_context() before each optimize().
    """

    def __init__(self, constraints: OptimizationConstraints, cov_lookback: int = 60,
                 benchmark_weights: dict[str, float] | None = None,
                 max_tracking_error: float | None = None):
        self._constraints = constraints
        self._cov_lookback = cov_lookback
        self._benchmark_weights = benchmark_weights  # V2.12.1: index enhancement
        self._max_te = max_tracking_error
        self._current_date: date | None = None
        self._universe_data: dict[str, pd.DataFrame] | None = None

    def set_context(self, current_date: date,
                    universe_data: dict[str, pd.DataFrame]) -> None:
        """Called by engine before each rebalance."""
        self._current_date = current_date
        self._universe_data = universe_data

    def optimize(self, alpha_weights: dict[str, float]) -> dict[str, float]:
        """Alpha signal → constrained optimal weights.

        alpha_weights: strategy's raw weights interpreted as relative expected excess
        returns (higher = more bullish). All output weights >= 0, sum <= 1.0.
        """
        symbols = [s for s, w in alpha_weights.items() if w > 0]
        if len(symbols) == 0:
            return {}
        if len(symbols) == 1:
            # Single stock: still respect max_weight (consistency with multi-stock behavior).
            # If max_weight=0.10, single stock gets 10%, rest is cash.
            s = symbols[0]
            return {s: min(1.0, self._constraints.max_weight)}

        total_alpha = sum(alpha_weights[s] for s in symbols)
        if total_alpha <= 0:
            return self._fallback(symbols)
        alpha = np.array([alpha_weights[s] / total_alpha for s in symbols])

        sigma = self._estimate_covariance(symbols)
        if sigma is None:
            return self._fallback(symbols)

        try:
            w = self._optimize(alpha, sigma, symbols)
        except Exception as exc:
            logger.warning("Optimization failed (%s), falling back to equal weight", exc)
            return self._fallback(symbols)

        return self._apply_constraints(dict(zip(symbols, w)))

    @abstractmethod
    def _optimize(self, alpha: np.ndarray, sigma: np.ndarray,
                  symbols: list[str]) -> np.ndarray:
        ...

    def _estimate_covariance(self, symbols: list[str]) -> np.ndarray | None:
        """Estimate covariance from universe_data up to current_date."""
        if self._universe_data is None or self._current_date is None:
            return None
        returns_list = []
        for sym in symbols:
            df = self._universe_data.get(sym)
            if df is None or df.empty:
                return None
            col = "adj_close" if "adj_close" in df.columns else "close"
            prices = df[col]
            if hasattr(df.index, 'date'):
                mask = df.index.date < self._current_date
            else:
                mask = df.index < pd.Timestamp(self._current_date)
            prices = prices[mask]
            if len(prices) < 3:
                return None
            prices = prices.iloc[-self._cov_lookback:]
            ret = prices.pct_change().dropna().values
            returns_list.append(ret)
        min_len = min(len(r) for r in returns_list)
        if min_len < 3:
            return None
        mat = np.column_stack([r[-min_len:] for r in returns_list])
        return ledoit_wolf_shrinkage(mat)

    def _fallback(self, symbols: list[str]) -> dict[str, float]:
        """Optimization failed → equal weight capped at max_weight."""
        max_w = self._constraints.max_weight
        n = len(symbols)
        w = min(1.0 / n, max_w)
        return {s: w for s in symbols}

    def _industry_groups(self, symbols: list[str]) -> dict[str, list[int]]:
        """Map industry → list of symbol indices."""
        groups: dict[str, list[int]] = {}
        for i, sym in enumerate(symbols):
            ind = self._constraints.industry_map.get(sym, "")
            if ind:
                groups.setdefault(ind, []).append(i)
        return groups

    def _apply_constraints(self, weights: dict[str, float]) -> dict[str, float]:
        """Post-optimization: clip max_weight + industry limits + normalize."""
        max_w = self._constraints.max_weight
        w = {k: min(max(v, 0.0), max_w) for k, v in weights.items()}
        # Industry constraints: iterative clip
        ind_map = self._constraints.industry_map
        for _ in range(5):
            ind_groups: dict[str, list[str]] = {}
            for sym in w:
                ind = ind_map.get(sym, "")
                if ind:
                    ind_groups.setdefault(ind, []).append(sym)
            for _ind, syms in ind_groups.items():
                ind_total = sum(w.get(s, 0) for s in syms)
                if ind_total > self._constraints.max_industry_weight + 1e-9:
                    scale = self._constraints.max_industry_weight / ind_total
                    for s in syms:
                        w[s] *= scale
        total = sum(w.values())
        if total > 1.0 + 1e-9:
            w = {k: v / total for k, v in w.items()}
        return {k: v for k, v in w.items() if v > 1e-10}


# ── Concrete Optimizers ───────────────────────────────────────────────

class MeanVarianceOptimizer(PortfolioOptimizer):
    """Markowitz mean-variance: min λ·w'Σw - w'α, s.t. long-only + constraints.

    α = strategy raw_weights (normalized), not historical returns.
    """

    def __init__(self, risk_aversion: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self.risk_aversion = risk_aversion

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)
        max_w = self._constraints.max_weight

        def objective(w):
            return float(self.risk_aversion * w @ sigma @ w - w @ alpha)

        cons = [{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}]
        for _ind, idx_list in self._industry_groups(symbols).items():
            cons.append({
                "type": "ineq",
                "fun": lambda w, idx=idx_list: float(
                    self._constraints.max_industry_weight - sum(w[i] for i in idx)
                ),
            })

        # V2.12.1: Tracking error constraint (active-universe approximation)
        if self._benchmark_weights and self._max_te:
            w_b = np.array([self._benchmark_weights.get(s, 0) for s in symbols])
            cons.append({
                "type": "ineq",
                "fun": lambda w, wb=w_b: float(
                    self._max_te ** 2 - (w - wb) @ sigma @ (w - wb)
                ),
            })

        bounds = [(0.0, max_w)] * n
        w0 = np.full(n, 1.0 / n)
        result = optimize.minimize(
            objective, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
            options={"maxiter": max(500, n * 5), "ftol": 1e-10},
        )
        if not result.success:
            raise RuntimeError(result.message)
        return np.clip(result.x, 0, max_w)


class MinVarianceOptimizer(PortfolioOptimizer):
    """Minimum variance: min w'Σw, s.t. long-only + constraints.

    Pure risk perspective — ignores alpha. Convex, guarantees global optimum.
    """

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)
        max_w = self._constraints.max_weight

        def objective(w):
            return float(w @ sigma @ w)

        cons = [{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}]
        for _ind, idx_list in self._industry_groups(symbols).items():
            cons.append({
                "type": "ineq",
                "fun": lambda w, idx=idx_list: float(
                    self._constraints.max_industry_weight - sum(w[i] for i in idx)
                ),
            })
        if self._benchmark_weights and self._max_te:
            w_b = np.array([self._benchmark_weights.get(s, 0) for s in symbols])
            cons.append({
                "type": "ineq",
                "fun": lambda w, wb=w_b: float(
                    self._max_te ** 2 - (w - wb) @ sigma @ (w - wb)
                ),
            })

        bounds = [(0.0, max_w)] * n
        w0 = np.full(n, 1.0 / n)
        result = optimize.minimize(
            objective, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
        )
        if not result.success:
            raise RuntimeError(result.message)
        return np.clip(result.x, 0, max_w)


class RiskParityOptimizer(PortfolioOptimizer):
    """Risk parity: equalize risk contributions across assets.

    Fallback: inverse-volatility weighting when optimization fails.
    """

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)

        def risk_contribution_obj(w):
            port_var = float(w @ sigma @ w)
            if port_var < 1e-20:
                return 0.0
            marginal = sigma @ w
            rc = w * marginal / port_var
            target = 1.0 / n
            return float(np.sum((rc - target) ** 2))

        vols = np.sqrt(np.diag(sigma))
        vols = np.maximum(vols, 1e-8)
        w0 = (1.0 / vols) / np.sum(1.0 / vols)

        max_w = self._constraints.max_weight
        bounds = [(1e-6, max_w)] * n
        cons = [{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}]
        # Industry constraints (same as MeanVariance/MinVariance)
        for _ind, idx_list in self._industry_groups(symbols).items():
            cons.append({
                "type": "ineq",
                "fun": lambda w, idx=idx_list: float(
                    self._constraints.max_industry_weight - sum(w[i] for i in idx)
                ),
            })

        result = optimize.minimize(
            risk_contribution_obj, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
            options={"maxiter": 1000},
        )
        if not result.success:
            return w0  # fallback: inverse-vol
        return result.x
