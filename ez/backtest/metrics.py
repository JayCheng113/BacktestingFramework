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

        # Guard: total_return < -1.0 would produce NaN with fractional exponent
        if total_return <= -1.0:
            ann_return = -1.0
        elif years > 0:
            ann_return = (1 + total_return) ** (1 / years) - 1
        else:
            ann_return = 0.0
        ann_vol = float(daily_returns.std() * np.sqrt(self._td))

        daily_rf = self._rf / self._td
        excess = daily_returns - daily_rf
        sharpe = float(excess.mean() / excess.std() * np.sqrt(self._td)) if excess.std() > 1e-10 else 0.0

        downside = excess[excess < 0]
        sortino = float(excess.mean() / downside.std() * np.sqrt(self._td)) if len(downside) > 0 and downside.std() > 1e-10 else 0.0

        running_max = equity_curve.cummax()
        drawdown = (equity_curve - running_max) / running_max
        max_dd = float(drawdown.min())

        # Max drawdown duration (bars in drawdown before recovery)
        in_drawdown = drawdown < 0
        if in_drawdown.any():
            groups = (~in_drawdown).cumsum()
            dd_durations = in_drawdown.groupby(groups).sum()
            max_dd_duration = int(dd_durations.max()) if len(dd_durations) > 0 else 0
        else:
            max_dd_duration = 0

        bench_total = (benchmark_curve.iloc[-1] / benchmark_curve.iloc[0]) - 1

        # Alpha & Beta (CAPM regression: R_strategy - Rf = alpha + beta * (R_bench - Rf))
        common = pd.concat([daily_returns, bench_returns], axis=1, keys=["s", "b"]).dropna()
        if len(common) > 1 and common["b"].std() > 1e-10:
            excess_s = common["s"] - daily_rf
            excess_b = common["b"] - daily_rf
            beta = float(excess_s.cov(excess_b) / excess_b.var())
            # Annualized alpha
            alpha = float((excess_s.mean() - beta * excess_b.mean()) * self._td)
        else:
            alpha, beta = 0.0, 0.0

        return {
            "total_return": float(total_return),
            "annualized_return": float(ann_return),
            "annualized_volatility": ann_vol,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "alpha": alpha,
            "beta": beta,
            "max_drawdown": max_dd,
            "max_drawdown_duration": max_dd_duration,
            "benchmark_return": float(bench_total),
            "trading_days": n_days,
        }
