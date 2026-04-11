"""Unit tests for DeterminismGuard."""
from __future__ import annotations
import random

import pandas as pd
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.determinism import DeterminismGuard


class _DeterministicFactor:
    name = "det"
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["close"].rolling(5).mean()
        return out


class _UnseededRandomPortfolio:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        weights = {sym: random.random() for sym in panel}
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}


class _DeterministicPortfolio:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _NoneOutput:
    warmup_period = 0
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        return None


def _ctx(user_class, kind="portfolio_strategy"):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind=kind, user_class=user_class,
    )


def test_deterministic_factor_passes():
    result = DeterminismGuard().check(_ctx(_DeterministicFactor, "factor"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_unseeded_random_warns():
    result = DeterminismGuard().check(_ctx(_UnseededRandomPortfolio))
    assert result.severity == GuardSeverity.WARN
    assert "different" in result.message.lower() or "determin" in result.message.lower()


def test_deterministic_portfolio_passes():
    result = DeterminismGuard().check(_ctx(_DeterministicPortfolio))
    assert result.severity == GuardSeverity.PASS


def test_none_output_passes():
    """Both runs return None → canonicalize equal → pass."""
    result = DeterminismGuard().check(_ctx(_NoneOutput))
    assert result.severity == GuardSeverity.PASS


def test_determinism_guard_never_blocks():
    """DeterminismGuard is Tier 2 warn — never block."""
    result = DeterminismGuard().check(_ctx(_UnseededRandomPortfolio))
    assert result.tier == "warn"
    assert result.severity != GuardSeverity.BLOCK
