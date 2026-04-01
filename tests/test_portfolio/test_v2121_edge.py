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
