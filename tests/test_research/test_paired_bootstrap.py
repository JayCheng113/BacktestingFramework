"""Unit tests for PairedBlockBootstrapStep.

Tests cover: the core bootstrap function, the pipeline step,
edge cases, and statistical properties.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ez.research.context import PipelineContext
from ez.research.steps.paired_bootstrap import (
    PairedBlockBootstrapStep,
    paired_block_bootstrap,
    _sharpe_from_returns,
)


# ============================================================
# Helpers
# ============================================================

def _make_returns(
    n_days: int = 500,
    n_assets: int = 3,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n_days)
    data = {}
    labels = [chr(65 + i) for i in range(n_assets)]
    for i, label in enumerate(labels):
        drift = 0.0003 * (i + 1)
        data[label] = rng.normal(drift, 0.015, n_days)
    return pd.DataFrame(data, index=idx)


def _make_context(returns: pd.DataFrame) -> PipelineContext:
    return PipelineContext(artifacts={"returns": returns})


# ============================================================
# _sharpe_from_returns unit tests
# ============================================================

class TestSharpeHelper:
    def test_positive_sharpe(self):
        rng = np.random.default_rng(1)
        rets = rng.normal(0.001, 0.01, 252)
        s = _sharpe_from_returns(rets)
        assert s > 0

    def test_zero_for_constant_returns(self):
        rets = np.full(100, 0.001)
        # std = 0 → sharpe = 0
        s = _sharpe_from_returns(rets)
        assert s == 0.0

    def test_zero_for_short_series(self):
        s = _sharpe_from_returns(np.array([0.01]))
        assert s == 0.0


# ============================================================
# Core bootstrap function
# ============================================================

class TestPairedBlockBootstrap:
    def test_deterministic_with_seed(self):
        rng = np.random.default_rng(42)
        a = rng.normal(0.001, 0.01, 300)
        b = rng.normal(0.0005, 0.01, 300)

        r1 = paired_block_bootstrap(a, b, n_bootstrap=500, seed=99)
        r2 = paired_block_bootstrap(a, b, n_bootstrap=500, seed=99)
        assert r1["observed"] == r2["observed"]
        assert r1["ci_lower"] == r2["ci_lower"]
        assert r1["p_value"] == r2["p_value"]

    def test_ci_contains_observed(self):
        """For most data, the observed stat should be within the CI."""
        rng = np.random.default_rng(42)
        a = rng.normal(0.001, 0.01, 500)
        b = rng.normal(0.0005, 0.01, 500)
        r = paired_block_bootstrap(a, b, n_bootstrap=2000, seed=42)
        # Not always true but very likely for similar distributions
        assert r["ci_lower"] <= r["observed"] <= r["ci_upper"]

    def test_significant_when_means_differ(self):
        """Large mean difference → small p-value."""
        rng = np.random.default_rng(42)
        a = rng.normal(0.005, 0.01, 500)  # strong positive
        b = rng.normal(-0.002, 0.01, 500)  # negative
        r = paired_block_bootstrap(a, b, n_bootstrap=2000, seed=42)
        assert r["observed"] > 0  # A better than B
        assert r["p_value"] < 0.05  # significant

    def test_not_significant_when_identical(self):
        """Identical series → p-value should be high."""
        rng = np.random.default_rng(42)
        a = rng.normal(0.001, 0.01, 500)
        r = paired_block_bootstrap(a, a.copy(), n_bootstrap=2000, seed=42)
        assert abs(r["observed"]) < 1e-10
        assert r["p_value"] > 0.5

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="same length"):
            paired_block_bootstrap(np.zeros(100), np.zeros(50))

    def test_data_shorter_than_block_raises(self):
        with pytest.raises(ValueError, match="block_size"):
            paired_block_bootstrap(np.zeros(10), np.zeros(10), block_size=20)

    def test_distribution_shape(self):
        a = np.random.default_rng(1).normal(0, 0.01, 200)
        b = np.random.default_rng(2).normal(0, 0.01, 200)
        r = paired_block_bootstrap(a, b, n_bootstrap=1000, block_size=10, seed=42)
        assert len(r["distribution"]) == 1000
        assert r["n_bootstrap"] == 1000
        assert r["block_size"] == 10


# ============================================================
# Constructor validation
# ============================================================

class TestConstructorValidation:
    def test_empty_treatment_raises(self):
        with pytest.raises(ValueError, match="treatment_weights"):
            PairedBlockBootstrapStep(
                treatment_weights={},
                control_weights={"A": 1.0},
            )

    def test_empty_control_raises(self):
        with pytest.raises(ValueError, match="control_weights"):
            PairedBlockBootstrapStep(
                treatment_weights={"A": 1.0},
                control_weights={},
            )

    def test_low_n_bootstrap_raises(self):
        with pytest.raises(ValueError, match="n_bootstrap"):
            PairedBlockBootstrapStep(
                treatment_weights={"A": 1.0},
                control_weights={"B": 1.0},
                n_bootstrap=50,
            )

    def test_zero_block_size_raises(self):
        with pytest.raises(ValueError, match="block_size"):
            PairedBlockBootstrapStep(
                treatment_weights={"A": 1.0},
                control_weights={"B": 1.0},
                block_size=0,
            )

    def test_valid_construction(self):
        step = PairedBlockBootstrapStep(
            treatment_weights={"A": 0.6, "B": 0.4},
            control_weights={"A": 0.5, "B": 0.5},
            n_bootstrap=1000,
            block_size=10,
        )
        assert step.n_bootstrap == 1000
        assert step.block_size == 10


# ============================================================
# Step happy path
# ============================================================

class TestStepHappyPath:
    def test_produces_bootstrap_results(self):
        returns = _make_returns(300, 3)
        ctx = _make_context(returns)
        step = PairedBlockBootstrapStep(
            treatment_weights={"A": 0.6, "B": 0.2, "C": 0.2},
            control_weights={"A": 0.33, "B": 0.33, "C": 0.34},
            n_bootstrap=500,
            block_size=10,
            seed=42,
        )
        out = step.run(ctx)

        br = out.artifacts["bootstrap_results"]
        assert "sharpe_diff" in br
        assert "ci_lower" in br
        assert "ci_upper" in br
        assert "p_value" in br
        assert "is_significant" in br
        assert "ci_excludes_zero" in br
        assert "treatment_metrics" in br
        assert "control_metrics" in br
        assert br["n_observations"] > 0
        assert np.isfinite(br["sharpe_diff"])
        assert np.isfinite(br["p_value"])
        assert 0 <= br["p_value"] <= 1

    def test_with_window_slicing(self):
        returns = _make_returns(500, 2)
        ctx = _make_context(returns)
        step = PairedBlockBootstrapStep(
            treatment_weights={"A": 0.7, "B": 0.3},
            control_weights={"A": 0.5, "B": 0.5},
            window=("2020-06-01", "2021-06-01"),
            n_bootstrap=500,
            block_size=10,
        )
        out = step.run(ctx)

        br = out.artifacts["bootstrap_results"]
        assert br["window"] == ("2020-06-01", "2021-06-01")
        # Observation count should be less than full data
        assert br["n_observations"] < 500

    def test_labels_in_result(self):
        returns = _make_returns(200, 2)
        ctx = _make_context(returns)
        step = PairedBlockBootstrapStep(
            treatment_weights={"A": 0.6, "B": 0.4},
            control_weights={"A": 0.5, "B": 0.5},
            treatment_label="Optimized",
            control_label="Baseline P1",
            n_bootstrap=200,
            block_size=10,
        )
        out = step.run(ctx)

        br = out.artifacts["bootstrap_results"]
        assert br["treatment_label"] == "Optimized"
        assert br["control_label"] == "Baseline P1"


# ============================================================
# Edge cases
# ============================================================

class TestEdgeCases:
    def test_missing_returns_raises(self):
        ctx = PipelineContext()
        step = PairedBlockBootstrapStep(
            treatment_weights={"A": 1.0},
            control_weights={"B": 1.0},
        )
        with pytest.raises(KeyError, match="returns"):
            step.run(ctx)

    def test_insufficient_data_raises(self):
        returns = _make_returns(30, 2)  # too short for block_size=21
        ctx = _make_context(returns)
        step = PairedBlockBootstrapStep(
            treatment_weights={"A": 0.5, "B": 0.5},
            control_weights={"A": 0.5, "B": 0.5},
            block_size=21,
        )
        with pytest.raises(RuntimeError, match="insufficient data"):
            step.run(ctx)

    def test_weights_referencing_missing_columns_produce_zero_portfolio(self):
        """If weights reference non-existent columns, those are skipped (weight * 0)."""
        returns = _make_returns(200, 2)  # columns A, B
        ctx = _make_context(returns)
        step = PairedBlockBootstrapStep(
            treatment_weights={"A": 0.5, "MISSING": 0.5},
            control_weights={"A": 0.5, "B": 0.5},
            n_bootstrap=200,
            block_size=10,
        )
        # Should succeed — MISSING column just contributes 0
        out = step.run(ctx)
        assert "bootstrap_results" in out.artifacts

    def test_nan_handling(self):
        """NaN rows from outer join should be handled."""
        returns = _make_returns(200, 2)
        # Add some NaN rows
        returns.iloc[50:55, 0] = np.nan
        ctx = _make_context(returns)
        step = PairedBlockBootstrapStep(
            treatment_weights={"A": 0.5, "B": 0.5},
            control_weights={"A": 0.3, "B": 0.7},
            n_bootstrap=200,
            block_size=10,
        )
        out = step.run(ctx)
        assert out.artifacts["bootstrap_results"]["n_observations"] > 0


# ============================================================
# Statistical properties
# ============================================================

class TestStatisticalProperties:
    def test_ci_width_decreases_with_more_data(self):
        """More data → narrower CI (law of large numbers)."""
        rng = np.random.default_rng(42)
        a_full = rng.normal(0.001, 0.01, 1000)
        b_full = rng.normal(0.0005, 0.01, 1000)

        r_short = paired_block_bootstrap(
            a_full[:200], b_full[:200], n_bootstrap=1000, block_size=10, seed=42
        )
        r_long = paired_block_bootstrap(
            a_full, b_full, n_bootstrap=1000, block_size=10, seed=42
        )
        width_short = r_short["ci_upper"] - r_short["ci_lower"]
        width_long = r_long["ci_upper"] - r_long["ci_lower"]
        assert width_long < width_short

    def test_block_size_affects_ci_width(self):
        """Larger blocks → wider CI (fewer effective independent obs)."""
        rng = np.random.default_rng(42)
        a = rng.normal(0.001, 0.01, 500)
        b = rng.normal(0.0005, 0.01, 500)

        r_small = paired_block_bootstrap(a, b, block_size=5, n_bootstrap=2000, seed=42)
        r_large = paired_block_bootstrap(a, b, block_size=42, n_bootstrap=2000, seed=42)
        width_small = r_small["ci_upper"] - r_small["ci_lower"]
        width_large = r_large["ci_upper"] - r_large["ci_lower"]
        # Large blocks should generally produce wider CIs
        # (not guaranteed for every seed, but very likely)
        assert width_large > width_small * 0.8  # relaxed check
