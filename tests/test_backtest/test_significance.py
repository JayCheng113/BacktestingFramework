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


def test_signal_permutation_path():
    """When signals + asset_returns provided, permute signals not returns."""
    np.random.seed(42)
    n = 200
    # Construct a strategy where timing matters: signals perfectly predict direction
    asset_returns = pd.Series(np.random.normal(0, 0.01, n))
    signals = pd.Series((asset_returns > 0).astype(float))  # perfect timing
    # Strategy returns = signal * asset_return (always positive when signal=1, zero when signal=0)
    strategy_returns = signals * asset_returns

    result = compute_significance(
        strategy_returns, signals=signals, asset_returns=asset_returns,
        n_bootstrap=200, n_permutations=500, seed=42,
    )
    # Perfect timing strategy should have low p-value (shuffling signals destroys edge)
    assert result.monte_carlo_p_value < 0.1
    assert result.sharpe_ci_lower > 0


def test_signal_permutation_no_edge():
    """Random signals should produce high p-value even with signal permutation."""
    np.random.seed(123)
    n = 200
    asset_returns = pd.Series(np.random.normal(0, 0.01, n))
    signals = pd.Series(np.random.choice([0.0, 1.0], size=n))  # random timing
    strategy_returns = signals * asset_returns

    result = compute_significance(
        strategy_returns, signals=signals, asset_returns=asset_returns,
        n_bootstrap=200, n_permutations=500, seed=42,
    )
    # Random signals → high p-value
    assert result.monte_carlo_p_value > 0.05
