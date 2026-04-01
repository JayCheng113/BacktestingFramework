"""Contract test for PortfolioOptimizer ABC — any implementation must pass these.

Add new optimizer implementations to `all_optimizers()` — contract tests auto-validate.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.optimizer import (
    PortfolioOptimizer, MeanVarianceOptimizer, MinVarianceOptimizer,
    RiskParityOptimizer, OptimizationConstraints,
)


def _make_universe(n_symbols=10, n_days=100, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    data = {}
    for i in range(n_symbols):
        sym = f"S{i:03d}"
        prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.01 * (i + 1), n_days))
        data[sym] = pd.DataFrame({
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n_days),
        }, index=dates)
    return data


def all_optimizers() -> list[PortfolioOptimizer]:
    c = OptimizationConstraints(max_weight=0.30, max_industry_weight=0.50)
    return [
        MeanVarianceOptimizer(risk_aversion=1.0, constraints=c, cov_lookback=60),
        MeanVarianceOptimizer(risk_aversion=0.1, constraints=c, cov_lookback=60),
        MinVarianceOptimizer(constraints=c, cov_lookback=60),
        RiskParityOptimizer(constraints=c, cov_lookback=60),
    ]


@pytest.fixture(params=all_optimizers(), ids=lambda o: f"{type(o).__name__}(λ={getattr(o,'risk_aversion','N/A')})")
def optimizer(request):
    return request.param


@pytest.fixture
def universe():
    return _make_universe()


@pytest.fixture
def context_date():
    return date(2023, 7, 1)


class TestPortfolioOptimizerContract:
    """Invariants that ANY PortfolioOptimizer implementation must satisfy."""

    def test_optimize_returns_dict(self, optimizer, universe, context_date):
        optimizer.set_context(context_date, universe)
        result = optimizer.optimize({f"S{i:03d}": 0.1 for i in range(10)})
        assert isinstance(result, dict)

    def test_all_weights_non_negative(self, optimizer, universe, context_date):
        """Long-only constraint: all weights >= 0."""
        optimizer.set_context(context_date, universe)
        result = optimizer.optimize({f"S{i:03d}": 0.1 for i in range(10)})
        for sym, w in result.items():
            assert w >= -1e-9, f"{sym} weight {w} < 0"

    def test_weights_sum_le_one(self, optimizer, universe, context_date):
        optimizer.set_context(context_date, universe)
        result = optimizer.optimize({f"S{i:03d}": 0.1 for i in range(10)})
        total = sum(result.values())
        assert total <= 1.0 + 1e-5, f"Sum {total} > 1.0"

    def test_max_weight_respected(self, optimizer, universe, context_date):
        """No single weight exceeds max_weight constraint."""
        optimizer.set_context(context_date, universe)
        result = optimizer.optimize({f"S{i:03d}": 0.5 for i in range(10)})
        max_w = optimizer._constraints.max_weight
        for sym, w in result.items():
            assert w <= max_w + 1e-4, f"{sym} weight {w} > max_weight {max_w}"

    def test_empty_alpha_returns_empty(self, optimizer):
        result = optimizer.optimize({})
        assert result == {}

    def test_single_stock_handled(self, optimizer):
        result = optimizer.optimize({"A": 1.0})
        assert isinstance(result, dict)
        assert len(result) <= 1

    def test_all_negative_alpha_returns_empty(self, optimizer):
        """Negative alpha signals should be filtered (w > 0 check)."""
        result = optimizer.optimize({"A": -0.5, "B": -0.3})
        assert result == {}

    def test_no_context_uses_fallback(self, optimizer):
        """Without set_context, should fallback to equal weight."""
        result = optimizer.optimize({"A": 0.5, "B": 0.3, "C": 0.2})
        assert isinstance(result, dict)
        assert all(w >= 0 for w in result.values())

    def test_deterministic_with_same_input(self, optimizer, universe, context_date):
        """Same input → same output (no randomness in optimization)."""
        optimizer.set_context(context_date, universe)
        alpha = {f"S{i:03d}": (i + 1) * 0.1 for i in range(5)}
        r1 = optimizer.optimize(alpha)
        optimizer.set_context(context_date, universe)
        r2 = optimizer.optimize(alpha)
        for sym in r1:
            assert abs(r1[sym] - r2.get(sym, 0)) < 1e-8, f"{sym}: {r1[sym]} != {r2.get(sym)}"

    def test_with_benchmark_weights(self, optimizer, universe, context_date):
        """benchmark_weights param should not crash optimization."""
        optimizer._benchmark_weights = {f"S{i:03d}": 0.1 for i in range(10)}
        optimizer._max_te = 0.10
        optimizer.set_context(context_date, universe)
        result = optimizer.optimize({f"S{i:03d}": 0.1 for i in range(10)})
        assert isinstance(result, dict)
        assert all(w >= -1e-9 for w in result.values())
        # Cleanup
        optimizer._benchmark_weights = None
        optimizer._max_te = None
