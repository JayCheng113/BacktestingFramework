"""Unit tests for ez.research.optimizers base types and 4 simple objectives."""
from __future__ import annotations
import math

import numpy as np
import pandas as pd
import pytest

from ez.research.optimizers import (
    OptimalWeights,
    Objective,
    MaxSharpe,
    MaxCalmar,
    MaxSortino,
    MinCVaR,
)


def _returns(values):
    return pd.Series(
        values,
        index=pd.date_range("2024-01-01", periods=len(values), freq="B"),
        dtype=float,
    )


# ============================================================
# OptimalWeights dataclass
# ============================================================

class TestOptimalWeights:
    def test_default_status_is_converged(self):
        ow = OptimalWeights(
            objective_name="X",
            weights={"a": 0.5, "b": 0.5},
        )
        assert ow.optimizer_status == "converged"
        assert ow.is_feasible
        assert ow.is_metrics == {}

    def test_infeasible_status(self):
        ow = OptimalWeights(
            objective_name="X",
            weights={"a": 0.0, "b": 0.0},
            optimizer_status="infeasible",
        )
        assert not ow.is_feasible

    def test_frozen(self):
        ow = OptimalWeights(objective_name="X", weights={})
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            ow.objective_name = "Y"


# ============================================================
# MaxSharpe
# ============================================================

class TestMaxSharpe:
    def test_normal_returns(self):
        rng = np.random.default_rng(42)
        rets = _returns(rng.normal(0.001, 0.015, 252))
        obj = MaxSharpe()
        val = obj.evaluate(rets)
        # Sharpe is finite, so -sharpe is finite
        assert math.isfinite(val)
        # MaxSharpe should prefer higher sharpe → return more negative

    def test_empty_returns_returns_inf(self):
        obj = MaxSharpe()
        assert obj.evaluate(pd.Series([], dtype=float)) == math.inf

    def test_single_return_returns_inf(self):
        obj = MaxSharpe()
        assert obj.evaluate(_returns([0.01])) == math.inf

    def test_total_loss_returns_inf(self):
        obj = MaxSharpe()
        assert obj.evaluate(_returns([0.0, -2.0])) == math.inf

    def test_better_sharpe_lower_value(self):
        """MaxSharpe is monotonic — better sharpe → lower (more negative) val."""
        rng = np.random.default_rng(7)
        # Series A: high mean, low vol
        a = _returns(rng.normal(0.002, 0.01, 200))
        # Series B: same mean, high vol
        b = _returns(rng.normal(0.002, 0.05, 200))
        obj = MaxSharpe()
        val_a = obj.evaluate(a)
        val_b = obj.evaluate(b)
        # A has higher sharpe → val_a is more negative → val_a < val_b
        assert val_a < val_b


# ============================================================
# MaxCalmar
# ============================================================

class TestMaxCalmar:
    def test_normal_returns_with_drawdown(self):
        # Use a clearly positive trend so calmar > 0 deterministically
        rets = [0.005] * 50 + [-0.03] + [0.005] * 50  # +25% trend, -3% dip
        obj = MaxCalmar()
        val = obj.evaluate(_returns(rets))
        assert math.isfinite(val)
        # Better calmar → more negative value
        assert val < 0

    def test_negative_calmar_returns_inf(self):
        """If annualized return is negative, calmar is non-positive
        and MaxCalmar treats it as infeasible (matches phase_o convention)."""
        rng = np.random.default_rng(7)
        rets = _returns(rng.normal(-0.005, 0.02, 200))
        obj = MaxCalmar()
        # Most random draws will give negative ret → inf
        val = obj.evaluate(rets)
        # Could be either depending on the rng, but if calmar > 0 we get
        # finite negative value
        m_check = pd.Series(rets).pct_change()  # just to compute
        # Test the contract directly
        from ez.research._metrics import compute_basic_metrics
        m = compute_basic_metrics(_returns(rets))
        if m and m["calmar"] <= 0:
            assert val == math.inf
        else:
            assert math.isfinite(val)

    def test_empty_returns_inf(self):
        assert MaxCalmar().evaluate(pd.Series([], dtype=float)) == math.inf


# ============================================================
# MaxSortino
# ============================================================

class TestMaxSortino:
    def test_normal_returns(self):
        rng = np.random.default_rng(42)
        rets = _returns(rng.normal(0.001, 0.015, 252))
        val = MaxSortino().evaluate(rets)
        assert math.isfinite(val)

    def test_empty_returns_inf(self):
        assert MaxSortino().evaluate(pd.Series([], dtype=float)) == math.inf


# ============================================================
# MinCVaR
# ============================================================

class TestMinCVaR:
    def test_default_alpha_5pct(self):
        obj = MinCVaR()
        assert obj.alpha == 0.05
        assert "5%" in obj.name

    def test_custom_alpha(self):
        obj = MinCVaR(alpha=0.10)
        assert obj.alpha == 0.10
        assert "10%" in obj.name

    def test_alpha_validation(self):
        with pytest.raises(ValueError, match="alpha must be in"):
            MinCVaR(alpha=0.0)
        with pytest.raises(ValueError, match="alpha must be in"):
            MinCVaR(alpha=1.0)
        with pytest.raises(ValueError, match="alpha must be in"):
            MinCVaR(alpha=-0.1)

    def test_normal_returns(self):
        rng = np.random.default_rng(42)
        rets = _returns(rng.normal(0.0, 0.02, 200))
        val = MinCVaR().evaluate(rets)
        # CVaR at 5% will be negative; -CVaR will be positive
        assert math.isfinite(val)
        assert val > 0  # -negative_cvar = positive

    def test_empty_returns_inf(self):
        assert MinCVaR().evaluate(pd.Series([], dtype=float)) == math.inf

    def test_too_few_observations_inf(self):
        # compute_cvar requires >= 10 obs
        assert MinCVaR().evaluate(_returns([0.01] * 5)) == math.inf

    def test_lower_loss_lower_value(self):
        """MinCVaR is monotonic — smaller losses → lower (better) val.

        Two series:
          - A: well-controlled returns (small tail loss)
          - B: same mean but heavy tails
        A's MinCVaR objective should be lower (better) than B's.
        """
        rng = np.random.default_rng(7)
        a = _returns(rng.normal(0.0, 0.005, 200))   # tight
        b_vals = list(rng.normal(0.0, 0.005, 195)) + [-0.20] * 5  # 5 huge losses
        b = _returns(b_vals)
        obj = MinCVaR(alpha=0.05)
        val_a = obj.evaluate(a)
        val_b = obj.evaluate(b)
        assert val_a < val_b  # tight loss tail wins


# ============================================================
# Objective ABC contract — subclasses must implement evaluate
# ============================================================

class TestObjectiveABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError, match="abstract"):
            Objective()

    def test_subclass_must_implement_evaluate(self):
        class _BadSubclass(Objective):
            pass
        with pytest.raises(TypeError, match="abstract"):
            _BadSubclass()
