# V2.13.2 Comprehensive Release Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close ALL deferred items from V2.13/V2.13.1 review rounds + V2.12.2 legacy. Ship frontend Phase 6. Leave zero known issues in the backlog.

**Scope:** 5 groups, 30 items total. Ordered by dependency: backend fixes first, then frontend, then tests, then docs.

---

## Group 1: Backend Bug Fixes (blocks frontend)

### 1.1 `/registry` endpoint — ml_alpha classified as builtin (Phase 5 M3)

**File:** `ez/api/routes/code.py:262-266`

**Bug:** `/registry` response has 4 categories: strategy/factor/portfolio_strategy/cross_factor. ML alpha subclasses from `ml_alphas/` dir are in CrossSectionalFactor registry with `__module__` starting with `"ml_alphas."`, but the `_classify` call for `"cross_factor"` only passes `user_prefixes=("cross_factors.",)`. So ml_alpha user files get classified as `builtin` (not editable).

**Fix:** Add `"ml_alpha"` as a 5th category in the return dict:

```python
return {
    "strategy": _classify(Strategy.get_registry(), ("strategies.",)),
    "factor": _classify(Factor.get_registry(), ("factors.",)),
    "portfolio_strategy": _classify(PortfolioStrategy.get_registry(), ("portfolio_strategies.",)),
    "cross_factor": _classify(
        {k: v for k, v in CrossSectionalFactor.get_registry().items()
         if not (v.__module__ or "").startswith("ml_alphas.")},
        ("cross_factors.",),
    ),
    "ml_alpha": _classify(
        {k: v for k, v in CrossSectionalFactor.get_registry().items()
         if (v.__module__ or "").startswith("ml_alphas.")},
        ("ml_alphas.",),
    ),
}
```

**Test:** Verify `/registry` response has `"ml_alpha"` key.

### 1.2 `stamp_tax_rate` default not market-gated (V2.12.2 legacy D6)

**File:** `ez/api/routes/portfolio.py:277`

**Bug:** `PortfolioCommonConfig.stamp_tax_rate` defaults to `0.0005` (A-share). Non-UI clients hitting US/HK market without explicitly passing `stamp_tax_rate=0` get Chinese stamp tax applied.

**Fix:** Add a `model_validator` to `PortfolioCommonConfig`:

```python
@model_validator(mode="after")
def _gate_stamp_tax_by_market(self):
    if self.market != "cn_stock" and self.stamp_tax_rate == 0.0005:
        # User didn't explicitly set it — zero it for non-CN markets
        self.stamp_tax_rate = 0.0
    return self
```

Note: this requires `market` field on PortfolioCommonConfig. Currently `market` is on the child classes (PortfolioRunRequest etc.). May need to lift `market` up or apply the validator on the children. Check during implementation.

**Test:** POST /run with market="us_stock" without stamp_tax_rate → verify stamp_tax_rate=0 in the result config.

### 1.3 `alpha_combiner` training window fixed 365 days (V2.12.2 legacy D2)

**File:** `ez/api/routes/portfolio.py:215`

**Bug:** `_compute_alpha_weights` uses `start - timedelta(days=365)` as the training window. Long-warmup custom factors get insufficient history.

**Fix:** Make training window dynamic: `max(365, max_factor_warmup * 2)`.

```python
train_start = start - timedelta(days=max(365, _max_factor_warmup(factors) * 2))
```

**Test:** Factor with warmup=400 → verify training window > 365.

### 1.4 Portfolio lookback warn → raise option (V2.12.2 legacy D4)

**File:** `ez/portfolio/engine.py:91-126`

**Current:** Lookback check only warns if `strategy.lookback_days < max(factor.warmup_period)`.

**Fix:** Keep warning as default. Add `strict_lookback: bool = False` to `run_portfolio_backtest`. When True, raise ValueError instead of warning. Document in CLAUDE.md.

**Test:** `strict_lookback=True` + insufficient lookback → ValueError.

---

## Group 2: Phase 6 Frontend

### 2.1 CodeEditor: `+ ML Alpha` button

**File:** `web/src/components/CodeEditor.tsx`

- [ ] Add `'ml_alpha'` to `CodeKind` type union (line 137)
- [ ] Add to `KIND_LABELS`: `ml_alpha: 'ML Alpha'`
- [ ] Add to `KIND_COLORS`: `ml_alpha: '#059669'` (emerald green, distinct from existing 4)
- [ ] Add `mlAlphaFiles` state array (mirror `crossFactorFiles`)
- [ ] Add sidebar group "ML Alpha" with file list
- [ ] Add "新建" button that calls `/api/code/template` with `kind: "ml_alpha"`
- [ ] Add save/delete handlers routing to `kind: "ml_alpha"`

**Test:** Manual — create ml_alpha file via UI, verify it appears in sidebar.

### 2.2 PortfolioPanel: ML Alpha factor category

**File:** `web/src/components/PortfolioPanel.tsx`

- [ ] Factor dropdown `<optgroup>` for "ML Alpha" — populated from `/registry` response `ml_alpha` category
- [ ] When user selects an ML Alpha factor, the factor name is passed to TopNRotation/MultiFactorRotation as before (MLAlpha IS a CrossSectionalFactor)

### 2.3 PortfolioFactorContent: ML Diagnostics sub-panel

**File:** `web/src/components/PortfolioFactorContent.tsx`

- [ ] New sub-tab "ML 诊断"
- [ ] Dropdown of registered ML alphas (from `/registry` ml_alpha category)
- [ ] Button "运行诊断" → calls `POST /api/portfolio/ml-alpha/diagnostics`
- [ ] Display:
  - Feature importance stability table (per feature, CV value, color-coded)
  - IS/OOS IC series line chart (ECharts, dual y-axis)
  - Overfitting score badge (green/yellow/red based on verdict)
  - Turnover metric display
  - Retrain cadence summary
  - Warnings list

### 2.4 TypeScript types

**File:** `web/src/types/index.ts`

```typescript
export interface DiagnosticsResult {
  feature_importance: Record<string, number[]>
  feature_importance_cv: Record<string, number | null>
  ic_series: Array<{
    retrain_date: string
    train_ic: number | null
    oos_ic: number | null
  }>
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

### 2.5 API client

**File:** `web/src/api/index.ts`

```typescript
export const mlAlphaDiagnostics = (data: any) =>
  api.post('/portfolio/ml-alpha/diagnostics', data)
```

### 2.6 Multi-strategy ensemble page (roadmap item)

**File:** `web/src/components/PortfolioPanel.tsx` or new component

- [ ] Sub-tab or section "多策略组合"
- [ ] Select sub-strategies from registered portfolio strategies
- [ ] Select mode: equal / manual / return_weighted / inverse_vol
- [ ] Manual weights input (when mode=manual)
- [ ] Run button → constructs StrategyEnsemble via Python API (needs a new API endpoint or leverages existing /run with a special strategy_name="StrategyEnsemble" + strategy_params)
- [ ] Comparison chart: ensemble equity curve vs individual sub-strategy curves

**Note:** This may require a new API helper to construct StrategyEnsemble from request params. Evaluate during implementation — if too complex for V2.13.2, defer the ensemble UI and just ship the ML Alpha frontend.

---

## Group 3: Test & Code Polish (from review rounds)

### 3.1 Stale docstrings (Phase 5 M1, M4)

**Files:** `ez/api/routes/code.py`

- [ ] `TemplateRequest.kind` inline comment: add `| "ml_alpha"` (line ~81)
- [ ] `list_files` docstring: add `ml_alpha` to the kind list (line 125)

### 3.2 Dead code cleanup (Phase 5 M2)

**File:** `ez/api/routes/code.py:22-40`

- [ ] `_get_registry_for_kind`: either add `"ml_alpha"` case or delete the function if it has zero call sites. Grep first.

### 3.3 MLAlpha `_predict` None branch no warning (Phase 2 M3)

**File:** `ez/portfolio/ml_alpha.py`

- [ ] In `_predict`, when `sym_features is None`: add a `_predict_none_warned` one-shot flag + warning (parallel to `_predict_feature_type_warned`)

### 3.4 `test_compute_skips_retrain_within_freq` boundary (Phase 1 M3)

**File:** `tests/test_portfolio/test_ml_alpha.py`

- [ ] Add a call at `elapsed == retrain_freq` (exact boundary) and assert retrain_count increments. Currently only tests `< freq` and `> freq`.

### 3.5 `test_non_numeric_dtype_logs_warning` assertion tightening (Phase 2 M2)

**File:** `tests/test_portfolio/test_ml_alpha.py`

- [ ] Change substring match from `"category" or "object"` to a more specific pattern like `"dtype"` + `"object"`.

### 3.6 StrategyEnsemble correlation dedup integration test (Phase 3 S1)

**File:** `tests/test_portfolio/test_ensemble.py`

- [ ] Test that calling `generate_weights` multiple times after warmup produces only 1 correlation warning (not growing).

### 3.7 StrategyEnsemble deepcopy of populated state (Phase 3 S3)

**File:** `tests/test_portfolio/test_ensemble.py`

- [ ] `copy.deepcopy(ensemble_with_populated_ledger)` → verify independent `_strategies`, `self.state`, `_sub_exception_warned`.

### 3.8 Frontend race token coverage (V2.12.2 D7)

**Files:** `web/src/components/PortfolioPanel.tsx`, `web/src/components/CodeEditor.tsx`

- [ ] `handleEvaluateFactors` (evalResult/corrResult): add runTokenRef pattern
- [ ] `handleFetchFundamental` (fundaStatus/qualityReport): add runTokenRef pattern
- [ ] `handleCompare` (compareData): add runTokenRef pattern
- [ ] `CodeEditor.loadFile` (code/filename): add runTokenRef pattern

---

## Group 4: V2.12.2 Legacy Items

### 4.1 Candidate search bool/enum support (D1)

**Files:** `web/src/components/CandidateSearch.tsx`, `ez/api/routes/candidates.py`

- [ ] `ParamRangeState` add `type: "number" | "bool" | "enum"` field
- [ ] `generateValues` handle bool → `[0, 1]`, enum → parse from comma-separated string
- [ ] Backend `ParamRangeRequest` accept `values: list[str]` for enum
- [ ] Frontend UI: toggle between number range input and enum input based on parameter schema `type` field

### 4.2 multi_select parameter search UX (D3)

**File:** `web/src/components/PortfolioPanel.tsx`

- [ ] Current: each selected factor runs independently as a single-factor combo
- [ ] Add checkbox: "组合搜索" — when checked, search all subsets of selected factors (power set)
- [ ] Warning about combinatorial explosion when power set > 32

### 4.3 WalkForward deepcopy unpicklable state doc (D5)

**File:** `CLAUDE.md`

- [ ] Already documented. Verify the warning is in Strategy base class docstring too. If not, add it.

---

## Group 5: Known Limitations Closure

### 5.1 LightGBM / XGBoost whitelist expansion (E1)

**Files:** `ez/portfolio/ml_alpha.py`, `tests/test_portfolio/test_ml_alpha_sklearn.py`

- [ ] Add `lightgbm` and `xgboost` to `pyproject.toml` `[ml]` optional group
- [ ] Add `LGBMRegressor` and `XGBRegressor` to `_build_supported_estimator_set()`
- [ ] Deepcopy regression test for each
- [ ] Determinism test (fixed random_state)
- [ ] End-to-end `run_portfolio_backtest` test for each
- [ ] Sandbox smoke test: `from lightgbm import LGBMRegressor` passes `check_syntax`
- [ ] Update `ML_ALPHA_TEMPLATE` docstring to mention LightGBM/XGBoost as supported
- [ ] Update `CLAUDE.md` Known Limitations: remove "V1 不支持 LightGBM/XGBoost"

**Note:** LightGBM booster pickle has known version quirks. Test with the installed version explicitly. XGBoost's `XGBRegressor` uses `n_jobs` (must default to 1). Both need `n_jobs=1` enforcement verification.

### 5.2 Lasso/LR/EN/DT portfolio integration tests (E2)

**File:** `tests/test_portfolio/test_ml_alpha_sklearn.py`

- [ ] `test_lasso_run_portfolio_backtest`
- [ ] `test_linear_regression_run_portfolio_backtest`
- [ ] `test_elastic_net_run_portfolio_backtest`
- [ ] `test_decision_tree_run_portfolio_backtest`

Each: construct MLAlpha with the estimator, run through `run_portfolio_backtest`, assert equity_curve non-empty + retrain_count >= 3.

---

## Execution Order

```
Group 1 (backend fixes) → Group 2 (frontend) → Group 3 (polish) → Group 4 (legacy) → Group 5 (whitelist)
```

**Rationale:**
- Group 1.1 (`/registry` fix) must land before Group 2 (frontend reads `/registry`)
- Group 1.2-1.4 are independent backend fixes, can parallel with Group 2
- Group 3 is all polish, can do anytime
- Group 4 is V2.12.2 legacy, lowest priority
- Group 5 (LightGBM/XGBoost) is feature expansion, do last

**Estimated scope:**
- Group 1: ~60 LOC backend, ~4 tests
- Group 2: ~500 LOC TypeScript, manual testing
- Group 3: ~50 LOC, ~6 tests
- Group 4: ~100 LOC frontend + backend, ~3 tests
- Group 5: ~40 LOC + ~8 tests (if LightGBM/XGBoost available in env)

**Total: ~750 LOC + ~21 tests**
