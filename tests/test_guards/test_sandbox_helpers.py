"""Sandbox-local guard helpers: _sandbox_registries_for_kind + _run_guards."""
from __future__ import annotations
from pathlib import Path

import pytest

from ez.agent.sandbox import _sandbox_registries_for_kind, _run_guards
from ez.api.routes.code import _get_all_registries_for_kind
from ez.testing.guards.suite import SuiteResult


@pytest.mark.parametrize("kind", [
    "strategy", "factor", "cross_factor", "portfolio_strategy", "ml_alpha",
])
def test_sandbox_and_routes_helpers_return_same_registries(kind):
    """Parity: both helpers MUST return the SAME dict objects (identity).

    Otherwise the guard rollback cleans a copy and leaves zombies in the
    real registry.
    """
    a = _sandbox_registries_for_kind(kind)
    b = _get_all_registries_for_kind(kind)
    assert len(a) == len(b), f"Helper length drift for kind={kind}: {len(a)} vs {len(b)}"
    for i, (reg_a, reg_b) in enumerate(zip(a, b)):
        assert reg_a is reg_b, (
            f"Identity drift at index {i} for kind={kind}: "
            f"_sandbox_registries_for_kind returned a different dict object "
            f"than _get_all_registries_for_kind."
        )


def test_sandbox_registries_unknown_kind_returns_empty():
    assert _sandbox_registries_for_kind("bogus") == []


def _write_clean_factor(tmp_path: Path) -> Path:
    code = '''
from ez.factor.base import Factor
import pandas as pd

class CleanSmokeFactorForRunGuards(Factor):
    name = "clean_smoke_factor_for_run_guards"
    warmup_period = 5

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["adj_close"].rolling(5).mean()
        return out
'''
    p = tmp_path / "clean_smoke_factor_for_run_guards.py"
    p.write_text(code, encoding="utf-8")
    return p


def test_run_guards_returns_suite_result(tmp_path):
    """_run_guards loads the user class and returns a SuiteResult."""
    file_path = _write_clean_factor(tmp_path)
    try:
        result = _run_guards(file_path.name, "factor", tmp_path)
        assert isinstance(result, SuiteResult)
        assert not result.blocked
    finally:
        # Clean up registry pollution from the in-process import
        from ez.factor.base import Factor
        mod_name = f"{tmp_path.name}.clean_smoke_factor_for_run_guards"
        for k in list(Factor._registry.keys()):
            if Factor._registry[k].__module__ == mod_name:
                Factor._registry.pop(k, None)
        for k in list(Factor._registry_by_key.keys()):
            if Factor._registry_by_key[k].__module__ == mod_name:
                Factor._registry_by_key.pop(k, None)
        import sys
        sys.modules.pop(mod_name, None)
