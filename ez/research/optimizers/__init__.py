"""Portfolio weight optimizers for the research pipeline (V2.20.1).

Public API:
  - ``OptimalWeights``: dataclass holding one optimization result
  - ``Objective``: abstract base for objective functions
  - ``MaxSharpe``, ``MaxCalmar``, ``MaxSortino``, ``MinCVaR``: built-in objectives
  - ``EpsilonConstraint``: epsilon-constraint optimization (V2.20.1 commit 3)
  - ``Optimizer``: abstract base for portfolio optimizers (V2.20.1 commit 4)
  - ``SimplexMultiObjectiveOptimizer``: differential_evolution wrapper (commit 4)

The optimizer layer is independent of ``ez.research.steps`` and can be
used standalone (e.g. in a Jupyter notebook to explore weight space)
without going through the pipeline framework.
"""
from .base import OptimalWeights, Objective, Optimizer
from .objectives import MaxSharpe, MaxCalmar, MaxSortino, MinCVaR
from .epsilon_constraint import EpsilonConstraint
from .simplex import SimplexMultiObjectiveOptimizer

__all__ = [
    "OptimalWeights",
    "Objective",
    "Optimizer",
    "MaxSharpe",
    "MaxCalmar",
    "MaxSortino",
    "MinCVaR",
    "EpsilonConstraint",
    "SimplexMultiObjectiveOptimizer",
]
