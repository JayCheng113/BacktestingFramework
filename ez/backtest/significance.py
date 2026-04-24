"""Statistical significance testing for backtest results.

[CORE] — interface frozen.

Tests whether a strategy's performance is statistically significant by:
1. Bootstrap CI: resample daily returns to estimate Sharpe confidence interval
2. Monte Carlo permutation: shuffle SIGNALS (not returns) to test if timing adds value
   - If randomly-timed signals produce similar Sharpe → strategy has no edge
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ez.types import SignificanceTest


def compute_significance(
    daily_returns: pd.Series,
    risk_free_rate: float = 0.03,
    n_bootstrap: int = 1000,
    n_permutations: int = 1000,
    seed: int | None = None,
    signals: pd.Series | None = None,
    asset_returns: pd.Series | None = None,
) -> SignificanceTest:
    """Bootstrap CI for Sharpe + Monte Carlo signal permutation test.

    Args:
        daily_returns: Strategy daily returns (for Bootstrap CI).
        signals: Position weight signals (for Monte Carlo permutation).
        asset_returns: Underlying asset daily returns (for Monte Carlo permutation).
        seed: RNG seed. None for true randomness, int for reproducibility (tests).

    Monte Carlo approach:
        If signals and asset_returns are provided, shuffle the signal ordering
        and recompute strategy returns as shuffled_signal * asset_return.
        This tests whether the strategy's TIMING adds value beyond random entry/exit.
        If not provided, falls back to permuting returns directly.
    """
    returns = daily_returns.dropna().values
    if len(returns) < 20:
        return SignificanceTest(
            sharpe_ci_lower=0.0, sharpe_ci_upper=0.0,
            monte_carlo_p_value=1.0, is_significant=False,
        )

    daily_rf = risk_free_rate / 252
    observed_sharpe = _sharpe(returns, daily_rf)

    rng = np.random.default_rng(seed)

    # 1. Bootstrap CI for Sharpe (vectorized: one matrix op, no Python loop)
    n_ret = len(returns)
    boot_indices = rng.integers(0, n_ret, size=(n_bootstrap, n_ret))
    boot_samples = returns[boot_indices]  # (n_bootstrap, n_ret)
    boot_excess = boot_samples - daily_rf
    boot_means = boot_excess.mean(axis=1)
    boot_stds = boot_excess.std(axis=1, ddof=1)
    boot_sharpes = np.where(boot_stds > 1e-10, boot_means / boot_stds * np.sqrt(252), 0.0)
    ci_lower = float(np.percentile(boot_sharpes, 2.5))
    ci_upper = float(np.percentile(boot_sharpes, 97.5))

    # 2. Monte Carlo: permute signals (preferred) or returns (fallback)
    if signals is not None and asset_returns is not None:
        sig_vals = signals.dropna().values
        ar_vals = asset_returns.reindex(signals.index).fillna(0.0).values
        n = min(len(sig_vals), len(ar_vals))
        sig_vals = sig_vals[:n]
        ar_vals = ar_vals[:n]

        if np.std(sig_vals) < 1e-10:
            return SignificanceTest(
                sharpe_ci_lower=ci_lower, sharpe_ci_upper=ci_upper,
                monte_carlo_p_value=1.0, is_significant=False,
            )

        # Vectorized: generate all permutation indices at once
        perm_indices = np.array([rng.permutation(n) for _ in range(n_permutations)])
        perm_signals = sig_vals[perm_indices]          # (n_perm, n)
        perm_returns = perm_signals * ar_vals           # (n_perm, n)
        perm_excess = perm_returns - daily_rf
        perm_means = perm_excess.mean(axis=1)
        perm_stds = perm_excess.std(axis=1, ddof=1)
        perm_sharpes = np.where(perm_stds > 1e-10, perm_means / perm_stds * np.sqrt(252), 0.0)
    else:
        perm_indices = np.array([rng.permutation(n_ret) for _ in range(n_permutations)])
        perm_samples = returns[perm_indices]
        perm_excess = perm_samples - daily_rf
        perm_means = perm_excess.mean(axis=1)
        perm_stds = perm_excess.std(axis=1, ddof=1)
        perm_sharpes = np.where(perm_stds > 1e-10, perm_means / perm_stds * np.sqrt(252), 0.0)

    p_value = float(np.mean(perm_sharpes >= observed_sharpe))

    return SignificanceTest(
        sharpe_ci_lower=ci_lower,
        sharpe_ci_upper=ci_upper,
        monte_carlo_p_value=p_value,
        is_significant=p_value < 0.05,
    )


def _sharpe(returns: np.ndarray, daily_rf: float) -> float:
    # V2.12.1 reviewer round 5: ddof=1 to match ez/backtest/metrics.py and
    # ez/portfolio/engine.py. numpy ndarray.std() defaults to ddof=0 which
    # gave a displayed-Sharpe-vs-CI inconsistency on short OOS windows.
    excess = returns - daily_rf
    std = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
    if std < 1e-10:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(252))
