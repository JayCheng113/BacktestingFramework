"""Unit tests for ez.research._metrics."""
from __future__ import annotations
import math

import numpy as np
import pandas as pd
import pytest

from ez.research._metrics import compute_basic_metrics, compute_cvar


def _returns(values):
    return pd.Series(
        values,
        index=pd.date_range("2024-01-01", periods=len(values), freq="B"),
        dtype=float,
    )


# ============================================================
# compute_basic_metrics
# ============================================================

class TestComputeBasicMetrics:
    def test_none_input(self):
        assert compute_basic_metrics(None) is None

    def test_empty_series(self):
        assert compute_basic_metrics(pd.Series([], dtype=float)) is None

    def test_single_value(self):
        assert compute_basic_metrics(_returns([0.01])) is None

    def test_all_nan(self):
        s = pd.Series([float("nan")] * 5, index=pd.date_range("2024-01-01", periods=5, freq="B"))
        assert compute_basic_metrics(s) is None

    def test_total_loss_more_than_100_percent_returns_none(self):
        # -200% return on day 2 → equity goes negative
        s = _returns([0.0, -2.0])
        assert compute_basic_metrics(s) is None

    def test_normal_returns_returns_full_dict(self):
        rng = np.random.default_rng(42)
        s = _returns(rng.normal(0.0005, 0.015, 252))
        m = compute_basic_metrics(s)
        assert m is not None
        # All expected keys present
        for key in ["ret", "sharpe", "sortino", "vol", "dd", "mdd_abs", "calmar"]:
            assert key in m, f"missing key: {key}"
        # All values are floats
        for k, v in m.items():
            assert isinstance(v, float)

    def test_dd_is_negative(self):
        # A clear drawdown: up-up-down
        s = _returns([0.05, 0.05, -0.20])
        m = compute_basic_metrics(s)
        assert m is not None
        assert m["dd"] < 0

    def test_mdd_abs_equals_abs_dd(self):
        s = _returns([0.05, 0.05, -0.20, 0.10])
        m = compute_basic_metrics(s)
        assert m is not None
        assert m["mdd_abs"] == abs(m["dd"])

    def test_calmar_zero_when_no_drawdown(self):
        # Monotonically increasing returns → no drawdown → mdd_abs ≈ 0
        s = _returns([0.001] * 100)
        m = compute_basic_metrics(s)
        assert m is not None
        # mdd_abs should be near zero, calmar consequently 0
        assert m["mdd_abs"] < 1e-9
        assert m["calmar"] == 0.0

    def test_calmar_positive_when_positive_ret_with_drawdown(self):
        # Positive overall return with a drawdown midway
        rng = np.random.default_rng(7)
        rets = list(rng.normal(0.001, 0.01, 100))
        rets[50] = -0.05  # inject a single drop
        s = _returns(rets)
        m = compute_basic_metrics(s)
        assert m is not None
        assert m["ret"] != 0
        if m["mdd_abs"] > 1e-9:
            expected_calmar = m["ret"] / m["mdd_abs"]
            assert abs(m["calmar"] - expected_calmar) < 1e-9

    def test_sharpe_zero_for_constant_returns(self):
        """Constant returns → zero std → sharpe should be 0 (V2.12.2 guard)."""
        s = _returns([0.001] * 50)
        m = compute_basic_metrics(s)
        assert m is not None
        assert m["sharpe"] == 0.0

    def test_short_series_does_not_crash(self):
        """2-bar series should produce a dict (vol guarded)."""
        s = _returns([0.01, 0.02])
        m = compute_basic_metrics(s)
        assert m is not None
        # At minimum sharpe should not be NaN
        assert not math.isnan(m["sharpe"])


# ============================================================
# compute_cvar
# ============================================================

class TestComputeCVaR:
    def test_none_input(self):
        assert compute_cvar(None) is None

    def test_too_few_observations(self):
        assert compute_cvar(_returns([-0.01, 0.02])) is None
        assert compute_cvar(_returns([-0.01] * 9)) is None

    def test_alpha_out_of_range(self):
        with pytest.raises(ValueError, match="alpha must be in"):
            compute_cvar(_returns([0.01] * 20), alpha=0.0)
        with pytest.raises(ValueError, match="alpha must be in"):
            compute_cvar(_returns([0.01] * 20), alpha=1.0)
        with pytest.raises(ValueError, match="alpha must be in"):
            compute_cvar(_returns([0.01] * 20), alpha=-0.1)

    def test_cvar_is_negative_for_loss_distribution(self):
        rng = np.random.default_rng(42)
        # Distribution centered slightly negative
        s = _returns(rng.normal(-0.001, 0.02, 100))
        cvar = compute_cvar(s)
        assert cvar is not None
        assert cvar < 0

    def test_cvar_alpha_5_uses_worst_5_percent(self):
        # Construct a known series: 95 zeros + 5 large losses
        s = _returns([0.0] * 95 + [-0.10] * 5)
        cvar = compute_cvar(s, alpha=0.05)
        assert cvar is not None
        # The 5th percentile of [0]*95 + [-0.10]*5 is about -0.10
        # The tail (returns ≤ -0.10) average is -0.10
        assert -0.105 < cvar < -0.095

    def test_cvar_drops_nan(self):
        # Mix in some NaN — should be ignored
        rng = np.random.default_rng(7)
        values = rng.normal(0.0, 0.02, 50).tolist()
        values[10] = float("nan")
        values[20] = float("nan")
        s = _returns(values)
        cvar = compute_cvar(s)
        assert cvar is not None
        assert not math.isnan(cvar)

    def test_cvar_alpha_in_range_check(self):
        # Use a tight alpha
        s = _returns([0.01] * 50 + [-0.05] * 50)
        cvar_5 = compute_cvar(s, alpha=0.05)
        cvar_10 = compute_cvar(s, alpha=0.10)
        assert cvar_5 is not None
        assert cvar_10 is not None
        # 5% tail and 10% tail are both inside the lower half
        # so both should be in the loss region
        assert cvar_5 < 0
        assert cvar_10 < 0


# ============================================================
# Integration: compute_basic_metrics output is consistent with
# ez.backtest.metrics.MetricsCalculator on the equivalent equity curve
# ============================================================

def test_compute_basic_metrics_matches_underlying_calculator():
    """Sanity: the wrapper should not introduce numerical drift
    relative to the underlying MetricsCalculator."""
    from ez.backtest.metrics import MetricsCalculator

    rng = np.random.default_rng(123)
    rets = _returns(rng.normal(0.001, 0.015, 200))

    # Our wrapper
    m_wrap = compute_basic_metrics(rets)
    assert m_wrap is not None

    # Same input via the underlying calculator
    equity = (1 + rets).cumprod()
    bench = pd.Series([1.0] * len(equity), index=equity.index)
    calc = MetricsCalculator()
    m_calc = calc.compute(equity, bench)

    # Compare key-by-key (with renames)
    assert abs(m_wrap["ret"] - m_calc["annualized_return"]) < 1e-12
    assert abs(m_wrap["sharpe"] - m_calc["sharpe_ratio"]) < 1e-12
    assert abs(m_wrap["sortino"] - m_calc["sortino_ratio"]) < 1e-12
    assert abs(m_wrap["vol"] - m_calc["annualized_volatility"]) < 1e-12
    assert abs(m_wrap["dd"] - m_calc["max_drawdown"]) < 1e-12
