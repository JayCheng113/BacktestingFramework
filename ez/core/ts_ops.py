"""Time series operations — Python/pandas implementations.

These are the C++ replacement points for V2.1. Each function is a thin wrapper
around pandas that can be swapped for a nanobind-bound C++ implementation
without changing any caller.

Convention: all functions take a pd.Series and return a pd.Series of same length.
NaN fills positions where the window is insufficient (min_periods=window).
"""
from __future__ import annotations

import pandas as pd


def rolling_mean(s: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    return s.rolling(window=window, min_periods=window).mean()


def rolling_std(s: pd.Series, window: int, ddof: int = 1) -> pd.Series:
    """Rolling standard deviation (sample std by default, ddof=1)."""
    return s.rolling(window=window, min_periods=window).std(ddof=ddof)


def ewm_mean(s: pd.Series, span: int) -> pd.Series:
    """Exponential weighted moving average."""
    return s.ewm(span=span, min_periods=span).mean()


def diff(s: pd.Series, periods: int = 1) -> pd.Series:
    """First difference."""
    return s.diff(periods=periods)


def pct_change(s: pd.Series, periods: int = 1) -> pd.Series:
    """Percentage change over N periods."""
    return s.pct_change(periods=periods)
