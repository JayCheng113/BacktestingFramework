"""Unit tests for ez.testing.guards.base: data types and Guard ABC."""
from __future__ import annotations
from pathlib import Path

from ez.testing.guards.base import (
    Guard, GuardContext, GuardResult, GuardSeverity,
)


def test_guard_severity_enum_values():
    assert GuardSeverity.PASS.value == "pass"
    assert GuardSeverity.WARN.value == "warn"
    assert GuardSeverity.BLOCK.value == "block"


def test_guard_result_passed_property():
    r = GuardResult(guard_name="X", severity=GuardSeverity.PASS, tier="block", message="")
    assert r.passed is True
    assert r.blocked is False


def test_guard_result_blocked_property():
    r = GuardResult(guard_name="X", severity=GuardSeverity.BLOCK, tier="block", message="err")
    assert r.blocked is True
    assert r.passed is False


def test_guard_result_warn_is_neither_passed_nor_blocked():
    r = GuardResult(guard_name="X", severity=GuardSeverity.WARN, tier="warn", message="w")
    assert r.passed is False
    assert r.blocked is False


def test_guard_context_defaults():
    ctx = GuardContext(
        filename="foo.py",
        module_name="strategies.foo",
        file_path=Path("strategies/foo.py"),
        kind="strategy",
    )
    assert ctx.user_class is None
    assert ctx.instantiation_error is None


def test_guard_applies_kind_check():
    class _X(Guard):
        name = "X"
        applies_to = ("factor",)
        def check(self, context):
            return GuardResult("X", GuardSeverity.PASS, "block", "")
    g = _X()
    assert g.applies("factor") is True
    assert g.applies("strategy") is False
