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
