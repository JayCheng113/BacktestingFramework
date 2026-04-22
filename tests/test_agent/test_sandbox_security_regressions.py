"""Sandbox security regression tests.

Recovered from tests/test_research/test_codex_round2_regressions.py during
the 2026-04-21 dead-code refactoring. These tests are independent of the
deleted research steps and verify sandbox escape prevention.

Round-3 codex review:
  - P1-1: forbidden imports (ez.agent.sandbox, ez.testing.guards, ez.api.routes.code)

Round-4 Claude reviewer audit:
  - P1-A: attribute chain attack via legal `import ez` + `ez.agent.sandbox.<attr>`
"""
from __future__ import annotations

import pytest


class TestP11ForbiddenSandboxImports:
    """Codex round-2 P1-1: user code must not be able to import
    ez.agent.sandbox or its internal functions.

    V2.21: _reload_lock moved to closure, but sandbox module is still
    forbidden to prevent access to _get_reload_lock and other internals."""

    def test_direct_import_blocked(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "import ez.agent.sandbox",
            "from ez.agent.sandbox import _reload_lock",
            "from ez.agent import sandbox",
            "from ez.agent.sandbox import check_syntax",
        ]:
            errs = check_syntax(code)
            assert errs, f"P1-1 regression: {code!r} should be blocked"
            assert any("Forbidden import" in e for e in errs)

    def test_guards_internals_blocked(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "from ez.testing.guards import suite",
            "from ez.testing import guards",
            "import ez.testing.guards.suite",
        ]:
            errs = check_syntax(code)
            assert errs, f"P1-1 regression: {code!r} should be blocked"

    def test_routes_code_blocked(self):
        from ez.agent.sandbox import check_syntax
        errs = check_syntax("from ez.api.routes.code import save_and_validate")
        assert errs, "P1-1 regression: routes/code import should be blocked"

    def test_legitimate_imports_still_allowed(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "from ez.factor.base import Factor",
            "from ez.strategy.base import Strategy",
            "import pandas",
            "from ez.portfolio.cross_factor import CrossSectionalFactor",
        ]:
            errs = check_syntax(code)
            assert not errs, f"P1-1 regression: {code!r} should be allowed, got {errs}"

    def test_reload_lock_basic_acquire_release(self):
        """Lock can be acquired + released without exception.

        V2.21: lock moved to closure, accessed via _get_reload_lock().
        """
        from ez.agent.sandbox import _get_reload_lock
        lock = _get_reload_lock()
        with lock:
            pass  # acquire + release without nesting


class TestP1AAttributeChainAttack:
    """Codex round-4 P1-A: `import ez` is legal but
    ``ez.agent.sandbox.<anything>`` is an attribute traversal that
    reaches into a forbidden module. The round-3 ImportFrom check did
    not catch this -- only the new AST attribute-chain reconstruction
    in check_syntax does.

    V2.21: _reload_lock moved to closure, but the forbidden-module check
    still blocks any attribute access on ez.agent.sandbox.
    """

    def test_attribute_chain_via_root_import_blocked(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "import ez\nx = ez.agent.sandbox._get_reload_lock",
            "import ez\nez.agent.sandbox._get_reload_lock()",
            "import ez.agent\nx = ez.agent.sandbox._get_reload_lock",
            "import ez.agent\nsb = ez.agent.sandbox",
        ]:
            errs = check_syntax(code)
            assert errs, f"P1-A regression: {code!r} should be blocked"
            assert any("attribute chain" in e.lower() or "forbidden" in e.lower() for e in errs)

    def test_attribute_chain_to_guards_blocked(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "import ez\nx = ez.testing.guards.suite.GuardSuite",
            "import ez.testing\ny = ez.testing.guards.suite",
        ]:
            errs = check_syntax(code)
            assert errs, f"P1-A regression: {code!r} should be blocked"

    def test_legitimate_attribute_chains_still_allowed(self):
        from ez.agent.sandbox import check_syntax
        for code in [
            "import ez\nfrom ez.factor.base import Factor",
            "import pandas as pd\ndf = pd.DataFrame()",
            "import numpy as np\nx = np.zeros(10)",
        ]:
            errs = check_syntax(code)
            assert not errs, f"P1-A regression: {code!r} blocked but should be allowed: {errs}"

    def test_reload_lock_is_NOT_rlock_after_round4(self):
        """Round-4 reverted RLock back to Lock so user-code lock attacks
        manifest as immediate hangs (loud) instead of silent persistent
        holds (count poisoning under RLock).

        V2.21: lock is now closure-captured via _get_reload_lock().
        """
        from ez.agent.sandbox import _get_reload_lock
        lock = _get_reload_lock()
        assert lock.acquire(blocking=False)
        try:
            second = lock.acquire(blocking=False)
            if second:
                lock.release()
            assert not second, (
                "P1-A regression: reload lock is an RLock, not a Lock. "
                "RLock allows silent persistent holds via reentrance."
            )
        finally:
            lock.release()

    def test_reload_lock_not_module_attr(self):
        """V2.21: _reload_lock should NOT exist as a module attribute."""
        import ez.agent.sandbox as sandbox_mod
        assert not hasattr(sandbox_mod, "_reload_lock"), (
            "V2.21 regression: _reload_lock should be closure-captured, "
            "not a module-level attribute."
        )
