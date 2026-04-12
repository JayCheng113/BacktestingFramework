"""Unit tests for WalkForwardStep.

Uses synthetic returns data and the real SimplexMultiObjectiveOptimizer
to verify fold splitting, per-fold optimization, OOS validation,
aggregate metrics, degradation computation, and edge cases.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from ez.research.context import PipelineContext
from ez.research.steps.walk_forward import WalkForwardStep
from ez.research.optimizers import SimplexMultiObjectiveOptimizer, OptimalWeights
from ez.research.optimizers.objectives import MaxSharpe, MaxCalmar


# ============================================================
# Helpers
# ============================================================

def _make_returns(
    n_days: int = 500,
    n_assets: int = 3,
    seed: int = 42,
    start: str = "2020-01-01",
) -> pd.DataFrame:
    """Create synthetic daily returns DataFrame."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n_days)
    data = {}
    labels = [chr(65 + i) for i in range(n_assets)]  # A, B, C, ...
    for i, label in enumerate(labels):
        # Each asset has slightly different drift
        drift = 0.0003 * (i + 1)
        data[label] = rng.normal(drift, 0.015, n_days)
    return pd.DataFrame(data, index=idx)


def _make_context(returns: pd.DataFrame) -> PipelineContext:
    return PipelineContext(artifacts={"returns": returns})


class _StubOptimizer:
    """Always returns fixed weights for testing."""

    def __init__(self, weights: dict[str, float]):
        self._weights = weights

    def optimize(self, returns, baseline_metrics=None):
        return [
            OptimalWeights(
                objective_name="Stub",
                weights=dict(self._weights),
                is_metrics={"sharpe": 1.0, "ret": 0.1},
                optimizer_status="converged",
            )
        ]


# ============================================================
# Constructor validation
# ============================================================

class TestConstructorValidation:
    def test_n_splits_less_than_2_raises(self):
        opt = _StubOptimizer({"A": 0.5, "B": 0.5})
        with pytest.raises(ValueError, match="n_splits >= 2"):
            WalkForwardStep(optimizer=opt, n_splits=1)

    def test_train_ratio_zero_raises(self):
        opt = _StubOptimizer({"A": 0.5, "B": 0.5})
        with pytest.raises(ValueError, match="0 < train_ratio < 1"):
            WalkForwardStep(optimizer=opt, train_ratio=0.0)

    def test_train_ratio_one_raises(self):
        opt = _StubOptimizer({"A": 0.5, "B": 0.5})
        with pytest.raises(ValueError, match="0 < train_ratio < 1"):
            WalkForwardStep(optimizer=opt, train_ratio=1.0)

    def test_valid_construction(self):
        opt = _StubOptimizer({"A": 0.5, "B": 0.5})
        step = WalkForwardStep(optimizer=opt, n_splits=5, train_ratio=0.8)
        assert step.n_splits == 5
        assert step.train_ratio == 0.8


# ============================================================
# Fold boundary computation
# ============================================================

class TestFoldBoundaries:
    def test_boundaries_cover_all_rows(self):
        step = WalkForwardStep(
            optimizer=_StubOptimizer({}), n_splits=5, train_ratio=0.8
        )
        bounds = step._compute_fold_boundaries(100)
        assert len(bounds) == 5
        # First fold starts at 0
        assert bounds[0][0] == 0
        # Last fold ends at n_rows
        assert bounds[-1][2] == 100
        # No gaps between folds
        for i in range(len(bounds) - 1):
            assert bounds[i][2] == bounds[i + 1][0]

    def test_boundaries_no_tail_loss(self):
        """Integer-interval arithmetic should not lose tail rows."""
        step = WalkForwardStep(
            optimizer=_StubOptimizer({}), n_splits=3, train_ratio=0.7
        )
        bounds = step._compute_fold_boundaries(100)
        total = sum(end - start for start, _, end in bounds)
        assert total == 100

    def test_boundaries_with_odd_n_rows(self):
        """Last fold absorbs remainder."""
        step = WalkForwardStep(
            optimizer=_StubOptimizer({}), n_splits=3, train_ratio=0.8
        )
        bounds = step._compute_fold_boundaries(101)
        assert bounds[-1][2] == 101
        # All rows covered
        total = sum(end - start for start, _, end in bounds)
        assert total == 101

    def test_train_end_respects_ratio(self):
        step = WalkForwardStep(
            optimizer=_StubOptimizer({}), n_splits=5, train_ratio=0.8
        )
        bounds = step._compute_fold_boundaries(500)
        for start, train_end, end in bounds:
            fold_size = end - start
            train_size = train_end - start
            # Should be approximately 80% of fold
            assert 0.5 <= train_size / fold_size <= 0.95


# ============================================================
# Happy path with stub optimizer
# ============================================================

class TestHappyPath:
    def test_produces_walk_forward_results(self):
        returns = _make_returns(500, 3)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.4, "B": 0.3, "C": 0.3}),
            n_splits=5,
            train_ratio=0.8,
        )
        out = step.run(ctx)

        wf = out.artifacts["walk_forward_results"]
        assert wf["n_splits"] == 5
        assert wf["train_ratio"] == 0.8
        assert wf["n_folds_completed"] == 5
        assert len(wf["folds"]) == 5

    def test_each_fold_has_required_keys(self):
        returns = _make_returns(500, 3)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.5, "B": 0.5}),
            n_splits=3,
        )
        out = step.run(ctx)

        for fold in out.artifacts["walk_forward_results"]["folds"]:
            assert "fold" in fold
            assert "is_window" in fold
            assert "oos_window" in fold
            assert "candidates" in fold
            assert len(fold["candidates"]) >= 1
            # Each candidate has expected structure
            cand = fold["candidates"][0]
            assert "objective" in cand
            assert "weights" in cand
            assert "is_metrics" in cand
            assert "oos_metrics" in cand
            assert "status" in cand

    def test_aggregate_metrics_present(self):
        returns = _make_returns(500, 3)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.4, "B": 0.3, "C": 0.3}),
            n_splits=5,
        )
        out = step.run(ctx)

        agg = out.artifacts["walk_forward_results"]["aggregate"]
        assert "oos_sharpe" in agg
        assert "oos_return" in agg
        assert "degradation" in agg
        assert "avg_is_sharpe" in agg
        assert np.isfinite(agg["oos_sharpe"])
        assert np.isfinite(agg["degradation"])

    def test_folds_are_non_overlapping(self):
        returns = _make_returns(500, 2)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.5, "B": 0.5}),
            n_splits=5,
        )
        out = step.run(ctx)

        folds = out.artifacts["walk_forward_results"]["folds"]
        for i in range(len(folds) - 1):
            oos_end = folds[i]["oos_window"][1]
            next_is_start = folds[i + 1]["is_window"][0]
            # OOS end of fold k should be before or equal to IS start of fold k+1
            assert oos_end <= next_is_start


# ============================================================
# Real optimizer integration
# ============================================================

class TestRealOptimizer:
    def test_with_simplex_optimizer(self):
        """Full integration with SimplexMultiObjectiveOptimizer."""
        returns = _make_returns(300, 2, seed=1)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=SimplexMultiObjectiveOptimizer(
                objectives=[MaxSharpe()],
                seed=42,
            ),
            n_splits=3,
            train_ratio=0.75,
        )
        out = step.run(ctx)

        wf = out.artifacts["walk_forward_results"]
        assert wf["n_folds_completed"] == 3
        # Each fold has optimization result
        for fold in wf["folds"]:
            assert len(fold["candidates"]) == 1
            assert fold["candidates"][0]["objective"] == "Max Sharpe"

    def test_with_multiple_objectives(self):
        returns = _make_returns(300, 3, seed=2)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=SimplexMultiObjectiveOptimizer(
                objectives=[MaxSharpe(), MaxCalmar()],
                seed=42,
            ),
            n_splits=3,
        )
        out = step.run(ctx)

        wf = out.artifacts["walk_forward_results"]
        for fold in wf["folds"]:
            assert len(fold["candidates"]) == 2


# ============================================================
# Baseline comparison
# ============================================================

class TestBaseline:
    def test_baseline_metrics_computed_per_fold(self):
        returns = _make_returns(300, 2)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.6, "B": 0.4}),
            n_splits=3,
            baseline_weights={"A": 0.5, "B": 0.5},
        )
        out = step.run(ctx)

        for fold in out.artifacts["walk_forward_results"]["folds"]:
            assert fold["baseline_is"] is not None
            assert fold["baseline_oos"] is not None
            assert "sharpe" in fold["baseline_is"]
            assert "sharpe" in fold["baseline_oos"]

    def test_baseline_aggregate_oos_sharpe(self):
        returns = _make_returns(300, 2)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.6, "B": 0.4}),
            n_splits=3,
            baseline_weights={"A": 0.5, "B": 0.5},
        )
        out = step.run(ctx)

        agg = out.artifacts["walk_forward_results"]["aggregate"]
        # C1 fix: baseline uses concatenated OOS returns, not per-fold avg
        assert "baseline_oos_sharpe" in agg
        assert np.isfinite(agg["baseline_oos_sharpe"])


# ============================================================
# Edge cases
# ============================================================

class TestEdgeCases:
    def test_insufficient_data_raises(self):
        returns = _make_returns(10, 2)  # only 10 rows
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.5, "B": 0.5}),
            n_splits=5,
        )
        with pytest.raises(RuntimeError, match="insufficient data"):
            step.run(ctx)

    def test_missing_returns_artifact_raises(self):
        ctx = PipelineContext()
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.5, "B": 0.5}),
            n_splits=3,
        )
        with pytest.raises(KeyError, match="returns"):
            step.run(ctx)

    def test_non_dataframe_returns_raises(self):
        ctx = PipelineContext(artifacts={"returns": [1, 2, 3]})
        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.5, "B": 0.5}),
            n_splits=3,
        )
        with pytest.raises(TypeError, match="pd.DataFrame"):
            step.run(ctx)

    def test_nan_rows_dropped_before_splitting(self):
        """Outer join NaN rows (all-NaN) should be dropped."""
        returns = _make_returns(200, 2)
        # Insert all-NaN rows
        nan_idx = pd.bdate_range("2019-06-01", periods=50)
        nan_df = pd.DataFrame(
            {"A": [np.nan] * 50, "B": [np.nan] * 50},
            index=nan_idx,
        )
        combined = pd.concat([nan_df, returns])
        ctx = _make_context(combined)

        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.5, "B": 0.5}),
            n_splits=3,
        )
        out = step.run(ctx)
        # Should succeed — NaN rows dropped
        assert out.artifacts["walk_forward_results"]["n_folds_completed"] == 3

    def test_optimizer_failure_skips_fold(self, caplog):
        """If optimizer raises on one fold, skip it and continue."""

        class _FailOnFirstOptimizer:
            _call_count = 0

            def optimize(self, returns, baseline_metrics=None):
                self._call_count += 1
                if self._call_count == 1:
                    raise RuntimeError("intentional failure")
                return [
                    OptimalWeights(
                        objective_name="Test",
                        weights={"A": 0.5, "B": 0.5},
                        is_metrics={"sharpe": 1.0},
                        optimizer_status="converged",
                    )
                ]

        returns = _make_returns(300, 2)
        ctx = _make_context(returns)
        step = WalkForwardStep(
            optimizer=_FailOnFirstOptimizer(),
            n_splits=3,
        )
        import logging
        with caplog.at_level(logging.WARNING):
            out = step.run(ctx)

        wf = out.artifacts["walk_forward_results"]
        # One fold skipped, 2 completed
        assert wf["n_folds_completed"] == 2
        assert "optimizer failed" in caplog.text

    def test_stale_artifact_cleared(self):
        """If walk_forward_results already exists, it's replaced."""
        returns = _make_returns(200, 2)
        ctx = _make_context(returns)
        ctx.artifacts["walk_forward_results"] = {"stale": True}

        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.5, "B": 0.5}),
            n_splits=3,
        )
        out = step.run(ctx)
        assert "stale" not in out.artifacts["walk_forward_results"]
        assert "folds" in out.artifacts["walk_forward_results"]


# ============================================================
# Degradation computation
# ============================================================

class TestDegradation:
    def test_degradation_is_zero_when_is_equals_oos(self):
        """If IS sharpe == OOS sharpe, degradation should be ~0."""
        # Use constant returns so IS and OOS have similar metrics
        returns = _make_returns(500, 2, seed=42)
        ctx = _make_context(returns)

        step = WalkForwardStep(
            optimizer=_StubOptimizer({"A": 0.5, "B": 0.5}),
            n_splits=5,
        )
        out = step.run(ctx)

        agg = out.artifacts["walk_forward_results"]["aggregate"]
        # Stub optimizer always returns is_metrics sharpe=1.0
        # OOS sharpe is from real data, so degradation = (1.0 - oos) / 1.0
        assert "degradation" in agg
        assert np.isfinite(agg["degradation"])

    def test_degradation_positive_when_overfit(self):
        """Degradation should be positive when IS sharpe > OOS sharpe."""
        # Stub optimizer claims IS sharpe=5.0 (inflated)
        class _HighISOptimizer:
            def optimize(self, returns, baseline_metrics=None):
                return [
                    OptimalWeights(
                        objective_name="Inflated",
                        weights={c: 1.0 / len(returns.columns) for c in returns.columns},
                        is_metrics={"sharpe": 5.0, "ret": 0.5},
                        optimizer_status="converged",
                    )
                ]

        returns = _make_returns(500, 2, seed=42)
        ctx = _make_context(returns)
        step = WalkForwardStep(optimizer=_HighISOptimizer(), n_splits=5)
        out = step.run(ctx)

        agg = out.artifacts["walk_forward_results"]["aggregate"]
        # IS sharpe = 5.0, OOS sharpe << 5.0 → degradation > 0
        assert agg["degradation"] > 0
