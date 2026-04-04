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


def test_ic_decay_monotonic_for_lagged_predictor(evaluator):
    """Factor with true forward-looking signal at lag p=1 must show IC decay:
    ic_decay[1] > ic_decay[5] when the factor actually leads returns by 1 period.

    Construction: returns[t] = factor[t-1] * strong + noise. Then the "forward_returns"
    input (indexed by prediction time t) correlates with factor[t] at lag 0 (contemporaneous
    via ic_mean), and the ic_decay shift-based lookup weakens with horizon because longer
    shifts introduce more independent noise into the correlation.
    """
    rng = np.random.default_rng(42)
    n = 500
    factor = pd.Series(rng.standard_normal(n))
    # Make returns[t] predicted by factor[t-1]: factor leads by 1
    # But ic_decay uses forward_returns.shift(-p) vs factor[t]; if forward_returns[t] =
    # factor[t-1] * k, then shift(-1)[t] = factor[t] * k → perfect corr at p=1.
    forward_returns = pd.Series(
        np.r_[0.0, factor.values[:-1]] * 0.03 + rng.standard_normal(n) * 0.001
    )
    result = evaluator.evaluate(factor, forward_returns, periods=[1, 5, 10])
    # Sanity: all decay values finite
    for p, v in result.ic_decay.items():
        assert np.isfinite(v), f"ic_decay[{p}] not finite: {v}"
    # True decay: p=1 captures the lead → strongest; p=5/10 weaker
    assert result.ic_decay[1] > result.ic_decay[5], (
        f"Expected monotone decay but ic_decay[1]={result.ic_decay[1]:.3f} "
        f"<= ic_decay[5]={result.ic_decay[5]:.3f}"
    )
    assert abs(result.ic_decay[1]) > 0.3, (
        f"Lead-1 signal should produce measurable decay[1] IC, got {result.ic_decay[1]:.3f}"
    )


def test_constant_factor_returns_all_zero_metrics(evaluator):
    """A constant factor has zero variance → ALL IC metrics must be exactly 0.0 (not NaN).

    This is a regression test for the V2.12.1 post-review fix: previously ic_mean,
    rank_ic_mean, and turnover leaked NaN to FactorAnalysis, which then corrupted JSON
    serialization. The fix adds _nan_safe() sanitization at every metric output point.
    """
    n = 100
    factor = pd.Series([1.0] * n)  # constant — no information
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.standard_normal(n) * 0.01)
    result = evaluator.evaluate(factor, returns, periods=[1, 5])
    # ALL metrics must be exactly 0.0 — no NaN leakage anywhere in FactorAnalysis
    assert result.ic_mean == 0.0, f"ic_mean leaked NaN/non-zero: {result.ic_mean}"
    assert result.rank_ic_mean == 0.0, f"rank_ic_mean leaked: {result.rank_ic_mean}"
    assert result.icir == 0.0
    assert result.rank_icir == 0.0
    assert result.turnover == 0.0, f"turnover leaked NaN: {result.turnover}"
    for p, v in result.ic_decay.items():
        assert v == 0.0, f"ic_decay[{p}] leaked: {v}"


def test_constant_returns_do_not_crash(evaluator):
    """Constant returns (zero-variance target) should also produce all-zero metrics."""
    rng = np.random.default_rng(7)
    n = 100
    factor = pd.Series(rng.standard_normal(n))
    returns = pd.Series([0.01] * n)  # constant returns
    result = evaluator.evaluate(factor, returns, periods=[1])
    # Zero variance in either side → correlation undefined → zero metrics
    assert result.ic_mean == 0.0
    assert result.rank_ic_mean == 0.0
    assert result.icir == 0.0
    assert result.ic_decay[1] == 0.0


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
