"""Round 2 (codex review) regression tests.

Each test maps directly to a finding from the V2.19.0 codex post-review:
  - P1 #1: drop_probe_module dual-dict restore must respect last-write-wins
  - P1 #2: LookaheadGuard non-determinism gate must catch call-order drift
  - P2 #1: Hook 2/3 rollback re-register failure must surface as half-state
  - P2 #2: Engine-sliced kinds must use a fresh panel per invocation

If any of these fails in the future, codex's findings have regressed.
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd
import pytest

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.lookahead import LookaheadGuard
from ez.testing.guards.determinism import DeterminismGuard
from ez.testing.guards.weight_sum import WeightSumGuard
from ez.testing.guards.suite import drop_probe_module


# ============================================================
# P1 #1: drop_probe_module last-write-wins restore
# ============================================================

def test_drop_probe_restores_last_write_wins_for_same_named_classes():
    """Two same-named classes in different modules: after probe + drop,
    name-keyed registry must point to the LAST-inserted (not first).

    Test isolation: classes are created inside the test, and the code
    after creation pops EVERY entry the test could have introduced —
    including the auto-registered keys that ``__init_subclass__`` adds
    using the test module's own name (`tests.test_guards...`). The
    finally block restores the registries to their pre-test snapshot.
    """
    from ez.factor.base import Factor

    # Snapshot pre-test state so the finally block can rebuild it.
    pre_registry = dict(Factor._registry)
    pre_registry_by_key = dict(Factor._registry_by_key)

    try:
        class V_A(Factor):
            name = "p1_test_factor"
            warmup_period = 0
            def compute(self, data):
                return data.copy()

        class V_B(Factor):
            name = "p1_test_factor"
            warmup_period = 0
            def compute(self, data):
                return data.copy()

        class V_PROBE(Factor):
            name = "p1_test_factor"
            warmup_period = 0
            def compute(self, data):
                return data.copy()

        # Pop every auto-registered key for these 3 classes BEFORE renaming —
        # __init_subclass__ already inserted entries under the test module's
        # name, and renaming __module__ doesn't update the registry.
        for cls in (V_A, V_B, V_PROBE):
            for reg in (Factor._registry, Factor._registry_by_key):
                for k in [k for k, v in reg.items() if v is cls]:
                    reg.pop(k, None)

        # Force same __name__ but different __module__ (post-pop is fine).
        V_A.__name__ = V_B.__name__ = V_PROBE.__name__ = "P1RestoreTestFactor"
        V_A.__module__ = "factors._p1_restore_a"
        V_B.__module__ = "factors._p1_restore_b"
        V_PROBE.__module__ = "_guard_probe._p1_restore_probe"

        # Insert V_A first (older), then V_B (newer = last-write).
        Factor._registry_by_key["factors._p1_restore_a.P1RestoreTestFactor"] = V_A
        Factor._registry_by_key["factors._p1_restore_b.P1RestoreTestFactor"] = V_B
        Factor._registry["P1RestoreTestFactor"] = V_B  # last-write-wins

        # Probe import: displaces name-keyed entry to V_PROBE
        Factor._registry_by_key["_guard_probe._p1_restore_probe.P1RestoreTestFactor"] = V_PROBE
        Factor._registry["P1RestoreTestFactor"] = V_PROBE

        # Drop the probe module
        drop_probe_module("_guard_probe._p1_restore_probe", "factor")

        restored = Factor._registry.get("P1RestoreTestFactor")
        assert restored is V_B, (
            f"P1 #1 regression: drop_probe restored {restored!r} "
            f"({getattr(restored, '__module__', '?')}), expected V_B "
            f"({V_B.__module__}) — last-write-wins violated."
        )
        assert restored is not V_A, "Restored to older V_A instead of newer V_B"
    finally:
        # Hard restore: clear and rebuild from snapshot. This is the only
        # way to guarantee no leak from test-internal class definitions.
        Factor._registry.clear()
        Factor._registry.update(pre_registry)
        Factor._registry_by_key.clear()
        Factor._registry_by_key.update(pre_registry_by_key)


# ============================================================
# P1 #2: LookaheadGuard non-determinism gate vs call-order drift
# ============================================================

class _CallOrderState:
    """Module-level call counter shared across fresh instances of
    _CallOrderDriftStrategy below — simulates a strategy where
    call_count affects the output (e.g. cached state, global counter)."""
    counter = 0

    @classmethod
    def reset(cls):
        cls.counter = 0


class _CallOrderDriftStrategy:
    """First 2 calls return constant 0 (would pass a 2-run nondet gate),
    subsequent calls drift in a call-count-dependent way. Has nothing
    to do with future data."""

    def required_factors(self):
        return []

    def generate_signals(self, data):
        _CallOrderState.counter += 1
        if _CallOrderState.counter <= 2:
            return pd.Series([0.0] * len(data), index=data.index)
        return pd.Series(
            [(_CallOrderState.counter + i) % 2 for i in range(len(data))],
            index=data.index,
            dtype=float,
        )


def test_call_order_drift_strategy_returns_warn_not_block():
    """A non-deterministic strategy that happens to return constant on
    its first 2 calls must be classified as WARN (non-deterministic),
    not BLOCK (lookahead). The 5-run preflight catches the drift on
    calls 3-5."""
    _CallOrderState.reset()
    ctx = GuardContext(
        filename="x.py",
        module_name="x",
        file_path=Path("/tmp/x.py"),
        kind="strategy",
        user_class=_CallOrderDriftStrategy,
    )
    result = LookaheadGuard().check(ctx)
    assert result.severity == GuardSeverity.WARN, (
        f"P1 #2 regression: call-order-drift strategy got "
        f"{result.severity.value}, expected WARN. Message: {result.message}"
    )
    assert "non-deterministic" in result.message.lower(), (
        f"Expected 'non-deterministic' in message, got: {result.message}"
    )


# ============================================================
# P2 #2: Engine-sliced kinds must use fresh panel per invocation
# ============================================================

class _MutatingCrossFactor:
    """Cross-sectional factor that intentionally pollutes the panel by
    zeroing adj_close after reading. The factor itself is deterministic
    — given the same input it always produces the same output. The bug
    we're guarding against is the GUARD shared a panel across runs and
    saw the second run as different."""
    name = "mutating_cross"
    warmup_period = 0

    def compute(self, panel, target_date):
        sym = next(iter(panel))
        val = float(panel[sym]["adj_close"].iloc[-1])
        # In-place pollution
        panel[sym].loc[:, "adj_close"] = 0.0
        return pd.Series({sym: val})


def test_determinism_guard_uses_fresh_panel_per_run():
    """DeterminismGuard's two clean runs must each get a fresh panel,
    so user code in-place mutating the panel does not surface as
    'two runs differ'."""
    ctx = GuardContext(
        filename="x.py",
        module_name="x",
        file_path=Path("/tmp/x.py"),
        kind="cross_factor",
        user_class=_MutatingCrossFactor,
    )
    result = DeterminismGuard().check(ctx)
    assert result.severity == GuardSeverity.PASS, (
        f"P2 #2 regression: deterministic mutating cross_factor got "
        f"{result.severity.value}, expected PASS. Panel pollution leaked. "
        f"Message: {result.message}"
    )


class _PriceDependentMutatingPortfolio:
    """Portfolio whose output **depends on the panel content**, AND mutates
    the panel in place after reading. Under shared-panel guard infra:
      - call 1: price > 1.0 → return equal-weight (sum=1.0). Then poison panel.
      - call 2..5: panel poisoned → price=0.0 → return over-leveraged
        sum=500 → WeightSumGuard BLOCKs.

    Under fresh-panel guard infra: every call sees pristine prices,
    every call returns equal-weight, sum=1.0 every time → PASS.

    This is a STRONG canary — without the round-2 fix, the guard MUST
    surface a violation. (Verified by reverting build_mock_panel to
    cached-reference and watching this test fail.)
    """
    warmup_period = 0

    def generate_weights(self, panel, target_date, prev_w, prev_r):
        sym = next(iter(panel))
        price = float(panel[sym]["adj_close"].iloc[-1])
        # Mutate panel in place — simulates user's accidental in-place bug
        panel[sym].loc[:, "adj_close"] = 0.0
        if price > 1.0:
            n = len(panel)
            return {s: 1.0 / n for s in panel}  # sum = 1.0
        # price was poisoned by a previous call → over-leveraged response
        return {s: 100.0 for s in panel}  # sum = 500


def test_weight_sum_guard_uses_fresh_panel_per_date():
    """WeightSumGuard checks 5 dates. Each invocation must get a fresh
    panel so that user code mutation doesn't bleed across dates.

    Strong canary: under shared-panel infra, dates 2..5 see poisoned
    prices and return sum=500 → BLOCK. Under fresh-panel infra, all 5
    dates pass.
    """
    ctx = GuardContext(
        filename="x.py",
        module_name="x",
        file_path=Path("/tmp/x.py"),
        kind="portfolio_strategy",
        user_class=_PriceDependentMutatingPortfolio,
    )
    result = WeightSumGuard().check(ctx)
    assert result.severity == GuardSeverity.PASS, (
        f"P2 #2 regression: price-dependent mutating portfolio got "
        f"{result.severity.value}, expected PASS. Panel pollution leaked "
        f"across dates. Message: {result.message}"
    )


class _PriceDependentNegativeMutatingPortfolio:
    """Same pattern as above but for NonNegativeWeightsGuard. Returns
    a NEGATIVE weight on calls where the price is poisoned (<=0)."""
    warmup_period = 0

    def generate_weights(self, panel, target_date, prev_w, prev_r):
        sym = next(iter(panel))
        price = float(panel[sym]["adj_close"].iloc[-1])
        panel[sym].loc[:, "adj_close"] = 0.0
        if price > 1.0:
            n = len(panel)
            return {s: 1.0 / n for s in panel}  # all positive
        # poisoned: surface a negative weight to trigger non-negative warn
        syms = list(panel)
        return {syms[0]: -0.1, syms[1]: 0.5, syms[2]: 0.6}


def test_non_negative_weights_guard_uses_fresh_panel_per_date():
    """NonNegativeWeightsGuard 5-date sweep: under shared-panel infra,
    poisoned dates surface negative weights → WARN. Under fresh-panel
    infra, all 5 dates see clean prices → PASS."""
    from ez.testing.guards.non_negative import NonNegativeWeightsGuard

    ctx = GuardContext(
        filename="x.py",
        module_name="x",
        file_path=Path("/tmp/x.py"),
        kind="portfolio_strategy",
        user_class=_PriceDependentNegativeMutatingPortfolio,
    )
    result = NonNegativeWeightsGuard().check(ctx)
    assert result.severity == GuardSeverity.PASS, (
        f"P2 #2 regression: price-dependent mutating portfolio (negative) got "
        f"{result.severity.value}, expected PASS. Panel pollution leaked "
        f"across dates. Message: {result.message}"
    )


# ============================================================
# P2 #1: Hook 2/3 restore_err must surface as half-state, not silent log
# (verified via integration test that monkey-patches _reload_factor_code)
# ============================================================

def test_factor_guard_rollback_restore_failure_surfaces_critical(tmp_path, monkeypatch):
    """When guard blocks AND _reload_factor_code fails to re-register the
    backup, the response must include a CRITICAL half-state warning,
    not just the guard failure message."""
    from ez.agent import sandbox

    factors_dir = tmp_path / "factors"
    factors_dir.mkdir()
    monkeypatch.setattr(sandbox, "_FACTORS_DIR", factors_dir)
    monkeypatch.setattr(sandbox, "_KIND_DIR_MAP", {
        **sandbox._KIND_DIR_MAP,
        "factor": factors_dir,
    })

    clean = '''
from ez.factor.base import Factor
import pandas as pd

class P2RolloverTestFactor(Factor):
    name = "p2_rollover_test_factor"
    warmup_period = 5
    def compute(self, data):
        out = data.copy()
        out[self.name] = data["adj_close"].rolling(5).mean()
        return out
'''
    buggy = '''
from ez.factor.base import Factor
import numpy as np
import pandas as pd

class P2RolloverTestFactor(Factor):
    name = "p2_rollover_test_factor"
    warmup_period = 0
    def compute(self, data):
        out = data.copy()
        out[self.name] = np.log(data["close"] - data["close"])
        return out
'''
    # First save: clean. Should pass.
    r1 = sandbox.save_and_validate_code("p2_rollover_test_factor.py", clean, "factor")
    assert r1["success"] is True, r1.get("errors")

    # Now monkey-patch _reload_factor_code to raise ON THE BACKUP RELOAD
    # path. The first call (hot-reload of new code) succeeds normally,
    # then the guard blocks, and the second call (restore backup) raises.
    original_reload = sandbox._reload_factor_code
    call_count = {"n": 0}
    def flaky_reload(filename, target_dir):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: hot-reload of buggy code — let it succeed
            return original_reload(filename, target_dir)
        # Second call: restore backup — fail
        raise RuntimeError("simulated re-register failure")
    monkeypatch.setattr(sandbox, "_reload_factor_code", flaky_reload)

    r2 = sandbox.save_and_validate_code(
        "p2_rollover_test_factor.py", buggy, "factor", overwrite=True
    )
    assert r2["success"] is False
    errs_combined = " ".join(r2.get("errors", []))
    assert "CRITICAL" in errs_combined, (
        f"P2 #1 regression: backup restore failure not surfaced. "
        f"Errors: {r2.get('errors')}"
    )
    assert "half-state" in errs_combined.lower() or "refresh" in errs_combined.lower()

    # Cleanup registry pollution from this test
    monkeypatch.setattr(sandbox, "_reload_factor_code", original_reload)
    from ez.factor.base import Factor
    for k in list(Factor._registry.keys()):
        if Factor._registry[k].__module__ == "factors.p2_rollover_test_factor":
            Factor._registry.pop(k, None)
    for k in list(Factor._registry_by_key.keys()):
        if Factor._registry_by_key[k].__module__ == "factors.p2_rollover_test_factor":
            Factor._registry_by_key.pop(k, None)
    import sys
    sys.modules.pop("factors.p2_rollover_test_factor", None)
