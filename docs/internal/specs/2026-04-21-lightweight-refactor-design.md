# Lightweight Refactor Design

**Date**: 2026-04-21
**Goal**: 保留全部功能，通过清理 dead code、消除重复、拆解大文件实现轻量化和高效化
**Scope**: 源码 + 测试同步瘦身
**Estimated reduction**: ~15-18% (8-10K source lines + corresponding tests)

---

## Phase 1: Dead Code Removal

### 1.1 Research Steps (confirmed dead — no production import path)

Delete files:
- `ez/research/steps/run_strategies.py`
- `ez/research/steps/run_portfolio.py`
- `ez/research/steps/data_load.py`
- `ez/research/steps/report.py`
- `ez/research/optimizers/epsilon_constraint.py`

Update `ez/research/steps/__init__.py` and `ez/research/optimizers/__init__.py` to remove re-exports.

**Keep**: `steps/paired_bootstrap.py` (used by `ez/api/routes/validation.py`), `steps/walk_forward.py`, `steps/nested_oos.py`, `optimizers/simplex.py`, `optimizers/objectives.py`.

### 1.2 Corresponding Test Removal

Delete tests that ONLY test the removed files. Verify each test file's imports before deleting — if a test file also covers retained code, only remove the dead-code test functions, not the whole file.

### 1.3 Research __init__ Cleanup

After removing dead steps, slim down `ez/research/steps/__init__.py` and `ez/research/optimizers/__init__.py` to only export what remains.

---

## Phase 2: Duplicate Function Consolidation

### 2.1 ez/live/_utils.py — shared QMT utilities

Extract to new file `ez/live/_utils.py`:

| Function | Current locations | Copies |
|----------|------------------|--------|
| `_utc_now()` | qmt_broker, qmt_session_owner, qmt_host, reconcile | 4 |
| `_coerce_timestamp()` | qmt_broker, qmt_session_owner | 2 |
| `_get_field()` | qmt_broker, qmt_session_owner | 2 |
| `_qmt_request_failed_immediately()` | qmt_broker, qmt_session_owner | 2 |
| `_positive_or_none()` | allocation, risk | 2 |
| `_fraction_or_none()` | allocation, risk | 2 |

Original definitions replaced with `from ez.live._utils import ...`.

### 2.2 ez/agent/sandbox.py — parameterized merge

**Reload functions** (3 → 1):
- `_reload_user_strategy()` (line 1042-1099)
- `_reload_portfolio_code()` (line 1623-1674)
- `_reload_factor_code()` (line 1677-1715)

Merge into `_reload_code(filename, kind, target_dir, registry_class, dual_registry=False)`.

Differences handled by parameters:
- `registry_class`: Strategy / PortfolioStrategy / CrossSectionalFactor / Factor
- `dual_registry`: whether to clear both `_registry` and `_registry_by_key`
- `kind`: for log messages

**Validate functions** (2 → 1):
- `_validate_strategy_inprocess()`
- `_validate_portfolio_inprocess()`

Merge into `_validate_inprocess(kind, base_class, ...)`.

**Save-and-validate flow**: The three `save_and_validate_*` paths share the pattern: backup → write → contract test → guard → hot-reload → rollback-on-error. Extract the shared skeleton into `_save_validate_flow(kind, code, ...)` with kind-specific hooks as parameters.

**Estimated saving**: ~150-200 lines in sandbox.py.

---

## Phase 3: Split God Classes / Large Files

### 3.1 scheduler.py (4315 lines → ~4 files)

Split `Scheduler` class by extracting inner method clusters into collaborator classes:

| New file | Extracted from | Responsibility | Est. lines |
|----------|---------------|----------------|------------|
| `ez/live/_qmt_projection.py` | `_build_qmt_runtime_projection` + helpers | QMT runtime projection building | ~400 |
| `ez/live/_broker_pump.py` | `_pump_broker_state_locked` + helpers | Broker state pump cycle | ~350 |
| `ez/live/_snapshot_context.py` | `_collect_real_qmt_snapshot_context` + helpers | Snapshot data collection | ~300 |

`Scheduler` retains orchestration logic, delegates to collaborator instances. Public interface unchanged.

### 3.2 qmt_session_owner.py (3253 lines → 2 files)

| New file | Extracted class | Lines |
|----------|----------------|-------|
| `ez/live/qmt_callback_bridge.py` | `_XtQuantTraderCallbackBridge` + `_CallbackConsumerThread` | ~800 |

`qmt_session_owner.py` keeps `QMTSessionManager` + `XtQuantShadowClient`. Public interface unchanged.

### 3.3 deployment_store.py (1791 lines → 2 files)

| New file | Extracted from | Lines |
|----------|---------------|-------|
| `ez/live/broker_order_links.py` | `_upsert_broker_order_links_locked`, `_upsert_broker_cancel_ack_link_locked`, `list_broker_order_links_*` | ~350 |

`DeploymentStore` delegates broker-order-link operations to `BrokerOrderLinkRepository`, which receives the DuckDB connection + lock from the store.

### 3.4 API route files — helper extraction

**ez/api/routes/portfolio.py** (1792 lines):
- Extract `_fetch_data`, `_ensure_benchmark`, `_ensure_fundamental_data` → `ez/api/_portfolio_helpers.py` (~200 lines)
- Extract `_compute_alpha_weights`, `_build_optimizer_risk_factories` → same file

**ez/api/routes/live.py** (1356 lines):
- Extract `_build_qmt_submit_gate`, `_build_qmt_release_gate`, `_build_qmt_readiness_summary` → `ez/api/_live_helpers.py` (~200 lines)
- Extract `_record_to_dict`, `_health_to_dict`, serialization helpers → same file

### 3.5 sandbox.py (1782 lines)

After Phase 2 deduplication, sandbox.py should drop to ~1550 lines. Further split:
- Extract AST security checking (`_FORBIDDEN_MODULES`, `_FORBIDDEN_FULL_MODULES`, AST visitor) → `ez/agent/_ast_guard.py` (~150 lines)

---

## Phase 4: Test Cleanup

### 4.1 Remove orphaned tests

Tests that only exercise deleted dead code (from Phase 1) are removed.

### 4.2 Consolidate test fixtures

Scan `tests/test_live/` for duplicate fixture patterns (mock scheduler, mock deployment store, etc.) and extract shared fixtures into `tests/test_live/conftest.py` if not already there.

### 4.3 Verify no regressions

Run full test suite after each phase. Use `scripts/run_pytest_safe.sh`.

---

## Execution Constraints

1. **No public API changes**: All refactoring is internal. No import path changes visible to users or API consumers.
2. **Phased commits**: One commit per sub-phase (1.1, 1.2, 2.1, 2.2, etc.) for easy bisect/rollback.
3. **Test after each phase**: Full test suite must pass before moving to next phase.
4. **Internal imports only**: New files (like `_utils.py`, `_qmt_projection.py`) use underscore prefix to signal internal-only.
5. **CLAUDE.md updates**: Update module CLAUDE.md files to reflect new file structure after Phase 3.

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Circular imports from extraction | New files import from parents, not vice versa; collaborator pattern avoids cycles |
| Breaking QMT integration | QMT code is only split, not modified; all existing tests must pass |
| Missing a production reference to "dead" code | Verified via grep; paired_bootstrap.py kept precisely because of this |
| Large merge conflicts with in-flight work | Phased commits minimize conflict surface |

---

## Success Criteria

- All 3048+ backend tests pass
- All 96 frontend tests pass
- No file > 3000 lines (currently scheduler.py is 4315, qmt_session_owner.py is 3253)
- No function defined in more than one file within ez/live/
- Total source code reduction of 8-10K lines
