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
from typing import Any, Optional

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


def deflated_sharpe_ratio(
    returns: pd.Series,
    n_trials: int = 1,
    sr_benchmark: float = 0.0,
) -> Optional[dict[str, float]]:
    """Deflated Sharpe Ratio (de Prado, 2014).

    Adjusts observed Sharpe for:
    1. Number of trials tested (multiple testing penalty)
    2. Non-normality (skewness and kurtosis)
    3. Sample size

    Returns the probability that the true Sharpe exceeds `sr_benchmark`.
    A DSR close to 1.0 means the strategy is very likely to have genuine
    alpha; close to 0.5 means the observed Sharpe is likely lucky noise.

    Formula:
        DSR = Φ( (SR - SR_0) × √(n-1) /
                 √(1 - skew × SR + (kurt - 1) / 4 × SR²) )

    where SR_0 = sr_benchmark + expected max SR under null with n_trials.
    If n_trials > 1, we apply a Bonferroni-like adjustment:
        SR_0 ≈ sr_benchmark + √(2 × log(n_trials))

    Parameters
    ----------
    returns : pd.Series
        Daily returns.
    n_trials : int
        Number of strategies / parameter combinations tested. 1 = no
        multiple-testing adjustment.
    sr_benchmark : float
        Null-hypothesis Sharpe (typically 0.0 for "alpha > 0" test).

    Returns
    -------
    dict with keys:
        sharpe : float — observed annualized Sharpe
        deflated_sharpe : float — DSR probability (0 to 1)
        expected_max_sr : float — SR_0 threshold adjusted for n_trials
        skew, kurt : float — daily return moments
    None if insufficient data.
    """
    arr = returns.dropna().to_numpy()
    n = len(arr)
    if n < 30:
        return None

    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    if std < 1e-12:
        return None

    # Daily Sharpe → annualized
    sharpe_daily = mean / std
    sharpe_annual = sharpe_daily * np.sqrt(252)

    # Higher moments (on daily returns)
    centered = arr - mean
    skew = float(np.mean(centered**3) / std**3) if std > 0 else 0.0
    # Kurtosis (non-excess, normal = 3). For DSR we need EXCESS kurtosis
    # (γ₄ = kurt - 3) per Bailey & de Prado (2014) Eq. 9.
    kurt = float(np.mean(centered**4) / std**4) if std > 0 else 3.0
    excess_kurt = kurt - 3.0

    # Expected max Sharpe under null (Bailey & de Prado 2014 Eq. 10):
    #   E[max SR_N] ≈ (1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))
    # where γ = 0.5772 is Euler-Mascheroni. This is the Gumbel-based
    # expected maximum of N iid standard normal samples.
    # Review I1 fix: prior version used `sqrt((1-γ)·2·log N)` which was
    # neither the full formula nor the standard `sqrt(2 log N)` approx,
    # and was inconsistent with `minimum_backtest_length` below.
    if n_trials > 1:
        from math import e as math_e
        from scipy.stats import norm
        gamma_euler = 0.5772156649
        # All daily-scale (SR units); annualize at the end via sqrt(252).
        max_sr_daily_null = (
            (1 - gamma_euler) * float(norm.ppf(1 - 1 / n_trials))
            + gamma_euler * float(norm.ppf(1 - 1 / (n_trials * math_e)))
        )
        # sr_benchmark is annualized — convert to daily before adding.
        expected_max_daily = sr_benchmark / np.sqrt(252) + max_sr_daily_null / np.sqrt(252)
    else:
        expected_max_daily = sr_benchmark / np.sqrt(252)

    expected_max_annual = expected_max_daily * np.sqrt(252)

    # DSR denominator (Bailey & de Prado 2014 Eq. 9):
    #   √(1 - skew × SR + (γ₄ / 4) × SR²)   where γ₄ = excess kurtosis
    denom_sq = 1.0 - skew * sharpe_daily + (excess_kurt / 4.0) * sharpe_daily**2
    if denom_sq <= 0:
        # Pathological moments — denominator undefined
        return {
            "sharpe": sharpe_annual,
            "deflated_sharpe": 0.0,
            "expected_max_sr": expected_max_annual,
            "skew": skew,
            "kurt": kurt,
            "excess_kurt": excess_kurt,
            "warning": "DSR undefined: denominator non-positive due to extreme moments",
        }

    # Daily-frame test statistic
    z = (sharpe_daily - expected_max_daily) * np.sqrt(n - 1) / np.sqrt(denom_sq)

    # Standard normal CDF
    from math import erf
    dsr = 0.5 * (1 + erf(z / np.sqrt(2)))

    return {
        "sharpe": float(sharpe_annual),
        "deflated_sharpe": float(dsr),
        "expected_max_sr": float(expected_max_annual),
        "skew": float(skew),
        "kurt": float(kurt),
        "excess_kurt": float(excess_kurt),
    }


def minimum_backtest_length(
    sharpe: float,
    alpha: float = 0.05,
    n_trials: int = 1,
) -> Optional[float]:
    """Minimum Backtest Length (de Prado).

    Minimum years of data needed for an observed Sharpe to be
    statistically significant at confidence level (1 - alpha), given
    n_trials has been searched.

    Formula (simplified, assumes normal returns):
        MinBTL ≈ (z_alpha / SR)² × (1 + adj_for_trials) / trading_days

    where adj_for_trials accounts for multiple-testing:
        SR_threshold = SR × √(1 - (1-γ) + γ×√(2 log n_trials) / SR)

    For n_trials=1, MinBTL ≈ (z_alpha / SR)² years (simplified).

    Parameters
    ----------
    sharpe : float
        Annualized Sharpe ratio (observed).
    alpha : float
        Significance level (default 0.05 → 95% confidence).
    n_trials : int
        Number of strategies tested.

    Returns
    -------
    Minimum backtest length in years. None if sharpe <= 0 (no valid
    MinBTL for unprofitable strategies).
    """
    if sharpe <= 0:
        return None

    # One-sided critical value
    from scipy.stats import norm
    z_alpha = float(norm.ppf(1 - alpha))

    # Basic MinBTL (no multiple-testing)
    years_basic = (z_alpha / sharpe) ** 2

    if n_trials > 1:
        # Adjust for multiple testing (Bonferroni-like):
        # require SR > √(2 log N) under null
        multiple_adj = np.sqrt(2 * np.log(n_trials))
        if sharpe <= multiple_adj:
            return None  # not enough evidence regardless of length
        # Conservative adjustment
        return float(years_basic * (1 + multiple_adj / sharpe))

    return float(years_basic)


def annual_breakdown(
    returns: pd.Series,
    min_days: int = 5,
) -> dict[str, Any]:
    """Split a daily-returns Series by calendar year and compute per-year metrics.

    Parameters
    ----------
    returns : pd.Series
        Daily returns with DatetimeIndex.
    min_days : int, default 5
        Minimum days required for a year to be included. Short partial
        years (e.g. backtest ending mid-January) are dropped.

    Returns a dict with:
        per_year : list[dict] — per-year {year, sharpe, ret, mdd, n_days}
        worst_year : int | None — year with lowest Sharpe
        best_year : int | None — year with highest Sharpe
        profitable_ratio : float — fraction of years with POSITIVE RETURN
            (year_return > 0)
        consistency_score : float — fraction of years with POSITIVE SHARPE
            (year_sharpe > 0) — distinct from profitable_ratio: a year
            with positive return but sharpe <= 0 (high volatility eating
            the return) counts toward profitable_ratio but NOT consistency
    """
    empty = {
        "per_year": [],
        "worst_year": None,
        "best_year": None,
        "profitable_ratio": 0.0,
        "consistency_score": 0.0,
    }
    clean = returns.dropna()
    if len(clean) == 0 or not isinstance(clean.index, pd.DatetimeIndex):
        return empty

    per_year: list[dict[str, Any]] = []
    grouped = clean.groupby(clean.index.year)
    for year, group in grouped:
        if len(group) < min_days:
            continue  # skip tiny partial years
        metrics = compute_basic_metrics(group)
        if metrics is None:
            continue
        per_year.append({
            "year": int(year),
            "sharpe": metrics["sharpe"],
            "ret": metrics["ret"],
            "mdd": metrics["dd"],
            "n_days": len(group),
        })

    if not per_year:
        return empty

    sharpes = [y["sharpe"] for y in per_year]
    worst_idx = int(np.argmin(sharpes))
    best_idx = int(np.argmax(sharpes))
    n_profitable = sum(1 for y in per_year if y["ret"] > 0)
    n_positive_sharpe = sum(1 for s in sharpes if s > 0)

    return {
        "per_year": per_year,
        "worst_year": per_year[worst_idx]["year"],
        "best_year": per_year[best_idx]["year"],
        "profitable_ratio": n_profitable / len(per_year),
        "consistency_score": n_positive_sharpe / len(sharpes),
    }
