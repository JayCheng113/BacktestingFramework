"""Built-in objective functions for portfolio optimization (V2.20.1).

V2.20.1 commit 2 ships 4 simple objectives:
  - MaxSharpe
  - MaxCalmar
  - MaxSortino
  - MinCVaR (negative tail mean — minimize the loss)

EpsilonConstraint (commit 3) is in a separate module because it has
its own safe-eval machinery for constraint string parsing.
"""
from __future__ import annotations
import math
from typing import Optional

import pandas as pd

from .base import Objective
from .._metrics import compute_basic_metrics, compute_cvar


class MaxSharpe(Objective):
    """Maximize the Sharpe ratio. Returns -sharpe."""
    name = "Max Sharpe"

    def evaluate(self, port_returns, baseline_metrics=None):
        m = compute_basic_metrics(port_returns)
        if m is None:
            return math.inf
        return -m["sharpe"]


class MaxCalmar(Objective):
    """Maximize the Calmar ratio (annualized return / abs(max drawdown)).

    Returns inf when calmar is non-positive (matches the
    `validation/phase_o_nested_oos.py` convention — a strategy with
    negative annualized return is treated as infeasible for Max Calmar).
    """
    name = "Max Calmar"

    def evaluate(self, port_returns, baseline_metrics=None):
        m = compute_basic_metrics(port_returns)
        if m is None:
            return math.inf
        if m["calmar"] <= 0:
            return math.inf
        return -m["calmar"]


class MaxSortino(Objective):
    """Maximize the Sortino ratio."""
    name = "Max Sortino"

    def evaluate(self, port_returns, baseline_metrics=None):
        m = compute_basic_metrics(port_returns)
        if m is None:
            return math.inf
        return -m["sortino"]


class MinCVaR(Objective):
    """Minimize the loss tail (i.e., MAXIMIZE the CVaR value, which is
    typically a negative number). The objective is to make the worst
    α% of returns LESS bad — equivalent to making CVaR(α) closer to
    zero or even positive.

    A returns -CVaR (so the optimizer's minimization → CVaR maximization).
    """
    def __init__(self, alpha: float = 0.05):
        if not (0 < alpha < 1):
            raise ValueError(f"alpha must be in (0, 1), got {alpha}")
        self.alpha = alpha
        self.name = f"Min CVaR {int(alpha * 100)}%"

    def evaluate(self, port_returns, baseline_metrics=None):
        cvar = compute_cvar(port_returns, self.alpha)
        if cvar is None:
            return math.inf
        # CVaR is typically negative (loss). Maximizing it = minimizing
        # the tail loss. Optimizer minimizes, so return -CVaR.
        return -cvar
