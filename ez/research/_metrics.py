"""Optimizer-friendly metric computation.

Thin wrapper around ``ez.backtest.metrics.MetricsCalculator`` that:

1. Accepts a daily-returns Series (most natural input for an optimizer)
   instead of an equity curve.
2. Returns short-form keys (``ret``, ``sharpe``, ``dd``, ``calmar``,
   ``mdd_abs``) instead of long-form (``annualized_return``,
   ``sharpe_ratio``, ``max_drawdown``). The optimizer's objective
   functions read these keys directly, so short names reduce noise.
3. Adds Calmar (not in MetricsCalculator) as ``ret / mdd_abs``.
4. Adds CVaR via a separate function — also not in MetricsCalculator.

The underlying formulas come from MetricsCalculator (V2.12.2 unified
sharpe/sortino with ``ddof=1``), so V2.19.0/V2.20.0 metric semantics
flow through unchanged. Only the input/output shapes are different.
"""
from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd

from ez.backtest.metrics import MetricsCalculator

# Module-level singleton — MetricsCalculator is stateless beyond
# (risk_free_rate, trading_days), so a single instance is fine for
# all optimizer calls within a process.
_calculator = MetricsCalculator()


def compute_basic_metrics(returns: pd.Series) -> Optional[dict[str, float]]:
    """Convert a daily-returns Series to a short-key metrics dict.

    Returns None when the input is too small or pathological:
      - empty Series
      - Series with < 2 observations
      - returns that produce a non-positive equity curve
        (loss > 100%, which would crash the annualized return formula)

    On success the dict has these keys:
      - ``ret``       : annualized return (decimal, e.g. 0.12 for 12%)
      - ``sharpe``    : Sharpe ratio (V2.12.2 standard formula, ddof=1)
      - ``sortino``   : Sortino ratio (V2.12.2 standard formula)
      - ``vol``       : annualized volatility
      - ``dd``        : max drawdown (negative number, e.g. -0.18)
      - ``mdd_abs``   : abs(dd), convenient for epsilon-constraints
      - ``calmar``    : ret / mdd_abs (zero if mdd_abs is too small)

    The benchmark-dependent metrics from MetricsCalculator (alpha, beta,
    benchmark_return) are intentionally NOT exposed — the optimizer
    operates on absolute portfolio metrics, not relative ones.
    """
    if returns is None or len(returns) < 2:
        return None

    cleaned = returns.dropna()
    if len(cleaned) < 2:
        return None

    # Build the equity curve. Start at 1.0 so the curve units don't
    # matter. Use fillna(0) — any NaN return is treated as no change.
    equity = (1.0 + cleaned).cumprod()
    if equity.iloc[-1] <= 0:
        # Total loss > 100%; MetricsCalculator's annualized return
        # formula would produce NaN or complex.
        return None

    # Use a flat benchmark so alpha/beta are zero (we don't expose them).
    flat_benchmark = pd.Series(
        [1.0] * len(equity), index=equity.index
    )

    metrics = _calculator.compute(equity, flat_benchmark)

    ret = float(metrics["annualized_return"])
    dd = float(metrics["max_drawdown"])  # negative
    mdd_abs = abs(dd)
    calmar = ret / mdd_abs if mdd_abs > 1e-10 else 0.0

    return {
        "ret": ret,
        "sharpe": float(metrics["sharpe_ratio"]),
        "sortino": float(metrics["sortino_ratio"]),
        "vol": float(metrics["annualized_volatility"]),
        "dd": dd,
        "mdd_abs": mdd_abs,
        "calmar": float(calmar),
    }


def compute_cvar(returns: pd.Series, alpha: float = 0.05) -> Optional[float]:
    """Conditional VaR at the lower-tail ``alpha`` quantile.

    CVaR(α) = E[ R | R ≤ Q_α(R) ], where Q_α is the α-quantile.
    For α=0.05, CVaR is the average of the worst 5% of daily returns.
    The result is **typically negative** (a loss tail mean).

    Returns None for inputs with fewer than 10 observations — the
    quantile estimate is too unstable on tiny samples.

    Parameters
    ----------
    returns : pd.Series
        Daily returns. NaN values are dropped.
    alpha : float, default 0.05
        Tail probability. Must be in (0, 1).
    """
    if returns is None:
        return None
    if not (0 < alpha < 1):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    arr = returns.dropna().to_numpy()
    if len(arr) < 10:
        return None
    threshold = float(np.percentile(arr, alpha * 100))
    tail = arr[arr <= threshold]
    if len(tail) == 0:
        return float(threshold)
    return float(tail.mean())
