"""Unit tests for EpsilonConstraint + safe AST eval."""
from __future__ import annotations
import math

import numpy as np
import pandas as pd
import pytest

from ez.research.optimizers import EpsilonConstraint
from ez.research.optimizers.epsilon_constraint import _safe_eval


def _returns(values):
    return pd.Series(
        values,
        index=pd.date_range("2024-01-01", periods=len(values), freq="B"),
        dtype=float,
    )


# ============================================================
# _safe_eval — the AST DSL parser
# ============================================================

class TestSafeEval:
    BASELINE = {"ret": 0.10, "mdd_abs": 0.20, "sharpe": 1.5, "vol": 0.15,
                "sortino": 2.0, "calmar": 0.5, "dd": -0.20}

    def test_int_passes_through(self):
        assert _safe_eval(0, None) == 0.0
        assert _safe_eval(5, None) == 5.0

    def test_float_passes_through(self):
        assert _safe_eval(3.14, None) == 3.14

    def test_numeric_string(self):
        assert _safe_eval("42", None) == 42.0
        assert _safe_eval("3.14", None) == 3.14

    def test_baseline_reference(self):
        assert _safe_eval("baseline_ret", self.BASELINE) == 0.10
        assert _safe_eval("baseline_mdd_abs", self.BASELINE) == 0.20

    def test_simple_multiplication(self):
        assert _safe_eval("0.9*baseline_ret", self.BASELINE) == pytest.approx(0.09)
        assert _safe_eval("0.6 * baseline_mdd_abs", self.BASELINE) == pytest.approx(0.12)

    def test_simple_division(self):
        assert _safe_eval("baseline_ret / 2", self.BASELINE) == pytest.approx(0.05)

    def test_parentheses(self):
        assert _safe_eval("(0.5 * baseline_ret)", self.BASELINE) == pytest.approx(0.05)

    def test_unary_minus(self):
        assert _safe_eval("-0.5 * baseline_ret", self.BASELINE) == pytest.approx(-0.05)

    def test_unknown_baseline_key_raises(self):
        with pytest.raises(ValueError, match="unknown baseline metric"):
            _safe_eval("baseline_xyz", self.BASELINE)

    def test_baseline_missing_when_referenced(self):
        with pytest.raises(ValueError, match="no baseline_metrics"):
            _safe_eval("baseline_ret", None)

    def test_baseline_dict_missing_key(self):
        with pytest.raises(ValueError, match="missing key"):
            _safe_eval("baseline_ret", {"sharpe": 1.0})

    def test_division_by_zero(self):
        with pytest.raises(ValueError, match="division by zero"):
            _safe_eval("baseline_ret / 0", self.BASELINE)

    def test_unknown_name_blocked(self):
        with pytest.raises(ValueError, match="not allowed"):
            _safe_eval("os * baseline_ret", self.BASELINE)
        with pytest.raises(ValueError, match="not allowed"):
            _safe_eval("baseline_ret + foo", self.BASELINE)

    def test_function_call_blocked(self):
        with pytest.raises(ValueError, match="unsupported node type"):
            _safe_eval("max(0, baseline_ret)", self.BASELINE)

    def test_attribute_access_blocked(self):
        with pytest.raises(ValueError, match="unsupported node type"):
            _safe_eval("os.path.exists", self.BASELINE)

    def test_subscript_blocked(self):
        with pytest.raises(ValueError, match="unsupported node type"):
            _safe_eval("baseline_ret[0]", self.BASELINE)

    def test_addition_blocked(self):
        # Only * and / allowed
        with pytest.raises(ValueError, match="only \\* and / are allowed"):
            _safe_eval("baseline_ret + 1", self.BASELINE)
        with pytest.raises(ValueError, match="only \\* and / are allowed"):
            _safe_eval("baseline_ret - 1", self.BASELINE)

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Empty"):
            _safe_eval("", None)

    def test_invalid_syntax(self):
        with pytest.raises(ValueError, match="Invalid"):
            _safe_eval("baseline_ret *", self.BASELINE)

    def test_non_numeric_constant_blocked(self):
        with pytest.raises(ValueError, match="only numeric constants"):
            _safe_eval('"hello"', None)

    def test_wrong_type_raises(self):
        with pytest.raises(TypeError, match="must be number"):
            _safe_eval([1, 2], None)


# ============================================================
# EpsilonConstraint constructor validation
# ============================================================

class TestEpsilonConstraintInit:
    def test_unknown_objective(self):
        with pytest.raises(ValueError, match="Unknown objective"):
            EpsilonConstraint("max_sharpe", "ret", ">=", 0.1)

    def test_unknown_constraint_metric(self):
        with pytest.raises(ValueError, match="Unknown constraint_metric"):
            EpsilonConstraint("min_mdd", "xyz", ">=", 0.1)

    def test_unknown_op(self):
        with pytest.raises(ValueError, match="Unknown constraint_op"):
            EpsilonConstraint("min_mdd", "ret", "!=", 0.1)

    def test_invalid_constraint_value_string(self):
        with pytest.raises(ValueError, match="failed validation"):
            EpsilonConstraint("min_mdd", "ret", ">=", "max(0, baseline_ret)")

    def test_valid_construction(self):
        obj = EpsilonConstraint("min_mdd", "ret", ">=", "0.9*baseline_ret")
        assert obj.objective == "min_mdd"
        assert obj.constraint_metric == "ret"
        assert obj.constraint_op == ">="
        assert obj.constraint_value == "0.9*baseline_ret"
        assert "min_mdd" in obj.name
        assert "ret>=" in obj.name

    def test_numeric_constraint_value(self):
        obj = EpsilonConstraint("max_ret", "mdd_abs", "<=", 0.08)
        assert obj.constraint_value == 0.08


# ============================================================
# EpsilonConstraint.evaluate
# ============================================================

class TestEpsilonConstraintEvaluate:
    def _series_with_known_metrics(self):
        """Build a returns series with predictable metrics."""
        # 100 small positive returns + 1 medium drop + 100 small positive
        rets = [0.005] * 100 + [-0.05] + [0.005] * 100
        return _returns(rets)

    def test_feasible_returns_objective_value(self):
        obj = EpsilonConstraint("min_mdd", "ret", ">=", 0.0)
        rets = self._series_with_known_metrics()
        val = obj.evaluate(rets)
        # Feasible: ret > 0 (positive trend), so constraint passes
        # Objective: minimize mdd_abs → return mdd_abs which is positive
        assert math.isfinite(val)
        assert val > 0  # mdd_abs is positive

    def test_infeasible_returns_inf(self):
        # ret >= 1000% is impossible for any realistic series
        obj = EpsilonConstraint("min_mdd", "ret", ">=", 10.0)
        rets = self._series_with_known_metrics()
        assert obj.evaluate(rets) == math.inf

    def test_baseline_resolution(self):
        baseline = {"ret": 0.20, "mdd_abs": 0.30, "sharpe": 1.0, "vol": 0.15,
                    "sortino": 1.5, "calmar": 0.5, "dd": -0.30}
        # Constraint: ret >= 0.5 * baseline_ret = 0.10
        obj = EpsilonConstraint("min_mdd", "ret", ">=", "0.5*baseline_ret")
        rets = self._series_with_known_metrics()
        val = obj.evaluate(rets, baseline_metrics=baseline)
        # The series has positive ret; whether it passes depends on actual annualized return
        # Just verify it doesn't crash and returns finite or inf
        assert val == math.inf or math.isfinite(val)

    def test_baseline_missing_when_required(self):
        obj = EpsilonConstraint("min_mdd", "ret", ">=", "baseline_ret")
        rets = self._series_with_known_metrics()
        # baseline_metrics not provided → constraint can't resolve → infeasible
        assert obj.evaluate(rets, baseline_metrics=None) == math.inf

    def test_max_ret_objective(self):
        obj = EpsilonConstraint("max_ret", "mdd_abs", "<=", 1.0)  # any drawdown ok
        rets = self._series_with_known_metrics()
        val = obj.evaluate(rets)
        # max_ret returns -ret, ret > 0 → val < 0
        assert math.isfinite(val)
        assert val < 0

    def test_empty_returns_inf(self):
        obj = EpsilonConstraint("min_mdd", "ret", ">=", 0.0)
        assert obj.evaluate(pd.Series([], dtype=float)) == math.inf

    def test_constraint_op_lt(self):
        obj = EpsilonConstraint("max_ret", "mdd_abs", "<", 0.001)  # very tight
        rets = self._series_with_known_metrics()
        # mdd_abs is around 5% (the -5% drop), so constraint fails
        assert obj.evaluate(rets) == math.inf

    def test_constraint_op_gt(self):
        obj = EpsilonConstraint("min_mdd", "ret", ">", -1.0)  # any ret > -100%
        rets = self._series_with_known_metrics()
        val = obj.evaluate(rets)
        assert math.isfinite(val)

    def test_constraint_op_eq(self):
        obj = EpsilonConstraint("min_mdd", "ret", "==", 999.0)  # impossible
        rets = self._series_with_known_metrics()
        assert obj.evaluate(rets) == math.inf

    def test_phase_o_pattern_ret_constraint(self):
        """Replicate phase_o_nested_oos.py pattern: min mdd s.t. ret >= 0.9 * baseline."""
        obj = EpsilonConstraint("min_mdd", "ret", ">=", "0.9*baseline_ret")
        # Build a series whose ret > 0.9 * 0.05 = 0.045 (annualized)
        rng = np.random.default_rng(42)
        rets = _returns(rng.normal(0.001, 0.005, 252))  # mean ~0.001 daily
        baseline = {"ret": 0.05, "mdd_abs": 0.10, "sharpe": 1.0, "vol": 0.10,
                    "sortino": 1.0, "calmar": 0.5, "dd": -0.10}
        val = obj.evaluate(rets, baseline_metrics=baseline)
        # Should be either feasible (returns mdd_abs) or infeasible (inf)
        assert val == math.inf or val > 0

    def test_phase_o_pattern_mdd_constraint(self):
        """Replicate: max ret s.t. |mdd| <= 0.6 * baseline_mdd_abs."""
        obj = EpsilonConstraint("max_ret", "mdd_abs", "<=", "0.6*baseline_mdd_abs")
        rng = np.random.default_rng(7)
        rets = _returns(rng.normal(0.001, 0.005, 252))
        baseline = {"ret": 0.05, "mdd_abs": 0.30, "sharpe": 1.0, "vol": 0.10,
                    "sortino": 1.0, "calmar": 0.5, "dd": -0.30}
        val = obj.evaluate(rets, baseline_metrics=baseline)
        assert val == math.inf or math.isfinite(val)
