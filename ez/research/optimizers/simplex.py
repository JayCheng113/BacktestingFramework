"""SimplexMultiObjectiveOptimizer: differential_evolution wrapper for
long-only portfolio weight optimization on a probability simplex.

The simplex constraint is:
  - All weights >= 0 (long-only A-share)
  - sum(weights) <= 1.0 (residual is implicit cash)

For an N-asset portfolio, we optimize over N-1 free weights and compute
the N-th as ``max(1 - sum(others), 0)``. The bounds for differential_evolution
are ``[(0, 1)] * (N-1)``, but the simplex constraint is enforced inside the
objective function (returning inf for any infeasible point).

Multi-objective: one ``optimize()`` call iterates over the optimizer's
``objectives`` list and runs differential_evolution per objective. Returns
``list[OptimalWeights]``, one per objective.

Reference: ``validation/phase_o_nested_oos.py::optimize_on_window`` is the
hand-rolled equivalent that this class replaces.
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
        # We optimize over the first n-1 weights; the n-th is computed
        # as max(1 - sum, 0).
        bounds = [(0.0, 1.0)] * (n - 1)

        # Drop NaN rows and convert to numpy for inner-loop performance
        clean_returns = returns.dropna()
        if len(clean_returns) < 2:
            raise ValueError(
                f"SimplexMultiObjectiveOptimizer: returns has < 2 valid rows after NaN drop"
            )
        returns_arr = clean_returns.values  # shape (n_days, n_assets)
        index = clean_returns.index

        def _portfolio_returns(w_partial: np.ndarray) -> pd.Series:
            """Build the daily portfolio return Series for partial weights."""
            wf = max(1.0 - float(sum(w_partial)), 0.0)
            full_w = np.append(np.asarray(w_partial, dtype=float), wf)
            port = (returns_arr * full_w).sum(axis=1)
            return pd.Series(port, index=index)

        def _is_feasible(w_partial: np.ndarray) -> bool:
            if any(wi < -1e-9 for wi in w_partial):
                return False
            if sum(w_partial) > 1.0 + 1e-9:
                return False
            return True

        results: list[OptimalWeights] = []
        for obj in self.objectives:
            def _fun(w, _obj=obj):
                if not _is_feasible(w):
                    return math.inf
                port = _portfolio_returns(w)
                try:
                    return _obj.evaluate(port, baseline_metrics)
                except Exception:
                    # Defensive: never let an objective bug crash the
                    # whole optimizer. Treat as infeasible.
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

            if res.fun >= 1e9 or not math.isfinite(res.fun):
                # No feasible solution found
                results.append(OptimalWeights(
                    objective_name=obj.name,
                    weights={label: 0.0 for label in labels},
                    is_metrics={},
                    optimizer_status="infeasible",
                ))
                continue

            w_partial = list(res.x)
            wf = max(1.0 - sum(w_partial), 0.0)
            weights_dict = dict(zip(labels, w_partial + [wf]))

            # Compute IS metrics for the converged weights
            port = _portfolio_returns(np.asarray(w_partial))
            is_metrics = compute_basic_metrics(port) or {}

            results.append(OptimalWeights(
                objective_name=obj.name,
                weights=weights_dict,
                is_metrics=is_metrics,
                optimizer_status="converged",
            ))

        return results
