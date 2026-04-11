"""Unit tests for LookaheadGuard: 3-run shuffle-future test.

Factor kind uses DataFrame-returning compute (new columns appended).
Strategy kind uses Series-returning generate_signals with required_factors().
"""
from __future__ import annotations
import time
import random

import pandas as pd
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.lookahead import LookaheadGuard
from ez.factor.base import Factor


# ============================================================
# Factor test subjects
# ============================================================

class _CleanFactor:
    """Rolling mean — only reads past, no lookahead."""
    name = "clean_factor"
    warmup_period = 5

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["close"].rolling(5).mean()
        return out


class _LookaheadShiftFactor:
    """shift(-1) = reads next day's close — classic lookahead."""
    name = "lookahead_shift"
    warmup_period = 0

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["close"].shift(-1)
        return out


class _LookaheadExplicitFactor:
    """Uses iloc[i+1] to read future explicitly."""
    name = "lookahead_explicit"
    warmup_period = 0

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        closes = df["close"].values
        n = len(closes)
        series_vals = [closes[min(i + 1, n - 1)] for i in range(n)]
        out[self.name] = series_vals
        return out


class _NonDeterministicFactor:
    """Unseeded RNG — LookaheadGuard should surface as WARN, not BLOCK."""
    name = "nondet_factor"
    warmup_period = 0

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["close"] + random.random()
        return out


# ============================================================
# Strategy test subjects (need Strategy.required_factors + generate_signals)
# ============================================================

class _PassThroughFactor(Factor):
    """Concrete factor that the clean strategy depends on."""
    name = "ptf_ma_5"
    warmup_period = 5

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        out = data.copy()
        out[self.name] = data["adj_close"].rolling(5).mean()
        return out


class _CleanStrategy:
    """required_factors=[] so we don't need to hit the factor registry."""
    def required_factors(self):
        return [_PassThroughFactor()]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        col = "ptf_ma_5"
        return (data["adj_close"] > data[col]).astype(float)


class _LookaheadStrategy:
    """Uses shift(-1) and returns the raw future value, so guard catches
    the shuffle directly (not via a boolean that can coincidentally match)."""
    def required_factors(self):
        return []

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        # Signal at position t is close[t+1] — direct future read.
        return data["close"].shift(-1).ffill()


def _ctx(user_class, kind):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind=kind, user_class=user_class,
    )


# ============================================================
# Factor tests
# ============================================================

def test_clean_factor_passes():
    result = LookaheadGuard().check(_ctx(_CleanFactor, "factor"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_shift_lookahead_factor_blocked():
    result = LookaheadGuard().check(_ctx(_LookaheadShiftFactor, "factor"))
    assert result.severity == GuardSeverity.BLOCK
    assert "future" in result.message.lower() or "shuffle" in result.message.lower()


def test_explicit_lookahead_factor_blocked():
    result = LookaheadGuard().check(_ctx(_LookaheadExplicitFactor, "factor"))
    assert result.severity == GuardSeverity.BLOCK


def test_non_deterministic_factor_warns_not_blocks():
    """Non-deterministic code cannot be lookahead-tested; returns WARN."""
    result = LookaheadGuard().check(_ctx(_NonDeterministicFactor, "factor"))
    assert result.severity == GuardSeverity.WARN
    assert "non-deterministic" in result.message.lower()


def test_user_class_none_blocks_with_reason():
    ctx = GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind="factor", user_class=None, instantiation_error="not found",
    )
    result = LookaheadGuard().check(ctx)
    assert result.severity == GuardSeverity.BLOCK
    assert "not found" in result.message or "could not load" in result.message.lower()


def test_runtime_under_150ms_per_guard():
    t0 = time.perf_counter()
    LookaheadGuard().check(_ctx(_CleanFactor, "factor"))
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 250, f"LookaheadGuard too slow: {elapsed:.1f} ms"


# ============================================================
# Strategy tests
# ============================================================

def test_clean_strategy_passes():
    result = LookaheadGuard().check(_ctx(_CleanStrategy, "strategy"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_lookahead_strategy_blocked():
    result = LookaheadGuard().check(_ctx(_LookaheadStrategy, "strategy"))
    assert result.severity == GuardSeverity.BLOCK


# ============================================================
# Scope tests — LookaheadGuard does NOT apply to engine-sliced kinds
# ============================================================

def test_lookahead_guard_does_not_apply_to_cross_factor():
    guard = LookaheadGuard()
    assert guard.applies("cross_factor") is False
    assert guard.applies("portfolio_strategy") is False
    assert guard.applies("ml_alpha") is False


def test_lookahead_guard_applies_to_factor_and_strategy():
    guard = LookaheadGuard()
    assert guard.applies("factor") is True
    assert guard.applies("strategy") is True


# ============================================================
# C1 regression: in-place mutation must NOT bypass LookaheadGuard
# ============================================================

class _InPlaceMutationLookahead:
    """Template-style factor: mutates input df in place + reads future.

    Without the C1 fix, _factor_output_at sees `input_cols` AFTER mutation,
    treats the new column as "pre-existing", returns an empty diff, and
    the guard silently passes.
    """
    name = "inplace_bad"
    warmup_period = 0

    def compute(self, data):
        # NO .copy() — mutate data in place.
        data[self.name] = data["close"].shift(-1)
        return data


def test_inplace_mutation_lookahead_is_blocked():
    """Regression for V2.19.0 post-review C1 — in-place mutation bypass."""
    result = LookaheadGuard().check(_ctx(_InPlaceMutationLookahead, "factor"))
    assert result.severity == GuardSeverity.BLOCK, (
        f"C1 regression: in-place mutation lookahead not caught. {result.message}"
    )


# ============================================================
# I1 regression: strategy multi-point compare must catch boolean lookahead
# ============================================================

class _RequiresNothing:
    def required_factors(self):
        return []


class _BooleanLookaheadStrategy(_RequiresNothing):
    """Boolean signal (0/1) derived from tomorrow vs today close.

    With single-scalar comparison, two distinct underlying computations
    can coincidentally agree ~50% of the time — silent pass. The
    multi-point compare looks at the last 10 positions and makes
    coincidental agreement exponentially less likely.
    """
    def generate_signals(self, data):
        import pandas as pd
        tomorrow = data["close"].shift(-1)
        return (tomorrow > data["close"]).astype(float)


def test_boolean_signal_lookahead_is_blocked_by_multi_point_compare():
    """Regression for V2.19.0 post-review I1 — scalar compare too weak."""
    result = LookaheadGuard().check(_ctx(_BooleanLookaheadStrategy, "strategy"))
    assert result.severity == GuardSeverity.BLOCK, (
        f"I1 regression: boolean-signal lookahead not caught by multi-point compare. "
        f"{result.message}"
    )
