"""Time series operations — C++ accelerated with Python/pandas fallback.

Tries to import C++ implementations from _ts_ops_cpp (built via nanobind).
Falls back to pandas wrappers if the C++ extension is not compiled.
Callers see the same interface either way.

Convention: all functions take a pd.Series and return a pd.Series of same length.
NaN fills positions where the window is insufficient (min_periods=window).
"""
from __future__ import annotations

import pandas as pd

_USE_CPP = False

try:
    from ez.core._ts_ops_cpp import (
        rolling_mean as _cpp_rolling_mean,
        rolling_std as _cpp_rolling_std,
        ewm_mean as _cpp_ewm_mean,
        diff as _cpp_diff,
        pct_change as _cpp_pct_change,
    )
    _USE_CPP = True
except ImportError:
    pass


def rolling_mean(s: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    if _USE_CPP:
        result = _cpp_rolling_mean(s.values, window)
        return pd.Series(result, index=s.index)
    return s.rolling(window=window, min_periods=window).mean()


def rolling_std(s: pd.Series, window: int, ddof: int = 1) -> pd.Series:
    """Rolling standard deviation (sample std by default, ddof=1)."""
    if _USE_CPP:
        result = _cpp_rolling_std(s.values, window, ddof)
        return pd.Series(result, index=s.index)
    return s.rolling(window=window, min_periods=window).std(ddof=ddof)


def ewm_mean(s: pd.Series, span: int) -> pd.Series:
    """Exponential weighted moving average."""
    if _USE_CPP:
        result = _cpp_ewm_mean(s.values, span)
        return pd.Series(result, index=s.index)
    return s.ewm(span=span, min_periods=span).mean()


def diff(s: pd.Series, periods: int = 1) -> pd.Series:
    """First difference."""
    if _USE_CPP:
        result = _cpp_diff(s.values, periods)
        return pd.Series(result, index=s.index)
    return s.diff(periods=periods)


def pct_change(s: pd.Series, periods: int = 1) -> pd.Series:
    """Percentage change over N periods."""
    if _USE_CPP:
        result = _cpp_pct_change(s.values, periods)
        return pd.Series(result, index=s.index)
    return s.pct_change(periods=periods, fill_method=None)
