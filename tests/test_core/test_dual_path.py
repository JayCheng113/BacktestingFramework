"""A2: C++/Python dual-path parity tests -- V2.3 correctness hardening.

For each ts_ops function, runs with _USE_CPP=True and _USE_CPP=False,
then compares results column-by-column. Difference must be < EPS.

Skipped automatically when C++ extension is not compiled.
"""

import numpy as np
import pandas as pd
import pytest

import ez.core.ts_ops as ts_ops

EPS = 0.01  # V2.3 universal tolerance

# Skip entire module if C++ is not available
cpp_available = False
try:
    from ez.core._ts_ops_cpp import rolling_mean as _probe  # noqa: F401
    cpp_available = True
except ImportError:
    pass

pytestmark = pytest.mark.skipif(not cpp_available, reason="C++ extension not compiled")


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_series():
    """Regular price series — no NaN."""
    return pd.Series(
        [100, 102, 98, 105, 110, 95, 100, 108, 112, 106,
         103, 107, 111, 104, 99, 101, 105, 109, 113, 108],
        dtype=float,
        name="price",
    )


@pytest.fixture
def nan_series():
    """Series with NaN gaps to test NaN handling parity."""
    return pd.Series(
        [100, np.nan, 98, 105, 110, np.nan, 100, 108, np.nan, 106,
         103, 107, np.nan, 104, 99, 101, 105, np.nan, 113, 108],
        dtype=float,
        name="price_nan",
    )


@pytest.fixture
def short_series():
    """Very short series — edge case."""
    return pd.Series([50.0, 51.0, 49.0], dtype=float, name="short")


@pytest.fixture
def constant_series():
    """Constant values — std should be 0."""
    return pd.Series([42.0] * 15, dtype=float, name="const")


@pytest.fixture
def zero_series():
    """Series with zeros — tests div-by-zero in pct_change."""
    return pd.Series([0.0, 1.0, 0.0, 2.0, 0.0], dtype=float, name="zeros")


ALL_SERIES = ["clean_series", "nan_series", "short_series", "constant_series"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_dual(func_name: str, series: pd.Series, **kwargs):
    """Run a ts_ops function in both C++ and Python mode, return both results."""
    func = getattr(ts_ops, func_name)
    orig = ts_ops._USE_CPP

    try:
        ts_ops._USE_CPP = True
        cpp_result = func(series.copy(), **kwargs)

        ts_ops._USE_CPP = False
        py_result = func(series.copy(), **kwargs)
    finally:
        ts_ops._USE_CPP = orig

    return cpp_result, py_result


def _assert_parity(cpp: pd.Series, py: pd.Series, label: str = ""):
    """Assert two series are identical within EPS, handling NaN."""
    assert len(cpp) == len(py), f"{label}: length mismatch {len(cpp)} vs {len(py)}"

    cpp_vals = cpp.values
    py_vals = py.values

    for i in range(len(cpp_vals)):
        c, p = cpp_vals[i], py_vals[i]
        both_nan = np.isnan(c) and np.isnan(p)
        both_inf = np.isinf(c) and np.isinf(p) and np.sign(c) == np.sign(p)

        if both_nan or both_inf:
            continue

        if np.isnan(c) != np.isnan(p):
            pytest.fail(f"{label} idx {i}: NaN mismatch (cpp={c}, py={p})")
        if np.isinf(c) != np.isinf(p):
            pytest.fail(f"{label} idx {i}: inf mismatch (cpp={c}, py={p})")

        assert abs(c - p) <= EPS, (
            f"{label} idx {i}: cpp={c:.8f} py={p:.8f} diff={abs(c - p):.10f}"
        )

    # Also check series name preserved
    assert cpp.name == py.name, f"{label}: name mismatch {cpp.name} vs {py.name}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRollingMeanParity:
    @pytest.mark.parametrize("series_name", ALL_SERIES)
    @pytest.mark.parametrize("window", [1, 3, 5, 10])
    def test_parity(self, series_name, window, request):
        s = request.getfixturevalue(series_name)
        if window > len(s):
            pytest.skip("window > series length")
        cpp, py = _run_dual("rolling_mean", s, window=window)
        _assert_parity(cpp, py, f"rolling_mean(w={window})")


class TestRollingStdParity:
    @pytest.mark.parametrize("series_name", ALL_SERIES)
    @pytest.mark.parametrize("window", [2, 3, 5, 10])
    @pytest.mark.parametrize("ddof", [0, 1])
    def test_parity(self, series_name, window, ddof, request):
        s = request.getfixturevalue(series_name)
        if window > len(s):
            pytest.skip("window > series length")
        cpp, py = _run_dual("rolling_std", s, window=window, ddof=ddof)
        _assert_parity(cpp, py, f"rolling_std(w={window},ddof={ddof})")


class TestEwmMeanParity:
    @pytest.mark.parametrize("series_name", ALL_SERIES)
    @pytest.mark.parametrize("span", [2, 3, 5, 10])
    def test_parity(self, series_name, span, request):
        s = request.getfixturevalue(series_name)
        if span > len(s):
            pytest.skip("span > series length")
        cpp, py = _run_dual("ewm_mean", s, span=span)
        _assert_parity(cpp, py, f"ewm_mean(span={span})")


class TestDiffParity:
    @pytest.mark.parametrize("series_name", ALL_SERIES)
    @pytest.mark.parametrize("periods", [1, 2, 5])
    def test_parity(self, series_name, periods, request):
        s = request.getfixturevalue(series_name)
        if periods >= len(s):
            pytest.skip("periods >= series length")
        cpp, py = _run_dual("diff", s, periods=periods)
        _assert_parity(cpp, py, f"diff(p={periods})")


class TestPctChangeParity:
    @pytest.mark.parametrize("series_name", ALL_SERIES)
    @pytest.mark.parametrize("periods", [1, 2, 5])
    def test_parity(self, series_name, periods, request):
        s = request.getfixturevalue(series_name)
        if periods >= len(s):
            pytest.skip("periods >= series length")
        cpp, py = _run_dual("pct_change", s, periods=periods)
        _assert_parity(cpp, py, f"pct_change(p={periods})")

    def test_div_by_zero_parity(self, zero_series):
        """Both paths should produce inf for x/0."""
        cpp, py = _run_dual("pct_change", zero_series, periods=1)
        _assert_parity(cpp, py, "pct_change(div-by-zero)")


class TestParityParameterValidation:
    """Both paths should raise the same errors for invalid params."""

    def test_rolling_mean_non_positive_window(self):
        s = pd.Series([1.0, 2.0, 3.0])
        for use_cpp in [True, False]:
            ts_ops._USE_CPP = use_cpp
            with pytest.raises(ValueError, match="positive"):
                ts_ops.rolling_mean(s, window=0)
        ts_ops._USE_CPP = cpp_available

    def test_rolling_std_non_positive_window(self):
        s = pd.Series([1.0, 2.0, 3.0])
        for use_cpp in [True, False]:
            ts_ops._USE_CPP = use_cpp
            with pytest.raises(ValueError, match="positive"):
                ts_ops.rolling_std(s, window=-1)
        ts_ops._USE_CPP = cpp_available

    def test_ewm_mean_non_positive_span(self):
        s = pd.Series([1.0, 2.0, 3.0])
        for use_cpp in [True, False]:
            ts_ops._USE_CPP = use_cpp
            with pytest.raises(ValueError, match="positive"):
                ts_ops.ewm_mean(s, span=0)
        ts_ops._USE_CPP = cpp_available

    def test_diff_non_positive_periods(self):
        s = pd.Series([1.0, 2.0, 3.0])
        for use_cpp in [True, False]:
            ts_ops._USE_CPP = use_cpp
            with pytest.raises(ValueError, match="positive"):
                ts_ops.diff(s, periods=0)
        ts_ops._USE_CPP = cpp_available

    def test_pct_change_non_positive_periods(self):
        s = pd.Series([1.0, 2.0, 3.0])
        for use_cpp in [True, False]:
            ts_ops._USE_CPP = use_cpp
            with pytest.raises(ValueError, match="positive"):
                ts_ops.pct_change(s, periods=-1)
        ts_ops._USE_CPP = cpp_available
