"""Unit tests for NaNInfGuard.

Factor kind uses DataFrame-returning compute; guard scans new columns only.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.nan_inf import NaNInfGuard


class _CleanFactor:
    name = "clean_factor"
    warmup_period = 20

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["close"].rolling(20).mean()
        return out


class _LogOfZeroFactor:
    """Division-free log-of-zero → -inf."""
    name = "log_zero"
    warmup_period = 0

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = np.log(df["close"] - df["close"])
        return out


class _NaNCrossFactor:
    name = "nan_cross"
    warmup_period = 0

    def compute(self, panel, target_date) -> pd.Series:
        return pd.Series({s: float("nan") for s in panel})


class _InfPortfolio:
    warmup_period = 0

    def generate_weights(self, panel, target_date, prev_w, prev_r):
        return {s: float("inf") for s in panel}


class _CleanPortfolio:
    warmup_period = 0

    def generate_weights(self, panel, target_date, prev_w, prev_r):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _EmptyPortfolio:
    warmup_period = 0

    def generate_weights(self, panel, target_date, prev_w, prev_r):
        return {}


def _ctx(user_class, kind):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind=kind, user_class=user_class,
    )


def test_clean_factor_passes_with_warmup_nans():
    result = NaNInfGuard().check(_ctx(_CleanFactor, "factor"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_log_of_zero_factor_blocked():
    result = NaNInfGuard().check(_ctx(_LogOfZeroFactor, "factor"))
    assert result.severity == GuardSeverity.BLOCK
    assert "nan" in result.message.lower() or "inf" in result.message.lower()


def test_nan_cross_factor_blocked():
    result = NaNInfGuard().check(_ctx(_NaNCrossFactor, "cross_factor"))
    assert result.severity == GuardSeverity.BLOCK


def test_inf_portfolio_blocked():
    result = NaNInfGuard().check(_ctx(_InfPortfolio, "portfolio_strategy"))
    assert result.severity == GuardSeverity.BLOCK


def test_clean_portfolio_passes():
    result = NaNInfGuard().check(_ctx(_CleanPortfolio, "portfolio_strategy"))
    assert result.severity == GuardSeverity.PASS


def test_empty_portfolio_passes():
    """Empty dict has no bad values → pass."""
    result = NaNInfGuard().check(_ctx(_EmptyPortfolio, "portfolio_strategy"))
    assert result.severity == GuardSeverity.PASS
