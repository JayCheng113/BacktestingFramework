# V2.13 Phase 2: MLDiagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `MLDiagnostics` — a companion diagnostics tool that answers "can I trust this MLAlpha's backtest?" by computing feature importance stability, IS/OOS IC decay, turnover, and retrain cadence on a historical date range. Pure Python API (Phase 6 will add frontend).

**Architecture:** MLDiagnostics uses **Option C (fresh instance + polling)** — it creates a fresh MLAlpha via `MLAlpha(**alpha.config_dict())`, drives it through the diagnostic date range via `compute()` calls, and reads `alpha.diagnostics_snapshot()` to observe retrain events. **Zero MLAlpha code changes** (the Phase 2 prerequisite `diagnostics_snapshot()` and `config_dict()` were added in commit `4b976a6`). No private attribute access outside of these two public methods.

**Design decisions** (confirmed by user):
1. **IS IC = model predictions vs actual forward returns** on IS window; OOS IC = same on OOS window. Optional `train_r2` for fit quality.
2. **OOS window adaptive**: `oos_window_days = min(max(retrain_freq, 21), 42)` — self-adjusts for different retrain frequencies.
3. **Verdict thresholds parameterized** via `DiagnosticsConfig` — defaults provided, user/frontend can override.
4. **JSON-compatible output** — `DiagnosticsResult.to_dict()` returns only `str/float/int/bool/list/dict/None`.

**Tech Stack:** No new dependencies. Uses existing `scipy.stats.spearmanr`, `numpy`, `pandas`, `ez.portfolio.ml_alpha.MLAlpha`, `ez.portfolio.calendar.TradingCalendar`.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `ez/portfolio/ml_diagnostics.py` | `DiagnosticsConfig` + `DiagnosticsResult` + `MLDiagnostics` class (~300 lines) |
| `tests/test_portfolio/test_ml_diagnostics.py` | Unit + integration tests (~350 lines, ~25 tests) |

### Modified Files

| File | Changes |
|------|---------|
| `ez/portfolio/__init__.py` | Export `MLDiagnostics`, `DiagnosticsResult`, `DiagnosticsConfig` |
| `CLAUDE.md` | Test count update, V2.13 Phase 2 entry |
| `ez/portfolio/CLAUDE.md` | Files table + Status entry |
| `docs/core-changes/v2.3-roadmap.md` | Check off F9 |

### CORE files — NONE modified

---

## Phase 2 Tasks

### Task 2.1: DiagnosticsConfig + DiagnosticsResult dataclasses

**Files:**
- Create: `ez/portfolio/ml_diagnostics.py`
- Create: `tests/test_portfolio/test_ml_diagnostics.py`

- [ ] **Step 2.1.1: Write failing tests**

```python
"""V2.13 Phase 2: MLDiagnostics unit tests."""
from __future__ import annotations
from datetime import date, datetime
import json
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn", reason="V2.13 MLDiagnostics tests require scikit-learn")


def test_diagnostics_config_defaults():
    from ez.portfolio.ml_diagnostics import DiagnosticsConfig
    cfg = DiagnosticsConfig()
    assert cfg.severe_overfit_threshold == 0.5
    assert cfg.mild_overfit_threshold == 0.2
    assert cfg.high_turnover_threshold == 0.6
    assert cfg.top_n_for_turnover == 10


def test_diagnostics_result_to_dict_json_serializable():
    from ez.portfolio.ml_diagnostics import DiagnosticsResult
    result = DiagnosticsResult()
    d = result.to_dict()
    # Must not raise
    json_str = json.dumps(d)
    assert len(json_str) > 10
    # All keys are strings
    assert all(isinstance(k, str) for k in d.keys())


def test_ml_diagnostics_import():
    from ez.portfolio.ml_diagnostics import MLDiagnostics
    assert MLDiagnostics is not None


def test_ml_diagnostics_init():
    from ez.portfolio.ml_diagnostics import MLDiagnostics
    from ez.portfolio.ml_alpha import MLAlpha
    from sklearn.linear_model import Ridge

    alpha = MLAlpha(
        name="t",
        model_factory=lambda: Ridge(),
        feature_fn=lambda df: pd.DataFrame({"f": df["adj_close"].pct_change(1)}).dropna(),
        target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
        train_window=60, retrain_freq=20, purge_days=5,
    )
    diag = MLDiagnostics(alpha)
    assert diag._source_alpha is alpha
```

- [ ] **Step 2.1.2: Implement skeleton**

```python
"""V2.13 F9: MLDiagnostics — overfitting detection for MLAlpha.

Diagnostics computed:
1. Feature importance stability (coef_ / feature_importances_ CV)
2. IS/OOS IC decay (train_ic vs oos_ic per retrain, overfitting_score)
3. Turnover analysis (top-N retention rate across predictions)
4. Retrain cadence (gap consistency vs expected retrain_freq)

MLDiagnostics uses Option C (fresh instance + polling): creates a fresh
MLAlpha via config_dict(), drives it through the date range, observes
retrain events via diagnostics_snapshot(). Zero private attr access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date, datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ez.portfolio.ml_alpha import MLAlpha
from ez.portfolio.calendar import TradingCalendar


@dataclass
class DiagnosticsConfig:
    """Parameterized verdict thresholds. Defaults are sensible starting
    points but should be tuned per strategy type (e.g., high-frequency
    rotations naturally have higher baseline turnover)."""

    severe_overfit_threshold: float = 0.5
    mild_overfit_threshold: float = 0.2
    high_turnover_threshold: float = 0.6
    top_n_for_turnover: int = 10


@dataclass
class DiagnosticsResult:
    """All-in-one diagnostics report. Every field is JSON-serializable."""

    # ── Feature importance stability ──
    feature_importance: dict[str, list[float]] = field(default_factory=dict)
    feature_importance_cv: dict[str, float] = field(default_factory=dict)

    # ── IS / OOS IC series ──
    ic_series: list[dict] = field(default_factory=list)
    mean_train_ic: float = 0.0
    mean_oos_ic: float = 0.0
    overfitting_score: float = 0.0

    # ── Turnover ──
    turnover_series: list[dict] = field(default_factory=list)
    avg_turnover: float = 0.0

    # ── Retrain cadence ──
    retrain_dates: list[str] = field(default_factory=list)
    retrain_count: int = 0
    expected_retrain_freq: int = 0
    actual_avg_gap_days: float = 0.0

    # ── Summary ──
    verdict: str = "unknown"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """JSON-serializable dict. No numpy, no pandas, no sklearn."""
        def _clean(v: Any) -> Any:
            if isinstance(v, (np.integer, np.int64)):
                return int(v)
            if isinstance(v, (np.floating, np.float64)):
                return float(v)
            if isinstance(v, np.ndarray):
                return v.tolist()
            if isinstance(v, dict):
                return {str(k): _clean(vv) for k, vv in v.items()}
            if isinstance(v, list):
                return [_clean(vv) for vv in v]
            return v
        return {k: _clean(v) for k, v in self.__dict__.items()}


class MLDiagnostics:
    """Diagnose overfitting risk of a configured MLAlpha."""

    def __init__(
        self,
        alpha: MLAlpha,
        config: DiagnosticsConfig | None = None,
    ):
        self._source_alpha = alpha
        self._config = config or DiagnosticsConfig()

    def run(
        self,
        universe_data: dict[str, pd.DataFrame],
        calendar: TradingCalendar,
        start: _date,
        end: _date,
        eval_freq: str = "weekly",
    ) -> DiagnosticsResult:
        """Walk through [start, end], compute all diagnostics."""
        # TODO: Tasks 2.2-2.4
        return DiagnosticsResult(
            expected_retrain_freq=self._source_alpha._retrain_freq,
        )
```

- [ ] **Step 2.1.3: Run — expect pass (skeleton)**
- [ ] **Step 2.1.4: Commit**

---

### Task 2.2: Core diagnostic walk-through loop + retrain cadence

**Files:**
- Modify: `ez/portfolio/ml_diagnostics.py`
- Modify: `tests/test_portfolio/test_ml_diagnostics.py`

The core loop that everything else builds on: create a fresh MLAlpha, walk through dates, detect retrain events, record cadence.

- [ ] **Step 2.2.1: Write failing test**

```python
class TestDiagnosticsWalkthrough:
    def _make_universe(self, n_days=400, n_stocks=5, seed=42):
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
        data = {}
        for i in range(n_stocks):
            prices = 100 * np.cumprod(1 + rng.normal(0.0003*(i+1), 0.012, n_days))
            data[f"S{i:02d}"] = pd.DataFrame({
                "open": prices, "high": prices*1.005, "low": prices*0.995,
                "close": prices, "adj_close": prices,
                "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
            }, index=dates)
        from ez.portfolio.calendar import TradingCalendar
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        return data, cal, dates

    def test_retrain_cadence_matches_expected(self):
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge

        data, cal, dates = self._make_universe(n_days=400)
        alpha = MLAlpha(
            name="t",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=lambda df: pd.DataFrame({"ret": df["adj_close"].pct_change(1)}).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=60, retrain_freq=20, purge_days=5,
        )
        diag = MLDiagnostics(alpha)
        result = diag.run(data, cal, dates[100].date(), dates[-1].date())

        assert result.retrain_count >= 3
        assert result.expected_retrain_freq == 20
        assert len(result.retrain_dates) == result.retrain_count
        # Average gap should be close to retrain_freq (±30% tolerance)
        assert 14 <= result.actual_avg_gap_days <= 28, (
            f"Expected ~20 day gaps, got {result.actual_avg_gap_days:.1f}"
        )

    def test_diagnostic_alpha_does_not_modify_original(self):
        """The source alpha must be untouched after diagnostics run."""
        from ez.portfolio.ml_diagnostics import MLDiagnostics
        from ez.portfolio.ml_alpha import MLAlpha
        from sklearn.linear_model import Ridge

        data, cal, dates = self._make_universe(n_days=400)
        alpha = MLAlpha(
            name="t",
            model_factory=lambda: Ridge(alpha=1.0),
            feature_fn=lambda df: pd.DataFrame({"ret": df["adj_close"].pct_change(1)}).dropna(),
            target_fn=lambda df: df["adj_close"].pct_change(5).shift(-5),
            train_window=60, retrain_freq=20, purge_days=5,
        )
        assert alpha._retrain_count == 0
        assert alpha._current_model is None

        diag = MLDiagnostics(alpha)
        diag.run(data, cal, dates[100].date(), dates[-1].date())

        # Original alpha must still be untouched
        assert alpha._retrain_count == 0
        assert alpha._current_model is None
```

- [ ] **Step 2.2.2: Implement `run()` core loop**

The loop creates a fresh `MLAlpha(**self._source_alpha.config_dict())`, iterates through `calendar.rebalance_dates(start, end, eval_freq)`, calls `compute()` at each date, polls `diagnostics_snapshot()` for retrain events.

- [ ] **Step 2.2.3: Run — expect pass**
- [ ] **Step 2.2.4: Commit**

---

### Task 2.3: Feature importance stability

**Files:**
- Modify: `ez/portfolio/ml_diagnostics.py`
- Modify: `tests/test_portfolio/test_ml_diagnostics.py`

- [ ] **Step 2.3.1: Write failing tests**

```python
class TestFeatureImportanceStability:
    def test_ridge_importance_captured_across_retrains(self):
        """Ridge coef_ should be captured at each retrain event.
        With deterministic data, coefficients should be relatively
        stable (CV < 1.0)."""
        # ... create alpha with 2 features, run diagnostics
        assert "ret1" in result.feature_importance
        assert "ret5" in result.feature_importance
        assert len(result.feature_importance["ret1"]) == result.retrain_count
        assert result.feature_importance_cv["ret1"] < 2.0

    def test_random_forest_importance_captured(self):
        """RF uses feature_importances_ instead of coef_."""
        # ... create alpha with RF, run diagnostics
        assert all(v >= 0 for vs in result.feature_importance.values() for v in vs)

    def test_cv_high_for_noisy_features(self):
        """A feature that is pure noise should have high CV (unstable
        importance across retrains)."""
        # ... create alpha with one signal + one noise feature
        # noise feature CV should be >> signal feature CV
```

- [ ] **Step 2.3.2: Implement feature importance collection in the walk-through loop**

At each retrain event (detected by `snapshot["retrain_count"]` incrementing), extract `snapshot["feature_importance"]` and append values to per-feature lists. After the walk, compute CV = std/|mean| per feature.

- [ ] **Step 2.3.3: Run — expect pass**
- [ ] **Step 2.3.4: Commit**

---

### Task 2.4: IS/OOS IC decay

**Files:**
- Modify: `ez/portfolio/ml_diagnostics.py`
- Modify: `tests/test_portfolio/test_ml_diagnostics.py`

This is the most complex metric. Need to compute per-retrain IS IC and OOS IC.

- [ ] **Step 2.4.1: Write failing tests**

```python
class TestISOOSICDecay:
    def test_is_ic_higher_than_oos_ic_on_overfit_alpha(self):
        """Construct a scenario where the model overfits: high IS IC,
        low OOS IC → overfitting_score > 0.2."""
        # Use a very small train_window (20) + RF with high depth
        # → model memorizes training data, poor generalization

    def test_overfitting_score_near_zero_for_simple_ridge(self):
        """A simple Ridge with regularization should not overfit badly
        on synthetic data with a real signal → score < 0.3."""

    def test_ic_series_has_one_entry_per_retrain(self):
        """Each retrain checkpoint should produce one (train_ic, oos_ic)
        pair in ic_series."""
        assert len(result.ic_series) == result.retrain_count
        for entry in result.ic_series:
            assert "retrain_date" in entry
            assert "train_ic" in entry
            assert "oos_ic" in entry

    def test_oos_window_adaptive(self):
        """OOS window should adapt to retrain_freq:
        retrain_freq=20 → oos_window=21 (floor)
        retrain_freq=60 → oos_window=42 (cap)"""
```

- [ ] **Step 2.4.2: Implement IS/OOS IC computation**

At each retrain event:
- **IS IC**: call `diagnostic_alpha._build_training_panel(universe_data, retrain_date)` to get (X_train, y_train). Get model predictions via `model.predict(X_train.to_numpy())`. Compute `spearmanr(predictions, y_train.to_numpy())`.
- **OOS IC**: collect the next `oos_window_days` eval dates' factor scores. For each OOS date, get forward returns for the scored symbols. Compute `spearmanr(scores, forward_returns)` per date, average.

Note: IS IC uses `_build_training_panel` — this is the ONE case where we call a "private" MLAlpha method. Justified because:
1. It's read-only (doesn't mutate state)
2. There's no public equivalent (and adding one would be over-engineering for Phase 2)
3. Phase 2 and Phase 1 are in the same package (`ez.portfolio`)

Alternative: reconstruct the panel independently (duplicate code). We choose the pragmatic path with a TODO comment for Phase 3+ to evaluate whether a public `get_training_panel()` is warranted.

- [ ] **Step 2.4.3: Run — expect pass**
- [ ] **Step 2.4.4: Commit**

---

### Task 2.5: Turnover analysis

**Files:**
- Modify: `ez/portfolio/ml_diagnostics.py`
- Modify: `tests/test_portfolio/test_ml_diagnostics.py`

- [ ] **Step 2.5.1: Write failing tests**

```python
class TestTurnoverAnalysis:
    def test_constant_rankings_zero_turnover(self):
        """A factor with perfectly stable rankings → turnover ~0."""
        # Use a momentum factor on data where stock ordering is constant

    def test_random_rankings_high_turnover(self):
        """A random factor → turnover ~0.6-0.8."""

    def test_turnover_series_matches_eval_dates(self):
        """One turnover entry per consecutive pair of eval dates."""
        assert len(result.turnover_series) > 0
        for entry in result.turnover_series:
            assert "date" in entry
            assert "retention_rate" in entry
            assert 0.0 <= entry["retention_rate"] <= 1.0
```

- [ ] **Step 2.5.2: Implement turnover collection**

In the walk-through loop: at each eval date, after `compute()`, get the top-N symbols from scores. Compare with previous top-N. Record retention rate.

- [ ] **Step 2.5.3: Run — expect pass**
- [ ] **Step 2.5.4: Commit**

---

### Task 2.6: Verdict + warnings logic

**Files:**
- Modify: `ez/portfolio/ml_diagnostics.py`
- Modify: `tests/test_portfolio/test_ml_diagnostics.py`

- [ ] **Step 2.6.1: Write failing tests**

```python
class TestVerdict:
    def test_severe_overfit_verdict(self):
        """overfitting_score > 0.5 → verdict = 'severe_overfit'"""

    def test_mild_overfit_verdict(self):
        """0.2 < overfitting_score <= 0.5 → verdict = 'mild_overfit'"""

    def test_unstable_verdict(self):
        """avg_turnover > 0.6 but no overfit → verdict = 'unstable'"""

    def test_healthy_verdict(self):
        """Low overfit + low turnover → verdict = 'healthy'"""

    def test_custom_config_thresholds(self):
        """DiagnosticsConfig overrides change verdict boundaries."""
        from ez.portfolio.ml_diagnostics import DiagnosticsConfig
        config = DiagnosticsConfig(
            severe_overfit_threshold=0.3,
            high_turnover_threshold=0.4,
        )
        # ... run with config, verify different verdict

    def test_warnings_generated(self):
        """Diagnostic warnings list should contain human-readable
        messages pointing at specific issues."""
        assert isinstance(result.warnings, list)
        # At least one of: overfit warning, turnover warning, feature
        # stability warning, retrain cadence warning
```

- [ ] **Step 2.6.2: Implement verdict + warnings at end of `run()`**

```python
# After collecting all metrics:
if result.overfitting_score > config.severe_overfit_threshold:
    result.verdict = "severe_overfit"
    result.warnings.append(f"IS IC ({result.mean_train_ic:.3f}) >> OOS IC ({result.mean_oos_ic:.3f}) — overfitting_score={result.overfitting_score:.2f}")
elif result.overfitting_score > config.mild_overfit_threshold:
    result.verdict = "mild_overfit"
    result.warnings.append(f"Mild IS→OOS IC decay: {result.overfitting_score:.2f}")
elif result.avg_turnover > config.high_turnover_threshold:
    result.verdict = "unstable"
    result.warnings.append(f"High turnover: {result.avg_turnover:.2f} — signal may be noise-driven")
else:
    result.verdict = "healthy"

# Feature stability warnings
for feat, cv in result.feature_importance_cv.items():
    if cv > 2.0:
        result.warnings.append(f"Feature '{feat}' has very high CV={cv:.2f} — unstable importance across retrains")
```

- [ ] **Step 2.6.3: Run — expect pass**
- [ ] **Step 2.6.4: Commit**

---

### Task 2.7: Package exports + to_dict() integration test

**Files:**
- Modify: `ez/portfolio/__init__.py`
- Modify: `tests/test_portfolio/test_ml_diagnostics.py`

- [ ] **Step 2.7.1: Add exports**

```python
from ez.portfolio.ml_diagnostics import MLDiagnostics, DiagnosticsResult, DiagnosticsConfig
```

- [ ] **Step 2.7.2: Write end-to-end integration test**

```python
def test_full_diagnostics_pipeline_json_serializable():
    """Run the complete diagnostics pipeline on a real Ridge alpha
    and verify the output is fully JSON-serializable and contains
    all expected fields."""
    # ... create alpha, run diagnostics, call to_dict(), json.dumps()
    assert "verdict" in d
    assert "feature_importance_cv" in d
    assert "ic_series" in d
    assert "turnover_series" in d
    assert "retrain_dates" in d
    assert "warnings" in d
```

- [ ] **Step 2.7.3: Commit**

---

### Task 2.8: Code review + CLAUDE.md update

- [ ] **Step 2.8.1: Run full test suite**
- [ ] **Step 2.8.2: Dispatch code-reviewer subagent**
- [ ] **Step 2.8.3: Address Critical/Important feedback**
- [ ] **Step 2.8.4: Update CLAUDE.md + ez/portfolio/CLAUDE.md + roadmap**
- [ ] **Step 2.8.5: Commit + push**

---

## Self-Review Checklist

### 1. Design decisions from user confirmation
- [x] Option C (fresh instance + polling) via `config_dict()` + `diagnostics_snapshot()`
- [x] IS IC = model predictions vs actual forward returns on IS window
- [x] OOS window adaptive: `min(max(retrain_freq, 21), 42)`
- [x] Verdict thresholds in `DiagnosticsConfig` (parameterized, not hardcoded)
- [x] `DiagnosticsResult.to_dict()` returns JSON-serializable dict
- [x] MLAlpha private attr access limited to `_build_training_panel` (one call, read-only, same package)

### 2. Zero MLAlpha modifications
- [x] `diagnostics_snapshot()` and `config_dict()` already landed in commit `4b976a6`
- [x] Phase 2 code only READS from MLAlpha, never writes

### 3. Test coverage targets
- [x] ~25 tests planned across 6 test classes
- [x] Edge cases: empty universe, model without coef_, all-NaN scores
- [x] Integration: full pipeline → to_dict() → json.dumps()
- [x] Isolation: source alpha untouched after diagnostics run

---

## Execution Recommendation

8 tasks, estimated ~20 tests. Use **subagent-driven-development** for Tasks 2.2-2.6 (core implementation), inline for 2.1 (skeleton) and 2.7-2.8 (exports + review).

Code review gate after Task 2.6 (before exports/docs) — catch correctness issues while the context is fresh.
