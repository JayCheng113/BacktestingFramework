"""Unit tests for NestedOOSStep."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ez.research.context import PipelineContext
from ez.research.pipeline import ResearchPipeline, StepError
from ez.research.steps.nested_oos import NestedOOSStep
from ez.research.optimizers import (
    SimplexMultiObjectiveOptimizer,
    MaxSharpe,
    MaxCalmar,
    EpsilonConstraint,
)


def _make_returns(seed=42, n_days=500):
    """Build a 3-asset returns DataFrame spanning 2023-2024."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n_days, freq="B")
    return pd.DataFrame({
        "A": rng.normal(0.001, 0.015, n_days),
        "B": rng.normal(0.0002, 0.005, n_days),
        "C": rng.normal(0.0005, 0.008, n_days),
    }, index=idx)


def _ctx(returns=None):
    if returns is None:
        returns = _make_returns()
    return PipelineContext(artifacts={"returns": returns})


def _optimizer(**kwargs):
    return SimplexMultiObjectiveOptimizer(
        objectives=[MaxSharpe()],
        max_iter=200,  # matches phase_o convention
        **kwargs,
    )


# ============================================================
# Constructor validation
# ============================================================

class TestConstructorValidation:
    def test_invalid_is_window(self):
        with pytest.raises(ValueError, match="is_window"):
            NestedOOSStep(
                is_window=None,
                oos_window=("2024-01-01", "2024-12-31"),
                optimizer=_optimizer(),
            )

    def test_invalid_oos_window(self):
        with pytest.raises(ValueError, match="oos_window"):
            NestedOOSStep(
                is_window=("2023-01-01", "2023-12-31"),
                oos_window=(),
                optimizer=_optimizer(),
            )

    def test_non_optimizer_raises(self):
        with pytest.raises(TypeError, match="Optimizer"):
            NestedOOSStep(
                is_window=("2023-01-01", "2023-12-31"),
                oos_window=("2024-01-01", "2024-12-31"),
                optimizer="not_an_optimizer",
            )


# ============================================================
# Window validation
# ============================================================

class TestWindowValidation:
    def test_overlap_raises(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2024-06-30"),
            oos_window=("2024-01-01", "2024-12-31"),  # starts BEFORE IS ends
            optimizer=_optimizer(),
        )
        with pytest.raises(ValueError, match="OOS must be strictly after IS"):
            step.run(_ctx())

    def test_empty_is_window_raises(self):
        step = NestedOOSStep(
            is_window=("2020-01-01", "2020-12-31"),  # no data in 2020
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
        )
        with pytest.raises(RuntimeError, match="IS window"):
            step.run(_ctx())

    def test_empty_oos_window_raises(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2030-01-01", "2030-12-31"),  # no data in 2030
            optimizer=_optimizer(),
        )
        with pytest.raises(RuntimeError, match="OOS window"):
            step.run(_ctx())


# ============================================================
# Happy path
# ============================================================

class TestHappyPath:
    def test_single_objective_returns_one_candidate(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
        )
        out = step.run(_ctx())
        results = out.artifacts["nested_oos_results"]
        assert "candidates" in results
        assert len(results["candidates"]) == 1
        cand = results["candidates"][0]
        assert cand["objective"] == "Max Sharpe"
        assert "weights" in cand
        assert "is_metrics" in cand
        assert "oos_metrics" in cand
        assert cand["status"] == "converged"
        # Weights sum <= 1
        assert sum(cand["weights"].values()) <= 1.001
        # Weights keys match returns columns
        assert set(cand["weights"].keys()) == {"A", "B", "C"}

    def test_multi_objective_returns_all_candidates(self):
        opt = SimplexMultiObjectiveOptimizer(
            objectives=[MaxSharpe(), MaxCalmar()],
            max_iter=200,
        )
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=opt,
        )
        out = step.run(_ctx())
        candidates = out.artifacts["nested_oos_results"]["candidates"]
        assert len(candidates) == 2
        names = {c["objective"] for c in candidates}
        assert "Max Sharpe" in names
        assert "Max Calmar" in names

    def test_oos_metrics_populated(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
        )
        out = step.run(_ctx())
        oos_m = out.artifacts["nested_oos_results"]["candidates"][0]["oos_metrics"]
        # Should have standard metric keys
        assert "ret" in oos_m
        assert "sharpe" in oos_m
        assert "dd" in oos_m

    def test_windows_recorded_as_strings(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
        )
        out = step.run(_ctx())
        r = out.artifacts["nested_oos_results"]
        assert r["is_window"] == ("2023-01-01", "2023-12-31")
        assert r["oos_window"] == ("2024-01-01", "2024-12-31")


# ============================================================
# Baseline comparison
# ============================================================

class TestBaseline:
    def test_baseline_is_and_oos_computed(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
            baseline_weights={"A": 0.7, "B": 0.15, "C": 0.15},
        )
        out = step.run(_ctx())
        r = out.artifacts["nested_oos_results"]
        assert r["baseline_weights"] == {"A": 0.7, "B": 0.15, "C": 0.15}
        assert r["baseline_is"] is not None
        assert "ret" in r["baseline_is"]
        assert r["baseline_oos"] is not None
        assert "ret" in r["baseline_oos"]

    def test_no_baseline_results_in_none(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
        )
        out = step.run(_ctx())
        r = out.artifacts["nested_oos_results"]
        assert r["baseline_weights"] is None
        assert r["baseline_is"] is None
        assert r["baseline_oos"] is None

    def test_baseline_unknown_label_raises(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
            baseline_weights={"A": 0.5, "MISSING": 0.5},
        )
        with pytest.raises(ValueError, match="unknown labels"):
            step.run(_ctx())

    def test_baseline_label_for_epsilon_constraint(self):
        """baseline_label provides IS metrics for the alpha sleeve,
        used by EpsilonConstraint's "baseline_ret" references."""
        opt = SimplexMultiObjectiveOptimizer(
            objectives=[
                EpsilonConstraint("min_mdd", "ret", ">=", "0.5*baseline_ret"),
            ],
            max_iter=200,
        )
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=opt,
            baseline_label="A",
        )
        # Should not crash — baseline_label → IS metrics for "A"
        # → passed to optimizer.optimize(is_returns, baseline_metrics)
        out = step.run(_ctx())
        assert len(out.artifacts["nested_oos_results"]["candidates"]) == 1

    def test_baseline_label_unknown_raises(self):
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
            baseline_label="MISSING",
        )
        with pytest.raises(ValueError, match="baseline_label"):
            step.run(_ctx())


# ============================================================
# Pipeline integration
# ============================================================

class TestPipelineIntegration:
    def test_nested_oos_in_pipeline(self):
        """NestedOOSStep runs inside a ResearchPipeline."""
        pipeline = ResearchPipeline([
            NestedOOSStep(
                is_window=("2023-01-01", "2023-12-31"),
                oos_window=("2024-01-01", "2024-12-31"),
                optimizer=_optimizer(),
                baseline_weights={"A": 0.7, "B": 0.15, "C": 0.15},
            ),
        ])
        ctx = _ctx()
        out = pipeline.run(ctx)
        assert "nested_oos_results" in out.artifacts
        assert len(out.history) == 1
        assert out.history[0].step_name == "nested_oos"
        assert out.history[0].status == "success"

    def test_missing_returns_raises_step_error(self):
        pipeline = ResearchPipeline([
            NestedOOSStep(
                is_window=("2023-01-01", "2023-12-31"),
                oos_window=("2024-01-01", "2024-12-31"),
                optimizer=_optimizer(),
            ),
        ])
        with pytest.raises(StepError) as exc_info:
            pipeline.run(PipelineContext())  # no returns artifact
        assert exc_info.value.step_name == "nested_oos"

    def test_stale_nested_oos_results_cleared_on_rerun(self):
        """Codex P2-5 pattern: stale artifacts from prior run cleared."""
        ctx = _ctx()
        ctx.artifacts["nested_oos_results"] = {"stale": True}
        step = NestedOOSStep(
            is_window=("2023-01-01", "2023-12-31"),
            oos_window=("2024-01-01", "2024-12-31"),
            optimizer=_optimizer(),
        )
        out = step.run(ctx)
        assert "stale" not in out.artifacts["nested_oos_results"]
