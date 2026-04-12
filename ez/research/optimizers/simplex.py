"""SimplexMultiObjectiveOptimizer: differential_evolution wrapper for
long-only portfolio weight optimization on a probability simplex.

The simplex constraint is:
  - All weights >= 0 (long-only A-share)
  - sum(weights) <= 1.0 (residual is implicit cash)

Parameterization (Claude reviewer round-6 C1 fix):
  We optimize over N unconstrained variables ``x ∈ [0, 1]^N`` and map
  them to the simplex via **stick-breaking** (incomplete Beta CDF):

    w_1 = x_1
    w_k = x_k * (1 - sum(w_1..w_{k-1}))   for k = 2..N
    cash = 1 - sum(w_1..w_N)

  Every point in ``[0,1]^N`` maps to a valid simplex point (sum <= 1,
  all >= 0), so the initial DE population is 100% feasible regardless
  of N. The previous ``[0,1]^N`` direct approach had feasibility rate
  ``1/N!`` which collapsed for N >= 8 (Claude reviewer verified N=10
  returned fun=inf with 200 iterations).

  Cash (sum < 1) is naturally reachable: when any ``x_k < 1``, some
  "remaining capacity" is left, which becomes implicit cash earning
  0% daily return.

Multi-objective: one ``optimize()`` call iterates over the optimizer's
``objectives`` list and runs differential_evolution per objective.

Reference: ``validation/phase_o_nested_oos.py::optimize_on_window`` is
the hand-rolled equivalent that this class replaces.
"""
from __future__ import annotations
import math
from typing import Optional

import numpy as np
import pandas as pd

from .base import Optimizer, OptimalWeights, Objective
from .._metrics import compute_basic_metrics


class SimplexMultiObjectiveOptimizer(Optimizer):
    """Multi-objective optimizer over a probability simplex.

    Parameters
    ----------
    objectives : list[Objective]
        One Objective instance per result desired. The same optimizer
        runs each objective independently and returns one OptimalWeights
        per objective. Empty list raises at construction.
    seed : int
        Random seed passed to scipy.optimize.differential_evolution
        for reproducibility. Default 42 (matches phase_o convention).
    max_iter : int
        Maximum DE iterations per objective. Default 200.
    pop_size : int
        DE population size multiplier. Default 25.
    tol : float
        DE convergence tolerance. Default 1e-7.
    """

    def __init__(
        self,
        objectives: list[Objective],
        seed: int = 42,
        max_iter: int = 200,
        pop_size: int = 25,
        tol: float = 1e-7,
    ):
        if not objectives:
            raise ValueError(
                "SimplexMultiObjectiveOptimizer requires at least one objective"
            )
        for obj in objectives:
            if not isinstance(obj, Objective):
                raise TypeError(
                    f"All entries must be Objective instances, "
                    f"got {type(obj).__name__}"
                )
        self.objectives = list(objectives)
        self.seed = seed
        self.max_iter = max_iter
        self.pop_size = pop_size
        self.tol = tol

    def optimize(
        self,
        returns: pd.DataFrame,
        baseline_metrics: Optional[dict[str, float]] = None,
    ) -> list[OptimalWeights]:
        """Run optimization for each registered objective.

        Parameters
        ----------
        returns : pd.DataFrame
            Index: trading dates. Columns: asset labels (e.g. ["A", "E", "F"]).
            Values: daily returns. Must have >= 2 columns and >= 2 rows.
        baseline_metrics : dict, optional
            Reference metrics for EpsilonConstraint resolution. Typically
            the alpha sleeve's IS standalone metrics.

        Returns
        -------
        list[OptimalWeights]
            One per objective, in the same order. Infeasible objectives
            (no DE convergence) return ``OptimalWeights(... status="infeasible")``
            with all weights = 0 — caller should filter by .is_feasible.
        """
        from scipy.optimize import differential_evolution

        if returns is None or len(returns) == 0:
            raise ValueError("SimplexMultiObjectiveOptimizer.optimize: empty returns")

        if not isinstance(returns, pd.DataFrame):
            raise TypeError(
                f"returns must be pd.DataFrame, got {type(returns).__name__}"
            )

        n = len(returns.columns)
        if n < 2:
            raise ValueError(
                f"SimplexMultiObjectiveOptimizer requires >= 2 assets, got {n}"
            )

        labels = list(returns.columns)
        # Claude reviewer round-6 C1: stick-breaking parameterization.
        # Optimize over x ∈ [0,1]^N, map to simplex weights via:
        #   w_1 = x_1
        #   w_k = x_k * (1 - sum(w_1..w_{k-1}))
        # Every point in [0,1]^N is 100% feasible. Cash = 1 - sum(w).
        bounds = [(0.0, 1.0)] * n

        # Drop NaN rows and convert to numpy for inner-loop performance
        clean_returns = returns.dropna()
        if len(clean_returns) < 2:
            raise ValueError(
                f"SimplexMultiObjectiveOptimizer: returns has < 2 valid rows after NaN drop"
            )
        returns_arr = clean_returns.values  # shape (n_days, n_assets)
        index = clean_returns.index

        def _stick_breaking(x: np.ndarray) -> np.ndarray:
            """Map x ∈ [0,1]^N to simplex weights via stick-breaking.

            w_1 = x_1
            w_k = x_k * (1 - sum(w_1..w_{k-1}))

            Guarantees: all w_k >= 0, sum(w) <= 1. Cash = 1 - sum(w).
            """
            w = np.zeros(n)
            remaining = 1.0
            for k in range(n):
                w[k] = x[k] * remaining
                remaining -= w[k]
            return w

        def _portfolio_returns(x: np.ndarray) -> pd.Series:
            """Map x to simplex weights, compute portfolio return."""
            w = _stick_breaking(x)
            port = (returns_arr * w).sum(axis=1)
            return pd.Series(port, index=index)

        results: list[OptimalWeights] = []
        for obj in self.objectives:
            def _fun(x, _obj=obj):
                port = _portfolio_returns(x)
                try:
                    return _obj.evaluate(port, baseline_metrics)
                except Exception:
                    return math.inf

            try:
                res = differential_evolution(
                    _fun,
                    bounds,
                    seed=self.seed,
                    maxiter=self.max_iter,
                    popsize=self.pop_size,
                    tol=self.tol,
                )
            except Exception as e:
                results.append(OptimalWeights(
                    objective_name=obj.name,
                    weights={label: 0.0 for label in labels},
                    is_metrics={},
                    optimizer_status=f"infeasible (DE crashed: {type(e).__name__})",
                ))
                continue

            # Codex round-6 P2: check res.success to distinguish
            # converged / max_iter / infeasible. The previous code
            # only checked res.fun >= 1e9, so max_iter was mis-tagged
            # as "converged".
            if res.fun >= 1e9 or not math.isfinite(res.fun):
                results.append(OptimalWeights(
                    objective_name=obj.name,
                    weights={label: 0.0 for label in labels},
                    is_metrics={},
                    optimizer_status="infeasible",
                ))
                continue

            if not res.success:
                status = "max_iter" if "iteration" in str(res.message).lower() else "infeasible"
            else:
                status = "converged"

            simplex_w = _stick_breaking(np.asarray(res.x))
            weights_dict = dict(zip(labels, [float(wi) for wi in simplex_w]))
            port = _portfolio_returns(np.asarray(res.x))
            is_metrics = compute_basic_metrics(port) or {}

            results.append(OptimalWeights(
                objective_name=obj.name,
                weights=weights_dict,
                is_metrics=is_metrics,
                optimizer_status=status,
            ))

        return results
