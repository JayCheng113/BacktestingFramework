"""Time series operations — C++ accelerated with Python/pandas fallback.

Tries to import C++ implementations from _ts_ops_cpp (built via nanobind).
Falls back to pandas wrappers if the C++ extension is not compiled.
Callers see the same interface either way.

Convention: all functions take a pd.Series and return a pd.Series of same length.
NaN fills positions where the window is insufficient (min_periods=window).
"""
from __future__ import annotations

import numpy as np
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


def _to_contig(s: pd.Series) -> np.ndarray:
    """Ensure contiguous float64 array for C++."""
    return np.ascontiguousarray(s.values, dtype=np.float64)


def rolling_mean(s: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    if window <= 0:
        raise ValueError("window must be positive")
    if _USE_CPP:
        return pd.Series(_cpp_rolling_mean(_to_contig(s), window), index=s.index, name=s.name)
    return s.rolling(window=window, min_periods=window).mean()


def rolling_std(s: pd.Series, window: int, ddof: int = 1) -> pd.Series:
    """Rolling standard deviation (sample std by default, ddof=1)."""
    if window <= 0:
        raise ValueError("window must be positive")
    if _USE_CPP:
        return pd.Series(_cpp_rolling_std(_to_contig(s), window, ddof), index=s.index, name=s.name)
    return s.rolling(window=window, min_periods=window).std(ddof=ddof)


def ewm_mean(s: pd.Series, span: int) -> pd.Series:
    """Exponential weighted moving average."""
    if span <= 0:
        raise ValueError("span must be positive")
    if _USE_CPP:
        return pd.Series(_cpp_ewm_mean(_to_contig(s), span), index=s.index, name=s.name)
    return s.ewm(span=span, min_periods=span).mean()


def diff(s: pd.Series, periods: int = 1) -> pd.Series:
    """First difference."""
    if periods <= 0:
        raise ValueError("periods must be positive")
    if _USE_CPP:
        return pd.Series(_cpp_diff(_to_contig(s), periods), index=s.index, name=s.name)
    return s.diff(periods=periods)


def pct_change(s: pd.Series, periods: int = 1) -> pd.Series:
    """Percentage change over N periods."""
    if periods <= 0:
        raise ValueError("periods must be positive")
    if _USE_CPP:
        return pd.Series(_cpp_pct_change(_to_contig(s), periods), index=s.index, name=s.name)
    return s.pct_change(periods=periods, fill_method=None)
