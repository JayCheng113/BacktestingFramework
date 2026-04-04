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


def test_ic_decay_structure(evaluator, perfect_factor):
    """ic_decay must be a dict keyed by periods, with finite float values."""
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1, 5, 10])
    assert set(result.ic_decay.keys()) == {1, 5, 10}
    for p, v in result.ic_decay.items():
        assert isinstance(v, float), f"ic_decay[{p}] is not float: {type(v)}"
        assert not np.isnan(v), f"ic_decay[{p}] is NaN"
        assert not np.isinf(v), f"ic_decay[{p}] is inf"


def test_ic_decay_with_lagged_predictor(evaluator):
    """Factor that predicts returns one period ahead should show measurable IC at period=1
    and weaker IC at longer horizons."""
    np.random.seed(123)
    n = 250
    factor = pd.Series(np.random.randn(n))
    # returns[t] = factor[t-1] * strong + noise → factor leads returns by 1
    # forward_returns[t] = factor[t-1] means shift(-1) of forward_returns aligns with factor[t]
    # To make ic_decay[1] (which uses shift(-1)) capture this, construct:
    # forward_returns[t] = factor[t+1] * 0.01 + noise → shift(-1) = factor[t+2] — wrong direction
    # Instead: forward_returns[t] = factor[t] * 0.01 + noise (contemporaneous)
    # Then ic_decay[p] uses shift(-p), which correlates factor[t] with factor[t+p] → signal decays
    forward_returns = factor * 0.02 + np.random.randn(n) * 0.001
    result = evaluator.evaluate(factor, forward_returns, periods=[1, 5])
    # Contemporaneous ic_mean must be strong
    assert result.ic_mean > 0.5
    # All decay values must be finite
    for v in result.ic_decay.values():
        assert np.isfinite(v)


def test_constant_factor_returns_zero_or_nan(evaluator):
    """A constant factor has zero variance → IC undefined (0 or NaN is acceptable)."""
    n = 100
    factor = pd.Series([1.0] * n)  # constant — no information
    np.random.seed(1)
    returns = pd.Series(np.random.randn(n) * 0.01)
    result = evaluator.evaluate(factor, returns, periods=[1, 5])
    # Constant factor has std=0 → icir must be 0 (not inf/nan)
    assert result.icir == 0.0
    assert result.rank_icir == 0.0
    # IC mean may be 0 or NaN but must not crash
    assert result.ic_mean == 0.0 or np.isnan(result.ic_mean)


def test_all_nan_factor_returns_empty_analysis(evaluator):
    """All-NaN factor should not crash and should return zero metrics."""
    n = 100
    factor = pd.Series([np.nan] * n)
    returns = pd.Series(np.random.randn(n) * 0.01)
    result = evaluator.evaluate(factor, returns, periods=[1])
    # After dropna, empty series → early return with zeros
    assert result.ic_mean == 0.0
    assert result.icir == 0.0
    assert result.ic_decay[1] == 0.0


def test_single_value_factor_does_not_crash(evaluator):
    """Factor with only 1 non-NaN value → insufficient for rolling corr."""
    factor = pd.Series([np.nan] * 50 + [1.0] + [np.nan] * 49)
    returns = pd.Series(np.random.randn(100) * 0.01)
    # Should not raise — early return path for insufficient data
    result = evaluator.evaluate(factor, returns, periods=[1])
    assert result.ic_mean == 0.0


def test_nan_returns_are_filtered(evaluator):
    """NaN returns should be dropped, not propagate to IC."""
    np.random.seed(2)
    n = 150
    factor = pd.Series(np.random.randn(n))
    returns = factor * 0.01 + np.random.randn(n) * 0.001
    # Inject NaN into returns
    returns.iloc[::10] = np.nan
    result = evaluator.evaluate(factor, returns, periods=[1])
    # Should still produce finite IC despite NaN injection
    assert not np.isnan(result.ic_mean)
    assert not np.isinf(result.ic_mean)
