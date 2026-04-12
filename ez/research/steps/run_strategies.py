"""RunStrategiesStep: run a list of single-stock strategies, collect returns + metrics.

Reads:
  - artifacts['universe_data']: dict[symbol → DataFrame] (from DataLoadStep)

Writes:
  - artifacts['returns']: pd.DataFrame indexed by date, columns = strategy labels
  - artifacts['metrics']: dict[label → dict[metric_name → value]]
  - artifacts['equity_curves']: dict[label → pd.Series]
  - artifacts['run_strategies_skipped']: list[(label, reason)] (only on partial failure)

V2.20.0 MVP scope: single-stock strategies only. Portfolio strategies
will be handled by a separate ``RunPortfolioStep`` in V2.20.x.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from ..pipeline import ResearchStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


class RunStrategiesStep(ResearchStep):
    name = "run_strategies"
    writes = ("returns", "metrics", "equity_curves")

    def __init__(
        self,
        strategies: dict[str, Any],
        initial_capital: float = 1_000_000.0,
        label_map: dict[str, str] | None = None,
    ):
        """
        Parameters
        ----------
        strategies : dict[label → Strategy instance]
            The label is the column name in the resulting returns frame.
        initial_capital : float
            Per-strategy starting capital. Default 1e6.
        label_map : dict[label → symbol], optional
            V2.20.1 P3-2 follow-up: maps each label to the
            ``universe_data`` symbol key. When not provided, the label
            IS the symbol key (V2.20.0 behavior).

            Example: ``label_map={"A": "EtfRotateCombo", "E": "511010.SH", "F": "518880.SH"}``
            makes the output returns DataFrame have columns
            ``["A", "E", "F"]`` while fetching data from symbols
            ``["EtfRotateCombo", "511010.SH", "518880.SH"]``.
        """
        if not strategies:
            raise ValueError("RunStrategiesStep requires at least one strategy")
        self.strategies = dict(strategies)
        self.initial_capital = float(initial_capital)
        self.label_map = dict(label_map) if label_map else None

    def _run_one(self, df: pd.DataFrame, strategy) -> tuple[pd.Series, dict, pd.Series]:
        """Run a single backtest. Returns (daily_returns, metrics, equity_curve).

        Lazy import keeps ez.research importable without ez.backtest
        being touched at module-load time.
        """
        from ez.backtest.engine import VectorizedBacktestEngine
        engine = VectorizedBacktestEngine()
        result = engine.run(df, strategy, self.initial_capital)
        return result.daily_returns, dict(result.metrics), result.equity_curve

    def run(self, context: PipelineContext) -> PipelineContext:
        # Codex round-3 P2-5: clear stale skipped artifact from any
        # prior run before doing work.
        context.artifacts.pop("run_strategies_skipped", None)

        ud = context.require("universe_data")
        returns_dict: dict[str, pd.Series] = {}
        metrics_dict: dict[str, dict] = {}
        equity_dict: dict[str, pd.Series] = {}
        skipped: list[tuple[str, str]] = []

        for label, strategy in self.strategies.items():
            # V2.20.1: label_map allows label != symbol
            symbol = (self.label_map.get(label, label) if self.label_map else label)
            if symbol not in ud:
                skipped.append((label, f"symbol '{symbol}' not in universe_data"))
                continue
            try:
                rets, metrics, equity = self._run_one(ud[symbol], strategy)
            except Exception as e:
                skipped.append((label, f"{type(e).__name__}: {e}"))
                continue
            returns_dict[label] = rets
            metrics_dict[label] = metrics
            equity_dict[label] = equity

        if not returns_dict:
            raise RuntimeError(
                f"RunStrategiesStep: no strategies ran successfully. "
                f"Skipped: {skipped}"
            )

        # Align all returns to a common date index, merge with existing
        returns_df = pd.DataFrame(returns_dict)
        existing_returns = context.artifacts.get("returns")
        if existing_returns is not None and isinstance(existing_returns, pd.DataFrame):
            # I2 review fix: drop duplicate columns before join to avoid
            # pandas suffixing (Alpha_x / Alpha_y) which breaks downstream.
            overlap = set(returns_df.columns) & set(existing_returns.columns)
            if overlap:
                logger.warning(
                    "RunStrategiesStep: labels %s already exist in returns — "
                    "overwriting. Use distinct labels to avoid data loss.",
                    sorted(overlap),
                )
                existing_returns = existing_returns.drop(columns=list(overlap))
            context.artifacts["returns"] = existing_returns.join(returns_df, how="outer")
        else:
            context.artifacts["returns"] = returns_df

        existing_metrics = context.artifacts.get("metrics")
        if isinstance(existing_metrics, dict):
            existing_metrics.update(metrics_dict)
            metrics_dict = existing_metrics
        context.artifacts["metrics"] = metrics_dict

        existing_eq = context.artifacts.get("equity_curves")
        if isinstance(existing_eq, dict):
            existing_eq.update(equity_dict)
            equity_dict = existing_eq
        context.artifacts["equity_curves"] = equity_dict
        if skipped:
            context.artifacts["run_strategies_skipped"] = skipped
        return context
