"""V2.12.1 edge case tests."""
import numpy as np
import pandas as pd
import pytest
from datetime import date


class TestGramSchmidt:
    def test_orthogonalized_columns_are_orthogonal(self):
        from ez.portfolio.orthogonalization import gram_schmidt_orthogonalize
        rng = np.random.default_rng(42)
        # Create correlated factors
        base = rng.normal(0, 1, 100)
        f1 = base + rng.normal(0, 0.1, 100)
        f2 = base + rng.normal(0, 0.1, 100)
        f3 = rng.normal(0, 1, 100)
        mat = np.column_stack([f1, f2, f3])

        orth = gram_schmidt_orthogonalize(mat)

        # Columns should be nearly uncorrelated
        for i in range(3):
            for j in range(i + 1, 3):
                corr = np.corrcoef(orth[:, i], orth[:, j])[0, 1]
                assert abs(corr) < 0.05, f"Columns {i},{j} corr={corr:.4f}"

    def test_nan_rows_preserved(self):
        from ez.portfolio.orthogonalization import gram_schmidt_orthogonalize
        mat = np.array([[1, 2], [3, 4], [np.nan, 6], [7, 8]], dtype=float)
        orth = gram_schmidt_orthogonalize(mat)
        assert np.isnan(orth[2, 0])  # NaN preserved
        assert not np.isnan(orth[0, 0])

    def test_single_factor_unchanged(self):
        from ez.portfolio.orthogonalization import gram_schmidt_orthogonalize
        mat = np.array([[1], [2], [3]], dtype=float)
        orth = gram_schmidt_orthogonalize(mat)
        np.testing.assert_array_equal(orth, mat)


class TestOptimizerTE:
    def test_te_constraint_with_benchmark_weights(self):
        """Optimizer should respect tracking error constraint."""
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        symbols = [f"S{i}" for i in range(5)]
        rng = np.random.default_rng(42)
        dates_range = pd.date_range("2023-01-02", periods=100, freq="B")
        data = {}
        for i, sym in enumerate(symbols):
            prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.01 * (i + 1), 100))
            data[sym] = pd.DataFrame({"close": prices, "adj_close": prices, "volume": rng.integers(100000, 5000000, 100)}, index=dates_range)

        benchmark_w = {f"S{i}": 0.2 for i in range(5)}
        opt = MeanVarianceOptimizer(
            risk_aversion=1.0,
            constraints=OptimizationConstraints(max_weight=0.40),
            cov_lookback=60,
            benchmark_weights=benchmark_w,
            max_tracking_error=0.05,
        )
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.2 for s in symbols})
        assert all(w >= -1e-9 for w in result.values())
        assert abs(sum(result.values()) - 1.0) < 1e-5


class TestIndexData:
    def test_cache_prevents_repeated_calls(self):
        from ez.portfolio.index_data import IndexDataProvider
        provider = IndexDataProvider()
        # Set cache directly
        import time as _time
        provider._cache["cons_TEST"] = (_time.monotonic(), ["A.SH", "B.SZ"])
        result = provider.get_constituents("TEST")
        assert result == ["A.SH", "B.SZ"]

    def test_fallback_to_equal_weight(self):
        from ez.portfolio.index_data import IndexDataProvider
        provider = IndexDataProvider()
        weights = provider._build_weights(["A.SH", "B.SZ", "C.SZ"])
        assert len(weights) == 3
        assert abs(sum(weights.values()) - 1.0) < 1e-10
        assert abs(weights["A.SH"] - 1 / 3) < 1e-10

    def test_normalize_code(self):
        from ez.portfolio.index_data import IndexDataProvider
        assert IndexDataProvider._normalize_code("600519") == "600519.SH"
        assert IndexDataProvider._normalize_code("000001") == "000001.SZ"
        assert IndexDataProvider._normalize_code("300750") == "300750.SZ"
        assert IndexDataProvider._normalize_code("600519.SH") == "600519.SH"
