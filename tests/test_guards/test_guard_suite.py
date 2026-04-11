"""Unit tests for GuardSuite orchestration, load_user_class, and runtime budget."""
from __future__ import annotations
import json
from pathlib import Path

import pandas as pd

from ez.testing.guards.base import (
    Guard, GuardContext, GuardResult, GuardSeverity,
)
from ez.testing.guards.suite import (
    GuardSuite, SuiteResult, load_user_class, default_guards,
)


class _PassGuard(Guard):
    name = "PassGuard"
    tier = "block"
    applies_to = ("strategy", "factor", "cross_factor", "portfolio_strategy", "ml_alpha")
    def check(self, context):
        return GuardResult(self.name, GuardSeverity.PASS, self.tier, "")


class _BlockGuard(Guard):
    name = "BlockGuard"
    tier = "block"
    applies_to = ("strategy",)
    def check(self, context):
        return GuardResult(self.name, GuardSeverity.BLOCK, self.tier, "blocked")


class _WarnGuard(Guard):
    name = "WarnGuard"
    tier = "warn"
    applies_to = ("strategy",)
    def check(self, context):
        return GuardResult(self.name, GuardSeverity.WARN, self.tier, "warn")


class _RaisingGuard(Guard):
    name = "RaisingGuard"
    tier = "block"
    applies_to = ("strategy",)
    def check(self, context):
        raise RuntimeError("guard bug!")


def _ctx(kind="strategy"):
    return GuardContext(
        filename="x.py", module_name="strategies.x",
        file_path=Path("/tmp/x.py"), kind=kind,
    )


def test_suite_runs_applicable_guards_only():
    suite = GuardSuite(guards=[_PassGuard(), _BlockGuard()])
    result = suite.run(_ctx(kind="factor"))
    assert len(result.results) == 1
    assert result.results[0].guard_name == "PassGuard"


def test_suite_collects_all_results():
    suite = GuardSuite(guards=[_PassGuard(), _WarnGuard()])
    result = suite.run(_ctx())
    assert len(result.results) == 2
    assert not result.blocked
    assert len(result.warnings) == 1


def test_suite_detects_block():
    suite = GuardSuite(guards=[_PassGuard(), _BlockGuard()])
    result = suite.run(_ctx())
    assert result.blocked
    assert len(result.blocks) == 1


def test_suite_catches_guard_exceptions():
    suite = GuardSuite(guards=[_RaisingGuard()])
    result = suite.run(_ctx())
    assert result.blocked
    assert "guard bug" in result.results[0].message.lower()


def test_suite_to_payload_is_json_serializable():
    suite = GuardSuite(guards=[_PassGuard(), _WarnGuard()])
    result = suite.run(_ctx())
    payload = result.to_payload()
    assert payload["blocked"] is False
    assert payload["n_warnings"] == 1
    json_str = json.dumps(payload)
    assert "PassGuard" in json_str


def test_load_user_class_missing_file_returns_error(tmp_path):
    missing = tmp_path / "nope.py"
    cls, err = load_user_class(missing, "test_nope", "strategy")
    assert cls is None
    assert err is not None
    assert "import failed" in err.lower() or "no such file" in err.lower()


def test_default_suite_has_five_guards():
    guards = default_guards()
    assert len(guards) == 5
    names = {g.name for g in guards}
    assert names == {
        "LookaheadGuard", "NaNInfGuard", "WeightSumGuard",
        "NonNegativeWeightsGuard", "DeterminismGuard",
    }


def test_runtime_budget_under_500ms_for_clean_factor():
    """Full default suite on a clean factor completes in < 500 ms on mock data."""
    class _CleanFactor:
        name = "clean"
        warmup_period = 20
        def compute(self, df):
            out = df.copy()
            out[self.name] = df["close"].rolling(20).mean()
            return out

    ctx = GuardContext(
        filename="clean.py", module_name="clean",
        file_path=Path("/tmp/clean.py"),
        kind="factor", user_class=_CleanFactor,
    )
    suite = GuardSuite()
    result = suite.run(ctx)
    assert not result.blocked
    # Budget: 500 ms on mock data.
    assert result.total_runtime_ms < 500, (
        f"Default suite too slow: {result.total_runtime_ms:.1f} ms"
    )
