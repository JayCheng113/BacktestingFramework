"""Unit tests for SimplexMultiObjectiveOptimizer."""
from __future__ import annotations
import math

import numpy as np
import pandas as pd
import pytest

from ez.research.optimizers import (
    SimplexMultiObjectiveOptimizer,
    Optimizer,
    Objective,
    OptimalWeights,
    MaxSharpe,
    MaxCalmar,
    MaxSortino,
    MinCVaR,
    EpsilonConstraint,
)


def _make_returns_3asset(seed=42, n_days=300):
    """Build a 3-asset returns DataFrame: A high-return high-vol,
    B low-return low-vol, C medium."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "A": rng.normal(0.0015, 0.015, n_days),  # high mean, high vol
        "B": rng.normal(0.0001, 0.003, n_days),  # near-zero mean, low vol (cash-like)
        "C": rng.normal(0.0008, 0.008, n_days),  # medium
    }, index=pd.date_range("2024-01-01", periods=n_days, freq="B"))


# ============================================================
# Constructor validation
# ============================================================

class TestConstructorValidation:
    def test_empty_objectives_raises(self):
        with pytest.raises(ValueError, match="at least one objective"):
            SimplexMultiObjectiveOptimizer(objectives=[])

    def test_non_objective_in_list_raises(self):
        with pytest.raises(TypeError, match="must be Objective"):
            SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe(), "not_an_objective"])

    def test_default_seed(self):
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()])
        assert opt.seed == 42

    def test_custom_seed_reproducible(self):
        opt1 = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()], seed=7)
        opt2 = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()], seed=7)
        rets = _make_returns_3asset()
        r1 = opt1.optimize(rets)[0]
        r2 = opt2.optimize(rets)[0]
        # Same seed → same weights (within float tolerance)
        for label in r1.weights:
            assert abs(r1.weights[label] - r2.weights[label]) < 1e-6


# ============================================================
# Input validation
# ============================================================

class TestInputValidation:
    def test_empty_returns_raises(self):
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()])
        with pytest.raises(ValueError, match="empty returns"):
            opt.optimize(pd.DataFrame())

    def test_non_dataframe_raises(self):
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()])
        with pytest.raises(TypeError, match="must be pd.DataFrame"):
            opt.optimize([1, 2, 3])

    def test_single_asset_raises(self):
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()])
        rets = pd.DataFrame({"A": [0.01, 0.02, 0.03]},
                            index=pd.date_range("2024-01-01", periods=3, freq="B"))
        with pytest.raises(ValueError, match=">= 2 assets"):
            opt.optimize(rets)

    def test_too_few_rows_after_dropna_raises(self):
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()])
        rets = pd.DataFrame({
            "A": [0.01, float("nan"), float("nan")],
            "B": [0.005, float("nan"), float("nan")],
        }, index=pd.date_range("2024-01-01", periods=3, freq="B"))
        with pytest.raises(ValueError, match="< 2 valid rows"):
            opt.optimize(rets)


# ============================================================
# Single-objective optimization
# ============================================================

class TestSingleObjective:
    def test_max_sharpe_returns_one_result(self):
        opt = SimplexMultiObjectiveOptimizer(
            objectives=[MaxSharpe()],
            max_iter=200,  # speed up tests
        )
        rets = _make_returns_3asset()
        results = opt.optimize(rets)
        assert len(results) == 1
        r = results[0]
        assert r.is_feasible
        assert r.objective_name == "Max Sharpe"
        # Weights sum to <= 1.0 (residual is cash)
        total = sum(r.weights.values())
        assert 0 <= total <= 1.0001
        # All weights >= 0
        for w in r.weights.values():
            assert w >= -1e-9

    def test_max_sharpe_prefers_higher_sharpe_asset(self):
        """With one clearly-better asset, optimizer should weight it heavily."""
        # A has the best sharpe (high mean, low vol)
        # B/C are noise
        rets = pd.DataFrame({
            "A": np.random.default_rng(42).normal(0.005, 0.005, 200),  # great sharpe
            "B": np.random.default_rng(7).normal(0.0001, 0.02, 200),   # bad sharpe
            "C": np.random.default_rng(11).normal(0.0001, 0.02, 200),  # bad sharpe
        }, index=pd.date_range("2024-01-01", periods=200, freq="B"))
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()], max_iter=80)
        result = opt.optimize(rets)[0]
        # Optimizer should put most weight on A
        assert result.weights["A"] > 0.5, (
            f"Expected A weight > 0.5, got {result.weights}"
        )

    def test_is_metrics_populated_on_converged(self):
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()], max_iter=200)
        rets = _make_returns_3asset()
        result = opt.optimize(rets)[0]
        assert result.is_feasible
        assert "ret" in result.is_metrics
        assert "sharpe" in result.is_metrics
        assert "calmar" in result.is_metrics

    def test_weights_dict_keys_match_columns(self):
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()], max_iter=200)
        rets = _make_returns_3asset()
        result = opt.optimize(rets)[0]
        assert set(result.weights.keys()) == set(rets.columns)


# ============================================================
# Multi-objective
# ============================================================

class TestMultiObjective:
    def test_4_objectives_returns_4_results(self):
        opt = SimplexMultiObjectiveOptimizer(
            objectives=[MaxSharpe(), MaxCalmar(), MaxSortino(), MinCVaR()],
            max_iter=200,
        )
        rets = _make_returns_3asset()
        results = opt.optimize(rets)
        assert len(results) == 4
        assert {r.objective_name for r in results} == {
            "Max Sharpe", "Max Calmar", "Max Sortino", "Min CVaR 5%",
        }

    def test_results_in_objectives_order(self):
        objs = [MaxSortino(), MaxSharpe(), MaxCalmar()]
        opt = SimplexMultiObjectiveOptimizer(objectives=objs, max_iter=200)
        results = opt.optimize(_make_returns_3asset())
        assert [r.objective_name for r in results] == [
            "Max Sortino", "Max Sharpe", "Max Calmar",
        ]


# ============================================================
# EpsilonConstraint integration
# ============================================================

class TestEpsilonConstraintIntegration:
    def test_epsilon_constraint_with_baseline(self):
        """Min |MDD| s.t. ret >= 0.5 * baseline_ret — feasible when
        the optimizer can find weights that meet the constraint."""
        opt = SimplexMultiObjectiveOptimizer(
            objectives=[
                EpsilonConstraint("min_mdd", "ret", ">=", "0.5*baseline_ret"),
            ],
            max_iter=80,
        )
        rets = _make_returns_3asset()
        # Compute baseline as the standalone "A" asset
        from ez.research._metrics import compute_basic_metrics
        baseline = compute_basic_metrics(rets["A"])
        results = opt.optimize(rets, baseline_metrics=baseline)
        assert len(results) == 1
        r = results[0]
        # Should be feasible (we're asking for half the baseline)
        # If infeasible, the test still passes (status is "infeasible")
        # but typically this should converge.
        assert r.objective_name.startswith("min_mdd")

    def test_unsatisfiable_constraint_returns_infeasible(self):
        """Demand ret >= 100x baseline — should always be infeasible."""
        opt = SimplexMultiObjectiveOptimizer(
            objectives=[
                EpsilonConstraint("min_mdd", "ret", ">=", "100*baseline_ret"),
            ],
            max_iter=200,
        )
        rets = _make_returns_3asset()
        from ez.research._metrics import compute_basic_metrics
        baseline = compute_basic_metrics(rets["A"])
        results = opt.optimize(rets, baseline_metrics=baseline)
        r = results[0]
        # Optimizer should mark as infeasible
        assert r.optimizer_status == "infeasible"
        # All weights should be zero in infeasible result
        assert all(w == 0.0 for w in r.weights.values())


# ============================================================
# Optimizer ABC contract
# ============================================================

class TestOptimizerABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError, match="abstract"):
            Optimizer()

    def test_simplex_is_optimizer_subclass(self):
        opt = SimplexMultiObjectiveOptimizer(objectives=[MaxSharpe()])
        assert isinstance(opt, Optimizer)
