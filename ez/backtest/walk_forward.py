"""Walk-Forward robustness validation.

[CORE] -- V1: fixed-parameter validation. V2 adds parameter optimization.
"""
from __future__ import annotations

import pandas as pd

from ez.backtest.engine import VectorizedBacktestEngine
from ez.strategy.base import Strategy
from ez.types import BacktestResult, WalkForwardResult


class WalkForwardValidator:
    """Split data into rolling train/test windows and measure OOS degradation."""

    def __init__(self, engine: VectorizedBacktestEngine | None = None):
        self._engine = engine or VectorizedBacktestEngine()

    def validate(
        self,
        data: pd.DataFrame,
        strategy: Strategy,
        n_splits: int = 5,
        train_ratio: float = 0.7,
        initial_capital: float = 100000.0,
    ) -> WalkForwardResult:
        n = len(data)
        window_size = n // n_splits
        if window_size < 20:
            raise ValueError(
                f"Not enough data for {n_splits} splits "
                f"(need {n_splits * 20} bars, got {n})"
            )

        train_size = int(window_size * train_ratio)
        splits: list[BacktestResult] = []
        oos_equities: list[pd.Series] = []
        is_sharpes: list[float] = []
        oos_sharpes: list[float] = []

        for i in range(n_splits):
            start = i * window_size
            train_end = start + train_size
            test_end = min(start + window_size, n)
            if test_end > n:
                break

            train_data = data.iloc[start:train_end]
            test_data = data.iloc[train_end:test_end]
            if len(test_data) < 5:
                continue

            # In-sample
            is_result = self._engine.run(train_data, strategy, initial_capital)
            is_sharpes.append(is_result.metrics.get("sharpe_ratio", 0.0))

            # Out-of-sample
            oos_result = self._engine.run(test_data, strategy, initial_capital)
            oos_sharpes.append(oos_result.metrics.get("sharpe_ratio", 0.0))

            splits.append(oos_result)
            oos_equities.append(oos_result.equity_curve)

        # Combine OOS equity curves
        oos_equity = (
            pd.concat(oos_equities, ignore_index=True)
            if oos_equities
            else pd.Series([initial_capital])
        )

        # OOS aggregate metrics
        oos_metrics: dict[str, float] = {}
        if oos_sharpes:
            oos_metrics["sharpe_ratio"] = sum(oos_sharpes) / len(oos_sharpes)

        # Degradation: how much worse is OOS vs IS
        is_mean = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
        oos_mean = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0.0
        degradation = (
            (is_mean - oos_mean) / abs(is_mean) if abs(is_mean) > 1e-10 else 0.0
        )

        return WalkForwardResult(
            splits=splits,
            oos_equity_curve=oos_equity,
            oos_metrics=oos_metrics,
            is_vs_oos_degradation=degradation,
            overfitting_score=max(0.0, degradation),
        )
