"""Statistical significance testing for backtest results.

[CORE] — interface frozen.
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
) -> SignificanceTest:
    """Bootstrap CI for Sharpe + Monte Carlo permutation test.

    Args:
        seed: RNG seed. None for true randomness, int for reproducibility (tests).
    """
    returns = daily_returns.dropna().values
    if len(returns) < 20:
        return SignificanceTest(
            sharpe_ci_lower=0.0, sharpe_ci_upper=0.0,
            monte_carlo_p_value=1.0, is_significant=False,
        )

    daily_rf = risk_free_rate / 252
    observed_sharpe = _sharpe(returns, daily_rf)

    # Bootstrap CI
    rng = np.random.default_rng(seed)
    boot_sharpes = np.array([
        _sharpe(rng.choice(returns, size=len(returns), replace=True), daily_rf)
        for _ in range(n_bootstrap)
    ])
    ci_lower = float(np.percentile(boot_sharpes, 2.5))
    ci_upper = float(np.percentile(boot_sharpes, 97.5))

    # Monte Carlo permutation
    perm_sharpes = np.array([
        _sharpe(rng.permutation(returns), daily_rf)
        for _ in range(n_permutations)
    ])
    p_value = float(np.mean(perm_sharpes >= observed_sharpe))

    return SignificanceTest(
        sharpe_ci_lower=ci_lower,
        sharpe_ci_upper=ci_upper,
        monte_carlo_p_value=p_value,
        is_significant=p_value < 0.05,
    )


def _sharpe(returns: np.ndarray, daily_rf: float) -> float:
    excess = returns - daily_rf
    std = excess.std()
    if std < 1e-10:
        return 0.0
    return float(excess.mean() / std * np.sqrt(252))
