import pytest
from ez.backtest.walk_forward import WalkForwardValidator
from ez.strategy.builtin.ma_cross import MACrossStrategy


def test_walk_forward_runs(sample_df):
    validator = WalkForwardValidator()
    # Use short warmup strategy so splits are large enough
    strategy = MACrossStrategy(short_period=3, long_period=5)
    result = validator.validate(sample_df, strategy, n_splits=2)
    assert len(result.splits) > 0
    assert result.overfitting_score >= 0


def test_walk_forward_too_few_data():
    import pandas as pd

    small_df = pd.DataFrame({"adj_close": [1, 2, 3], "open": [1, 2, 3]})
    validator = WalkForwardValidator()
    strategy = MACrossStrategy(short_period=2, long_period=3)
    with pytest.raises(ValueError, match="OOS window too short|Not enough data"):
        validator.validate(small_df, strategy, n_splits=5)
