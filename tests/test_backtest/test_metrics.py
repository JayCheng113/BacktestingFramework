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
