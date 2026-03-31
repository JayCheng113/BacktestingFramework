"""Walk-Forward robustness validation.

[CORE] -- V1: fixed-parameter validation. V2 adds parameter optimization.

IMPORTANT: Each split's data is strictly non-overlapping to prevent data leakage.
The engine handles warmup internally — if a split is too short after warmup,
the engine returns a minimal empty result (this is correct behavior).
"""
from __future__ import annotations

import pandas as pd

from ez.backtest.engine import VectorizedBacktestEngine
from ez.strategy.base import Strategy
from ez.types import BacktestResult, WalkForwardResult


class WalkForwardValidator:
    """Split data into rolling train/test windows and measure OOS degradation.

    Data layout for n_splits=3, train_ratio=0.7:
    |---train70%---|--test30%--|---train70%---|--test30%--|---train70%---|--test30%--|
    ^              ^           ^              ^           ^              ^
    Split 0                   Split 1                   Split 2

    NO data overlap between IS and OOS, or between adjacent splits.
    Each split is passed to the engine as-is. The engine computes factor warmup
    internally and trims it. If a split is too short, it returns 0 trades.
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
        if n_splits < 2:
            raise ValueError(f"n_splits must be >= 2, got {n_splits}")
        if not (0.0 < train_ratio < 1.0):
            raise ValueError(f"train_ratio must be in (0, 1), got {train_ratio}")
        n = len(data)
        window_size = n // n_splits

        # Each split needs enough bars for the engine to produce meaningful results
        warmup = 0
        for factor in strategy.required_factors():
            warmup = max(warmup, factor.warmup_period)

        min_tradeable = 10
        test_size = window_size - int(window_size * train_ratio)
        if test_size < warmup + min_tradeable:
            max_splits = n // (int((warmup + min_tradeable) / (1 - train_ratio)) + 1)
            raise ValueError(
                f"OOS window too short for {n_splits} splits: each test has {test_size} bars "
                f"but strategy needs {warmup} warmup + {min_tradeable} tradeable = {warmup + min_tradeable}. "
                f"Try n_splits<={max(1, max_splits)} or use a shorter-warmup strategy."
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

            # Strictly non-overlapping: IS = [window_start, train_end), OOS = [train_end, test_end)
            train_data = data.iloc[window_start:train_end]
            test_data = data.iloc[train_end:test_end]

            if len(test_data) < min_tradeable:
                continue

            # Engine handles warmup internally — short data → empty result (no trades)
            is_result = self._engine.run(train_data, strategy, initial_capital)
            is_sharpes.append(is_result.metrics.get("sharpe_ratio", 0.0))

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
