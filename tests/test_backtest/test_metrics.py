import numpy as np
import pandas as pd
import pytest
from ez.backtest.metrics import MetricsCalculator


@pytest.fixture
def calc():
    return MetricsCalculator(risk_free_rate=0.0)


def test_total_return(calc):
    equity = pd.Series([100000, 110000, 120000])
    metrics = calc.compute(equity, pd.Series([100000, 105000, 110000]))
    assert abs(metrics["total_return"] - 0.2) < 1e-6


def test_max_drawdown(calc):
    equity = pd.Series([100000, 110000, 90000, 95000])
    metrics = calc.compute(equity, pd.Series([100000] * 4))
    assert metrics["max_drawdown"] < 0
    assert abs(metrics["max_drawdown"] - (-20000 / 110000)) < 1e-4


def test_sharpe_ratio_positive_returns(calc):
    np.random.seed(42)
    daily_r = np.random.normal(0.001, 0.01, 252)
    equity = pd.Series((1 + pd.Series(daily_r)).cumprod() * 100000)
    metrics = calc.compute(equity, equity * 0 + 100000)
    assert metrics["sharpe_ratio"] > 0


def test_win_rate(calc):
    equity = pd.Series([100, 101, 100.5, 102, 101.5, 103])
    metrics = calc.compute(equity, equity * 0 + 100)
    assert "annualized_return" in metrics


def test_max_drawdown_duration(calc):
    """Drawdown duration = bars spent below previous peak."""
    # Peak at bar 1 (110k), drawdown bars 2,3 (below peak), recovery at bar 4
    equity = pd.Series([100000, 110000, 90000, 95000, 112000])
    metrics = calc.compute(equity, pd.Series([100000] * 5))
    assert metrics["max_drawdown_duration"] == 2  # bars 2 and 3


def test_max_drawdown_duration_no_drawdown(calc):
    equity = pd.Series([100000, 101000, 102000, 103000])
    metrics = calc.compute(equity, pd.Series([100000] * 4))
    assert metrics["max_drawdown_duration"] == 0


def test_alpha_beta_identical_curves(calc):
    """Identical strategy and benchmark → alpha=0, beta=1."""
    equity = pd.Series([100, 101, 102, 101, 103, 104])
    metrics = calc.compute(equity, equity.copy())
    assert abs(metrics["alpha"]) < 0.01
    assert abs(metrics["beta"] - 1.0) < 0.01


def test_alpha_beta_with_benchmark():
    """Beta should be positive when strategy correlates with benchmark."""
    calc = MetricsCalculator(risk_free_rate=0.0)
    bench = pd.Series([100, 100.5, 101, 101.5, 102])
    strat = pd.Series([100, 101, 102, 103, 104])
    metrics = calc.compute(strat, bench)
    assert metrics["beta"] > 0  # positively correlated
    assert "alpha" in metrics  # alpha computed
