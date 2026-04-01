"""Contract test for PortfolioStrategy ABC — any implementation must pass these.

Add new strategy implementations to `all_strategies()` — contract tests auto-validate.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.cross_factor import MomentumRank, VolumeRank
from ez.portfolio.portfolio_strategy import (
    PortfolioStrategy, TopNRotation, MultiFactorRotation,
)


def _make_universe(n_symbols=10, n_days=100, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    data = {}
    for i in range(n_symbols):
        sym = f"S{i:03d}"
        prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.02, n_days))
        data[sym] = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n_days),
        }, index=dates)
    return data


def all_strategies() -> list[PortfolioStrategy]:
    return [
        TopNRotation(MomentumRank(20), top_n=3),
        TopNRotation(VolumeRank(20), top_n=5),
        MultiFactorRotation(factors=[MomentumRank(20), VolumeRank(20)], top_n=3),
    ]


@pytest.fixture(params=all_strategies(), ids=lambda s: type(s).__name__)
def strategy(request):
    return request.param


@pytest.fixture
def universe():
    return _make_universe()


class TestPortfolioStrategyContract:
    """Invariants that ANY PortfolioStrategy implementation must satisfy."""

    def test_has_lookback_days(self, strategy):
        assert isinstance(strategy.lookback_days, int)
        assert strategy.lookback_days > 0

    def test_generate_weights_returns_dict(self, strategy, universe):
        dt = datetime(2023, 5, 15)
        weights = strategy.generate_weights(universe, dt, {}, {})
        assert isinstance(weights, dict)

    def test_weights_are_non_negative(self, strategy, universe):
        """Long-only: all weights >= 0."""
        dt = datetime(2023, 5, 15)
        weights = strategy.generate_weights(universe, dt, {}, {})
        for sym, w in weights.items():
            assert w >= -1e-9, f"{sym} weight {w} < 0"

    def test_weights_sum_le_one(self, strategy, universe):
        """Total weight <= 1.0 (remainder is cash)."""
        dt = datetime(2023, 5, 15)
        weights = strategy.generate_weights(universe, dt, {}, {})
        total = sum(weights.values())
        assert total <= 1.0 + 1e-6, f"Weights sum {total} > 1.0"

    def test_weights_keys_are_strings(self, strategy, universe):
        dt = datetime(2023, 5, 15)
        weights = strategy.generate_weights(universe, dt, {}, {})
        for key in weights:
            assert isinstance(key, str)

    def test_weights_symbols_in_universe(self, strategy, universe):
        """Returned symbols must be from the input universe."""
        dt = datetime(2023, 5, 15)
        weights = strategy.generate_weights(universe, dt, {}, {})
        for sym in weights:
            assert sym in universe, f"Symbol {sym} not in universe"

    def test_empty_universe_returns_empty(self, strategy):
        dt = datetime(2023, 5, 15)
        weights = strategy.generate_weights({}, dt, {}, {})
        assert len(weights) == 0

    def test_stateful_prev_weights_accepted(self, strategy, universe):
        """Strategy should accept prev_weights and prev_returns without error."""
        dt = datetime(2023, 5, 15)
        prev_w = {"S000": 0.3, "S001": 0.3, "S002": 0.4}
        prev_r = {"S000": 0.02, "S001": -0.01, "S002": 0.05}
        weights = strategy.generate_weights(universe, dt, prev_w, prev_r)
        assert isinstance(weights, dict)
