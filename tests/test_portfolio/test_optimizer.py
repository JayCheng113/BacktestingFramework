"""Tests for V2.12 portfolio optimizer."""
import numpy as np
import pandas as pd
import pytest


class TestLedoitWolfShrinkage:
    def test_basic_positive_definite(self):
        """Shrunk covariance must be positive definite."""
        from ez.portfolio.optimizer import ledoit_wolf_shrinkage
        rng = np.random.default_rng(42)
        returns = rng.normal(0, 0.02, (100, 5))
        sigma = ledoit_wolf_shrinkage(returns)
        assert sigma.shape == (5, 5)
        eigenvalues = np.linalg.eigvalsh(sigma)
        assert np.all(eigenvalues > 0), f"Not positive definite: {eigenvalues}"

    def test_wide_matrix_n_gt_t(self):
        """N > T: sample covariance is singular, shrinkage must fix it."""
        from ez.portfolio.optimizer import ledoit_wolf_shrinkage
        rng = np.random.default_rng(42)
        returns = rng.normal(0, 0.02, (10, 30))
        sigma = ledoit_wolf_shrinkage(returns)
        assert sigma.shape == (30, 30)
        eigenvalues = np.linalg.eigvalsh(sigma)
        assert np.all(eigenvalues > 0)

    def test_single_observation_fallback(self):
        """T < 2 should return identity-like fallback."""
        from ez.portfolio.optimizer import ledoit_wolf_shrinkage
        returns = np.array([[0.01, -0.02, 0.03]])
        sigma = ledoit_wolf_shrinkage(returns)
        assert sigma.shape == (3, 3)
        assert np.allclose(np.diag(sigma), 0.04, atol=0.001)

    def test_symmetry(self):
        from ez.portfolio.optimizer import ledoit_wolf_shrinkage
        rng = np.random.default_rng(42)
        returns = rng.normal(0, 0.02, (60, 8))
        sigma = ledoit_wolf_shrinkage(returns)
        assert np.allclose(sigma, sigma.T)


def _make_opt_data(symbols, n_days=100, seed=42):
    """Synthetic data for optimizer tests."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    data = {}
    for i, sym in enumerate(symbols):
        prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.01 * (i + 1), n_days))
        data[sym] = pd.DataFrame({
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n_days),
        }, index=dates)
    return data


class TestMeanVarianceOptimizer:
    def test_long_only_sum_one(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        from datetime import date
        symbols = [f"S{i}" for i in range(5)]
        data = _make_opt_data(symbols)
        opt = MeanVarianceOptimizer(
            risk_aversion=1.0,
            constraints=OptimizationConstraints(max_weight=0.40),
            cov_lookback=60,
        )
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.2 for s in symbols})
        assert all(w >= -1e-9 for w in result.values())
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_max_weight_respected(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        from datetime import date
        symbols = [f"S{i}" for i in range(10)]
        data = _make_opt_data(symbols)
        opt = MeanVarianceOptimizer(
            risk_aversion=0.5,
            constraints=OptimizationConstraints(max_weight=0.15),
            cov_lookback=60,
        )
        opt.set_context(date(2023, 7, 1), data)
        alpha = {s: (i + 1) * 0.1 for i, s in enumerate(symbols)}
        result = opt.optimize(alpha)
        for sym, w in result.items():
            assert w <= 0.15 + 1e-6, f"{sym} weight {w} exceeds max 0.15"

    def test_fallback_on_insufficient_data(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        from datetime import date
        symbols = ["A", "B", "C"]
        data = {s: pd.DataFrame({"close": [10.0], "adj_close": [10.0]},
                                index=pd.DatetimeIndex([date(2023, 7, 3)])) for s in symbols}
        opt = MeanVarianceOptimizer(constraints=OptimizationConstraints(max_weight=0.50), cov_lookback=60)
        opt.set_context(date(2023, 7, 3), data)
        result = opt.optimize({"A": 0.5, "B": 0.3, "C": 0.2})
        assert len(result) == 3

    def test_empty_returns_empty(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        opt = MeanVarianceOptimizer(constraints=OptimizationConstraints())
        assert opt.optimize({}) == {}

    def test_single_stock(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        opt = MeanVarianceOptimizer(constraints=OptimizationConstraints(max_weight=1.0))
        assert opt.optimize({"A": 1.0}) == {"A": 1.0}


class TestMinVarianceOptimizer:
    def test_long_only_sum_one(self):
        from ez.portfolio.optimizer import MinVarianceOptimizer, OptimizationConstraints
        from datetime import date
        symbols = [f"S{i}" for i in range(5)]
        data = _make_opt_data(symbols)
        opt = MinVarianceOptimizer(
            constraints=OptimizationConstraints(max_weight=0.40),
            cov_lookback=60,
        )
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.2 for s in symbols})
        assert all(w >= -1e-9 for w in result.values())
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_low_vol_gets_more_weight(self):
        """MinVariance should overweight low-vol stocks (5-stock test)."""
        from ez.portfolio.optimizer import MinVarianceOptimizer, OptimizationConstraints
        from datetime import date
        # S0 lowest vol, S4 highest vol (vol = 0.01*(i+1))
        symbols = [f"S{i}" for i in range(5)]
        data = _make_opt_data(symbols)
        opt = MinVarianceOptimizer(
            constraints=OptimizationConstraints(max_weight=0.50),
            cov_lookback=60,
        )
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.2 for s in symbols})
        # Lowest vol stock should have highest weight
        assert result.get("S0", 0) >= result.get("S4", 0)


class TestRiskParityOptimizer:
    def test_long_only_sum_one(self):
        from ez.portfolio.optimizer import RiskParityOptimizer, OptimizationConstraints
        from datetime import date
        symbols = [f"S{i}" for i in range(5)]
        data = _make_opt_data(symbols)
        opt = RiskParityOptimizer(
            constraints=OptimizationConstraints(max_weight=0.40),
            cov_lookback=60,
        )
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.2 for s in symbols})
        assert all(w >= -1e-9 for w in result.values())
        assert abs(sum(result.values()) - 1.0) < 0.02

    def test_does_not_crash_with_tight_constraints(self):
        from ez.portfolio.optimizer import RiskParityOptimizer, OptimizationConstraints
        from datetime import date
        symbols = [f"S{i}" for i in range(20)]
        data = _make_opt_data(symbols)
        opt = RiskParityOptimizer(
            constraints=OptimizationConstraints(max_weight=0.03),
            cov_lookback=60,
        )
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.05 for s in symbols})
        assert len(result) > 0
        assert all(w >= 0 for w in result.values())
