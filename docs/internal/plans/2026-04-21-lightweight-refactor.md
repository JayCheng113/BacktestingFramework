# Lightweight Refactor Implementation Plan

> **Status:** 历史实施计划，后续 QMT 代码已迁移到 `ez/live/qmt/` 子包；文件内旧路径保留用于追溯当时的拆分方案，不作为当前实现指南。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce codebase noise and cognitive load by removing dead code, eliminating duplicate functions, and splitting god classes — all without changing any public API.

**Architecture:** Four-phase approach: (1) dead code removal, (2) duplicate function consolidation into shared modules, (3) god class decomposition via collaborator extraction, (4) test cleanup. Each phase produces a passing test suite before the next begins.

**Tech Stack:** Python 3.12+, pytest, DuckDB, FastAPI, React 19

**Test command:** `./scripts/run_pytest_safe.sh tests/ -x -q` (macOS readline shim required)

---

## File Structure

### New files to create:
- `ez/live/_utils.py` — shared utility functions extracted from QMT modules
- `ez/live/_qmt_projection.py` — QMT runtime projection builder (from scheduler.py)
- `ez/live/_broker_pump.py` — broker state pump logic (from scheduler.py)
- `ez/live/_snapshot_collectors.py` — snapshot context collection (from scheduler.py)
- `ez/live/qmt_callback_bridge.py` — callback bridge + consumer thread (from qmt_session_owner.py)
- `ez/live/_broker_order_links.py` — broker order link repository (from deployment_store.py)
- `ez/api/_portfolio_helpers.py` — portfolio route helper functions
- `ez/api/_live_helpers.py` — live route helper functions

### Files to delete:
- `ez/research/steps/run_strategies.py`
- `ez/research/steps/run_portfolio.py`
- `ez/research/steps/data_load.py`
- `ez/research/steps/report.py`
- `ez/research/optimizers/epsilon_constraint.py`
- `tests/test_research/test_data_load.py`
- `tests/test_research/test_report.py`
- `tests/test_research/test_run_portfolio.py`
- `tests/test_research/test_epsilon_constraint.py`
- `tests/test_research/test_e2e.py`
- `tests/test_research/test_codex_round2_regressions.py`

### Files to modify:
- `ez/research/steps/__init__.py` — remove dead re-exports
- `ez/research/optimizers/__init__.py` — remove dead re-export
- `ez/live/qmt_broker.py` — replace duplicate defs with imports
- `ez/live/qmt_session_owner.py` — extract callback bridge, replace duplicate defs
- `ez/live/qmt_host.py` — replace duplicate def with import
- `ez/live/reconcile.py` — replace duplicate def with import
- `ez/live/allocation.py` — replace duplicate defs with imports
- `ez/live/risk.py` — replace duplicate defs with imports
- `ez/live/scheduler.py` — extract collaborator classes
- `ez/live/deployment_store.py` — extract broker order link repository
- `ez/agent/sandbox.py` — merge duplicate reload/validate functions
- `ez/api/routes/portfolio.py` — extract helpers
- `ez/api/routes/live.py` — extract helpers
- `tests/test_research/test_run_strategies.py` — remove dead-code-dependent test
- `tests/test_research/test_paired_bootstrap.py` — remove dead import if present

---

## Task 1: Remove Dead Research Steps

**Files:**
- Delete: `ez/research/steps/run_strategies.py`, `ez/research/steps/run_portfolio.py`, `ez/research/steps/data_load.py`, `ez/research/steps/report.py`
- Modify: `ez/research/steps/__init__.py`

- [ ] **Step 1: Delete the four dead step files**

```bash
rm ez/research/steps/run_strategies.py
rm ez/research/steps/run_portfolio.py
rm ez/research/steps/data_load.py
rm ez/research/steps/report.py
```

- [ ] **Step 2: Update steps/__init__.py**

Replace the full content of `ez/research/steps/__init__.py` with:

```python
"""Concrete research steps.

- NestedOOSStep — IS optimize -> OOS validate -> baseline compare
- WalkForwardStep — rolling N-fold walk-forward weight optimization
- PairedBlockBootstrapStep — paired block bootstrap CI for strategy comparison
"""
from .nested_oos import NestedOOSStep
from .walk_forward import WalkForwardStep
from .paired_bootstrap import PairedBlockBootstrapStep

__all__ = [
    "NestedOOSStep",
    "WalkForwardStep",
    "PairedBlockBootstrapStep",
]
```

- [ ] **Step 3: Verify no import errors**

```bash
python -c "from ez.research.steps import NestedOOSStep, WalkForwardStep, PairedBlockBootstrapStep; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add -A ez/research/steps/
git commit -m "refactor: remove dead research steps (run_strategies, run_portfolio, data_load, report)"
```

---

## Task 2: Remove Dead Epsilon Constraint Optimizer

**Files:**
- Delete: `ez/research/optimizers/epsilon_constraint.py`
- Modify: `ez/research/optimizers/__init__.py`

- [ ] **Step 1: Delete the file**

```bash
rm ez/research/optimizers/epsilon_constraint.py
```

- [ ] **Step 2: Update optimizers/__init__.py**

Replace content with:

```python
"""Portfolio weight optimizers for the research pipeline.

Public API:
  - ``OptimalWeights``: dataclass holding one optimization result
  - ``Objective``: abstract base for objective functions
  - ``MaxSharpe``, ``MaxCalmar``, ``MaxSortino``, ``MinCVaR``: built-in objectives
  - ``Optimizer``: abstract base for portfolio optimizers
  - ``SimplexMultiObjectiveOptimizer``: differential_evolution wrapper
"""
from .base import OptimalWeights, Objective, Optimizer
from .objectives import MaxSharpe, MaxCalmar, MaxSortino, MinCVaR
from .simplex import SimplexMultiObjectiveOptimizer

__all__ = [
    "OptimalWeights",
    "Objective",
    "Optimizer",
    "MaxSharpe",
    "MaxCalmar",
    "MaxSortino",
    "MinCVaR",
    "SimplexMultiObjectiveOptimizer",
]
```

- [ ] **Step 3: Verify**

```bash
python -c "from ez.research.optimizers import SimplexMultiObjectiveOptimizer, MaxSharpe; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add -A ez/research/optimizers/
git commit -m "refactor: remove dead EpsilonConstraint optimizer"
```

---

## Task 3: Remove Dead Research Tests

**Files:**
- Delete: `tests/test_research/test_data_load.py`, `tests/test_research/test_report.py`, `tests/test_research/test_run_portfolio.py`, `tests/test_research/test_epsilon_constraint.py`, `tests/test_research/test_e2e.py`, `tests/test_research/test_codex_round2_regressions.py`
- Modify: `tests/test_research/test_run_strategies.py` (remove DataLoadStep-dependent test)
- Modify: `tests/test_research/test_paired_bootstrap.py` (remove RunPortfolioStep import if present)

- [ ] **Step 1: Delete pure dead-code test files**

```bash
rm tests/test_research/test_data_load.py
rm tests/test_research/test_report.py
rm tests/test_research/test_run_portfolio.py
rm tests/test_research/test_epsilon_constraint.py
rm tests/test_research/test_e2e.py
rm tests/test_research/test_codex_round2_regressions.py
```

- [ ] **Step 2: Edit test_run_strategies.py — remove DataLoadStep-dependent test**

Find and remove the `test_pipeline_chains_data_load_and_run_strategies` function (around line 223-243) and any `DataLoadStep` import. Read the file first to confirm exact lines.

- [ ] **Step 3: Edit test_paired_bootstrap.py — check for RunPortfolioStep import**

Read the file and remove any `RunPortfolioStep` import or test that depends on it. If the import is conditional and unused, remove it.

- [ ] **Step 4: Run surviving research tests**

```bash
./scripts/run_pytest_safe.sh tests/test_research/ -x -q
```

Expected: all remaining tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A tests/test_research/
git commit -m "refactor: remove orphaned research tests for deleted steps/optimizers"
```

---

## Task 4: Extract ez/live/_utils.py — Shared Utilities

**Files:**
- Create: `ez/live/_utils.py`
- Modify: `ez/live/qmt_broker.py`, `ez/live/qmt_session_owner.py`, `ez/live/qmt_host.py`, `ez/live/reconcile.py`, `ez/live/allocation.py`, `ez/live/risk.py`

- [ ] **Step 1: Create ez/live/_utils.py**

```python
"""Shared private utilities for the live trading module.

Extracted to eliminate cross-file duplication of identical helper functions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def coerce_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return utc_now()


def get_field(raw: Any, *names: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        for name in names:
            if name in raw:
                return raw[name]
        return default
    for name in names:
        if hasattr(raw, name):
            return getattr(raw, name)
    return default


def qmt_request_failed_immediately(result: Any) -> bool:
    if isinstance(result, bool):
        return not result
    if isinstance(result, (int, float)):
        return result < 0
    return False


def positive_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def fraction_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return min(value, 1.0)
```

Note: functions are named without the leading underscore since they are module-private via the `_utils` filename prefix.

- [ ] **Step 2: Update qmt_broker.py — replace 4 functions**

In `ez/live/qmt_broker.py`:
- Remove the definitions of `_utc_now` (line 48-49), `_coerce_timestamp` (line 55-68), `_get_field` (line 71-80), `_qmt_request_failed_immediately` (line 156-161)
- Add at the top imports section:

```python
from ez.live._utils import (
    utc_now as _utc_now,
    coerce_timestamp as _coerce_timestamp,
    get_field as _get_field,
    qmt_request_failed_immediately as _qmt_request_failed_immediately,
)
```

The `as _name` aliases preserve all call sites unchanged.

- [ ] **Step 3: Update qmt_session_owner.py — replace 4 functions**

Same pattern: remove definitions of `_utc_now` (line 32-33), `_coerce_timestamp` (line 104-117), `_get_field` (line 135-144), `_qmt_request_failed_immediately` (line 1192-1197). Add import aliases.

```python
from ez.live._utils import (
    utc_now as _utc_now,
    coerce_timestamp as _coerce_timestamp,
    get_field as _get_field,
    qmt_request_failed_immediately as _qmt_request_failed_immediately,
)
```

- [ ] **Step 4: Update qmt_host.py — replace _utc_now**

Remove `_utc_now` definition (line 47-48). Add:

```python
from ez.live._utils import utc_now as _utc_now
```

- [ ] **Step 5: Update reconcile.py — replace _utc_now**

Remove `_utc_now` definition (line 347-348). Add:

```python
from ez.live._utils import utc_now as _utc_now
```

- [ ] **Step 6: Update allocation.py — replace 2 functions**

Remove `_positive_or_none` (line 440-447) and `_fraction_or_none` (line 418-427). Add:

```python
from ez.live._utils import (
    positive_or_none as _positive_or_none,
    fraction_or_none as _fraction_or_none,
)
```

- [ ] **Step 7: Update risk.py — replace 2 functions**

Remove `_positive_or_none` (line 400-407) and `_fraction_or_none` (line 410-419). Add:

```python
from ez.live._utils import (
    positive_or_none as _positive_or_none,
    fraction_or_none as _fraction_or_none,
)
```

- [ ] **Step 8: Run live tests**

```bash
./scripts/run_pytest_safe.sh tests/test_live/ -x -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add ez/live/_utils.py ez/live/qmt_broker.py ez/live/qmt_session_owner.py ez/live/qmt_host.py ez/live/reconcile.py ez/live/allocation.py ez/live/risk.py
git commit -m "refactor: extract shared live utilities to _utils.py, eliminate 14 duplicate function defs"
```

---

## Task 5: Merge Duplicate sandbox.py Functions

**Files:**
- Modify: `ez/agent/sandbox.py`

- [ ] **Step 1: Read sandbox.py reload/validate functions**

Read lines 1042-1146 (reload + validate functions) and lines 1623-1716 (portfolio/factor reload). Confirm exact boundaries.

- [ ] **Step 2: Create unified _reload_code function**

Replace the three `_reload_*` functions with a single parameterized function. Add this function and remove the three originals:

```python
def _reload_code(
    filename: str,
    kind: str,
    target_dir: Path,
    base_classes: list[type],
    dual_registry: bool = True,
) -> dict:
    """Generic hot-reload for strategy/portfolio/factor code.

    Parameters
    ----------
    filename : str
        The .py file name (not full path).
    kind : str
        Human-readable label for logging ("strategy", "portfolio_strategy", "factor", etc.).
    target_dir : Path
        Directory containing the file.
    base_classes : list[type]
        The base class(es) whose registry to clear for this module.
    dual_registry : bool
        If True, clear both ``_registry`` and ``_registry_by_key`` dicts.
    """
    stem = Path(filename).stem
    module_name = stem
    file_path = target_dir / filename

    if not file_path.exists():
        return {"reloaded": False, "error": f"{filename} not found in {target_dir}"}

    with _get_reload_lock():
        # --- clear old registry entries for this module ---
        for base_cls in base_classes:
            if hasattr(base_cls, "_registry"):
                base_cls._registry = {
                    k: v for k, v in base_cls._registry.items()
                    if getattr(v, "__module__", None) != module_name
                }
            if dual_registry and hasattr(base_cls, "_registry_by_key"):
                base_cls._registry_by_key = {
                    k: v for k, v in base_cls._registry_by_key.items()
                    if getattr(v, "__module__", None) != module_name
                }

        # --- purge old module from sys.modules ---
        if module_name in sys.modules:
            del sys.modules[module_name]

        # --- delete .pyc cache ---
        pyc_dir = file_path.parent / "__pycache__"
        if pyc_dir.exists():
            for pyc in pyc_dir.glob(f"{stem}.*.pyc"):
                pyc.unlink(missing_ok=True)
        importlib.invalidate_caches()

        # --- reimport ---
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            return {"reloaded": False, "error": f"Cannot create import spec for {filename}"}
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)

    logger.info("reloaded %s code: %s", kind, filename)
    return {"reloaded": True, "module": module_name}
```

Then update each call site to use the new function. For example, where `_reload_user_strategy(filename)` was called, replace with:

```python
from ez.strategy.base import Strategy
_reload_code(filename, "strategy", _STRATEGIES_DIR, [Strategy], dual_registry=False)
```

- [ ] **Step 3: Create unified _validate_inprocess function**

Replace `_validate_strategy_inprocess` and `_validate_portfolio_inprocess` with:

```python
def _validate_inprocess(
    file_path: Path,
    kind: str,
    base_classes: list[type],
) -> dict:
    """In-process validation: import the file and check for expected subclass."""
    stem = file_path.stem
    check_name = f"_check_{stem}"
    try:
        spec = importlib.util.spec_from_file_location(check_name, str(file_path))
        if spec is None or spec.loader is None:
            return {"passed": False, "output": f"Cannot load {file_path.name}"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        found = any(
            isinstance(v, type) and issubclass(v, tuple(base_classes)) and v not in base_classes
            for v in vars(mod).values()
        )
        if not found:
            class_names = " or ".join(c.__name__ for c in base_classes)
            return {"passed": False, "output": f"No {class_names} subclass found in {file_path.name}"}
        return {"passed": True, "output": f"{kind} validation passed"}
    except Exception as exc:
        return {"passed": False, "output": str(exc)}
```

Update call sites accordingly.

- [ ] **Step 4: Run agent tests**

```bash
./scripts/run_pytest_safe.sh tests/test_agent/ -x -q
```

Expected: all tests pass.

- [ ] **Step 5: Run full sandbox-related API tests**

```bash
./scripts/run_pytest_safe.sh tests/test_api/test_code_registry.py -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add ez/agent/sandbox.py
git commit -m "refactor: merge 5 duplicate reload/validate functions in sandbox.py into 2 parameterized versions"
```

---

## Task 6: Extract QMT Callback Bridge from qmt_session_owner.py

**Files:**
- Create: `ez/live/qmt_callback_bridge.py`
- Modify: `ez/live/qmt_session_owner.py`

- [ ] **Step 1: Read qmt_session_owner.py lines 1214-1700**

Confirm the exact boundary of `_XtQuantTraderCallbackBridge` class and its helper functions (`_invoke_xtquant_cancel_api`, `_invoke_xtquant_order_api`).

- [ ] **Step 2: Create qmt_callback_bridge.py**

Move these to the new file:
- `_XtQuantTraderCallbackBridge` class (line 1214 to ~line 1700)
- `_invoke_xtquant_cancel_api` (line 1200)
- `_invoke_xtquant_order_api` (line 1207)

Add necessary imports at the top of the new file. Keep the original class name.

- [ ] **Step 3: Update qmt_session_owner.py**

Replace the moved code with an import:

```python
from ez.live.qmt_callback_bridge import (
    _XtQuantTraderCallbackBridge,
    _invoke_xtquant_cancel_api,
    _invoke_xtquant_order_api,
)
```

- [ ] **Step 4: Check all other files that import from qmt_session_owner**

```bash
grep -rn "from ez.live.qmt_session_owner import" ez/ tests/
```

Verify none of them import `_XtQuantTraderCallbackBridge` directly (they shouldn't since it's private). If any do, update the import path.

- [ ] **Step 5: Run live tests**

```bash
./scripts/run_pytest_safe.sh tests/test_live/ -x -q
```

- [ ] **Step 6: Commit**

```bash
git add ez/live/qmt_callback_bridge.py ez/live/qmt_session_owner.py
git commit -m "refactor: extract _XtQuantTraderCallbackBridge to qmt_callback_bridge.py (~500 lines)"
```

---

## Task 7: Extract Broker Order Link Repository from deployment_store.py

**Files:**
- Create: `ez/live/_broker_order_links.py`
- Modify: `ez/live/deployment_store.py`

- [ ] **Step 1: Read deployment_store.py broker order link methods**

Read lines 909-1732 to identify all broker-order-link related methods:
- `get_broker_order_link` (line 909)
- `find_broker_order_link` (line 938)
- `list_broker_order_links_by_broker_order_id` (line 982)
- `list_broker_order_links` (line 1034)
- `_upsert_broker_execution_event_link_locked` (line 1186)
- `_upsert_broker_submit_ack_link_locked` (line 1220)
- `_has_later_cancel_failed_runtime_locked` (line 1273)
- `_upsert_broker_cancel_requested_link_locked` (line 1341)
- `_upsert_broker_cancel_ack_link_locked` (line 1427)
- `_upsert_broker_cancel_failed_link_locked` (line 1567)
- `_upsert_broker_order_links_locked` (line 1636)

- [ ] **Step 2: Create _broker_order_links.py**

Extract all broker-order-link methods into a `BrokerOrderLinkRepository` class that receives `conn` and `_lock` from the parent store:

```python
"""Broker order link persistence — extracted from DeploymentStore."""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

import duckdb


class BrokerOrderLinkRepository:
    """Manages broker_order_links table operations.

    Receives a DuckDB connection and threading.RLock from the parent
    DeploymentStore — no independent lifecycle.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    # ... move all broker-order-link methods here, replacing self._conn / self._lock references
```

- [ ] **Step 3: Update deployment_store.py**

In `DeploymentStore.__init__`, create the repository:

```python
from ez.live._broker_order_links import BrokerOrderLinkRepository
# in __init__:
self._broker_links = BrokerOrderLinkRepository(self._conn, self._lock)
```

Delegate all broker-order-link calls. For public methods, add thin delegation:

```python
def get_broker_order_link(self, deployment_id: str, local_order_id: str) -> dict | None:
    return self._broker_links.get_broker_order_link(deployment_id, local_order_id)
```

For private `_upsert_*` methods called internally, replace `self._upsert_broker_order_links_locked(...)` with `self._broker_links._upsert_broker_order_links_locked(...)`.

- [ ] **Step 4: Run live tests**

```bash
./scripts/run_pytest_safe.sh tests/test_live/ -x -q
```

- [ ] **Step 5: Run API live tests**

```bash
./scripts/run_pytest_safe.sh tests/test_api/test_live_api.py -x -q
```

- [ ] **Step 6: Commit**

```bash
git add ez/live/_broker_order_links.py ez/live/deployment_store.py
git commit -m "refactor: extract BrokerOrderLinkRepository from deployment_store.py (~550 lines)"
```

---

## Task 8: Extract Scheduler Collaborators (QMT Projection)

**Files:**
- Create: `ez/live/_qmt_projection.py`
- Modify: `ez/live/scheduler.py`

- [ ] **Step 1: Identify projection methods in scheduler.py**

Read lines 1780-2020 (`_build_qmt_runtime_projection` and `_persist_qmt_runtime_projection`) plus helper methods:
- `_parse_gate_verdict` (line 1678)
- `_extract_qmt_account_id` (line 1688)
- `_extract_runtime_event_account_id` (line 1700)
- `_extract_account_event_account_id` (line 1709)
- `_get_latest_runtime_event_for_account` (line 1714)
- `_get_latest_account_event_for_account` (line 1754)
- `_build_qmt_runtime_projection` (line 1780)
- `_persist_qmt_runtime_projection` (line 2000)

- [ ] **Step 2: Create _qmt_projection.py**

Extract as standalone functions that accept the required data as arguments (store, events, etc.) rather than accessing `self`. This avoids circular dependency with Scheduler:

```python
"""QMT runtime projection builder — extracted from Scheduler.

All functions are stateless and receive their dependencies as arguments.
"""
from __future__ import annotations
# ... extracted functions with explicit parameters instead of self
```

- [ ] **Step 3: Update scheduler.py**

Import and delegate from the extracted module:

```python
from ez.live._qmt_projection import (
    build_qmt_runtime_projection,
    persist_qmt_runtime_projection,
    parse_gate_verdict,
    # ...
)
```

Replace `self._build_qmt_runtime_projection(...)` calls with `build_qmt_runtime_projection(self._store, ...)`.

- [ ] **Step 4: Run live + API tests**

```bash
./scripts/run_pytest_safe.sh tests/test_live/ tests/test_api/test_live_api.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add ez/live/_qmt_projection.py ez/live/scheduler.py
git commit -m "refactor: extract QMT projection builder from scheduler.py (~350 lines)"
```

---

## Task 9: Extract Scheduler Collaborators (Broker State Pump)

**Files:**
- Create: `ez/live/_broker_pump.py`
- Modify: `ez/live/scheduler.py`

- [ ] **Step 1: Identify pump methods**

Read `_pump_broker_state_locked` (line 2450, ~170 lines) and its direct helpers:
- `_derive_shadow_business_date` (line 2020)
- `_build_shadow_sync_events` (line 2037)
- `_append_real_qmt_owner_events` (line 2100)
- `_refresh_real_qmt_cancel_projection` (line 2223)
- `_build_shadow_execution_report_events` (line 2253)
- `_build_shadow_runtime_events` (line 2281)

- [ ] **Step 2: Create _broker_pump.py**

Extract as stateless functions. The main `pump_broker_state` function accepts the engine, store, and other dependencies as arguments.

- [ ] **Step 3: Update scheduler.py — delegate to extracted functions**

- [ ] **Step 4: Run tests**

```bash
./scripts/run_pytest_safe.sh tests/test_live/ tests/test_api/test_live_api.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add ez/live/_broker_pump.py ez/live/scheduler.py
git commit -m "refactor: extract broker state pump from scheduler.py (~350 lines)"
```

---

## Task 10: Extract Scheduler Collaborators (Snapshot Collectors)

**Files:**
- Create: `ez/live/_snapshot_collectors.py`
- Modify: `ez/live/scheduler.py`

- [ ] **Step 1: Identify snapshot collection methods**

These methods collect and serialize snapshot/reconcile data:
- `_collect_shadow_snapshot_context` (line 619)
- `_serialize_position_reconcile` (line 746)
- `_serialize_trade_reconcile` (line 778)
- `_broker_positions_from_snapshot` (line 816)
- `_broker_trades_from_snapshot` (line 834)
- `_local_trades_from_engine` (line 860)
- `_collect_shadow_position_reconcile` (line 877)
- `_collect_shadow_trade_reconcile` (line 924)
- `_collect_real_qmt_position_reconcile` (line 975)
- `_collect_real_qmt_trade_reconcile` (line 1030)
- `_collect_shadow_sync_bundle` (line 1089)
- `_collect_real_qmt_owner_sync_bundle` (line 1152)
- `_collect_real_qmt_snapshot_context` (line 1549)

- [ ] **Step 2: Create _snapshot_collectors.py**

Extract all as stateless functions.

- [ ] **Step 3: Update scheduler.py**

- [ ] **Step 4: Run tests**

```bash
./scripts/run_pytest_safe.sh tests/test_live/ tests/test_api/test_live_api.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add ez/live/_snapshot_collectors.py ez/live/scheduler.py
git commit -m "refactor: extract snapshot collectors from scheduler.py (~500 lines)"
```

---

## Task 11: Extract API Portfolio Helpers

**Files:**
- Create: `ez/api/_portfolio_helpers.py`
- Modify: `ez/api/routes/portfolio.py`

- [ ] **Step 1: Read portfolio.py helper functions**

Read the file and identify all private helper functions (prefixed with `_`) that are not route handlers. Key targets:
- Data fetching: `_fetch_data`, `_ensure_benchmark`, `_ensure_fundamental_data`
- Weight computation: `_compute_alpha_weights`, `_build_optimizer_risk_factories`
- Factor resolution helpers

- [ ] **Step 2: Create _portfolio_helpers.py**

Move helper functions to the new file. Keep the route handler functions in `portfolio.py`.

- [ ] **Step 3: Update portfolio.py — add imports from helpers**

- [ ] **Step 4: Run portfolio API tests**

```bash
./scripts/run_pytest_safe.sh tests/test_api/test_portfolio_api.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add ez/api/_portfolio_helpers.py ez/api/routes/portfolio.py
git commit -m "refactor: extract portfolio route helpers (~300 lines)"
```

---

## Task 12: Extract API Live Helpers

**Files:**
- Create: `ez/api/_live_helpers.py`
- Modify: `ez/api/routes/live.py`

- [ ] **Step 1: Read live.py helper functions**

Key targets:
- QMT gate builders: `_build_qmt_submit_gate`, `_build_qmt_release_gate`, `_build_qmt_readiness_summary`
- Serialization: `_record_to_dict`, `_health_to_dict`, other `_*_to_dict` functions

- [ ] **Step 2: Create _live_helpers.py**

Move helper functions.

- [ ] **Step 3: Update live.py — add imports**

- [ ] **Step 4: Run live API tests**

```bash
./scripts/run_pytest_safe.sh tests/test_api/test_live_api.py -x -q
```

- [ ] **Step 5: Commit**

```bash
git add ez/api/_live_helpers.py ez/api/routes/live.py
git commit -m "refactor: extract live route helpers (~250 lines)"
```

---

## Task 13: Test Fixture Consolidation

**Files:**
- Modify: `tests/test_live/conftest.py` (create if not exists)
- Modify: various `tests/test_live/test_*.py` files

- [ ] **Step 1: Scan for duplicate fixtures in test_live/**

```bash
grep -rn "^def \|^@pytest.fixture" tests/test_live/ | grep -i "mock\|fake\|dummy\|fixture" | sort
```

Identify fixtures defined in multiple test files that could be shared.

- [ ] **Step 2: Extract shared fixtures to conftest.py**

Move duplicated fixtures (mock scheduler, mock store, mock engines) to `tests/test_live/conftest.py`. Update test files to remove local definitions and rely on conftest.

- [ ] **Step 3: Run all live tests**

```bash
./scripts/run_pytest_safe.sh tests/test_live/ -x -q
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_live/
git commit -m "refactor: consolidate duplicate test fixtures in test_live/"
```

---

## Task 14: Full Regression + Documentation Update

**Files:**
- Modify: `ez/live/CLAUDE.md`
- Modify: `CLAUDE.md` (if file structure references need update)

- [ ] **Step 1: Run full backend test suite**

```bash
./scripts/run_pytest_safe.sh tests/ -x -q
```

Expected: 3048+ tests pass (same as baseline).

- [ ] **Step 2: Run frontend tests**

```bash
cd web && npm test -- --watchAll=false && cd ..
```

Expected: 96 tests pass.

- [ ] **Step 3: Update ez/live/CLAUDE.md**

Add notes about new file structure:
- `_utils.py` — shared utilities
- `qmt_callback_bridge.py` — callback bridge (from qmt_session_owner.py)
- `_broker_order_links.py` — broker order link repository (from deployment_store.py)
- `_qmt_projection.py` — projection builder (from scheduler.py)
- `_broker_pump.py` — broker state pump (from scheduler.py)
- `_snapshot_collectors.py` — snapshot collection (from scheduler.py)

- [ ] **Step 4: Verify line count reduction**

```bash
find ez -name "*.py" -exec cat {} + | wc -l
find tests -name "*.py" -exec cat {} + | wc -l
```

Compare against baseline (ez: 47,967 lines, tests: 59,402 lines).

- [ ] **Step 5: Final commit**

```bash
git add ez/live/CLAUDE.md CLAUDE.md
git commit -m "docs: update CLAUDE.md for post-refactor file structure"
```
