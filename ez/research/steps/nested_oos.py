"""NestedOOSStep: IS optimize → OOS validate → baseline compare.

Replaces ``validation/phase_o_nested_oos.py``'s 150-line ``main()``
(Phase O.1 + O.2 sections) with a single ResearchStep that:

1. Slices the upstream ``returns`` DataFrame into IS and OOS windows
2. Computes baseline metrics on IS for epsilon-constraint resolution
3. Calls ``optimizer.optimize(is_returns, baseline_metrics)`` to get
   a list of candidate weight sets (one per objective)
4. Evaluates each candidate on the OOS window (portfolio return =
   weighted sum of asset returns)
5. Evaluates the baseline weight set on both IS and OOS for comparison
6. Writes all results into ``artifacts['nested_oos_results']``

The step does NOT do its own data loading or strategy execution — it
consumes the ``returns`` artifact produced by ``RunStrategiesStep`` or
by the user manually. This decoupling keeps the step testable on
synthetic data without touching the network or the backtest engine.

Reads:
  artifacts['returns']: pd.DataFrame[date × asset_label]

Writes:
  artifacts['nested_oos_results']: dict with structure documented below

V2.20.1 — first real-world test of the ez.research framework.
"""
from __future__ import annotations
from datetime import date
from typing import Optional

import pandas as pd

from ..pipeline import ResearchStep
from ..context import PipelineContext
from ..optimizers.base import Optimizer
from .._metrics import compute_basic_metrics


class NestedOOSStep(ResearchStep):
    """Nested OOS validation: IS optimize → OOS evaluate → baseline compare.

    Writes ``artifacts['nested_oos_results']``:

    .. code-block:: python

        {
            "is_window": ("2015-01-01", "2019-12-31"),
            "oos_window": ("2020-01-01", "2024-12-31"),
            "candidates": [
                {
                    "objective": "Max Calmar",
                    "weights": {"A": 0.48, "E": 0.28, "F": 0.24},
                    "is_metrics": {"ret": ..., "sharpe": ..., ...},
                    "oos_metrics": {"ret": ..., "sharpe": ..., ...},
                    "status": "converged",
                },
                ...
            ],
            "baseline_weights": {"A": 0.70, "E": 0.15, "F": 0.15},
            "baseline_is": {"ret": ..., "sharpe": ..., ...},
            "baseline_oos": {"ret": ..., "sharpe": ..., ...},
        }
    """

    name = "nested_oos"
    writes = ("nested_oos_results",)

    def __init__(
        self,
        is_window: tuple[str | date, str | date],
        oos_window: tuple[str | date, str | date],
        optimizer: Optimizer,
        baseline_weights: Optional[dict[str, float]] = None,
        baseline_label: Optional[str] = None,
    ):
        if not is_window or len(is_window) != 2:
            raise ValueError("is_window must be (start, end)")
        if not oos_window or len(oos_window) != 2:
            raise ValueError("oos_window must be (start, end)")
        if not isinstance(optimizer, Optimizer):
            raise TypeError(
                f"optimizer must be an Optimizer instance, got {type(optimizer).__name__}"
            )
        self.is_window = is_window
        self.oos_window = oos_window
        self.optimizer = optimizer
        self.baseline_weights = dict(baseline_weights) if baseline_weights else None
        self.baseline_label = baseline_label

    @staticmethod
    def _slice(df: pd.DataFrame, window: tuple) -> pd.DataFrame:
        start = pd.Timestamp(window[0])
        end = pd.Timestamp(window[1])
        return df.loc[(df.index >= start) & (df.index <= end)]

    @staticmethod
    def _portfolio_returns(returns: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
        """Weighted sum of asset returns → single portfolio return Series."""
        total = pd.Series(0.0, index=returns.index, dtype=float)
        for label, w in weights.items():
            if label in returns.columns:
                total = total + returns[label] * w
        return total

    def _validate_no_overlap(self):
        is_end = pd.Timestamp(self.is_window[1])
        oos_start = pd.Timestamp(self.oos_window[0])
        if oos_start <= is_end:
            raise ValueError(
                f"NestedOOSStep: OOS window starts {oos_start.date()} which is "
                f"<= IS end {is_end.date()}. OOS must be strictly after IS "
                f"to prevent data leakage."
            )

    def run(self, context: PipelineContext) -> PipelineContext:
        # Clear stale artifact from prior run
        context.artifacts.pop("nested_oos_results", None)

        self._validate_no_overlap()
        returns = context.require("returns")

        if not isinstance(returns, pd.DataFrame):
            raise TypeError(
                f"NestedOOSStep: 'returns' artifact must be pd.DataFrame, "
                f"got {type(returns).__name__}"
            )

        # Slice IS / OOS
        is_returns = self._slice(returns, self.is_window)
        if is_returns.empty or len(is_returns) < 2:
            raise RuntimeError(
                f"NestedOOSStep: IS window {self.is_window} produced "
                f"{len(is_returns)} rows (need >= 2). "
                f"Returns range: {returns.index.min().date()} to "
                f"{returns.index.max().date()}"
            )
        oos_returns = self._slice(returns, self.oos_window)
        if oos_returns.empty or len(oos_returns) < 2:
            raise RuntimeError(
                f"NestedOOSStep: OOS window {self.oos_window} produced "
                f"{len(oos_returns)} rows (need >= 2)."
            )

        # Baseline metrics on IS — for EpsilonConstraint resolution
        baseline_metrics = None
        if self.baseline_label:
            if self.baseline_label not in is_returns.columns:
                raise ValueError(
                    f"baseline_label '{self.baseline_label}' not in returns "
                    f"columns: {list(is_returns.columns)}"
                )
            baseline_metrics = compute_basic_metrics(is_returns[self.baseline_label])

        # IS optimize
        candidates = self.optimizer.optimize(is_returns, baseline_metrics)

        # OOS validate each candidate
        validated = []
        for cand in candidates:
            port_oos = self._portfolio_returns(oos_returns, cand.weights)
            oos_metrics = compute_basic_metrics(port_oos) or {}
            validated.append({
                "objective": cand.objective_name,
                "weights": dict(cand.weights),
                "is_metrics": dict(cand.is_metrics),
                "oos_metrics": oos_metrics,
                "status": cand.optimizer_status,
            })

        # Baseline on IS + OOS for reference
        baseline_is_m = None
        baseline_oos_m = None
        if self.baseline_weights:
            missing = set(self.baseline_weights) - set(returns.columns)
            if missing:
                raise ValueError(
                    f"baseline_weights references unknown labels: {missing}. "
                    f"Available: {list(returns.columns)}"
                )
            port_is = self._portfolio_returns(is_returns, self.baseline_weights)
            port_oos = self._portfolio_returns(oos_returns, self.baseline_weights)
            baseline_is_m = compute_basic_metrics(port_is)
            baseline_oos_m = compute_basic_metrics(port_oos)

        context.artifacts["nested_oos_results"] = {
            "is_window": (str(self.is_window[0]), str(self.is_window[1])),
            "oos_window": (str(self.oos_window[0]), str(self.oos_window[1])),
            "candidates": validated,
            "baseline_weights": self.baseline_weights,
            "baseline_is": baseline_is_m,
            "baseline_oos": baseline_oos_m,
        }
        return context
