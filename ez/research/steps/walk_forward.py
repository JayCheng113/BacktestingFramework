"""WalkForwardStep: rolling walk-forward weight optimization.

Rolling N-fold version of NestedOOSStep:
  For each fold k = 0..N-1:
    IS  = returns[fold_start : train_end]
    OOS = returns[train_end : fold_end]
    Optimize weights on IS → validate on OOS

Reads:
  - artifacts['returns']: pd.DataFrame[date × label] (from RunStrategiesStep
    or RunPortfolioStep)

Writes:
  - artifacts['walk_forward_results']: dict with per-fold and aggregate results

V2.20.3: replaces validation/phase_p_walk_forward.py pattern.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from ..pipeline import ResearchStep
from ..context import PipelineContext
from .._metrics import compute_basic_metrics

logger = logging.getLogger(__name__)


class WalkForwardStep(ResearchStep):
    """Rolling walk-forward optimization across N folds.

    Splits ``artifacts['returns']`` into N non-overlapping folds using
    integer-interval arithmetic (no tail loss). For each fold, optimizes
    portfolio weights on the IS window and validates on OOS.

    Aggregates by concatenating all OOS returns into a single series
    and recomputing metrics from the combined curve (not averaging
    per-fold Sharpes, per V2.12.2 lesson).
    """

    name = "walk_forward"
    writes = ("walk_forward_results",)

    def __init__(
        self,
        optimizer: Any,
        n_splits: int = 5,
        train_ratio: float = 0.80,
        baseline_weights: dict[str, float] | None = None,
        baseline_label: str | None = None,
    ):
        """
        Parameters
        ----------
        optimizer : Optimizer
            Already-configured optimizer (same as NestedOOSStep).
        n_splits : int
            Number of folds. Must be >= 2.
        train_ratio : float
            Fraction of each fold used for IS. Must be in (0, 1).
        baseline_weights : dict[str, float], optional
            Reference weight set for per-fold comparison.
        baseline_label : str, optional
            Single-asset label for epsilon-constraint baseline metrics.
        """
        if n_splits < 2:
            raise ValueError(f"WalkForwardStep requires n_splits >= 2, got {n_splits}")
        if not (0.0 < train_ratio < 1.0):
            raise ValueError(
                f"WalkForwardStep requires 0 < train_ratio < 1, got {train_ratio}"
            )

        self.optimizer = optimizer
        self.n_splits = n_splits
        self.train_ratio = train_ratio
        self.baseline_weights = dict(baseline_weights) if baseline_weights else None
        self.baseline_label = baseline_label

    def _compute_fold_boundaries(self, n_rows: int) -> list[tuple[int, int, int]]:
        """Compute (fold_start, train_end, fold_end) for each fold.

        Uses integer-interval arithmetic (same as ez/backtest/walk_forward.py)
        to prevent tail loss: last fold absorbs remainder.

        Returns list of (start_idx, train_end_idx, end_idx) — half-open [start, end).
        """
        boundaries = []
        for i in range(self.n_splits):
            fold_start = i * n_rows // self.n_splits
            fold_end = (i + 1) * n_rows // self.n_splits
            fold_size = fold_end - fold_start
            train_size = max(2, int(fold_size * self.train_ratio))
            train_end = fold_start + train_size
            # Ensure OOS has at least 2 rows
            if fold_end - train_end < 2:
                train_end = max(fold_start + 2, fold_end - 2)
            boundaries.append((fold_start, train_end, fold_end))
        return boundaries

    def _weighted_returns(
        self, returns: pd.DataFrame, weights: dict[str, float]
    ) -> pd.Series:
        """Compute weighted portfolio returns from a weights dict."""
        port = pd.Series(0.0, index=returns.index)
        for label, w in weights.items():
            if label in returns.columns:
                port = port + returns[label].fillna(0.0) * w
        return port

    def run(self, context: PipelineContext) -> PipelineContext:
        returns = context.require("returns")

        if not isinstance(returns, pd.DataFrame):
            raise TypeError(
                f"WalkForwardStep: 'returns' must be pd.DataFrame, "
                f"got {type(returns).__name__}"
            )

        # Drop rows where ALL columns are NaN (outer join gaps)
        clean_returns = returns.dropna(how="all")
        n_rows = len(clean_returns)

        if n_rows < self.n_splits * 4:
            raise RuntimeError(
                f"WalkForwardStep: insufficient data ({n_rows} rows) for "
                f"{self.n_splits} folds. Need at least {self.n_splits * 4} rows."
            )

        boundaries = self._compute_fold_boundaries(n_rows)
        folds_results = []
        all_oos_returns = []
        all_is_sharpes = []
        succeeded_fold_indices: set[int] = set()  # I3 review fix

        for fold_idx, (start, train_end, end) in enumerate(boundaries):
            is_data = clean_returns.iloc[start:train_end]
            oos_data = clean_returns.iloc[train_end:end]

            if len(is_data) < 2 or len(oos_data) < 2:
                logger.warning(
                    "WalkForwardStep fold %d: skipping (IS=%d, OOS=%d rows)",
                    fold_idx, len(is_data), len(oos_data),
                )
                continue

            # IS baseline metrics for epsilon-constraint resolution
            baseline_metrics = None
            if self.baseline_label and self.baseline_label in is_data.columns:
                baseline_metrics = compute_basic_metrics(
                    is_data[self.baseline_label].dropna()
                )

            # Optimize on IS
            try:
                candidates = self.optimizer.optimize(is_data, baseline_metrics)
            except Exception as e:
                logger.warning(
                    "WalkForwardStep fold %d: optimizer failed: %s", fold_idx, e
                )
                continue

            # Validate each candidate on OOS
            validated = []
            for cand in candidates:
                oos_port = self._weighted_returns(oos_data, cand.weights)
                oos_metrics = compute_basic_metrics(oos_port.dropna()) or {}
                validated.append({
                    "objective": cand.objective_name,
                    "weights": cand.weights,
                    "is_metrics": cand.is_metrics,
                    "oos_metrics": oos_metrics,
                    "status": cand.optimizer_status,
                })

            # IS aggregate sharpe (for degradation)
            best_cand = max(
                (c for c in candidates if c.is_feasible),
                key=lambda c: c.is_metrics.get("sharpe", float("-inf")),
                default=None,
            )
            if best_cand:
                is_sharpe = best_cand.is_metrics.get("sharpe", 0.0)
                all_is_sharpes.append(is_sharpe)
                # Use best candidate's weights for OOS aggregation
                oos_port = self._weighted_returns(oos_data, best_cand.weights)
                all_oos_returns.append(oos_port)
                succeeded_fold_indices.add(fold_idx)  # I3: track for baseline parity

            # Baseline per fold
            baseline_is = None
            baseline_oos = None
            if self.baseline_weights:
                is_port = self._weighted_returns(is_data, self.baseline_weights)
                baseline_is = compute_basic_metrics(is_port.dropna())
                oos_port_bl = self._weighted_returns(oos_data, self.baseline_weights)
                baseline_oos = compute_basic_metrics(oos_port_bl.dropna())

            is_window = (
                str(is_data.index[0].date() if hasattr(is_data.index[0], "date") else is_data.index[0]),
                str(is_data.index[-1].date() if hasattr(is_data.index[-1], "date") else is_data.index[-1]),
            )
            oos_window = (
                str(oos_data.index[0].date() if hasattr(oos_data.index[0], "date") else oos_data.index[0]),
                str(oos_data.index[-1].date() if hasattr(oos_data.index[-1], "date") else oos_data.index[-1]),
            )

            folds_results.append({
                "fold": fold_idx,
                "is_window": is_window,
                "oos_window": oos_window,
                "candidates": validated,
                "baseline_is": baseline_is,
                "baseline_oos": baseline_oos,
            })

        if not folds_results:
            raise RuntimeError(
                "WalkForwardStep: all folds failed — no valid results."
            )

        # Aggregate: concatenate OOS returns → recompute metrics
        aggregate = {}
        if all_oos_returns:
            combined_oos = pd.concat(all_oos_returns)
            combined_metrics = compute_basic_metrics(combined_oos.dropna())
            if combined_metrics:
                aggregate["oos_sharpe"] = combined_metrics["sharpe"]
                aggregate["oos_return"] = combined_metrics["ret"]
                aggregate["oos_vol"] = combined_metrics["vol"]
                aggregate["oos_mdd"] = combined_metrics["dd"]

            # IS average sharpe (for degradation computation)
            if all_is_sharpes:
                avg_is_sharpe = float(np.mean(all_is_sharpes))
                aggregate["avg_is_sharpe"] = avg_is_sharpe
                oos_sharpe = aggregate.get("oos_sharpe", 0.0)
                if abs(avg_is_sharpe) > 1e-10:
                    aggregate["degradation"] = (
                        (avg_is_sharpe - oos_sharpe) / abs(avg_is_sharpe)
                    )
                else:
                    aggregate["degradation"] = 0.0

        # Baseline aggregate: concatenate OOS returns and recompute.
        # I3 review fix: only include folds where the optimizer succeeded,
        # so baseline and optimized cover the exact same time windows.
        if self.baseline_weights and all_oos_returns:
            bl_oos_parts = []
            for fold_idx, (start, train_end, end) in enumerate(boundaries):
                if fold_idx not in succeeded_fold_indices:
                    continue  # skip folds where optimizer failed
                oos_data = clean_returns.iloc[train_end:end]
                if len(oos_data) >= 2:
                    bl_port = self._weighted_returns(oos_data, self.baseline_weights)
                    bl_oos_parts.append(bl_port)
            if bl_oos_parts:
                combined_bl_oos = pd.concat(bl_oos_parts)
                bl_metrics = compute_basic_metrics(combined_bl_oos.dropna())
                if bl_metrics:
                    aggregate["baseline_oos_sharpe"] = bl_metrics["sharpe"]
                    aggregate["baseline_oos_return"] = bl_metrics["ret"]

        # Clear stale artifact
        context.artifacts.pop("walk_forward_results", None)

        context.artifacts["walk_forward_results"] = {
            "n_splits": self.n_splits,
            "train_ratio": self.train_ratio,
            "n_folds_completed": len(folds_results),
            "folds": folds_results,
            "aggregate": aggregate,
        }
        return context
