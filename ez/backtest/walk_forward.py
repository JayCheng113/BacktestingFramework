"""Walk-Forward robustness validation.

[CORE] -- V1: fixed-parameter validation. V2 adds parameter optimization.
"""
from __future__ import annotations

import pandas as pd

from ez.backtest.engine import VectorizedBacktestEngine
from ez.strategy.base import Strategy
from ez.types import BacktestResult, WalkForwardResult


class WalkForwardValidator:
    """Split data into rolling train/test windows and measure OOS degradation.

    Each split prepends extra warmup data so the engine can compute factors
    before the actual train/test range begins. Without this, splits shorter
    than the strategy's warmup period produce zero trades.
    """

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

        # Determine warmup needed by strategy's factors
        warmup = 0
        for factor in strategy.required_factors():
            warmup = max(warmup, factor.warmup_period)

        # Each split needs: warmup + enough bars for actual trading
        min_tradeable = 10  # minimum bars after warmup to produce meaningful results
        min_window = warmup + min_tradeable
        window_size = n // n_splits

        if window_size < min_tradeable:
            raise ValueError(
                f"Not enough data for {n_splits} splits with warmup={warmup}: "
                f"each window has {window_size} bars, need at least {min_tradeable} tradeable bars. "
                f"Try fewer splits or more data."
            )

        train_size = int(window_size * train_ratio)
        splits: list[BacktestResult] = []
        oos_equities: list[pd.Series] = []
        is_sharpes: list[float] = []
        oos_sharpes: list[float] = []

        for i in range(n_splits):
            window_start = i * window_size
            train_end = window_start + train_size
            test_end = min(window_start + window_size, n)
            if test_end > n:
                break

            # Prepend warmup data from before the window start
            # This gives the engine enough history to compute factors
            warmup_start = max(0, window_start - warmup)

            train_data = data.iloc[warmup_start:train_end]
            test_data = data.iloc[max(0, train_end - warmup):test_end]

            if len(test_data) <= warmup:
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
