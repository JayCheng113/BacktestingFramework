"""Optimizer base types: OptimalWeights, Objective."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class OptimalWeights:
    """Single optimization result.

    Attributes
    ----------
    objective_name : str
        The label of the objective that produced this result. Stable
        for the same Objective class + same constructor args.
    weights : dict[str, float]
        ``{asset_label → weight}``. For long-only A-share simplex, all
        weights are >= 0 and sum to <= 1.0 (the residual is implicit
        cash). The optimizer guarantees the keys exactly match the
        columns of the input ``returns`` DataFrame, in the same order.
    is_metrics : dict[str, float]
        Metrics computed on the IS window for this weight set, in
        short-key form (see ``ez.research._metrics.compute_basic_metrics``).
        Empty dict if the optimization was infeasible.
    optimizer_status : str
        One of:
        - ``"converged"`` — optimizer found a feasible solution
        - ``"infeasible"`` — no feasible solution found (e.g., the
          epsilon-constraint had no satisfying weights)
        - ``"max_iter"`` — optimizer hit max iterations without
          converging (rare with differential_evolution)
    """
    objective_name: str
    weights: dict[str, float]
    is_metrics: dict[str, float] = field(default_factory=dict)
    optimizer_status: str = "converged"

    @property
    def is_feasible(self) -> bool:
        return self.optimizer_status == "converged"


class Objective(ABC):
    """Abstract base for an optimization objective.

    Subclasses define WHAT to optimize (and any constraints). The
    ``evaluate`` method receives the portfolio's daily-return Series
    plus an optional baseline metrics dict (for epsilon-constraint
    resolution against e.g. ``baseline_ret * 0.9``) and returns a
    scalar to MINIMIZE.

    Conventions:
      - Maximize-style objectives (Sharpe, Calmar, ...) return the
        negated metric, e.g. ``return -m["sharpe"]``.
      - Minimize-style objectives (CVaR-loss, MDD, ...) return the
        positive metric.
      - Infeasible solutions return ``float("inf")``. The optimizer
        treats inf as "do not pick this point".
      - The class-level ``name`` attribute is the user-visible label
        rendered in reports. Constructors that take parameters should
        compose the name string in ``__init__``.
    """
    name: str = "Objective"

    @abstractmethod
    def evaluate(
        self,
        port_returns: pd.Series,
        baseline_metrics: Optional[dict[str, float]] = None,
    ) -> float:
        """Lower is better. Return float('inf') for infeasible solutions."""
        raise NotImplementedError


class Optimizer(ABC):
    """Base class for portfolio weight optimizers.

    Subclasses implement ``optimize(returns, baseline_metrics) →
    list[OptimalWeights]``. Multi-objective is the default contract:
    one optimizer wraps multiple objectives and returns one result
    per objective.

    The optimizer DOES NOT slice the returns DataFrame — the caller
    (typically a NestedOOSStep or WalkForwardStep) is responsible
    for handing in just the IS window.
    """

    @abstractmethod
    def optimize(
        self,
        returns: pd.DataFrame,
        baseline_metrics: Optional[dict[str, float]] = None,
    ) -> list["OptimalWeights"]:
        raise NotImplementedError
