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
from typing import Any

import pandas as pd

from ..pipeline import ResearchStep
from ..context import PipelineContext


class RunStrategiesStep(ResearchStep):
    name = "run_strategies"
    writes = ("returns", "metrics", "equity_curves")

    def __init__(
        self,
        strategies: dict[str, Any],
        initial_capital: float = 1_000_000.0,
    ):
        """
        Parameters
        ----------
        strategies : dict[label → Strategy instance]
            The label is the column name in the resulting returns frame
            AND the symbol key into ``universe_data``. If you want a
            different label than the symbol, set the strategy's symbol
            internally and use a custom label here — but in that case
            also pass ``symbol_for_label`` mapping (V2.20.x).
        initial_capital : float
            Per-strategy starting capital. Default 1e6.
        """
        if not strategies:
            raise ValueError("RunStrategiesStep requires at least one strategy")
        self.strategies = dict(strategies)
        self.initial_capital = float(initial_capital)

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
        ud = context.require("universe_data")
        returns_dict: dict[str, pd.Series] = {}
        metrics_dict: dict[str, dict] = {}
        equity_dict: dict[str, pd.Series] = {}
        skipped: list[tuple[str, str]] = []

        for label, strategy in self.strategies.items():
            if label not in ud:
                skipped.append((label, f"symbol '{label}' not in universe_data"))
                continue
            try:
                rets, metrics, equity = self._run_one(ud[label], strategy)
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

        # Align all returns to a common date index
        returns_df = pd.DataFrame(returns_dict)
        context.artifacts["returns"] = returns_df
        context.artifacts["metrics"] = metrics_dict
        context.artifacts["equity_curves"] = equity_dict
        if skipped:
            context.artifacts["run_strategies_skipped"] = skipped
        return context
