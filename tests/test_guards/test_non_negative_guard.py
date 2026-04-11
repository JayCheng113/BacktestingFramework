"""Unit tests for NonNegativeWeightsGuard."""
from __future__ import annotations
from pathlib import Path

import pandas as pd

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.non_negative import NonNegativeWeightsGuard


class _AllPositive:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _OneNegative:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        syms = list(panel)
        return {syms[0]: -0.1, syms[1]: 0.5, syms[2]: 0.6}


class _TinyNegative:
    """Below -1e-9 tolerance → not flagged."""
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        return {list(panel)[0]: -1e-12, list(panel)[1]: 0.9}


class _NegativeOnLateDateOnly:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        if pd.Timestamp(target_date) > pd.Timestamp("2024-09-01"):
            return {list(panel)[0]: -0.1, list(panel)[1]: 0.5}
        return {list(panel)[0]: 1.0}


def _ctx(user_class):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind="portfolio_strategy", user_class=user_class,
    )


def test_all_positive_passes():
    result = NonNegativeWeightsGuard().check(_ctx(_AllPositive))
    assert result.severity == GuardSeverity.PASS, result.message


def test_one_negative_warns():
    result = NonNegativeWeightsGuard().check(_ctx(_OneNegative))
    assert result.severity == GuardSeverity.WARN


def test_tiny_negative_tolerated():
    result = NonNegativeWeightsGuard().check(_ctx(_TinyNegative))
    assert result.severity == GuardSeverity.PASS


def test_late_date_bug_caught():
    result = NonNegativeWeightsGuard().check(_ctx(_NegativeOnLateDateOnly))
    assert result.severity == GuardSeverity.WARN
