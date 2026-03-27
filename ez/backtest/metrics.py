"""Performance metrics calculation.

[CORE] — append-only. New metrics can be added, existing must not change formula.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class MetricsCalculator:
    """Compute standard backtest performance metrics."""

    def __init__(self, risk_free_rate: float = 0.03, trading_days: int = 252):
        self._rf = risk_free_rate
        self._td = trading_days

    def compute(
        self, equity_curve: pd.Series, benchmark_curve: pd.Series,
    ) -> dict[str, float]:
        daily_returns = equity_curve.pct_change().dropna()
        bench_returns = benchmark_curve.pct_change().dropna()

        total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
        n_days = len(daily_returns)
        years = n_days / self._td if n_days > 0 else 1

        ann_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0
        ann_vol = float(daily_returns.std() * np.sqrt(self._td))

        daily_rf = self._rf / self._td
        excess = daily_returns - daily_rf
        sharpe = float(excess.mean() / excess.std() * np.sqrt(self._td)) if excess.std() > 1e-10 else 0.0

        downside = excess[excess < 0]
        sortino = float(excess.mean() / downside.std() * np.sqrt(self._td)) if len(downside) > 0 and downside.std() > 1e-10 else 0.0

        running_max = equity_curve.cummax()
        drawdown = (equity_curve - running_max) / running_max
        max_dd = float(drawdown.min())

        bench_total = (benchmark_curve.iloc[-1] / benchmark_curve.iloc[0]) - 1

        return {
            "total_return": float(total_return),
            "annualized_return": float(ann_return),
            "annualized_volatility": ann_vol,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_dd,
            "benchmark_return": float(bench_total),
            "trading_days": n_days,
        }
