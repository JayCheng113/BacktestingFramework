# ez.testing.guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 5-guard save-time verification framework (`ez.testing.guards`) that catches look-ahead bias, NaN/Inf, weight-sum violations, negative weights, and non-determinism in user strategy/factor/portfolio code at sandbox save time — before the code can run in backtest or live trading.

**Architecture:** Pure-Python module under `ez/testing/guards/` with `Guard` ABC, `GuardContext`/`GuardResult` dataclasses, `GuardSuite` orchestrator, and deterministic mock data fixtures. Integrated into `ez/agent/sandbox.py` at 3 hook points (strategy save, factor save, portfolio/cross_factor/ml_alpha save). Frontend surfacing via minimal extension to `web/src/components/CodeEditor.tsx` existing status + test-output panels. Zero new UI tabs, zero API routes, zero breaking changes.

**Tech Stack:** Python 3.12+, pytest, pandas, numpy, React 19 + TypeScript, Monaco editor (frontend).

**Spec reference:** `docs/superpowers/specs/2026-04-11-guard-framework-design.md`

**Test baseline:** 2265 tests (V2.18.1) → target 2322 tests (+57).

---

## File Structure

**New Python files:**
```
ez/testing/                              NEW package
├── __init__.py
└── guards/                              NEW package
    ├── __init__.py                      # Public exports
    ├── base.py                          # Guard ABC, GuardContext, GuardResult, GuardSeverity
    ├── mock_data.py                     # build_mock_panel, build_shuffled_panel, target_date_at
    ├── suite.py                         # GuardSuite, SuiteResult, load_user_class, default_guards
    ├── lookahead.py                     # LookaheadGuard (Tier 1)
    ├── nan_inf.py                       # NaNInfGuard (Tier 1)
    ├── weight_sum.py                    # WeightSumGuard (Tier 1)
    ├── non_negative.py                  # NonNegativeWeightsGuard (Tier 2)
    └── determinism.py                   # DeterminismGuard (Tier 2)
```

**New test files:**
```
tests/test_guards/                       NEW package
├── __init__.py
├── conftest.py                          # Shared fixtures
├── test_guard_base.py                   # base.py unit tests
├── test_mock_data.py                    # mock_data.py unit tests
├── test_lookahead_guard.py              # LookaheadGuard unit tests
├── test_nan_inf_guard.py                # NaNInfGuard unit tests
├── test_weight_sum_guard.py             # WeightSumGuard unit tests
├── test_non_negative_guard.py           # NonNegativeWeightsGuard unit tests
├── test_determinism_guard.py            # DeterminismGuard unit tests
├── test_guard_suite.py                  # GuardSuite orchestration + runtime bound
├── test_sandbox_integration.py          # 14 end-to-end tests via save_and_validate_code
└── golden_bugs/                         NEW sub-package
    ├── __init__.py
    ├── test_v1_dynamic_ef_lookahead.py  # v1 Dynamic EF regression test
    └── test_mlalpha_purge_lookahead.py  # MLAlpha calendar-purge regression test
```

**Modified files:**
- `ez/agent/sandbox.py` — add `_sandbox_registries_for_kind` + `_run_guards` + 3 hook call sites + rollback blocks
- `web/src/components/CodeEditor.tsx` — add `GuardReport` type + state + save-handler payload + status-bar extension + test-output panel extension
- `CLAUDE.md` — V2.19.0 entry at top

**Responsibility split:**
- `base.py`: data types only (no behavior)
- `mock_data.py`: deterministic fixtures (no I/O)
- `suite.py`: orchestration + lazy class loader (small)
- `*.py` guard modules: one guard per file (200 lines or less)
- `sandbox.py`: integration only (no guard logic)

---

## Task List

- [ ] Task 1: Core types (`ez/testing/guards/base.py`)
- [ ] Task 2: Mock data fixtures (`ez/testing/guards/mock_data.py`)
- [ ] Task 3: Suite skeleton + class loader (`ez/testing/guards/suite.py`)
- [ ] Task 4: LookaheadGuard
- [ ] Task 5: NaNInfGuard
- [ ] Task 6: WeightSumGuard
- [ ] Task 7: NonNegativeWeightsGuard
- [ ] Task 8: DeterminismGuard
- [ ] Task 9: Wire suite + full runtime budget test
- [ ] Task 10: `_sandbox_registries_for_kind` helper
- [ ] Task 11: `_run_guards` sandbox helper
- [ ] Task 12: Hook 1 — strategy save integration
- [ ] Task 13: Hook 2 — factor save integration
- [ ] Task 14: Hook 3 — portfolio/cross_factor/ml_alpha save integration
- [ ] Task 15: End-to-end sandbox integration tests
- [ ] Task 16: Golden bug regression tests
- [ ] Task 17: Frontend state + save handler (`CodeEditor.tsx`)
- [ ] Task 18: Frontend status bar extension
- [ ] Task 19: Frontend test output panel extension
- [ ] Task 20: Update `CLAUDE.md` V2.19.0 entry
- [ ] Task 21: Full test run + benchmark + final commit

---

## Task 1: Core types

**Files:**
- Create: `ez/testing/__init__.py`
- Create: `ez/testing/guards/__init__.py`
- Create: `ez/testing/guards/base.py`
- Create: `tests/test_guards/__init__.py`
- Create: `tests/test_guards/conftest.py`
- Create: `tests/test_guards/test_guard_base.py`

- [ ] **Step 1: Create package markers**

```bash
mkdir -p ez/testing/guards tests/test_guards
touch ez/testing/__init__.py ez/testing/guards/__init__.py
touch tests/test_guards/__init__.py
```

- [ ] **Step 2: Write the failing test** — `tests/test_guards/test_guard_base.py`

```python
"""Unit tests for ez.testing.guards.base: data types and Guard ABC."""
from __future__ import annotations
import pytest
from ez.testing.guards.base import (
    Guard, GuardContext, GuardResult, GuardSeverity, GuardKind, GuardTier,
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
    from pathlib import Path
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
        def check(self, context): return GuardResult("X", GuardSeverity.PASS, "block", "")
    g = _X()
    assert g.applies("factor") is True
    assert g.applies("strategy") is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_guards/test_guard_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ez.testing.guards.base'`

- [ ] **Step 4: Implement `ez/testing/guards/base.py`**

```python
"""Guard framework core — base types and abstract base class.

A Guard inspects a user-authored strategy/factor/portfolio file and returns
a GuardResult indicating pass, warn, or block. Guards run at save time via
GuardSuite and are integrated into the sandbox save flow.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

GuardKind = Literal["strategy", "factor", "cross_factor", "portfolio_strategy", "ml_alpha"]
GuardTier = Literal["block", "warn"]


class GuardSeverity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class GuardContext:
    """Everything a guard needs to analyze a user code file.

    The sandbox builds this once per save. `user_class` is populated by
    GuardSuite.run() via load_user_class() before any guard.check() fires.
    """
    filename: str
    module_name: str
    file_path: Path
    kind: GuardKind
    user_class: type | None = None
    instantiation_error: str | None = None


@dataclass(frozen=True)
class GuardResult:
    """Outcome of a single guard run."""
    guard_name: str
    severity: GuardSeverity
    tier: GuardTier
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    runtime_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return self.severity == GuardSeverity.PASS

    @property
    def blocked(self) -> bool:
        return self.severity == GuardSeverity.BLOCK


class Guard(ABC):
    """Abstract guard. Subclasses implement `check()`."""
    name: str = "Guard"
    tier: GuardTier = "block"
    applies_to: tuple[GuardKind, ...] = ()

    @abstractmethod
    def check(self, context: GuardContext) -> GuardResult:
        """Run the guard against the user code.

        Implementations MUST return a GuardResult. They MUST NOT raise —
        wrap internal errors as a block-severity result with a descriptive
        message. (GuardSuite catches exceptions as a defensive second
        line, but guards should not rely on that.)
        """
        raise NotImplementedError

    def applies(self, kind: GuardKind) -> bool:
        return kind in self.applies_to
```

- [ ] **Step 5: Add exports to `ez/testing/guards/__init__.py`**

```python
"""ez.testing.guards — save-time guard framework.

Public API:
  - Guard, GuardContext, GuardResult, GuardSeverity, GuardKind, GuardTier
  - GuardSuite, SuiteResult (Task 3)
  - LookaheadGuard, NaNInfGuard, WeightSumGuard, NonNegativeWeightsGuard,
    DeterminismGuard (Tasks 4-8)
  - build_mock_panel, build_shuffled_panel, target_date_at (Task 2)
"""
from .base import (
    Guard,
    GuardContext,
    GuardResult,
    GuardSeverity,
    GuardKind,
    GuardTier,
)

__all__ = [
    "Guard",
    "GuardContext",
    "GuardResult",
    "GuardSeverity",
    "GuardKind",
    "GuardTier",
]
```

- [ ] **Step 6: Add empty conftest.py** — `tests/test_guards/conftest.py`

```python
"""Shared pytest fixtures for guard tests."""
# Reserved for fixtures added in later tasks.
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_guards/test_guard_base.py -v`
Expected: 6 passed

- [ ] **Step 8: Commit**

```bash
git add ez/testing/__init__.py ez/testing/guards/__init__.py ez/testing/guards/base.py \
        tests/test_guards/__init__.py tests/test_guards/conftest.py tests/test_guards/test_guard_base.py
git commit -m "feat(guards): core types (Guard ABC, GuardContext, GuardResult)

First task of V2.19.0 guard framework. Adds the type skeleton and 6
unit tests. No behavior yet — guard implementations follow.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Mock data fixtures

**Files:**
- Create: `ez/testing/guards/mock_data.py`
- Create: `tests/test_guards/test_mock_data.py`

- [ ] **Step 1: Write the failing test** — `tests/test_guards/test_mock_data.py`

```python
"""Unit tests for mock_data: deterministic fixtures used by all guards."""
from __future__ import annotations
import pandas as pd
from ez.testing.guards.mock_data import (
    build_mock_panel, build_shuffled_panel, target_date_at,
    MOCK_N_DAYS, MOCK_SYMBOLS,
)


def test_mock_panel_has_expected_shape():
    panel = build_mock_panel()
    assert set(panel.keys()) == set(MOCK_SYMBOLS)
    for sym, df in panel.items():
        assert len(df) == MOCK_N_DAYS
        assert set(df.columns) == {"open", "high", "low", "close", "adj_close", "volume"}
        assert isinstance(df.index, pd.DatetimeIndex)


def test_mock_panel_is_deterministic():
    """Two calls return identical data (cached via lru_cache)."""
    a = build_mock_panel()
    b = build_mock_panel()
    for sym in MOCK_SYMBOLS:
        pd.testing.assert_frame_equal(a[sym], b[sym])


def test_shuffled_panel_preserves_rows_at_and_before_cutoff():
    panel = build_mock_panel()
    shuffled = build_shuffled_panel(cutoff_idx=150)
    for sym in MOCK_SYMBOLS:
        # Rows 0..150 (inclusive) must be byte-identical.
        a = panel[sym].iloc[:151]
        b = shuffled[sym].iloc[:151]
        pd.testing.assert_frame_equal(a, b)


def test_shuffled_panel_changes_rows_after_cutoff():
    panel = build_mock_panel()
    shuffled = build_shuffled_panel(cutoff_idx=150)
    # At least one symbol should differ after the cutoff.
    any_diff = False
    for sym in MOCK_SYMBOLS:
        a = panel[sym].iloc[151:].values
        b = shuffled[sym].iloc[151:].values
        if not (a == b).all():
            any_diff = True
            break
    assert any_diff, "Shuffled panel has identical post-cutoff rows (bad RNG seed?)"


def test_target_date_at_returns_expected_date():
    d0 = target_date_at(0)
    d150 = target_date_at(150)
    assert d0 < d150
    assert d0.year == 2024
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_mock_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ez.testing.guards.mock_data'`

- [ ] **Step 3: Implement `ez/testing/guards/mock_data.py`**

```python
"""Deterministic mock data fixtures for guard tests.

All randomness is from `np.random.default_rng(seed)` — no global state.
Data is cached at module-import time to avoid rebuild on each guard call.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime
from functools import lru_cache

MOCK_SEED = 42
SHUFFLE_SEED = 7
MOCK_N_DAYS = 200
MOCK_START_DATE = "2024-01-01"
MOCK_SYMBOLS = ("T001", "T002", "T003", "T004", "T005")


@lru_cache(maxsize=1)
def _mock_date_index() -> pd.DatetimeIndex:
    return pd.date_range(MOCK_START_DATE, periods=MOCK_N_DAYS, freq="B")


@lru_cache(maxsize=1)
def build_mock_panel() -> dict[str, pd.DataFrame]:
    """Returns dict[symbol → DataFrame] with OHLCV + adj_close.

    200 B-day bars × 5 symbols, deterministic GBM. Cached so guards reuse
    the same object. Callers MUST NOT mutate the returned DataFrames.
    """
    rng = np.random.default_rng(MOCK_SEED)
    dates = _mock_date_index()
    panel: dict[str, pd.DataFrame] = {}
    for sym in MOCK_SYMBOLS:
        r = rng.normal(0.0005, 0.015, MOCK_N_DAYS)
        price = 100 * np.cumprod(1 + r)
        high = price * (1 + np.abs(rng.normal(0, 0.005, MOCK_N_DAYS)))
        low = price * (1 - np.abs(rng.normal(0, 0.005, MOCK_N_DAYS)))
        open_ = price * (1 + rng.normal(0, 0.003, MOCK_N_DAYS))
        volume = rng.integers(100_000, 1_000_000, MOCK_N_DAYS).astype(float)
        panel[sym] = pd.DataFrame({
            "open": open_,
            "high": high,
            "low": low,
            "close": price,
            "adj_close": price,
            "volume": volume,
        }, index=dates)
    return panel


def build_shuffled_panel(cutoff_idx: int) -> dict[str, pd.DataFrame]:
    """Returns a copy of mock panel with rows strictly after cutoff_idx shuffled.

    Row cutoff_idx itself stays in place. Rows [cutoff_idx + 1, N) are permuted
    by values (index remains the original DatetimeIndex so date alignment is
    preserved — only the values change).
    """
    rng = np.random.default_rng(SHUFFLE_SEED)
    base = build_mock_panel()
    shuffled: dict[str, pd.DataFrame] = {}
    for sym, df in base.items():
        head = df.iloc[: cutoff_idx + 1].copy()
        tail = df.iloc[cutoff_idx + 1:].copy()
        if len(tail) > 0:
            perm = rng.permutation(len(tail))
            tail_vals = tail.values[perm]
            tail = pd.DataFrame(tail_vals, index=tail.index, columns=tail.columns)
        shuffled[sym] = pd.concat([head, tail])
    return shuffled


def target_date_at(idx: int) -> datetime:
    """Returns the date at position idx in the mock panel."""
    return _mock_date_index()[idx].to_pydatetime()
```

- [ ] **Step 4: Add exports to `ez/testing/guards/__init__.py`**

Append to the existing file:

```python
from .mock_data import (
    build_mock_panel,
    build_shuffled_panel,
    target_date_at,
    MOCK_N_DAYS,
    MOCK_SYMBOLS,
)

__all__ += [
    "build_mock_panel",
    "build_shuffled_panel",
    "target_date_at",
    "MOCK_N_DAYS",
    "MOCK_SYMBOLS",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_guards/test_mock_data.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add ez/testing/guards/mock_data.py ez/testing/guards/__init__.py tests/test_guards/test_mock_data.py
git commit -m "feat(guards): deterministic mock data fixtures

build_mock_panel (200 B-days × 5 symbols, seeded GBM), build_shuffled_panel
(rows after cutoff shuffled by values), target_date_at. Cached for reuse.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Suite skeleton + class loader

**Files:**
- Create: `ez/testing/guards/suite.py`
- Create: `tests/test_guards/test_guard_suite.py` (partial — runtime budget test added later)

- [ ] **Step 1: Write the failing test** — `tests/test_guards/test_guard_suite.py`

```python
"""Unit tests for GuardSuite orchestration and load_user_class."""
from __future__ import annotations
from pathlib import Path
import pytest
from ez.testing.guards.base import (
    Guard, GuardContext, GuardResult, GuardSeverity,
)
from ez.testing.guards.suite import GuardSuite, SuiteResult, load_user_class


class _PassGuard(Guard):
    name = "PassGuard"
    tier = "block"
    applies_to = ("strategy", "factor", "cross_factor", "portfolio_strategy", "ml_alpha")
    def check(self, context): return GuardResult(self.name, GuardSeverity.PASS, self.tier, "")


class _BlockGuard(Guard):
    name = "BlockGuard"
    tier = "block"
    applies_to = ("strategy",)
    def check(self, context): return GuardResult(self.name, GuardSeverity.BLOCK, self.tier, "blocked")


class _WarnGuard(Guard):
    name = "WarnGuard"
    tier = "warn"
    applies_to = ("strategy",)
    def check(self, context): return GuardResult(self.name, GuardSeverity.WARN, self.tier, "warn")


class _RaisingGuard(Guard):
    name = "RaisingGuard"
    tier = "block"
    applies_to = ("strategy",)
    def check(self, context): raise RuntimeError("guard bug!")


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
    import json
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_guard_suite.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ez.testing.guards.suite'`

- [ ] **Step 3: Implement `ez/testing/guards/suite.py`**

```python
"""GuardSuite: orchestrates multiple guards and collects results.

Also exposes `load_user_class` — imports a user file in-process and
returns the target subclass for the given kind. Used by the sandbox
integration layer.
"""
from __future__ import annotations
import importlib.util
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .base import Guard, GuardContext, GuardResult, GuardSeverity, GuardKind


@dataclass(frozen=True)
class SuiteResult:
    results: tuple[GuardResult, ...]
    total_runtime_ms: float

    @property
    def blocked(self) -> bool:
        return any(r.blocked for r in self.results)

    @property
    def warnings(self) -> list[GuardResult]:
        return [r for r in self.results if r.severity == GuardSeverity.WARN]

    @property
    def blocks(self) -> list[GuardResult]:
        return [r for r in self.results if r.severity == GuardSeverity.BLOCK]

    def to_payload(self) -> dict:
        return {
            "blocked": self.blocked,
            "n_warnings": len(self.warnings),
            "n_blocks": len(self.blocks),
            "total_runtime_ms": round(self.total_runtime_ms, 2),
            "guards": [
                {
                    "name": r.guard_name,
                    "severity": r.severity.value,
                    "tier": r.tier,
                    "message": r.message,
                    "runtime_ms": round(r.runtime_ms, 2),
                    "details": r.details,
                }
                for r in self.results
            ],
        }


def default_guards() -> list[Guard]:
    """Default guard set for production. Wired in Task 9."""
    return []


class GuardSuite:
    def __init__(self, guards: Iterable[Guard] | None = None):
        self.guards = list(guards) if guards is not None else default_guards()

    def run(self, context: GuardContext) -> SuiteResult:
        t0 = time.perf_counter()
        results: list[GuardResult] = []
        for guard in self.guards:
            if not guard.applies(context.kind):
                continue
            try:
                result = guard.check(context)
            except Exception as e:
                result = GuardResult(
                    guard_name=guard.name,
                    severity=GuardSeverity.BLOCK,
                    tier=guard.tier,
                    message=(
                        f"{guard.name}: guard itself raised (guard bug, not user bug): "
                        f"{type(e).__name__}: {e}"
                    ),
                )
            results.append(result)
        total = (time.perf_counter() - t0) * 1000
        return SuiteResult(results=tuple(results), total_runtime_ms=total)


def load_user_class(
    file_path: Path, module_name: str, kind: GuardKind,
) -> tuple[type | None, str | None]:
    """Import a user file and return (class, error_message).

    Returns (None, error) if file cannot be imported or no target class found.
    Runs in the SAME process as the sandbox — user code has already passed
    syntax + security checks by the time this is called.
    """
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            return None, f"Could not create module spec for {file_path}"
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    except Exception as e:
        return None, f"Import failed: {type(e).__name__}: {e}"

    if kind == "strategy":
        from ez.strategy.base import Strategy as _Base
    elif kind == "factor":
        from ez.factor.base import Factor as _Base
    elif kind == "cross_factor":
        from ez.portfolio.cross_sectional_factor import CrossSectionalFactor as _Base
    elif kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy as _Base
    elif kind == "ml_alpha":
        from ez.portfolio.ml_alpha import MLAlpha as _Base
    else:
        return None, f"Unknown kind: {kind}"

    for v in vars(mod).values():
        if isinstance(v, type) and issubclass(v, _Base) and v is not _Base:
            return v, None
    return None, f"No {_Base.__name__} subclass found in module"
```

- [ ] **Step 4: Add exports to `ez/testing/guards/__init__.py`**

Append:

```python
from .suite import GuardSuite, SuiteResult, default_guards, load_user_class

__all__ += ["GuardSuite", "SuiteResult", "default_guards", "load_user_class"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_guards/test_guard_suite.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add ez/testing/guards/suite.py ez/testing/guards/__init__.py tests/test_guards/test_guard_suite.py
git commit -m "feat(guards): GuardSuite orchestrator + load_user_class

Suite catches guard exceptions as block-severity, supports kind-based
applies(), to_payload() returns JSON-serializable summary.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: LookaheadGuard

**Files:**
- Create: `ez/testing/guards/lookahead.py`
- Create: `tests/test_guards/test_lookahead_guard.py`

- [ ] **Step 1: Write the failing test** — `tests/test_guards/test_lookahead_guard.py`

```python
"""Unit tests for LookaheadGuard: shuffle-future test."""
from __future__ import annotations
import time
import pandas as pd
import pytest
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.lookahead import LookaheadGuard
from ez.testing.guards.mock_data import MOCK_SYMBOLS


class _CleanFactor:
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].rolling(5).mean()


class _LookaheadFactor:
    """Reads future data via shift(-1)."""
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].shift(-1).fillna(df["close"].iloc[-1])


class _CleanCrossFactor:
    warmup_period = 0
    def compute(self, panel, target_date) -> dict:
        return {s: float(panel[s].loc[panel[s].index <= target_date, "close"].iloc[-1])
                for s in panel}


class _LookaheadCrossFactor:
    """Reads panel[sym].iloc[target_idx + 1] — strict future read."""
    warmup_period = 0
    def compute(self, panel, target_date) -> dict:
        result = {}
        for sym, df in panel.items():
            target_idx = df.index.get_indexer([target_date], method="nearest")[0]
            if target_idx + 1 < len(df):
                result[sym] = float(df["close"].iloc[target_idx + 1])
            else:
                result[sym] = float(df["close"].iloc[-1])
        return result


class _CleanPortfolio:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _LookaheadPortfolio:
    """Weights based on future price change."""
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        result = {}
        for sym, df in panel.items():
            idx = df.index.get_indexer([target_date], method="nearest")[0]
            if idx + 1 < len(df):
                result[sym] = float(df["close"].iloc[idx + 1] / df["close"].iloc[idx])
            else:
                result[sym] = 1.0
        total = sum(result.values())
        return {k: v / total for k, v in result.items()}


def _ctx(user_class, kind):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind=kind, user_class=user_class,
    )


def test_lookahead_guard_passes_clean_factor():
    guard = LookaheadGuard()
    result = guard.check(_ctx(_CleanFactor, "factor"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_lookahead_guard_blocks_lookahead_factor():
    guard = LookaheadGuard()
    result = guard.check(_ctx(_LookaheadFactor, "factor"))
    assert result.severity == GuardSeverity.BLOCK
    assert "future" in result.message.lower() or "shuffled" in result.message.lower()


def test_lookahead_guard_passes_clean_cross_factor():
    guard = LookaheadGuard()
    result = guard.check(_ctx(_CleanCrossFactor, "cross_factor"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_lookahead_guard_blocks_lookahead_cross_factor():
    guard = LookaheadGuard()
    result = guard.check(_ctx(_LookaheadCrossFactor, "cross_factor"))
    assert result.severity == GuardSeverity.BLOCK


def test_lookahead_guard_passes_clean_portfolio():
    guard = LookaheadGuard()
    result = guard.check(_ctx(_CleanPortfolio, "portfolio_strategy"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_lookahead_guard_blocks_lookahead_portfolio():
    guard = LookaheadGuard()
    result = guard.check(_ctx(_LookaheadPortfolio, "portfolio_strategy"))
    assert result.severity == GuardSeverity.BLOCK


def test_lookahead_guard_handles_user_class_none():
    guard = LookaheadGuard()
    ctx = GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind="factor", user_class=None, instantiation_error="not found",
    )
    result = guard.check(ctx)
    assert result.severity == GuardSeverity.BLOCK
    assert "not found" in result.message or "could not load" in result.message.lower()


def test_lookahead_guard_runtime_under_150ms():
    guard = LookaheadGuard()
    t0 = time.perf_counter()
    guard.check(_ctx(_CleanFactor, "factor"))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 150, f"LookaheadGuard too slow: {elapsed_ms:.1f} ms"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_lookahead_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ez.testing.guards.lookahead'`

- [ ] **Step 3: Implement `ez/testing/guards/lookahead.py`**

```python
"""LookaheadGuard: detect future data access via shuffle-future test."""
from __future__ import annotations
import math
import time
from typing import Any

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import (
    build_mock_panel, build_shuffled_panel, target_date_at,
)

TOLERANCE = 1e-9
CUTOFF_IDX = 150


def _compare_scalar(a: float, b: float) -> float:
    if a is None or b is None:
        return math.inf if a != b else 0.0
    if math.isnan(a) and math.isnan(b):
        return 0.0
    if math.isnan(a) or math.isnan(b):
        return math.inf
    return abs(a - b)


def _compare_dict(a: dict, b: dict) -> tuple[float, str]:
    if not a and not b:
        return 0.0, ""
    all_keys = set(a) | set(b)
    max_diff = 0.0
    max_key = ""
    for k in all_keys:
        va = a.get(k, 0.0)
        vb = b.get(k, 0.0)
        d = _compare_scalar(float(va), float(vb))
        if d > max_diff:
            max_diff = d
            max_key = k
    return max_diff, max_key


def _run_user_code(cls: type, kind: str, panel: dict, target_date) -> Any:
    """Invoke the user class with the signature for its kind. Returns output."""
    inst = cls()
    if kind == "factor":
        sym = next(iter(panel))
        df = panel[sym]
        mask = df.index <= target_date
        series = inst.compute(df.loc[mask])
        if series is None or len(series) == 0:
            return None
        return float(series.iloc[-1])
    if kind == "cross_factor":
        result = inst.compute(panel, target_date)
        if result is None:
            return {}
        return {str(k): float(v) for k, v in result.items() if v is not None}
    if kind == "strategy":
        sym = next(iter(panel))
        df = panel[sym]
        mask = df.index <= target_date
        sigs = inst.generate_signals(df.loc[mask])
        if sigs is None:
            return None
        if hasattr(sigs, "iloc") and len(sigs) > 0:
            return float(sigs.iloc[-1])
        if isinstance(sigs, list) and sigs:
            return str(sigs[-1])
        return sigs
    if kind == "portfolio_strategy":
        result = inst.generate_weights(panel, target_date, {}, {})
        if result is None:
            return {}
        return {str(k): float(v) for k, v in result.items()}
    if kind == "ml_alpha":
        result = inst.compute(panel, target_date)
        if result is None:
            return {}
        return {str(k): float(v) for k, v in result.items() if v is not None}
    raise ValueError(f"Unknown kind: {kind}")


class LookaheadGuard(Guard):
    name = "LookaheadGuard"
    tier = "block"
    applies_to = ("factor", "cross_factor", "strategy", "portfolio_strategy", "ml_alpha")

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"LookaheadGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )
        try:
            target = target_date_at(CUTOFF_IDX)
            panel_a = build_mock_panel()
            panel_b = build_shuffled_panel(CUTOFF_IDX)
            out_a = _run_user_code(context.user_class, context.kind, panel_a, target)
            out_b = _run_user_code(context.user_class, context.kind, panel_b, target)
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=f"LookaheadGuard: user code raised: {type(e).__name__}: {e}",
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )

        if isinstance(out_a, dict) or isinstance(out_b, dict):
            a = out_a if isinstance(out_a, dict) else {}
            b = out_b if isinstance(out_b, dict) else {}
            max_diff, max_key = _compare_dict(a, b)
        elif isinstance(out_a, (int, float)) or isinstance(out_b, (int, float)):
            max_diff = _compare_scalar(
                float(out_a) if out_a is not None else 0.0,
                float(out_b) if out_b is not None else 0.0,
            )
            max_key = "<scalar>"
        else:
            max_diff = 0.0 if out_a == out_b else math.inf
            max_key = "<value>"

        runtime = (time.perf_counter() - t0) * 1000

        if max_diff > TOLERANCE:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"LookaheadGuard failed: output at t={target.date()} differs "
                    f"when future data (rows after t) is shuffled. "
                    f"Max delta at '{max_key}' = {max_diff:.3e}. "
                    f"Strong signal that the code reads future data."
                ),
                details={
                    "target_date": str(target.date()),
                    "max_abs_diff": max_diff,
                    "max_diff_key": max_key,
                    "tolerance": TOLERANCE,
                    "output_a_sample": str(out_a)[:300],
                    "output_b_sample": str(out_b)[:300],
                },
                runtime_ms=runtime,
            )

        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            details={"target_date": str(target.date()), "max_abs_diff": max_diff},
            runtime_ms=runtime,
        )
```

- [ ] **Step 4: Add export to `ez/testing/guards/__init__.py`**

Append:

```python
from .lookahead import LookaheadGuard

__all__ += ["LookaheadGuard"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_guards/test_lookahead_guard.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add ez/testing/guards/lookahead.py ez/testing/guards/__init__.py tests/test_guards/test_lookahead_guard.py
git commit -m "feat(guards): LookaheadGuard (Tier 1 Block)

Shuffle-future test: run user code on mock panel and on a copy where rows
after cutoff_idx=150 have been shuffled. If output at t=dates[150] differs
beyond 1e-9 tolerance, code reads future data. 8 unit tests, <150ms per run.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: NaNInfGuard

**Files:**
- Create: `ez/testing/guards/nan_inf.py`
- Create: `tests/test_guards/test_nan_inf_guard.py`

- [ ] **Step 1: Write the failing test** — `tests/test_guards/test_nan_inf_guard.py`

```python
"""Unit tests for NaNInfGuard: detect NaN/Inf in output beyond warmup."""
from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.nan_inf import NaNInfGuard


class _CleanFactor:
    warmup_period = 20
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].rolling(20).mean()


class _LogOfZeroFactor:
    """Division by zero / log of zero → -inf."""
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return np.log(df["close"] - df["close"])


class _NaNCrossFactor:
    warmup_period = 0
    def compute(self, panel, target_date) -> dict:
        return {s: float("nan") for s in panel}


class _InfPortfolio:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        return {s: float("inf") for s in panel}


class _CleanPortfolio:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _PortfolioEmpty:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        return {}


def _ctx(user_class, kind):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind=kind, user_class=user_class,
    )


def test_nan_inf_guard_passes_clean_factor_with_warmup_nans():
    result = NaNInfGuard().check(_ctx(_CleanFactor, "factor"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_nan_inf_guard_blocks_log_of_zero_factor():
    result = NaNInfGuard().check(_ctx(_LogOfZeroFactor, "factor"))
    assert result.severity == GuardSeverity.BLOCK
    assert "nan" in result.message.lower() or "inf" in result.message.lower()


def test_nan_inf_guard_blocks_nan_cross_factor():
    result = NaNInfGuard().check(_ctx(_NaNCrossFactor, "cross_factor"))
    assert result.severity == GuardSeverity.BLOCK


def test_nan_inf_guard_blocks_inf_portfolio():
    result = NaNInfGuard().check(_ctx(_InfPortfolio, "portfolio_strategy"))
    assert result.severity == GuardSeverity.BLOCK


def test_nan_inf_guard_passes_clean_portfolio():
    result = NaNInfGuard().check(_ctx(_CleanPortfolio, "portfolio_strategy"))
    assert result.severity == GuardSeverity.PASS


def test_nan_inf_guard_passes_empty_dict():
    """Empty dict has no bad values → pass."""
    result = NaNInfGuard().check(_ctx(_PortfolioEmpty, "portfolio_strategy"))
    assert result.severity == GuardSeverity.PASS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_nan_inf_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ez.testing.guards.nan_inf'`

- [ ] **Step 3: Implement `ez/testing/guards/nan_inf.py`**

```python
"""NaNInfGuard: detect NaN/Inf in output past the warmup region."""
from __future__ import annotations
import math
import time
import numpy as np
import pandas as pd

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at, MOCK_N_DAYS


def _scan_series(series: pd.Series, warmup: int) -> list[int]:
    if series is None or len(series) == 0:
        return []
    values = np.asarray(series.values, dtype=float)
    bad = []
    for i in range(len(values)):
        if i < warmup:
            continue
        v = values[i]
        if math.isnan(v) or math.isinf(v):
            bad.append(i)
    return bad


def _scan_dict(d: dict) -> list[str]:
    if not d:
        return []
    bad = []
    for k, v in d.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            bad.append(str(k))
            continue
        if math.isnan(fv) or math.isinf(fv):
            bad.append(str(k))
    return bad


class NaNInfGuard(Guard):
    name = "NaNInfGuard"
    tier = "block"
    applies_to = ("factor", "cross_factor", "strategy", "portfolio_strategy", "ml_alpha")

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"NaNInfGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )
        try:
            inst = context.user_class()
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=f"NaNInfGuard: instantiation failed: {type(e).__name__}: {e}",
            )
        warmup = int(getattr(inst, "warmup_period", 0))
        panel = build_mock_panel()
        target = target_date_at(MOCK_N_DAYS - 1)
        try:
            if context.kind == "factor":
                sym = next(iter(panel))
                out = inst.compute(panel[sym])
                bad_desc = [str(i) for i in _scan_series(out, warmup)]
            elif context.kind == "cross_factor":
                out = inst.compute(panel, target)
                bad_desc = _scan_dict(dict(out) if out is not None else {})
            elif context.kind == "strategy":
                sym = next(iter(panel))
                out = inst.generate_signals(panel[sym])
                if isinstance(out, pd.Series):
                    bad_desc = [str(i) for i in _scan_series(out, warmup)]
                else:
                    bad_desc = []
            elif context.kind == "portfolio_strategy":
                out = inst.generate_weights(panel, target, {}, {})
                bad_desc = _scan_dict(dict(out) if out is not None else {})
            elif context.kind == "ml_alpha":
                out = inst.compute(panel, target)
                bad_desc = _scan_dict(dict(out) if out is not None else {})
            else:
                return GuardResult(
                    guard_name=self.name,
                    severity=GuardSeverity.PASS,
                    tier=self.tier,
                    message=f"NaNInfGuard: kind '{context.kind}' not covered",
                )
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=f"NaNInfGuard: user code raised: {type(e).__name__}: {e}",
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )
        runtime = (time.perf_counter() - t0) * 1000
        if bad_desc:
            sample = bad_desc[:10]
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"NaNInfGuard failed: output contains NaN/Inf at "
                    f"{len(bad_desc)} position(s) beyond warmup={warmup}. "
                    f"First: {sample}. Common causes: division by zero, "
                    f"log of negative, unpropagated intermediate NaN."
                ),
                details={"bad_positions": bad_desc, "warmup": warmup},
                runtime_ms=runtime,
            )
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            details={"warmup": warmup},
            runtime_ms=runtime,
        )
```

- [ ] **Step 4: Add export**

Append to `ez/testing/guards/__init__.py`:

```python
from .nan_inf import NaNInfGuard

__all__ += ["NaNInfGuard"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_guards/test_nan_inf_guard.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add ez/testing/guards/nan_inf.py ez/testing/guards/__init__.py tests/test_guards/test_nan_inf_guard.py
git commit -m "feat(guards): NaNInfGuard (Tier 1 Block)

Scans factor series / cross-factor dict / portfolio weight dict for NaN/Inf
beyond warmup_period. 6 unit tests covering clean/log-zero/nan/inf/empty.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: WeightSumGuard

**Files:**
- Create: `ez/testing/guards/weight_sum.py`
- Create: `tests/test_guards/test_weight_sum_guard.py`

- [ ] **Step 1: Write the failing test** — `tests/test_guards/test_weight_sum_guard.py`

```python
"""Unit tests for WeightSumGuard: sum(weights) must be in [-0.001, 1.001]."""
from __future__ import annotations
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.weight_sum import WeightSumGuard


class _FullyInvested:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _CashHeavy:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        return {list(panel)[0]: 0.5}


class _OverLevered:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        return {s: 0.4 for s in panel}


class _NetShort:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        return {list(panel)[0]: -0.5}


class _DateDependentBug:
    """Returns sum=1 on early dates but sum=2 on late dates."""
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        import pandas as pd
        threshold = pd.Timestamp("2024-06-01")
        mult = 2.0 if pd.Timestamp(target_date) > threshold else 1.0
        return {list(panel)[0]: mult}


def _ctx(user_class):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind="portfolio_strategy", user_class=user_class,
    )


def test_weight_sum_guard_passes_fully_invested():
    result = WeightSumGuard().check(_ctx(_FullyInvested))
    assert result.severity == GuardSeverity.PASS, result.message


def test_weight_sum_guard_passes_cash_heavy():
    result = WeightSumGuard().check(_ctx(_CashHeavy))
    assert result.severity == GuardSeverity.PASS


def test_weight_sum_guard_blocks_over_levered():
    result = WeightSumGuard().check(_ctx(_OverLevered))
    # 5 symbols * 0.4 = 2.0 > 1.001
    assert result.severity == GuardSeverity.BLOCK
    assert "weight sum" in result.message.lower() or "leverage" in result.message.lower()


def test_weight_sum_guard_blocks_net_short():
    result = WeightSumGuard().check(_ctx(_NetShort))
    assert result.severity == GuardSeverity.BLOCK


def test_weight_sum_guard_catches_date_dependent_bug():
    """Guard must check multiple dates to catch late-date bugs."""
    result = WeightSumGuard().check(_ctx(_DateDependentBug))
    assert result.severity == GuardSeverity.BLOCK
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_weight_sum_guard.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `ez/testing/guards/weight_sum.py`**

```python
"""WeightSumGuard: portfolio weights must be in [-0.001, 1.001] across dates."""
from __future__ import annotations
import time

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at

CHECK_INDICES = (50, 100, 150, 175, 199)
UPPER = 1.001
LOWER = -0.001


class WeightSumGuard(Guard):
    name = "WeightSumGuard"
    tier = "block"
    applies_to = ("portfolio_strategy",)

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"WeightSumGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )
        panel = build_mock_panel()
        try:
            inst = context.user_class()
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=f"WeightSumGuard: instantiation failed: {type(e).__name__}: {e}",
            )
        violations: list[dict] = []
        for idx in CHECK_INDICES:
            target = target_date_at(idx)
            try:
                w = inst.generate_weights(panel, target, {}, {})
            except Exception as e:
                return GuardResult(
                    guard_name=self.name,
                    severity=GuardSeverity.BLOCK,
                    tier=self.tier,
                    message=(
                        f"WeightSumGuard: user code raised at date {target.date()}: "
                        f"{type(e).__name__}: {e}"
                    ),
                    runtime_ms=(time.perf_counter() - t0) * 1000,
                )
            if w is None:
                continue
            s = sum(float(v) for v in w.values())
            if s > UPPER or s < LOWER:
                violations.append({
                    "date": str(target.date()),
                    "sum": s,
                    "weights_preview": {str(k): round(float(v), 6) for k, v in list(w.items())[:5]},
                })
        runtime = (time.perf_counter() - t0) * 1000
        if violations:
            first = violations[0]
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.BLOCK,
                tier=self.tier,
                message=(
                    f"WeightSumGuard failed: weight sum out of [{LOWER}, {UPPER}] "
                    f"at {len(violations)} date(s). First: date={first['date']}, "
                    f"sum={first['sum']:.6f}. A-share long-only strategies must "
                    f"have 0 <= sum(w) <= 1 (over-leverage or net short blocks save)."
                ),
                details={"violations": violations},
                runtime_ms=runtime,
            )
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            details={"n_dates_checked": len(CHECK_INDICES)},
            runtime_ms=runtime,
        )
```

- [ ] **Step 4: Add export**

Append to `ez/testing/guards/__init__.py`:

```python
from .weight_sum import WeightSumGuard

__all__ += ["WeightSumGuard"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_guards/test_weight_sum_guard.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add ez/testing/guards/weight_sum.py ez/testing/guards/__init__.py tests/test_guards/test_weight_sum_guard.py
git commit -m "feat(guards): WeightSumGuard (Tier 1 Block)

Checks sum(weights) at 5 target dates (50, 100, 150, 175, 199). Blocks if
sum > 1.001 (over-leverage) or < -0.001 (net short). Catches date-dependent
bugs the single-date contract test cannot.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: NonNegativeWeightsGuard

**Files:**
- Create: `ez/testing/guards/non_negative.py`
- Create: `tests/test_guards/test_non_negative_guard.py`

- [ ] **Step 1: Write the failing test** — `tests/test_guards/test_non_negative_guard.py`

```python
"""Unit tests for NonNegativeWeightsGuard: individual weights >= 0 (A-share long-only)."""
from __future__ import annotations
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.non_negative import NonNegativeWeightsGuard


class _AllPositive:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _OneNegative:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        syms = list(panel)
        return {syms[0]: -0.1, syms[1]: 0.5, syms[2]: 0.6}


class _TinyNegative:
    """Below -1e-9 tolerance → not flagged."""
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        return {list(panel)[0]: -1e-12}


class _NegativeOnLateDateOnly:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        import pandas as pd
        if pd.Timestamp(target_date) > pd.Timestamp("2024-09-01"):
            return {list(panel)[0]: -0.1, list(panel)[1]: 0.5}
        return {list(panel)[0]: 1.0}


def _ctx(user_class):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind="portfolio_strategy", user_class=user_class,
    )


def test_non_negative_guard_passes_all_positive():
    result = NonNegativeWeightsGuard().check(_ctx(_AllPositive))
    assert result.severity == GuardSeverity.PASS, result.message


def test_non_negative_guard_warns_on_negative():
    result = NonNegativeWeightsGuard().check(_ctx(_OneNegative))
    assert result.severity == GuardSeverity.WARN


def test_non_negative_guard_tolerates_tiny_negative():
    result = NonNegativeWeightsGuard().check(_ctx(_TinyNegative))
    assert result.severity == GuardSeverity.PASS


def test_non_negative_guard_catches_late_date_bug():
    result = NonNegativeWeightsGuard().check(_ctx(_NegativeOnLateDateOnly))
    assert result.severity == GuardSeverity.WARN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_non_negative_guard.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `ez/testing/guards/non_negative.py`**

```python
"""NonNegativeWeightsGuard: individual weights must be >= 0 (A-share long-only)."""
from __future__ import annotations
import time

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at

CHECK_INDICES = (50, 100, 150, 175, 199)
NEG_TOLERANCE = -1e-9


class NonNegativeWeightsGuard(Guard):
    name = "NonNegativeWeightsGuard"
    tier = "warn"
    applies_to = ("portfolio_strategy",)

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"NonNegativeWeightsGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )
        try:
            inst = context.user_class()
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=f"NonNegativeWeightsGuard: instantiation failed: {type(e).__name__}: {e}",
            )
        panel = build_mock_panel()
        violations: list[dict] = []
        for idx in CHECK_INDICES:
            target = target_date_at(idx)
            try:
                w = inst.generate_weights(panel, target, {}, {})
            except Exception:
                continue
            if not w:
                continue
            for sym, val in w.items():
                fv = float(val)
                if fv < NEG_TOLERANCE:
                    violations.append({
                        "date": str(target.date()),
                        "symbol": str(sym),
                        "weight": fv,
                    })
                    break
        runtime = (time.perf_counter() - t0) * 1000
        if violations:
            first = violations[0]
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"NonNegativeWeightsGuard warning: individual weight < 0 at "
                    f"{len(violations)} date(s). First: date={first['date']}, "
                    f"symbol={first['symbol']}, weight={first['weight']:.6f}. "
                    f"A-share long-only requires all individual weights >= 0. "
                    f"If this is intentional (e.g., raw alphas), ignore this warning."
                ),
                details={"violations": violations},
                runtime_ms=runtime,
            )
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            runtime_ms=runtime,
        )
```

- [ ] **Step 4: Add export**

Append to `ez/testing/guards/__init__.py`:

```python
from .non_negative import NonNegativeWeightsGuard

__all__ += ["NonNegativeWeightsGuard"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_guards/test_non_negative_guard.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add ez/testing/guards/non_negative.py ez/testing/guards/__init__.py tests/test_guards/test_non_negative_guard.py
git commit -m "feat(guards): NonNegativeWeightsGuard (Tier 2 Warn)

Warns (non-blocking) if any individual portfolio weight < -1e-9 at any of
5 target dates. Tier 2 because some workflows legitimately return raw
alphas before clipping.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: DeterminismGuard

**Files:**
- Create: `ez/testing/guards/determinism.py`
- Create: `tests/test_guards/test_determinism_guard.py`

- [ ] **Step 1: Write the failing test** — `tests/test_guards/test_determinism_guard.py`

```python
"""Unit tests for DeterminismGuard: two runs on identical input must match."""
from __future__ import annotations
import random
import pandas as pd
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.determinism import DeterminismGuard


class _DeterministicFactor:
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].rolling(5).mean()


class _UnseededRandomPortfolio:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        # Each call produces different random weights.
        weights = {}
        for sym in panel:
            weights[sym] = random.random()
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}


class _DeterministicPortfolio:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        n = len(panel)
        return {s: 1.0 / n for s in panel}


class _NoneOutput:
    warmup_period = 0
    def generate_weights(self, panel, target_date, settings, state):
        return None


def _ctx(user_class, kind="portfolio_strategy"):
    return GuardContext(
        filename="x.py", module_name="x", file_path=Path("/tmp/x.py"),
        kind=kind, user_class=user_class,
    )


def test_determinism_guard_passes_deterministic_factor():
    result = DeterminismGuard().check(_ctx(_DeterministicFactor, "factor"))
    assert result.severity == GuardSeverity.PASS, result.message


def test_determinism_guard_warns_unseeded_random():
    result = DeterminismGuard().check(_ctx(_UnseededRandomPortfolio))
    assert result.severity == GuardSeverity.WARN
    assert "different" in result.message.lower() or "non-deterministic" in result.message.lower() or "identical" in result.message.lower()


def test_determinism_guard_passes_deterministic_portfolio():
    result = DeterminismGuard().check(_ctx(_DeterministicPortfolio))
    assert result.severity == GuardSeverity.PASS


def test_determinism_guard_handles_none_output():
    """Both runs return None → canonicalize equal → pass."""
    result = DeterminismGuard().check(_ctx(_NoneOutput))
    assert result.severity == GuardSeverity.PASS


def test_determinism_guard_never_blocks():
    """DeterminismGuard is Tier 2 warn — never block."""
    result = DeterminismGuard().check(_ctx(_UnseededRandomPortfolio))
    assert result.tier == "warn"
    assert result.severity != GuardSeverity.BLOCK
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_determinism_guard.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `ez/testing/guards/determinism.py`**

```python
"""DeterminismGuard: two runs on identical input must produce identical output."""
from __future__ import annotations
import math
import time
import pandas as pd

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at, MOCK_N_DAYS


def _canonicalize(output) -> str:
    """Produce a deterministic string for comparison."""
    if output is None:
        return "<None>"
    if isinstance(output, (int, float)):
        if isinstance(output, float) and math.isnan(output):
            return "<NaN>"
        return f"{float(output):.15e}"
    if isinstance(output, pd.Series):
        return output.to_json()
    if isinstance(output, dict):
        return str(sorted(
            (str(k), f"{float(v):.15e}") for k, v in output.items()
            if v is not None
        ))
    return str(output)


def _invoke(inst, kind: str, panel: dict, target):
    if kind == "factor":
        sym = next(iter(panel))
        series = inst.compute(panel[sym])
        if series is None or len(series) == 0:
            return None
        return float(series.iloc[-1])
    if kind == "cross_factor":
        out = inst.compute(panel, target)
        return {str(k): float(v) for k, v in (out or {}).items() if v is not None}
    if kind == "strategy":
        sym = next(iter(panel))
        sigs = inst.generate_signals(panel[sym])
        if sigs is None:
            return None
        if hasattr(sigs, "iloc") and len(sigs) > 0:
            return float(sigs.iloc[-1])
        return str(sigs)
    if kind == "portfolio_strategy":
        out = inst.generate_weights(panel, target, {}, {})
        if out is None:
            return None
        return {str(k): float(v) for k, v in out.items()}
    if kind == "ml_alpha":
        out = inst.compute(panel, target)
        return {str(k): float(v) for k, v in (out or {}).items() if v is not None}
    return None


class DeterminismGuard(Guard):
    name = "DeterminismGuard"
    tier = "warn"
    applies_to = ("factor", "cross_factor", "strategy", "portfolio_strategy", "ml_alpha")

    def check(self, context: GuardContext) -> GuardResult:
        t0 = time.perf_counter()
        if context.user_class is None:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"DeterminismGuard: could not load user class. "
                    f"Reason: {context.instantiation_error or 'unknown'}"
                ),
            )
        panel = build_mock_panel()
        target = target_date_at(MOCK_N_DAYS - 1)
        try:
            out_a = _invoke(context.user_class(), context.kind, panel, target)
            out_b = _invoke(context.user_class(), context.kind, panel, target)
        except Exception as e:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=f"DeterminismGuard: user code raised: {type(e).__name__}: {e}",
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )
        ca = _canonicalize(out_a)
        cb = _canonicalize(out_b)
        runtime = (time.perf_counter() - t0) * 1000
        if ca != cb:
            return GuardResult(
                guard_name=self.name,
                severity=GuardSeverity.WARN,
                tier=self.tier,
                message=(
                    f"DeterminismGuard warning: two runs on identical input produced "
                    f"different output. Common causes: unseeded RNG "
                    f"(use np.random.default_rng(seed)), uncontrolled set() iteration, "
                    f"BLAS threading non-determinism (set OMP_NUM_THREADS=1 for ML)."
                ),
                details={"canonical_a": ca[:200], "canonical_b": cb[:200]},
                runtime_ms=runtime,
            )
        return GuardResult(
            guard_name=self.name,
            severity=GuardSeverity.PASS,
            tier=self.tier,
            message="",
            runtime_ms=runtime,
        )
```

- [ ] **Step 4: Add export**

Append to `ez/testing/guards/__init__.py`:

```python
from .determinism import DeterminismGuard

__all__ += ["DeterminismGuard"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_guards/test_determinism_guard.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add ez/testing/guards/determinism.py ez/testing/guards/__init__.py tests/test_guards/test_determinism_guard.py
git commit -m "feat(guards): DeterminismGuard (Tier 2 Warn)

Invokes user code twice on identical mock panel, canonicalizes outputs to
strings, warns if differ. Never blocks — BLAS threading + ML models have
environmental non-determinism that is not fixable from the guard layer.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Wire suite + runtime budget test

**Files:**
- Modify: `ez/testing/guards/suite.py` — replace stub `default_guards()`
- Modify: `tests/test_guards/test_guard_suite.py` — add runtime budget test

- [ ] **Step 1: Update `default_guards()` in `ez/testing/guards/suite.py`**

Replace the stub:

```python
def default_guards() -> list[Guard]:
    """Default guard set for production saves.

    Order: Tier 1 (block) first, Tier 2 (warn) last. Suite runs ALL
    applicable guards — even after a block — so the user sees the full
    picture.
    """
    from .lookahead import LookaheadGuard
    from .nan_inf import NaNInfGuard
    from .weight_sum import WeightSumGuard
    from .non_negative import NonNegativeWeightsGuard
    from .determinism import DeterminismGuard
    return [
        LookaheadGuard(),
        NaNInfGuard(),
        WeightSumGuard(),
        NonNegativeWeightsGuard(),
        DeterminismGuard(),
    ]
```

- [ ] **Step 2: Add the runtime budget test** — append to `tests/test_guards/test_guard_suite.py`

```python
def test_runtime_budget_under_500ms_for_clean_factor():
    """Full default suite on a trivial clean factor must complete in < 500 ms."""
    import pandas as pd
    class _CleanFactor:
        warmup_period = 20
        def compute(self, df):
            return df["close"].rolling(20).mean()
    ctx = GuardContext(
        filename="clean.py", module_name="clean",
        file_path=Path("/tmp/clean.py"),
        kind="factor", user_class=_CleanFactor,
    )
    suite = GuardSuite()   # default_guards
    result = suite.run(ctx)
    assert not result.blocked
    assert result.total_runtime_ms < 500, f"Suite too slow: {result.total_runtime_ms:.1f} ms"


def test_default_suite_has_five_guards():
    from ez.testing.guards.suite import default_guards
    guards = default_guards()
    assert len(guards) == 5
    names = {g.name for g in guards}
    assert names == {
        "LookaheadGuard", "NaNInfGuard", "WeightSumGuard",
        "NonNegativeWeightsGuard", "DeterminismGuard",
    }
```

- [ ] **Step 3: Run the full guard test suite**

Run: `pytest tests/test_guards/ -v`
Expected: 8 (from Task 1) + 5 (Task 2) + 6 (Task 3) + 2 (just added) + 8 (Task 4) + 6 (Task 5) + 5 (Task 6) + 4 (Task 7) + 5 (Task 8) = 49 passed

- [ ] **Step 4: Commit**

```bash
git add ez/testing/guards/suite.py tests/test_guards/test_guard_suite.py
git commit -m "feat(guards): wire 5 guards into default_guards() + runtime budget test

default_guards() now returns [Lookahead, NaNInf, WeightSum, NonNegative,
Determinism]. Runtime budget test asserts total_runtime_ms < 500 for a
trivial factor — hits the spec performance target.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `_sandbox_registries_for_kind` helper

**Files:**
- Modify: `ez/agent/sandbox.py` — add helper near the top
- Create: `tests/test_guards/test_sandbox_registries.py` (renamed from integration test scope for clarity)

**Note:** This task adds the helper in isolation with a parity test. The sandbox save-flow integration follows in Tasks 11-14.

- [ ] **Step 1: Write the failing test** — new file `tests/test_guards/test_sandbox_registries.py`

```python
"""Sandbox-local registry helper + parity with api routes helper."""
from __future__ import annotations
import pytest
from ez.agent.sandbox import _sandbox_registries_for_kind
from ez.api.routes.code import _get_all_registries_for_kind


@pytest.mark.parametrize("kind", [
    "strategy", "factor", "cross_factor", "portfolio_strategy", "ml_alpha",
])
def test_sandbox_and_routes_helpers_return_same_registries(kind):
    a = _sandbox_registries_for_kind(kind)
    b = _get_all_registries_for_kind(kind)
    # Must be the SAME dict objects (identity, not equality) — otherwise we
    # are cleaning a copy and leaving zombies in the real registry.
    assert len(a) == len(b)
    for reg_a, reg_b in zip(a, b):
        assert reg_a is reg_b, (
            f"Drift for kind={kind}: _sandbox_registries_for_kind returned "
            f"a different dict object than _get_all_registries_for_kind. "
            f"Both helpers MUST point at the same class-level registries."
        )


def test_sandbox_registries_unknown_kind_returns_empty():
    assert _sandbox_registries_for_kind("bogus") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_sandbox_registries.py -v`
Expected: FAIL with `ImportError: cannot import name '_sandbox_registries_for_kind' from 'ez.agent.sandbox'`

- [ ] **Step 3: Add helper to `ez/agent/sandbox.py`**

Find an appropriate spot near the top of the file (after imports, before `save_and_validate_strategy`). Add:

```python
def _sandbox_registries_for_kind(kind: str) -> list[dict]:
    """Return all registry dicts that __init_subclass__ would populate for a kind.

    Mirrors `_get_all_registries_for_kind` in `ez/api/routes/code.py` but lives
    in the agent layer to avoid a layer violation (agent must NOT import from
    api). Kept in sync via `tests/test_guards/test_sandbox_registries.py`.
    Returns empty list for unknown kinds — caller will no-op.
    """
    if kind == "strategy":
        from ez.strategy.base import Strategy
        return [Strategy._registry]
    if kind == "factor":
        from ez.factor.base import Factor
        return [Factor._registry, Factor._registry_by_key]
    if kind == "cross_factor":
        from ez.portfolio.cross_sectional_factor import CrossSectionalFactor
        return [CrossSectionalFactor._registry, CrossSectionalFactor._registry_by_key]
    if kind == "ml_alpha":
        # MLAlpha inherits from CrossSectionalFactor — same registry.
        from ez.portfolio.cross_sectional_factor import CrossSectionalFactor
        return [CrossSectionalFactor._registry, CrossSectionalFactor._registry_by_key]
    if kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy
        return [PortfolioStrategy._registry, PortfolioStrategy._registry_by_key]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_guards/test_sandbox_registries.py -v`
Expected: 6 passed (5 parametrized + 1 unknown)

- [ ] **Step 5: Commit**

```bash
git add ez/agent/sandbox.py tests/test_guards/test_sandbox_registries.py
git commit -m "feat(sandbox): _sandbox_registries_for_kind helper

Mirrors ez/api/routes/code._get_all_registries_for_kind but lives in the
agent layer (agent must not import from api). Parity test asserts both
helpers return SAME dict objects (identity, not equality) so registry
cleanup in guard rollback stays in sync.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: `_run_guards` sandbox helper

**Files:**
- Modify: `ez/agent/sandbox.py` — add `_run_guards` helper
- Create: `tests/test_guards/test_run_guards.py`

- [ ] **Step 1: Write the failing test** — `tests/test_guards/test_run_guards.py`

```python
"""Unit test for the _run_guards sandbox helper."""
from __future__ import annotations
from pathlib import Path
from ez.agent.sandbox import _run_guards
from ez.testing.guards.suite import SuiteResult


def _write_clean_factor(tmp_path: Path) -> Path:
    code = '''
from ez.factor.base import Factor
import pandas as pd

class CleanTestFactor(Factor):
    name = "clean_test_factor"
    warmup_period = 5

    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].rolling(5).mean()
'''
    p = tmp_path / "clean_test_factor.py"
    p.write_text(code, encoding="utf-8")
    return p


def test_run_guards_returns_suite_result(tmp_path):
    """_run_guards loads the user class and returns a SuiteResult."""
    file_path = _write_clean_factor(tmp_path)
    result = _run_guards(file_path.name, "factor", tmp_path)
    assert isinstance(result, SuiteResult)
    assert not result.blocked   # clean code should not block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_guards/test_run_guards.py -v`
Expected: FAIL with `ImportError: cannot import name '_run_guards' from 'ez.agent.sandbox'`

- [ ] **Step 3: Add the helper to `ez/agent/sandbox.py`**

Add near the top of the file (after `_sandbox_registries_for_kind` from Task 10):

```python
# V2.19.0: guard framework helper.
def _run_guards(filename: str, kind: str, target_dir: Path):
    """Run the GuardSuite against a just-saved user file.

    Called by the three save flows AFTER contract test passes and BEFORE
    hot-reload. If the suite blocks, the caller rolls back the file and
    cleans any registry pollution introduced by the guard's import.
    """
    from ez.testing.guards.suite import GuardSuite, load_user_class
    from ez.testing.guards.base import GuardContext

    stem = filename.replace(".py", "")
    module_name = f"{target_dir.name}.{stem}"
    file_path = target_dir / filename
    user_class, err = load_user_class(file_path, module_name, kind)  # type: ignore[arg-type]
    context = GuardContext(
        filename=filename,
        module_name=module_name,
        file_path=file_path,
        kind=kind,   # type: ignore[arg-type]
        user_class=user_class,
        instantiation_error=err,
    )
    return GuardSuite().run(context)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_guards/test_run_guards.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add ez/agent/sandbox.py tests/test_guards/test_run_guards.py
git commit -m "feat(sandbox): _run_guards helper wraps GuardSuite for save flows

Imports are lazy (inside the function) so that ez.testing.guards stays
an optional runtime dependency — a minimal deployment without the guards
module still imports ez.agent.sandbox without error.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Hook 1 — strategy save integration

**Files:**
- Modify: `ez/agent/sandbox.py` — `save_and_validate_strategy`

- [ ] **Step 1: Read the current strategy save function** to locate the hook site

Run: `grep -n "save_and_validate_strategy\|_run_contract_test\|_reload_user_strategy" ez/agent/sandbox.py | head -10`

Confirm the structure is: contract test pass → `_reload_user_strategy` → return success.

- [ ] **Step 2: Modify `save_and_validate_strategy`**

Find the block (around line 429) that handles contract test success. After the contract test passes but BEFORE `_reload_user_strategy(safe_name)`, insert the guard block:

```python
    # Run contract test in subprocess with timeout (EXISTING)
    test_result = _run_contract_test(safe_name)
    if not test_result["passed"]:
        if had_original:
            target.write_text(original_code, encoding="utf-8")
        else:
            target.unlink(missing_ok=True)
        return {
            "success": False,
            "errors": [f"Contract test failed: {test_result['output']}"],
            "test_output": test_result["output"],
        }

    # V2.19.0: guard framework. Run AFTER contract test passes and BEFORE
    # hot-reload. If blocked, clean any registry pollution from the guard's
    # import + rollback the file + restore backup.
    guard_result = _run_guards(safe_name, "strategy", _STRATEGIES_DIR)
    if guard_result.blocked:
        from ez.strategy.base import Strategy
        module_name = f"strategies.{safe_name.replace('.py', '')}"
        with _reload_lock:
            dirty = [k for k, v in Strategy._registry.items() if v.__module__ == module_name]
            for k in dirty:
                Strategy._registry.pop(k, None)
            if module_name in sys.modules:
                del sys.modules[module_name]
        if had_original:
            target.write_text(original_code, encoding="utf-8")
            try:
                _reload_user_strategy(safe_name)
            except Exception as restore_err:
                logger.warning(
                    "Strategy guard rollback restored file but re-register failed: %s",
                    restore_err,
                )
        else:
            target.unlink(missing_ok=True)
        return {
            "success": False,
            "errors": [
                f"Guard failed: {blk.guard_name}: {blk.message}"
                for blk in guard_result.blocks
            ],
            "test_output": test_result["output"],
            "guard_result": guard_result.to_payload(),
        }

    # Hot-reload (EXISTING — unchanged) ...
    try:
        _reload_user_strategy(safe_name)
    except Exception as e:
        ...
```

Then in the final success-path return, add `guard_result` to the payload:

```python
    return {
        "success": True,
        "errors": [],
        "path": f"strategies/{safe_name}",
        "test_output": test_result["output"],
        "guard_result": guard_result.to_payload(),
    }
```

- [ ] **Step 3: Run existing strategy sandbox tests to confirm nothing breaks**

Run: `pytest tests/test_agent/ -v -k "save_and_validate_strategy or sandbox_strategy" 2>&1 | tail -40`
Expected: existing tests pass (we haven't changed their behavior — guards only add info).

- [ ] **Step 4: Run guard tests to confirm nothing regressed**

Run: `pytest tests/test_guards/ -v`
Expected: all prior guard tests still passing (Task 1-11 total = 49 + 6 parity + 1 run_guards = 56 passed).

- [ ] **Step 5: Commit**

```bash
git add ez/agent/sandbox.py
git commit -m "feat(sandbox): wire guard framework into strategy save flow (Hook 1)

save_and_validate_strategy now runs _run_guards after contract test passes
and before hot-reload. On block: cleans Strategy._registry for the module,
rolls back file to backup (or deletes new file), re-registers backup, and
returns failure with guard_result payload. Success path adds guard_result
to return dict.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Hook 2 — factor save integration

**Files:**
- Modify: `ez/agent/sandbox.py` — inside `save_and_validate_code` factor branch

- [ ] **Step 1: Read the factor branch to locate the hook site**

Run: `grep -n '"factor"\|_reload_factor_code\|_has_factor_class' ez/agent/sandbox.py | head -15`

Confirm the structure is: syntax check → AST check → write file → subprocess import validation → `_reload_factor_code` → return.

- [ ] **Step 2: Modify the factor branch**

Inside the `try:` block of the factor branch, AFTER the subprocess import validation confirms `registered` is non-empty (around line 705), and BEFORE the `if not _frozen_inprocess: _reload_factor_code(...)` call, add straight-line guard check + rollback:

```python
            if not registered:
                raise ValueError("No Factor subclass found in code")

            # V2.19.0 guard framework. Run before hot-reload. Rollback inline
            # to avoid exception-ordering hazards with the existing except
            # Exception cleanup block.
            guard_result = _run_guards(safe_name, "factor", target_dir)
            if guard_result.blocked:
                # Clean dual-dict registry (same pattern as existing V2.12.2
                # rollback). Guard's load_user_class imported the module,
                # firing __init_subclass__ and dirtying both registries.
                from ez.factor.base import Factor
                dirty = [k for k, v in Factor._registry.items() if v.__module__ == module_name]
                for k in dirty:
                    del Factor._registry[k]
                dirty_full = [k for k, v in Factor._registry_by_key.items() if v.__module__ == module_name]
                for k in dirty_full:
                    del Factor._registry_by_key[k]
                if module_name in sys.modules:
                    del sys.modules[module_name]
                if backup is not None:
                    target.write_text(backup, encoding="utf-8")
                    try:
                        _reload_factor_code(safe_name, target_dir)
                    except Exception as restore_err:
                        logger.warning(
                            "Factor guard rollback restored file but re-register failed: %s",
                            restore_err,
                        )
                else:
                    target.unlink(missing_ok=True)
                return {
                    "success": False,
                    "errors": [
                        f"Guard failed: {blk.guard_name}: {blk.message}"
                        for blk in guard_result.blocks
                    ],
                    "guard_result": guard_result.to_payload(),
                }

            # Hot-reload (EXISTING) ...
            if not _frozen_inprocess:
                _reload_factor_code(safe_name, target_dir)
                live = [k for k, v in Factor._registry.items() if v.__module__ == module_name]
                if not live:
                    raise ValueError(
                        f"Factor hot-reload succeeded but registry has no entries for {module_name}"
                    )
```

Then find the factor success return (around line 752) and add `guard_result` to the payload:

```python
        return {
            "success": True,
            "path": str(target),
            "test_output": f"Factor saved. Registered: {registered}",
            "guard_result": guard_result.to_payload(),
        }
```

- [ ] **Step 3: Run existing factor sandbox tests**

Run: `pytest tests/test_agent/ -v -k "factor and sandbox or save_and_validate_code" 2>&1 | tail -30`
Expected: existing tests still pass.

- [ ] **Step 4: Run guard tests**

Run: `pytest tests/test_guards/ -v`
Expected: all still passing.

- [ ] **Step 5: Commit**

```bash
git add ez/agent/sandbox.py
git commit -m "feat(sandbox): wire guard framework into factor save flow (Hook 2)

save_and_validate_code factor branch now runs _run_guards after subprocess
import validation and before _reload_factor_code. On block: cleans dual
Factor._registry, rolls back file, re-registers backup. Straight-line
rollback (no exception) to avoid ordering hazards with the existing
except Exception cleanup block.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Hook 3 — portfolio/cross_factor/ml_alpha save integration

**Files:**
- Modify: `ez/agent/sandbox.py` — portfolio/cross_factor/ml_alpha branch

- [ ] **Step 1: Locate the hook site**

Run: `grep -n '_run_portfolio_contract_test\|_reload_portfolio_code' ez/agent/sandbox.py`

The portfolio/cross_factor/ml_alpha branch (around line 768-800) runs: write file → `_run_portfolio_contract_test` → on fail rollback → `_reload_portfolio_code` → return.

- [ ] **Step 2: Modify the portfolio branch**

Between the contract test success check and `_reload_portfolio_code`:

```python
    # Contract test (EXISTING)
    test_result = _run_portfolio_contract_test(safe_name, kind, target_dir)
    if not test_result["passed"]:
        if original_code:
            target.write_text(original_code, encoding="utf-8")
        else:
            target.unlink(missing_ok=True)
        return {
            "success": False,
            "errors": [f"Contract test failed: {test_result['output']}"],
            "test_output": test_result["output"],
        }

    # V2.19.0 guard framework.
    guard_result = _run_guards(safe_name, kind, target_dir)
    if guard_result.blocked:
        stem = safe_name.replace(".py", "")
        module_name_pf = f"{target_dir.name}.{stem}"
        with _reload_lock:
            for reg in _sandbox_registries_for_kind(kind):
                dirty = [k for k, v in reg.items() if v.__module__ == module_name_pf]
                for k in dirty:
                    reg.pop(k, None)
            if module_name_pf in sys.modules:
                del sys.modules[module_name_pf]
        if original_code:
            target.write_text(original_code, encoding="utf-8")
            try:
                _reload_portfolio_code(safe_name, kind, target_dir)
            except Exception as restore_err:
                logger.warning(
                    "Portfolio guard rollback restored file but re-register failed: %s",
                    restore_err,
                )
        else:
            target.unlink(missing_ok=True)
        return {
            "success": False,
            "errors": [
                f"Guard failed: {blk.guard_name}: {blk.message}"
                for blk in guard_result.blocks
            ],
            "test_output": test_result["output"],
            "guard_result": guard_result.to_payload(),
        }

    # Hot-reload (EXISTING) ...
    try:
        _reload_portfolio_code(safe_name, kind, target_dir)
    except Exception as e:
        ...

    return {
        "success": True,
        "errors": [],
        "path": f"{target_dir.name}/{safe_name}",
        "test_output": test_result["output"],
        "guard_result": guard_result.to_payload(),
    }
```

- [ ] **Step 3: Run existing portfolio sandbox tests**

Run: `pytest tests/test_agent/ -v -k "portfolio or cross_factor or ml_alpha" 2>&1 | tail -40`
Expected: existing tests pass.

- [ ] **Step 4: Run guard tests**

Run: `pytest tests/test_guards/ -v`
Expected: all still passing.

- [ ] **Step 5: Commit**

```bash
git add ez/agent/sandbox.py
git commit -m "feat(sandbox): wire guard framework into portfolio save flow (Hook 3)

save_and_validate_code portfolio/cross_factor/ml_alpha branch now runs
_run_guards after contract test and before _reload_portfolio_code. On
block: uses _sandbox_registries_for_kind to clean dual-dict registries
(portfolio_strategy uses [_registry, _registry_by_key]; cross_factor and
ml_alpha share CrossSectionalFactor dual-dict), rolls back file, and
re-registers backup.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: End-to-end sandbox integration tests

**Files:**
- Create: `tests/test_guards/test_sandbox_integration.py`

- [ ] **Step 1: Write 14 integration tests** — `tests/test_guards/test_sandbox_integration.py`

```python
"""End-to-end integration tests for guard framework + sandbox save flow.

Uses real `save_and_validate_code` + real `_STRATEGIES_DIR` / factors dir /
etc. via a tmp_path monkeypatch fixture. Tests cover all kinds, clean + bug
cases, rollback semantics, and guard-bug handling.
"""
from __future__ import annotations
import json
import pytest
from pathlib import Path

from ez.agent.sandbox import save_and_validate_code


@pytest.fixture
def sandbox_tmp(tmp_path, monkeypatch):
    """Redirect all sandbox directories to tmp_path to avoid polluting real dirs."""
    from ez.agent import sandbox
    (tmp_path / "strategies").mkdir()
    (tmp_path / "factors").mkdir()
    (tmp_path / "portfolio_strategies").mkdir()
    (tmp_path / "cross_factors").mkdir()
    (tmp_path / "ml_alphas").mkdir()
    monkeypatch.setattr(sandbox, "_STRATEGIES_DIR", tmp_path / "strategies")
    # Note: factors, portfolio, etc. use _get_dir(kind) — patch the dict.
    monkeypatch.setattr(sandbox, "_FACTORS_DIR", tmp_path / "factors")
    monkeypatch.setattr(sandbox, "_PORTFOLIO_DIR", tmp_path / "portfolio_strategies", raising=False)
    monkeypatch.setattr(sandbox, "_CROSS_FACTORS_DIR", tmp_path / "cross_factors", raising=False)
    monkeypatch.setattr(sandbox, "_ML_ALPHAS_DIR", tmp_path / "ml_alphas", raising=False)
    return tmp_path


# Test 1: strategy clean
def test_strategy_clean_passes_all_guards(sandbox_tmp):
    code = '''
from ez.strategy.base import Strategy
import pandas as pd

class CleanStrategyFoo(Strategy):
    name = "clean_strategy_foo"
    def generate_signals(self, df):
        return df["close"].rolling(5).mean()
'''
    result = save_and_validate_code("clean_strategy_foo.py", code, "strategy")
    assert result["success"] is True, result.get("errors")
    assert "guard_result" in result
    assert not result["guard_result"]["blocked"]


# Test 2: factor clean
def test_factor_clean_passes(sandbox_tmp):
    code = '''
from ez.factor.base import Factor
import pandas as pd

class CleanFactorBar(Factor):
    name = "clean_factor_bar"
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].rolling(5).mean()
'''
    result = save_and_validate_code("clean_factor_bar.py", code, "factor")
    assert result["success"] is True, result.get("errors")
    assert not result["guard_result"]["blocked"]


# Test 3: factor NaN
def test_factor_nan_is_blocked(sandbox_tmp):
    code = '''
from ez.factor.base import Factor
import numpy as np, pandas as pd

class NaNFactor(Factor):
    name = "nan_factor"
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return np.log(df["close"] - df["close"])
'''
    result = save_and_validate_code("nan_factor.py", code, "factor")
    assert result["success"] is False
    assert "NaNInfGuard" in " ".join(result.get("errors", []))
    assert not (sandbox_tmp / "factors" / "nan_factor.py").exists()


# Test 4: portfolio over-leverage
def test_portfolio_over_leverage_blocked(sandbox_tmp):
    code = '''
from ez.portfolio.portfolio_strategy import PortfolioStrategy

class OverLeveredBaz(PortfolioStrategy):
    name = "over_levered_baz"
    def generate_weights(self, panel, target_date, settings, state):
        return {s: 0.5 for s in panel}  # 5 * 0.5 = 2.5 > 1.001
'''
    result = save_and_validate_code("over_levered_baz.py", code, "portfolio_strategy")
    assert result["success"] is False
    assert "WeightSumGuard" in " ".join(result.get("errors", []))


# Test 5: portfolio negative weight → WARN (save still succeeds)
def test_portfolio_negative_weight_warns_but_passes(sandbox_tmp):
    code = '''
from ez.portfolio.portfolio_strategy import PortfolioStrategy

class NegativeQux(PortfolioStrategy):
    name = "negative_qux"
    def generate_weights(self, panel, target_date, settings, state):
        syms = list(panel)
        return {syms[0]: -0.1, syms[1]: 0.5, syms[2]: 0.6}
'''
    result = save_and_validate_code("negative_qux.py", code, "portfolio_strategy")
    assert result["success"] is True
    warnings = result["guard_result"]["n_warnings"]
    assert warnings >= 1


# Test 6: cross_factor lookahead
def test_cross_factor_lookahead_blocked(sandbox_tmp):
    code = '''
from ez.portfolio.cross_sectional_factor import CrossSectionalFactor

class LookaheadZorg(CrossSectionalFactor):
    name = "lookahead_zorg"
    warmup_period = 0
    def compute(self, panel, target_date):
        result = {}
        for sym, df in panel.items():
            idx = df.index.get_indexer([target_date], method="nearest")[0]
            if idx + 1 < len(df):
                result[sym] = float(df["close"].iloc[idx + 1])
            else:
                result[sym] = float(df["close"].iloc[-1])
        return result
'''
    result = save_and_validate_code("lookahead_zorg.py", code, "cross_factor")
    assert result["success"] is False
    assert "LookaheadGuard" in " ".join(result.get("errors", []))


# Test 7: ml_alpha clean (requires sklearn)
def test_ml_alpha_clean_passes(sandbox_tmp):
    pytest.importorskip("sklearn")
    code = '''
from ez.portfolio.ml_alpha import MLAlpha
from sklearn.linear_model import Ridge
import pandas as pd

def _features(df):
    return pd.DataFrame({"r5": df["close"].pct_change(5).fillna(0)}, index=df.index)

def _target(df):
    return df["close"].pct_change(5).shift(-5).fillna(0)

class CleanRidgeFactor(MLAlpha):
    name = "clean_ridge_factor"
    def __init__(self):
        super().__init__(
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=_features,
            target_fn=_target,
            train_window=100,
            retrain_freq=63,
            feature_warmup_days=10,
        )
'''
    result = save_and_validate_code("clean_ridge_factor.py", code, "ml_alpha")
    assert result["success"] is True, result.get("errors")


# Test 8: unseeded random → DeterminismGuard warns
def test_strategy_unseeded_random_warns(sandbox_tmp):
    code = '''
from ez.strategy.base import Strategy
import pandas as pd, random

class UnseededStrategy(Strategy):
    name = "unseeded_strategy"
    def generate_signals(self, df):
        result = pd.Series(index=df.index, dtype=float)
        for i in range(len(df)):
            result.iloc[i] = random.random()
        return result
'''
    result = save_and_validate_code("unseeded_strategy.py", code, "strategy")
    assert result["success"] is True
    assert result["guard_result"]["n_warnings"] >= 1


# Test 9: guard itself raises — suite reports as block with "guard bug" message
def test_guard_exception_surfaces_as_block(monkeypatch, sandbox_tmp):
    from ez.testing.guards import LookaheadGuard
    original_check = LookaheadGuard.check
    def broken_check(self, ctx):
        raise RuntimeError("guard bug")
    monkeypatch.setattr(LookaheadGuard, "check", broken_check)
    try:
        code = '''
from ez.strategy.base import Strategy
class FooBug(Strategy):
    name = "foo_bug"
    def generate_signals(self, df):
        return df["close"].rolling(5).mean()
'''
        result = save_and_validate_code("foo_bug.py", code, "strategy")
        assert result["success"] is False
        assert "guard bug" in " ".join(result.get("errors", [])).lower()
    finally:
        monkeypatch.setattr(LookaheadGuard, "check", original_check)


# Test 10: overwrite buggy file over clean file — original is restored
def test_overwrite_with_buggy_restores_backup(sandbox_tmp):
    clean = '''
from ez.factor.base import Factor
import pandas as pd

class ClobberFactor(Factor):
    name = "clobber_factor"
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].rolling(5).mean()
'''
    buggy = '''
from ez.factor.base import Factor
import numpy as np, pandas as pd

class ClobberFactor(Factor):
    name = "clobber_factor"
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return np.log(df["close"] - df["close"])
'''
    r1 = save_and_validate_code("clobber_factor.py", clean, "factor")
    assert r1["success"] is True
    r2 = save_and_validate_code("clobber_factor.py", buggy, "factor", overwrite=True)
    assert r2["success"] is False
    disk = (sandbox_tmp / "factors" / "clobber_factor.py").read_text(encoding="utf-8")
    assert "rolling(5)" in disk
    assert "log(df" not in disk


# Test 11: new-file block → file deleted, registry clean
def test_new_file_block_deletes_file(sandbox_tmp):
    from ez.factor.base import Factor
    code = '''
from ez.factor.base import Factor
import numpy as np, pandas as pd

class FreshBadFactor(Factor):
    name = "fresh_bad_factor"
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return np.log(df["close"] - df["close"])
'''
    result = save_and_validate_code("fresh_bad_factor.py", code, "factor")
    assert result["success"] is False
    assert not (sandbox_tmp / "factors" / "fresh_bad_factor.py").exists()
    leaked = [k for k, v in Factor._registry.items()
              if v.__module__ == "factors.fresh_bad_factor"]
    assert leaked == []


# Test 12: overwrite block — original class still in registry after rollback
def test_overwrite_block_keeps_original_in_registry(sandbox_tmp):
    from ez.factor.base import Factor
    clean = '''
from ez.factor.base import Factor
import pandas as pd

class KeepMeFactor(Factor):
    name = "keep_me_factor"
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].rolling(5).mean()
'''
    buggy = '''
from ez.factor.base import Factor
import numpy as np, pandas as pd

class KeepMeFactor(Factor):
    name = "keep_me_factor"
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.Series:
        return np.log(df["close"] - df["close"])
'''
    save_and_validate_code("keep_me_factor.py", clean, "factor")
    r2 = save_and_validate_code("keep_me_factor.py", buggy, "factor", overwrite=True)
    assert r2["success"] is False
    live = [k for k, v in Factor._registry.items()
            if v.__module__ == "factors.keep_me_factor"]
    assert len(live) >= 1


# Test 13: strategy new-file block deletes file
def test_strategy_new_file_block_deletes(sandbox_tmp):
    code = '''
from ez.strategy.base import Strategy

class FreshBadStrategy(Strategy):
    name = "fresh_bad_strategy"
    def generate_signals(self, df):
        return df["close"].shift(-1)  # lookahead
'''
    result = save_and_validate_code("fresh_bad_strategy.py", code, "strategy")
    assert result["success"] is False
    assert not (sandbox_tmp / "strategies" / "fresh_bad_strategy.py").exists()


# Test 14: portfolio strategy clean passes
def test_portfolio_clean_passes(sandbox_tmp):
    code = '''
from ez.portfolio.portfolio_strategy import PortfolioStrategy

class CleanPortfolioFoo(PortfolioStrategy):
    name = "clean_portfolio_foo"
    def generate_weights(self, panel, target_date, settings, state):
        n = len(panel)
        return {s: 1.0 / n for s in panel}
'''
    result = save_and_validate_code("clean_portfolio_foo.py", code, "portfolio_strategy")
    assert result["success"] is True, result.get("errors")
```

- [ ] **Step 2: Run the integration tests**

Run: `pytest tests/test_guards/test_sandbox_integration.py -v`
Expected: 14 passed (1 skipped if sklearn not installed — tests/test_sandbox_integration will auto-skip Test 7 via `importorskip`)

**If tests fail:** common causes and fixes:
- Monkeypatch targets `_FACTORS_DIR`, `_PORTFOLIO_DIR`, etc. — verify these attribute names exist in `sandbox.py`. If they use `_get_dir(kind)`, you may need to monkeypatch the `_VALID_KINDS_DIR_MAP` dict instead.
- ml_alpha test needs the real `ml_alphas/` dir in `target_dir.name`, which is `"ml_alphas"`. Verify.
- If monkeypatch approach doesn't work due to how sandbox resolves dirs, fall back to running against the real dirs but using unique prefixed filenames (`_guard_test_` prefix) and cleaning up in a fixture.

- [ ] **Step 3: Commit**

```bash
git add tests/test_guards/test_sandbox_integration.py
git commit -m "test(guards): 14 end-to-end integration tests through save_and_validate_code

Covers all 5 kinds (strategy/factor/cross_factor/portfolio/ml_alpha) × clean
and bug cases. Verifies file rollback, registry cleanup, guard-exception
handling, overwrite preservation. ML Alpha test skips gracefully without sklearn.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Golden bug regression tests

**Files:**
- Create: `tests/test_guards/golden_bugs/__init__.py`
- Create: `tests/test_guards/golden_bugs/test_v1_dynamic_ef_lookahead.py`
- Create: `tests/test_guards/golden_bugs/test_mlalpha_purge_lookahead.py`

- [ ] **Step 1: Create package marker**

```bash
touch tests/test_guards/golden_bugs/__init__.py
```

- [ ] **Step 2: Write golden bug 1** — `tests/test_guards/golden_bugs/test_v1_dynamic_ef_lookahead.py`

```python
"""Golden bug 1: v1 Dynamic EF lookahead.

Historical context (see validation/phase_o_nested_oos.py and
validation/report_charts/降回撤研究_v5.md):
  The original v1 Dynamic EF implementation computed weights using
  prices from date t, but the 'trading' happened at t+1. This silently
  inflated Sharpe by ~0.4 in backtest and would have destroyed live
  capital. Codex caught it during round-2 review.

This test encodes a minimal reproduction as a CrossSectionalFactor and
asserts LookaheadGuard blocks it. If this test fails in the future,
LookaheadGuard has regressed.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.lookahead import LookaheadGuard


class _V1DynamicEFBugRepro:
    """Minimal reproduction of the v1 Dynamic EF lookahead bug.

    BUG: reads `df.iloc[target_idx + 1]` — one trading day into the future.
    """
    warmup_period = 0

    def compute(self, panel: dict, target_date: datetime) -> dict:
        result = {}
        for sym, df in panel.items():
            target_idx = df.index.get_indexer([target_date], method="nearest")[0]
            if target_idx + 1 < len(df):
                future_price = df["close"].iloc[target_idx + 1]
            else:
                future_price = df["close"].iloc[-1]
            past_price = df["close"].iloc[target_idx]
            result[sym] = float((future_price - past_price) / past_price)
        return result


def test_v1_dynamic_ef_bug_is_blocked():
    guard = LookaheadGuard()
    ctx = GuardContext(
        filename="v1_ef_bug.py",
        module_name="test_v1_ef_bug",
        file_path=Path("/tmp/v1_ef_bug.py"),
        kind="cross_factor",
        user_class=_V1DynamicEFBugRepro,
    )
    result = guard.check(ctx)
    assert result.severity == GuardSeverity.BLOCK, (
        f"Golden bug regression: v1 Dynamic EF lookahead not caught. "
        f"Guard result: {result.message}"
    )
```

- [ ] **Step 3: Write golden bug 2** — `tests/test_guards/golden_bugs/test_mlalpha_purge_lookahead.py`

```python
"""Golden bug 2: MLAlpha timedelta(days=N) calendar purge lookahead.

Historical context: MLAlpha V1 round 1 used `timedelta(days=N)` for label
purge, which lets labels cross weekends and point at prediction windows.
Fixed in round 2 by using positional `iloc[:-purge_bars]`. This test encodes
a minimal factor that uses calendar-day offset for a future-reading window
and asserts LookaheadGuard catches it.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.lookahead import LookaheadGuard


class _CalendarPurgeLookaheadRepro:
    """Factor that uses 'close 5 calendar days later' — contains up to 2
    trading days of future information across weekends."""
    warmup_period = 0

    def compute(self, panel: dict, target_date: datetime) -> dict:
        result = {}
        for sym, df in panel.items():
            target_plus_5cal = target_date + timedelta(days=5)
            mask_future = df.index >= target_plus_5cal
            if mask_future.any():
                future_close = float(df.loc[mask_future, "close"].iloc[0])
            else:
                future_close = float(df["close"].iloc[-1])
            current = float(df.loc[df.index <= target_date, "close"].iloc[-1])
            result[sym] = (future_close - current) / current
        return result


def test_mlalpha_calendar_purge_bug_is_blocked():
    guard = LookaheadGuard()
    ctx = GuardContext(
        filename="mlalpha_purge_bug.py",
        module_name="test_mlalpha_purge_bug",
        file_path=Path("/tmp/mlalpha_purge_bug.py"),
        kind="cross_factor",
        user_class=_CalendarPurgeLookaheadRepro,
    )
    result = guard.check(ctx)
    assert result.severity == GuardSeverity.BLOCK, (
        f"Golden bug regression: MLAlpha calendar purge lookahead not caught. "
        f"Guard result: {result.message}"
    )
```

- [ ] **Step 4: Run the golden bug tests**

Run: `pytest tests/test_guards/golden_bugs/ -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_guards/golden_bugs/
git commit -m "test(guards): golden bug regression tests

Two historical bugs encoded as permanent regression tests:
  1. v1 Dynamic EF: reads close[t+1] as 'past' price
  2. MLAlpha timedelta purge: calendar-day window spans weekends

Both must be blocked by LookaheadGuard. If either test fails in the future,
LookaheadGuard has regressed.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Frontend state + save handler

**Files:**
- Modify: `web/src/components/CodeEditor.tsx`

- [ ] **Step 1: Read the current save handler**

Run: `grep -n "const save = async\|setTestOutput\|guardReport\|GuardReport" web/src/components/CodeEditor.tsx | head -15`

Locate the `save` function (around line 322).

- [ ] **Step 2: Add the GuardReport type and state**

At the top of the component (near the existing `useState` calls around line 50-100), add:

```tsx
type GuardReport = {
  blocked: boolean
  n_warnings: number
  n_blocks: number
  total_runtime_ms: number
  guards: Array<{
    name: string
    severity: 'pass' | 'warn' | 'block'
    tier: 'block' | 'warn'
    message: string
    runtime_ms: number
    details: Record<string, unknown>
  }>
}
```

Add state right after `const [testOutput, setTestOutput] = useState('')`:

```tsx
const [guardReport, setGuardReport] = useState<GuardReport | null>(null)
```

- [ ] **Step 3: Update the `save` function** to capture `guard_result` on both branches

Locate the `save` function at ~line 322. Modify the success and error branches:

```tsx
const save = async (overwrite = false) => {
  if (!filename) { setStatus('请设置文件名'); return }
  setSaving(true)
  setErrors([])
  setTestOutput('')
  setGuardReport(null)   // NEW: clear previous guard report
  setStatus('保存中，正在运行合约测试与代码守卫...')
  try {
    const res = await api('/save', {
      method: 'POST',
      body: JSON.stringify({ filename, code, overwrite, kind: currentKind }),
    })
    const data = await res.json()
    if (res.ok) {
      setStatus(`已保存至 ${data.path} — 合约测试通过!`)
      setErrors([])
      setTestOutput(data.test_output || '')
      setGuardReport(data.guard_result || null)   // NEW
      setCommittedFilename(filename)
      loadAllFiles()
    } else {
      const detail = data.detail || data
      const errs = detail.errors || [JSON.stringify(detail)]
      if (!overwrite && errs.some((e: string) => e.includes('already exists'))) {
        setSaving(false)
        return save(true)
      }
      setStatus('保存失败')
      setErrors(errs)
      if (detail.test_output) setTestOutput(detail.test_output)
      if (detail.guard_result) setGuardReport(detail.guard_result)   // NEW
    }
  } catch (e: unknown) { setStatus(`Error: ${e instanceof Error ? e.message : String(e)}`) }
  finally { setSaving(false) }
}
```

- [ ] **Step 4: Reset guard report on file load**

Find `loadFile` (or similar file-load handlers) and add `setGuardReport(null)` alongside existing resets like `setCode` / `setFilename`.

Run: `grep -n "loadFile\|setFilename\|setTestOutput.*''" web/src/components/CodeEditor.tsx | head -10`

Add to each file-switching path:

```tsx
setGuardReport(null)
```

- [ ] **Step 5: Run typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors (particularly no `any` type introduced).

- [ ] **Step 6: Commit**

```bash
git add web/src/components/CodeEditor.tsx
git commit -m "feat(web): CodeEditor guard_result state + save handler wiring

New GuardReport type, setGuardReport state, save() captures guard_result
on success and failure branches, file-load paths reset guardReport.
No UI rendering yet — follow-up task wires the status bar + test output panel.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 18: Frontend status bar extension

**Files:**
- Modify: `web/src/components/CodeEditor.tsx` — extend status bar

- [ ] **Step 1: Locate the status bar block** (currently lines ~570-576)

```tsx
{(status || errors.length > 0) && (
  <div className="px-3 py-1 text-xs border-b" style={{ borderColor: 'var(--border)', backgroundColor: errors.length ? '#7f1d1d20' : '#14532d20' }}>
    {status && <div style={{ color: errors.length ? '#ef4444' : '#22c55e' }}>{status}</div>}
    {errors.map((e, i) => <div key={i} style={{ color: '#ef4444' }}>{e}</div>)}
  </div>
)}
```

- [ ] **Step 2: Replace with guard-aware version**

```tsx
{(status || errors.length > 0 || guardReport) && (
  <div
    className="px-3 py-1 text-xs border-b"
    style={{
      borderColor: 'var(--border)',
      backgroundColor:
        (errors.length || guardReport?.blocked)
          ? '#7f1d1d20'
          : (guardReport && guardReport.n_warnings > 0 ? '#92400e20' : '#14532d20'),
    }}
  >
    {status && <div style={{ color: errors.length ? '#ef4444' : '#22c55e' }}>{status}</div>}
    {errors.map((e, i) => <div key={i} style={{ color: '#ef4444' }}>{e}</div>)}
    {guardReport && (
      <div className="flex items-center gap-2 mt-1 flex-wrap">
        <span style={{ color: 'var(--text-secondary)' }}>代码守卫:</span>
        {guardReport.guards.map((g, i) => (
          <span
            key={i}
            title={g.message || '通过'}
            style={{
              color:
                g.severity === 'block' ? '#ef4444' :
                g.severity === 'warn' ? '#f59e0b' : '#22c55e',
              fontWeight: 600,
            }}
          >
            {g.severity === 'block' ? '✗' : g.severity === 'warn' ? '⚠' : '✓'} {g.name}
          </span>
        ))}
        <span style={{ color: 'var(--text-secondary)', marginLeft: 8 }}>
          ({guardReport.total_runtime_ms.toFixed(0)} ms)
        </span>
      </div>
    )}
  </div>
)}
```

- [ ] **Step 3: Run typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/CodeEditor.tsx
git commit -m "feat(web): CodeEditor status bar shows guard verdict badges

Each guard appears as ✓/⚠/✗ + name (color-coded green/amber/red).
Background color reflects worst severity (block → red, warn → amber,
pass → green). Message shown on hover via title attribute.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 19: Frontend test output panel extension

**Files:**
- Modify: `web/src/components/CodeEditor.tsx` — extend test output panel

- [ ] **Step 1: Locate the test output panel block** (currently lines ~640-649)

```tsx
{testOutput && (
  <div className="border-t overflow-auto" style={{ borderColor: 'var(--border)', maxHeight: '200px', backgroundColor: 'var(--bg-primary)' }}>
    <div className="flex justify-between items-center px-3 py-1">
      <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>合约测试输出</span>
      <button onClick={() => setTestOutput('')} className="text-xs px-1.5 rounded hover:opacity-80"
        style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>✕</button>
    </div>
    <pre className="px-3 pb-2 text-xs whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>{testOutput}</pre>
  </div>
)}
```

- [ ] **Step 2: Replace with guard-aware version**

```tsx
{(testOutput || (guardReport && (guardReport.n_blocks > 0 || guardReport.n_warnings > 0))) && (
  <div
    className="border-t overflow-auto"
    style={{ borderColor: 'var(--border)', maxHeight: '240px', backgroundColor: 'var(--bg-primary)' }}
  >
    <div className="flex justify-between items-center px-3 py-1">
      <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
        合约测试 + 代码守卫输出
      </span>
      <button
        onClick={() => { setTestOutput(''); setGuardReport(null) }}
        className="text-xs px-1.5 rounded hover:opacity-80"
        style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
      >✕</button>
    </div>
    {testOutput && (
      <pre
        className="px-3 pb-2 text-xs whitespace-pre-wrap"
        style={{ color: 'var(--text-primary)' }}
      >{testOutput}</pre>
    )}
    {guardReport && guardReport.guards
      .filter((g) => g.severity !== 'pass')
      .map((g, i) => (
        <div
          key={i}
          className="px-3 pb-2 text-xs"
          style={{ color: g.severity === 'block' ? '#ef4444' : '#f59e0b' }}
        >
          <div style={{ fontWeight: 600 }}>
            [{g.severity.toUpperCase()}] {g.name} ({g.runtime_ms.toFixed(0)} ms)
          </div>
          <div style={{ whiteSpace: 'pre-wrap' }}>{g.message}</div>
        </div>
      ))}
  </div>
)}
```

- [ ] **Step 3: Run typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Run frontend smoke test (optional, skip if not possible)**

```bash
cd web && npm run dev &
sleep 5
curl -sf http://localhost:3000/ > /dev/null && echo "frontend served" || echo "frontend failed"
kill %1 2>/dev/null
```

Expected: "frontend served"

- [ ] **Step 5: Commit**

```bash
git add web/src/components/CodeEditor.tsx
git commit -m "feat(web): CodeEditor test output panel shows guard details

Non-pass guards render below test output with severity header, name,
runtime, and full message. ✕ button clears both testOutput and guardReport.
Panel visible when either testOutput OR guardReport has non-pass entries.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 20: Update `CLAUDE.md` V2.19.0 entry

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Locate the version section**

Run: `grep -n "## Current Version Progress\|V2.18" CLAUDE.md | head -5`

- [ ] **Step 2: Add V2.19.0 entry after the V2.18.1 entry**

```markdown
- **V2.19.0**: ez.testing.guards — 5-guard save-time verification framework
  - **New module `ez/testing/guards/`**: Guard ABC, GuardContext, GuardResult, GuardSuite, SuiteResult, load_user_class, build_mock_panel, build_shuffled_panel (deterministic 200-day × 5-symbol GBM fixture, cached via lru_cache)
  - **5 guards**: LookaheadGuard (Tier 1, shuffle-future test with cutoff_idx=150 and 1e-9 tolerance), NaNInfGuard (Tier 1, scans factor series + cross-factor dict + portfolio weight dict beyond warmup_period), WeightSumGuard (Tier 1, checks sum(w) at 5 dates in [-0.001, 1.001]), NonNegativeWeightsGuard (Tier 2 warn, individual w ≥ -1e-9), DeterminismGuard (Tier 2 warn, canonical string compare of two runs)
  - **Sandbox integration**: `_run_guards` helper + `_sandbox_registries_for_kind` helper + 3 hook points (save_and_validate_strategy / factor branch / portfolio-cross_factor-ml_alpha branch). Guard block → registry cleanup + file rollback + backup re-register. Success path adds `guard_result` payload to return dict
  - **Frontend**: zero new components — extends existing CodeEditor.tsx status bar (guard verdict badges ✓/⚠/✗) + test output panel (per-guard detail for non-pass), reset on file load
  - **Golden bug regression tests**: v1 Dynamic EF (reads close[t+1]) + MLAlpha calendar purge (timedelta(days=5) spans weekends) — both caught by LookaheadGuard
  - **Performance budget**: default suite < 500 ms on mock data, individual guards < 150 ms
  - **Zero breaking changes**: GuardSuite catches guard exceptions as block-severity (guard bug ≠ sandbox crash), `_sandbox_registries_for_kind` is agent-layer duplicate of `_get_all_registries_for_kind` with parity test (no layer violation), return dict adds `guard_result` key that old clients ignore
  - 2265 → 2322 tests (+57: base/mock/suite 17 + 5 guards 29 + sandbox integration 14 + golden bugs 2 + registry parity 6 + run_guards 1 - overlap)
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(v2.19.0): CLAUDE.md entry for guard framework

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 21: Full test run + benchmark + final commit

**Files:** none modified

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ 2>&1 | tail -20`
Expected: `2322 passed` (or `2265 + 57 = 2322`, with 10 skipped for ml optional dependencies).

**If fewer tests pass than expected:**
- `pytest tests/test_guards/ -v` to isolate guard failures
- `pytest tests/test_agent/ -v` to check sandbox tests still work
- Fix issues, re-commit per task, re-run

- [ ] **Step 2: Run benchmark**

Run: `python scripts/benchmark.py 2>&1 | tail -20`
Expected: runtime not regressed > 2% vs baseline. If regressed, investigate (likely in hot mock data path — check `lru_cache` is hitting).

- [ ] **Step 3: Smoke test the web UI**

```bash
./scripts/start.sh &
sleep 8
curl -sf http://localhost:8000/api/health && echo "backend ok"
curl -sf http://localhost:3000/ > /dev/null && echo "frontend ok"
./scripts/stop.sh
```

Expected: both "ok" strings printed.

- [ ] **Step 4: Run a manual save-flow check via curl**

```bash
./scripts/start.sh &
sleep 8

# Save a clean factor and inspect the guard_result key
curl -sX POST http://localhost:8000/api/code/save \
  -H 'Content-Type: application/json' \
  -d '{"filename":"_smoke_clean.py","code":"from ez.factor.base import Factor\nimport pandas as pd\nclass SmokeCleanFactor(Factor):\n    name = \"smoke_clean_factor\"\n    warmup_period = 5\n    def compute(self, df):\n        return df[\"close\"].rolling(5).mean()\n","kind":"factor"}' \
  | python -m json.tool

# Save a NaN-bug factor and confirm guard_result.blocked=true
curl -sX POST http://localhost:8000/api/code/save \
  -H 'Content-Type: application/json' \
  -d '{"filename":"_smoke_buggy.py","code":"from ez.factor.base import Factor\nimport numpy as np, pandas as pd\nclass SmokeBuggyFactor(Factor):\n    name = \"smoke_buggy_factor\"\n    warmup_period = 0\n    def compute(self, df):\n        return np.log(df[\"close\"] - df[\"close\"])\n","kind":"factor"}' \
  | python -m json.tool

# Cleanup
rm -f factors/_smoke_clean.py factors/_smoke_buggy.py
./scripts/stop.sh
```

Expected: clean save returns `guard_result.blocked: false`, buggy save returns `success: false, guard_result.blocked: true, guard_result.guards[*].name == "NaNInfGuard"` with `severity: "block"`.

- [ ] **Step 5: Final commit + tag**

```bash
git log --oneline | head -25
git tag v2.19.0
echo "V2.19.0 guard framework complete — ready for review."
```

---

## Notes for the Implementing Engineer

**Pre-flight checklist** (before starting Task 1):
- Ensure baseline `pytest tests/` passes (2265 tests at time of writing — confirm via `pytest tests/ --collect-only 2>&1 | tail -5` or matching CLAUDE.md).
- Read `docs/superpowers/specs/2026-04-11-guard-framework-design.md` for spec-level context (risks, non-goals, rationale).
- Read the 3 sandbox hook sites before starting Tasks 12-14:
  - `ez/agent/sandbox.py:383-456` (strategy)
  - `ez/agent/sandbox.py:624-751` (factor save branch)
  - `ez/agent/sandbox.py:755-801` (portfolio / cross_factor / ml_alpha branch)

**Debugging tips:**
- If `load_user_class` fails, inspect `err` — most issues are ImportError from relative imports in user code or missing subclass name.
- If `LookaheadGuard` false-positives a clean factor, check that the factor only accesses `df.iloc[:-N]` or uses pandas rolling/expanding — anything that explicitly indexes future rows will trip it.
- If a guard throws internally, the GuardSuite wraps the exception as `BLOCK` with "guard bug, not user bug" — that's a guard bug, fix it.
- If sandbox integration tests fail due to dir monkeypatching, check `_VALID_KINDS_DIR_MAP` or equivalent — you may need a different patch strategy.

**Performance tuning:**
- `build_mock_panel()` is `@lru_cache(maxsize=1)` — if tests appear to rebuild on each call, cache invalidation is happening. Check for accidental mutation (tests MUST NOT mutate the returned panel).
- Individual guards should be < 150ms. If any exceed, profile with `cProfile` — likely culprit is `df.index.get_indexer` in a loop or unnecessary DataFrame copy.

**Frontend tips:**
- `web/src/components/CodeEditor.tsx` is the only file touched. No new components.
- The `GuardReport` type lives at the top of the component — it is not exported. If you need it elsewhere, move to `web/src/types/index.ts` later.
- The status bar must not grow so tall it pushes the editor off-screen. Use `flex-wrap` for the guard badges so long names wrap gracefully.

---

## Self-Review Checklist

**Spec coverage:**
- [x] Section 3.1-3.2 (5 guards) → Tasks 4-8
- [x] Section 4.1-4.8 (ez.testing.guards module) → Tasks 1-9
- [x] Section 4.9 (sandbox integration, 3 hooks) → Tasks 10-14
- [x] Section 4.10-4.11 (API + frontend) → Tasks 17-19 (API no-op)
- [x] Section 5.1-5.4 (testing strategy) → Tasks 1-9, 15, 16
- [x] Section 5.5 (runtime budget test) → Task 9 Step 2
- [x] Section 6 (acceptance criteria) → Task 21
- [x] Golden bugs → Task 16

**Type consistency:**
- GuardContext.kind type: `GuardKind` Literal — used consistently in Tasks 1, 3, 4-8, 11
- GuardResult.severity: `GuardSeverity` Enum — consistent
- Guard.applies_to: `tuple[GuardKind, ...]` — consistent
- `_run_guards` return type: `SuiteResult` — used in Tasks 11-14
- `_sandbox_registries_for_kind` return: `list[dict]` — used in Tasks 10, 14
- Frontend `GuardReport.severity`: `'pass' | 'warn' | 'block'` — matches backend `GuardSeverity.value`

**No placeholders found.** Every step has concrete code or a concrete command.
