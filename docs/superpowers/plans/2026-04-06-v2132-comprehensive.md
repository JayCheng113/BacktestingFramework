# V2.13.2 Comprehensive Release Plan (v2 — codex reviewed)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close ALL deferred items from V2.13/V2.13.1 reviews + V2.12.2 legacy. Ship frontend Phase 6. Zero backlog.

**Scope changes from v1 (codex feedback):**
- G1.2 stamp_tax: use `model_fields_set` not value comparison
- G2 split: CodeEditor/types/API (no G1 dep) ∥ registry-driven UI (needs G1.1)
- G2.6 ensemble UI: split out as **optional**, not in main scope/estimate
- G3: removed 2 false "not done" items (tests already exist), kept 5 genuine items
- G4.2 power-set: hard cap at 64 combos, not just warning
- G5: moved to **V2.14 optional**, not in V2.13.2 main scope (env-sensitive)
- TS client: typed signatures, not `any`

---

## Group 1: Backend Bug Fixes

### 1.1 `/registry` — ml_alpha as 5th category

**File:** `ez/api/routes/code.py:262-266`

**Fix:** Filter CrossSectionalFactor registry into `cross_factor` (module starts with `cross_factors.`) and `ml_alpha` (module starts with `ml_alphas.`).

**Test:** `/registry` response has `"ml_alpha"` key with `builtin`/`user` sublists.

### 1.2 `stamp_tax_rate` market gate (Pydantic `model_fields_set`)

**File:** `ez/api/routes/portfolio.py` — PortfolioCommonConfig or child classes

**Fix (codex correction):** Use `self.model_fields_set` to detect if `stamp_tax_rate` was explicitly provided:

```python
@model_validator(mode="after")
def _gate_stamp_tax_by_market(self):
    if hasattr(self, "market") and self.market != "cn_stock":
        if "stamp_tax_rate" not in self.model_fields_set:
            self.stamp_tax_rate = 0.0
    return self
```

This only zeros the tax when the field was NOT explicitly set AND market is non-CN. If user explicitly passes `stamp_tax_rate=0.0005` for US market, it's respected.

**Note:** `market` is on child classes, not on `PortfolioCommonConfig`. Apply validator on `PortfolioRunRequest`, `PortfolioWFRequest`, `PortfolioSearchRequest` (all have `market`).

**Test:** POST /run with market="us_stock" without stamp_tax → verify 0; with explicit stamp_tax=0.001 → verify 0.001 preserved.

### 1.3 `alpha_combiner` training window dynamic

**File:** `ez/api/routes/portfolio.py:215`

**Fix:** `train_start = start - timedelta(days=max(365, _max_factor_warmup(factors) * 2))`

**Test:** Factor with warmup=400 → verify training window > 365.

### 1.4 Portfolio lookback `strict_lookback` option

**File:** `ez/portfolio/engine.py:91-126`

**Fix:** Add `strict_lookback: bool = False` param. When True, raise `ValueError` instead of warning.

**Test:** `strict_lookback=True` + insufficient lookback → ValueError.

---

## Group 2: Phase 6 Frontend

**Dependency split (codex):**
- **G2a** (no G1 dependency): 2.1 CodeEditor + 2.4 TS types + 2.5 API client — can start immediately
- **G2b** (needs G1.1): 2.2 factor dropdown + 2.3 diagnostics panel — wait for `/registry` fix

### 2.1 CodeEditor: `+ ML Alpha` (G2a — no blocker)

**File:** `web/src/components/CodeEditor.tsx`

- `CodeKind` union: add `'ml_alpha'`
- `KIND_LABELS`: `ml_alpha: 'ML Alpha'`
- `KIND_COLORS`: `ml_alpha: '#059669'`
- `mlAlphaFiles` state + sidebar group + 新建/save/delete routing

### 2.2 PortfolioPanel: ML Alpha factor category (G2b — needs G1.1)

**File:** `web/src/components/PortfolioPanel.tsx`

- Factor dropdown `<optgroup label="ML Alpha">` populated from `/registry` response `ml_alpha` category

### 2.3 PortfolioFactorContent: diagnostics panel (G2b — needs G1.1)

**File:** `web/src/components/PortfolioFactorContent.tsx`

- Sub-tab "ML 诊断"
- Dropdown of ML alphas from `/registry`
- "运行诊断" button → `POST /ml-alpha/diagnostics`
- Display: feature importance table + IS/OOS IC chart (ECharts) + verdict badge + turnover + warnings

### 2.4 TypeScript types (G2a — no blocker)

**File:** `web/src/types/index.ts`

```typescript
export interface MLDiagnosticsRequest {
  ml_alpha_name: string
  symbols: string[]
  market?: string
  start_date?: string
  end_date?: string
  eval_freq?: string
  forward_horizon?: number
  severe_overfit_threshold?: number
  mild_overfit_threshold?: number
  high_turnover_threshold?: number
  top_n_for_turnover?: number
}

export interface DiagnosticsResult {
  feature_importance: Record<string, (number | null)[]>
  feature_importance_cv: Record<string, number | null>
  ic_series: Array<{ retrain_date: string; train_ic: number | null; oos_ic: number | null }>
  mean_train_ic: number | null
  mean_oos_ic: number | null
  overfitting_score: number | null
  turnover_series: Array<{ date: string; retention_rate: number }>
  avg_turnover: number
  retrain_dates: string[]
  retrain_count: number
  expected_retrain_freq: number
  actual_avg_gap_days: number
  verdict: 'healthy' | 'mild_overfit' | 'severe_overfit' | 'unstable' | 'insufficient_data'
  warnings: string[]
}
```

### 2.5 API client — typed (G2a — no blocker, codex: no `any`)

**File:** `web/src/api/index.ts`

```typescript
export const mlAlphaDiagnostics = (data: MLDiagnosticsRequest) =>
  api.post<DiagnosticsResult>('/portfolio/ml-alpha/diagnostics', data)
```

---

## Group 2.6 (OPTIONAL — split out per codex)

### Ensemble UI

**Not in V2.13.2 main scope.** Requires new API helper to construct StrategyEnsemble from request params. Evaluate for V2.14.

---

## Group 3: Test & Code Polish (genuine remaining items only)

Items verified as still needed (codex: removed false positives):

### 3.1 Stale docstrings

- `TemplateRequest.kind` comment: add `"ml_alpha"` (code.py ~line 81)
- `list_files` docstring: add `ml_alpha` (code.py line 125)

### 3.2 Dead code: `_get_registry_for_kind`

Grep for call sites. If zero → delete. If >0 → add `"ml_alpha"` case.

### 3.3 `_predict` sym_features is None — add warning

**File:** `ez/portfolio/ml_alpha.py`

Add `_predict_none_warned` flag. When `sym_features is None` at predict stage, emit one-shot warning.

### 3.4 `test_compute_skips_retrain_within_freq` — add exact boundary

Test exists but only checks `< freq` and `> freq`. Add a call at `elapsed == retrain_freq` and assert retrain_count increments.

### 3.5 StrategyEnsemble correlation dedup — multi-call integration test

**File:** `tests/test_portfolio/test_ensemble.py`

Call `generate_weights` 3 times after warmup with identical subs. Assert `len(correlation_warnings) == 1` (not 3).

### 3.6 StrategyEnsemble deepcopy populated state

**File:** `tests/test_portfolio/test_ensemble.py`

`copy.deepcopy(ensemble_with_ledger_data)` → verify `_strategies`, `state`, `_sub_exception_warned` are all independent.

### 3.7 Frontend race token coverage (4 handlers)

**Files:** `PortfolioPanel.tsx`, `CodeEditor.tsx`

Add `runTokenRef` pattern to: `handleEvaluateFactors`, `handleFetchFundamental`, `handleCompare`, `CodeEditor.loadFile`.

---

## Group 4: V2.12.2 Legacy

### 4.1 bool/enum param search

**Files:** `CandidateSearch.tsx`, `ez/api/routes/candidates.py`

`ParamRangeState` add `type` field. `generateValues` handle bool/enum. Backend accept `values: list[str]`.

### 4.2 multi_select power-set UX

**File:** `PortfolioPanel.tsx`

Add "组合搜索" checkbox. **Hard cap at 64 combinations** (codex: not just warning). Above 64 → disable button + show "组合数超过 64,请减少因子数量".

### 4.3 WalkForward unpicklable state doc

Verify warning in Strategy base class docstring. If missing, add.

---

## Group 5: Whitelist Expansion (DEFERRED to V2.14 — codex)

**NOT in V2.13.2 scope.** Environment-sensitive (LightGBM/XGBoost install quirks). Separate release.

- 5.1 LightGBM + XGBoost whitelist + tests
- 5.2 Lasso/LR/EN/DT portfolio integration tests

---

## Execution Order

```
G1.1 (/registry fix)  ──→  G2b (dropdown + diagnostics panel)
         ↕ parallel                    ↕
G1.2-1.4 (backend)    ──→  G3 (polish)  ──→  G4 (legacy)
         ↕ parallel
G2a (CodeEditor + types + API client)
```

**Estimated scope (main, excluding G2.6 + G5):**
- G1: ~60 LOC, ~4 tests
- G2a+G2b: ~400 LOC TypeScript
- G3: ~40 LOC, ~4 tests
- G4: ~100 LOC, ~3 tests
- **Total: ~600 LOC + ~11 tests**
