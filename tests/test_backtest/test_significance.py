import numpy as np
import pandas as pd
from ez.backtest.significance import compute_significance


def test_significance_random_returns():
    """Random returns should not be significant."""
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0, 0.01, 252))
    result = compute_significance(returns, n_bootstrap=200, n_permutations=200, seed=42)
    assert result.monte_carlo_p_value > 0.01


def test_significance_strong_returns():
    """Strong positive returns should have positive bootstrap CI lower bound."""
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.002, 0.005, 252))
    result = compute_significance(returns, n_bootstrap=500, n_permutations=500, seed=42)
    assert result.sharpe_ci_lower > 0
    assert result.sharpe_ci_upper > result.sharpe_ci_lower
    # Monte Carlo permutation tests whether ordering matters (autocorrelation).
    # For i.i.d. returns, p-value is high since permutation preserves Sharpe.
    assert isinstance(result.monte_carlo_p_value, float)


def test_significance_too_few_data():
    returns = pd.Series([0.01, -0.01, 0.005])
    result = compute_significance(returns)
    assert result.is_significant is False
    assert result.monte_carlo_p_value == 1.0
