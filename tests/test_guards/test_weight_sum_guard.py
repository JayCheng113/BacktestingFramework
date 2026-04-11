"""Unit tests for WeightSumGuard."""
from __future__ import annotations
from pathlib import Path

import pandas as pd

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.weight_sum import WeightSumGuard


class _FullyInvested:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _CashHeavy:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        return {list(panel)[0]: 0.5}


class _OverLevered:
    """5 symbols × 0.5 = 2.5 — far over-leveraged."""
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        return {s: 0.5 for s in panel}


class _NetShort:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        return {list(panel)[0]: -0.5}


class _DateDependentBug:
    """Returns sum=1 on early dates but sum=2 on late dates."""
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        threshold = pd.Timestamp("2024-06-01")
        mult = 2.0 if pd.Timestamp(target_date) > threshold else 1.0
        return {list(panel)[0]: mult}


def _ctx(user_class):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind="portfolio_strategy", user_class=user_class,
    )


def test_fully_invested_passes():
    result = WeightSumGuard().check(_ctx(_FullyInvested))
    assert result.severity == GuardSeverity.PASS, result.message


def test_cash_heavy_passes():
    result = WeightSumGuard().check(_ctx(_CashHeavy))
    assert result.severity == GuardSeverity.PASS


def test_over_levered_blocked():
    result = WeightSumGuard().check(_ctx(_OverLevered))
    assert result.severity == GuardSeverity.BLOCK
    assert "sum" in result.message.lower() or "leverage" in result.message.lower()


def test_net_short_blocked():
    result = WeightSumGuard().check(_ctx(_NetShort))
    assert result.severity == GuardSeverity.BLOCK


def test_date_dependent_bug_caught():
    """Guard checks 5 dates — catches date-dependent over-leverage."""
    result = WeightSumGuard().check(_ctx(_DateDependentBug))
    assert result.severity == GuardSeverity.BLOCK
