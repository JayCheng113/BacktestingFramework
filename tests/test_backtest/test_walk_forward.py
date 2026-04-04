import numpy as np
import pandas as pd
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
    small_df = pd.DataFrame({"adj_close": [1, 2, 3], "open": [1, 2, 3]})
    validator = WalkForwardValidator()
    strategy = MACrossStrategy(short_period=2, long_period=3)
    with pytest.raises(ValueError, match="OOS window too short|Not enough data"):
        validator.validate(small_df, strategy, n_splits=5)


def _make_df(n: int, seed: int = 0) -> pd.DataFrame:
    """Build a sample price dataframe with n rows."""
    rng = np.random.default_rng(seed)
    prices = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "adj_close": prices,
        "volume": rng.integers(100_000, 1_000_000, n).astype(float),
    }, index=dates)


class TestWalkForwardValidation:
    """Parameter validation: V2.10 added n_splits/train_ratio bounds."""

    def test_n_splits_below_2_rejected(self):
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(500)
        with pytest.raises(ValueError, match="n_splits must be >= 2"):
            validator.validate(df, strategy, n_splits=1)

    def test_n_splits_zero_rejected(self):
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(500)
        with pytest.raises(ValueError, match="n_splits must be >= 2"):
            validator.validate(df, strategy, n_splits=0)

    def test_train_ratio_zero_rejected(self):
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(500)
        with pytest.raises(ValueError, match="train_ratio must be in"):
            validator.validate(df, strategy, n_splits=3, train_ratio=0.0)

    def test_train_ratio_one_rejected(self):
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(500)
        with pytest.raises(ValueError, match="train_ratio must be in"):
            validator.validate(df, strategy, n_splits=3, train_ratio=1.0)

    def test_train_ratio_negative_rejected(self):
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(500)
        with pytest.raises(ValueError, match="train_ratio must be in"):
            validator.validate(df, strategy, n_splits=3, train_ratio=-0.1)


class TestWalkForwardDataIsolation:
    """Verify IS/OOS windows are strictly non-overlapping."""

    def test_splits_do_not_overlap_in_time(self):
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(500)
        result = validator.validate(df, strategy, n_splits=3, train_ratio=0.7)
        # Every split's equity_curve has a timestamp range; adjacent splits must not overlap
        assert len(result.splits) >= 2
        prev_end = None
        for split in result.splits:
            if hasattr(split, "equity_curve") and len(split.equity_curve) > 0:
                curr_start = split.equity_curve.index[0]
                if prev_end is not None:
                    assert curr_start >= prev_end, (
                        f"Split start {curr_start} overlaps previous end {prev_end}"
                    )
                prev_end = split.equity_curve.index[-1]

    def test_overfitting_score_is_finite(self):
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(500)
        result = validator.validate(df, strategy, n_splits=3)
        assert np.isfinite(result.overfitting_score)
        assert result.overfitting_score >= 0


class TestWalkForwardBoundary:
    """Edge cases: minimal viable data, many splits."""

    def test_minimum_n_splits_2(self):
        """n_splits=2 is the minimum allowed."""
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(400)
        result = validator.validate(df, strategy, n_splits=2)
        assert len(result.splits) >= 1

    def test_many_splits_with_sufficient_data(self):
        """n_splits=10 should succeed when data is long enough."""
        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        df = _make_df(2000)  # large enough for 10 splits
        result = validator.validate(df, strategy, n_splits=10)
        assert len(result.splits) >= 1

    def test_oos_too_short_gives_helpful_error(self):
        """Error message should suggest max_splits."""
        validator = WalkForwardValidator()
        # MACross 200 needs long warmup
        strategy = MACrossStrategy(short_period=50, long_period=200)
        df = _make_df(500)
        with pytest.raises(ValueError, match="Try n_splits"):
            validator.validate(df, strategy, n_splits=10)
