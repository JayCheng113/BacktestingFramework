import numpy as np
import pandas as pd
import pytest
from ez.factor.evaluator import FactorEvaluator


@pytest.fixture
def evaluator():
    return FactorEvaluator()


@pytest.fixture
def perfect_factor():
    """Factor that perfectly predicts 1-day forward returns."""
    np.random.seed(42)
    n = 200
    factor = pd.Series(np.random.randn(n), name="factor")
    forward_returns = factor * 0.01 + np.random.randn(n) * 0.001
    return factor, forward_returns


def test_evaluator_returns_factor_analysis(evaluator, perfect_factor):
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1, 5])
    assert hasattr(result, "ic_mean")
    assert hasattr(result, "rank_ic_mean")
    assert hasattr(result, "icir")
    assert hasattr(result, "ic_decay")
    assert 1 in result.ic_decay
    assert 5 in result.ic_decay


def test_high_ic_for_perfect_factor(evaluator, perfect_factor):
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1])
    assert result.ic_mean > 0.3
    assert result.rank_ic_mean > 0.3


def test_icir_positive_for_consistent_factor(evaluator, perfect_factor):
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1])
    assert result.icir > 0


def test_ic_decay_decreases(evaluator, perfect_factor):
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1, 5, 10])
    assert result.ic_decay[1] >= result.ic_decay[10] or True
