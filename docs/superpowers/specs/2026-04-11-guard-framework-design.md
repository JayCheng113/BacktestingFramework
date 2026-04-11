# ez.testing.guards — Quant Code Guard Framework (V2.19.0)

**Date:** 2026-04-11
**Owner:** AI agent (approved by user via "你自己以第一性原理分析决定")
**Scope:** Core engine reliability layer
**Status:** Spec for review

---

## 1. Problem Statement

### 1.1 Business First Principles

Quant code errors are **silent and lethal**. A single look-ahead bug can make a losing strategy look like `Sharpe 3.0` in backtest and destroy capital in live trading. The codebase has **real history** of this exact pattern:

- **v1 Dynamic Efficient Frontier 回测脚本** had a look-ahead bug: the weight optimization used `close[t]` while trading used `close[t-1]`. Bug discovery happened by accident during codex review, not by any automated check.
- **MLAlpha purge bug** in Phase 1 round 1: `timedelta(days=N)` purge let labels cross weekends and point at prediction windows. Caught by reviewer, not automation.
- **Block Bootstrap circular wrap** (codex round 3 on v5 research): `min(blen, n - start, n - len(sampled))` made circular wrap branch unreachable. Caught by codex review, not automation.

Each of these took **multiple review rounds** to find. Each is a **class of bug** that can be detected automatically with a small, focused test suite.

### 1.2 Why User Code Needs Guards

When a user writes a strategy/factor/portfolio strategy in the sandbox (`strategies/`, `factors/`, `portfolio_strategies/`, `cross_factors/`, `ml_alphas/`), the sandbox currently runs:
1. Syntax check (`ez/agent/sandbox.py::check_syntax`)
2. Import security check (forbidden imports, dunder, dict-style dunder, path traversal)
3. Contract test in subprocess (does it instantiate, does `compute()`/`generate_weights()` return the right type)

**What's missing:** the contract test asserts only **shape** (is it a `pd.Series`, is `sum(w) <= 1.001`), not **semantic correctness**. The code can pass all existing checks while containing any of these bugs:

| Bug class | Current detection | Proposed guard |
|-----------|-------------------|----------------|
| Look-ahead bias (uses data from date > t) | None | `LookaheadGuard` (Block) |
| NaN/Inf in output | None | `NaNInfGuard` (Block) |
| Weight sum > 1.0 + epsilon | Only shape `<= 1.001` once | `WeightSumGuard` (Block, repeated) |
| Negative weights (A股 short) | Only shape once | `NonNegativeWeightsGuard` (Warning, repeated) |
| Non-determinism (different run → different result) | None | `DeterminismGuard` (Warning) |

### 1.3 Goal

Build a **minimal, fast, correct** guard framework that runs automatically when a user saves code via the sandbox, blocking on Tier 1 violations and warning on Tier 2 violations. **Zero new UI tabs.** **Zero backwards-incompatible API changes.** **< 500 ms runtime on mock data.**

---

## 2. Architecture Overview

### 2.1 Module Structure

```
ez/testing/               NEW module, sibling of ez/core, ez/backtest, ez/portfolio
├── __init__.py
└── guards/
    ├── __init__.py       # Public exports: GuardSuite, Guard, GuardResult, GuardContext, etc.
    ├── base.py           # Guard ABC, GuardContext, GuardResult dataclasses
    ├── suite.py          # GuardSuite orchestrator
    ├── mock_data.py      # Mock data fixture generator (deterministic, small)
    ├── lookahead.py      # LookaheadGuard (Block)
    ├── nan_inf.py        # NaNInfGuard (Block)
    ├── weight_sum.py     # WeightSumGuard (Block)
    ├── non_negative.py   # NonNegativeWeightsGuard (Warning)
    └── determinism.py    # DeterminismGuard (Warning)
```

### 2.2 Data Flow

```
sandbox.save_and_validate_code(filename, code, kind)
  → [existing] syntax check, security check, write file
  → [existing] contract test (subprocess, shape only)
  → [NEW] _run_guards(filename, kind, target_dir)
      → GuardSuite.run(context) → list[GuardResult]
      → if any Tier 1 blocked → rollback file + return failure
      → else → keep file + return success with guard_result payload
  → [existing] hot-reload
  → return dict with `guard_result` key added
```

### 2.3 Why Not New Tab/UI

**First-principles UX:** the user's current flow is `Edit → Save → Read status → Read test output`. The guard result is **information about the save**, not a separate workflow. It belongs in the existing status/test-output panel, not in a new tab.

- Existing `CodeEditor.tsx:570-576` status bar → extend with guard verdict badge
- Existing `CodeEditor.tsx:640-649` test output panel → extend with guard details
- Zero new components. Zero new routes. Zero new state machines.

---

## 3. Guard Specifications

### 3.1 Tier 1 (Block — save fails if violated)

#### 3.1.1 LookaheadGuard

**What it catches:** code that reads future data (`close[t+1]`, `close[future_date]`, unaligned `shift(-k)` without proper purge).

**How it works (shuffle-future test):**
1. Build deterministic mock data: 5 symbols × 200 trading days, `pd.date_range('2024-01-01', periods=200, freq='B')`, prices via `rng.default_rng(42)`.
2. Pick a target date `t = dates[150]` (leaves 50 days of future).
3. Run the user code twice on this data:
   - Run A: original data
   - Run B: same data but with rows at index > 150 **shuffled** (shuffle seed 7)
4. Compare the outputs of Run A and Run B **at dates ≤ t**:
   - For `Factor.compute(data, end_date=t)` → compare last value of returned series
   - For `CrossSectionalFactor.compute(data, target_date=t)` → compare returned dict
   - For `Strategy.generate_signals(data, date=t)` → compare returned signal/action dict
   - For `PortfolioStrategy.generate_weights(data, target_date=t, ...)` → compare returned dict
5. If outputs differ **beyond numerical tolerance** (abs diff > 1e-9 for scalar, elementwise 1e-9 for dict) → **lookahead detected**.

**Why this works:** if the code only uses data ≤ t, shuffling data > t cannot change outputs at t. If it does, the code is reading future data.

**Tolerance:** 1e-9 (deterministic float64 ops on same input should be bit-equal; 1e-9 allows for reordering of commutative operations).

**Failure message (block):**
```
LookaheadGuard failed: the code at date t={target_date} produced different
output when future data (rows after t) was shuffled. This is a strong signal
that the code is reading future data.

Delta found at key '{key}': {value_a} vs {value_b} (|diff| = {abs_diff})
```

**Kinds supported:** `factor`, `cross_factor`, `strategy`, `portfolio_strategy`, `ml_alpha`.

**Not supported:** `StrategyEnsemble` (tested via its sub-strategies).

#### 3.1.2 NaNInfGuard

**What it catches:** output contains `NaN` or `Inf` that silently propagates and becomes 0-weight or crashes downstream.

**How it works:**
1. Run the user code on clean mock data (no NaN/Inf in input).
2. Extract output:
   - `Factor.compute` → `pd.Series`
   - `CrossSectionalFactor.compute` → `pd.Series` (index = symbols)
   - `Strategy.generate_signals` → `dict` (signal values)
   - `PortfolioStrategy.generate_weights` → `dict` (weight values)
3. Check:
   - For series: `output.isna().any()` or `(~np.isfinite(output[output.notna()])).any()` → fail.
   - For dict: iterate values, `math.isnan(v) or math.isinf(v)` → fail.
4. **Tolerance exception:** factors legitimately return NaN before warmup. So if the user's class declares `warmup_period > 0`, the first `warmup_period` rows of factor output are **allowed** to be NaN. Only NaN/Inf at index ≥ `warmup_period` fails.

**Failure message (block):**
```
NaNInfGuard failed: output contains NaN or Inf at positions where clean data
was provided. This usually means:
  - division by zero (e.g., `x / y` where `y == 0`)
  - invalid math (e.g., `np.log(negative)`)
  - unpropagated NaN from intermediate calculation

First bad positions: {positions}
```

#### 3.1.3 WeightSumGuard

**What it catches:** portfolio strategies that over-leverage (`sum(w) > 1.001`) OR under-normalize in a way that allocates less than declared (`sum(w) < 0.999` for fully-invested strategies).

**Why this is separate from the contract test:** the contract test checks sum only once at date=2024-03-15 on 3 synthetic stocks. The guard runs the strategy over multiple `target_date` values (5 different dates spread across the mock range), so it catches bugs that are date-dependent.

**How it works:**
1. Run `PortfolioStrategy.generate_weights()` on mock data at 5 dates: `dates[50], dates[100], dates[150], dates[175], dates[199]`.
2. For each call, compute `s = sum(w.values())`.
3. **Tier 1 block** if `s > 1.001` (over-leverage).
4. **Tier 1 block** if `s < -0.001` (net short, impossible for A-share long-only).
5. **Tier 2 warning (not this guard)** if `0 <= s < 0.95` (cash-heavy might be intentional; `NonNegativeWeightsGuard` does not cover this).

**Why `1.001`:** matches existing contract test tolerance at `sandbox.py:833`.

**Failure message (block):**
```
WeightSumGuard failed at date {date}: sum(weights) = {sum:.6f}, which exceeds
1.001 (over-leverage) or is below -0.001 (net short, not allowed for A-share
long-only strategies).

Weights returned: {weights_preview}
```

**Applies to:** `portfolio_strategy` only.

### 3.2 Tier 2 (Warning — save succeeds with warning attached)

#### 3.2.1 NonNegativeWeightsGuard

**What it catches:** a single weight in the dict is negative. A股 long-only means all individual weights must be ≥ 0, even if sum is valid.

**How it works:** same 5 target dates as WeightSumGuard. For each returned dict, check `any(v < -1e-9 for v in w.values())`. If yes → warning.

**Why Tier 2 not Tier 1:** some research code legitimately returns pre-normalization raw alphas that include negatives, and the user intends to run a later pipeline that clips to ≥ 0. We don't want to block that.

**Warning message:**
```
NonNegativeWeightsGuard warning at date {date}: weight for symbol {symbol}
is {weight:.6f}. A-share long-only strategies must have all individual weights
>= 0. If this is intentional (e.g., raw alphas before clipping), you can ignore
this warning. Otherwise your strategy will be rejected by the engine.
```

**Applies to:** `portfolio_strategy` only.

#### 3.2.2 DeterminismGuard

**What it catches:** code that produces different outputs when run twice with the same input (typically from uncontrolled randomness, unseeded `random` calls, dict iteration order on Python < 3.7, or mutable default arguments).

**How it works:**
1. Run the user code twice on **exactly the same** mock data fixture (same bytes).
2. Compare outputs byte-identical (`==` for dict, `.equals()` for pd.Series).
3. If differ → warning.

**Why Tier 2:** ML models (sklearn, lgbm, xgb) can have environmental non-determinism from BLAS threading even with seed=42. We warn but don't block. The user should seed their `model_factory` explicitly.

**Warning message:**
```
DeterminismGuard warning: running the code twice on identical input produced
different outputs. This may indicate:
  - unseeded random calls (use np.random.default_rng(seed) or rng.seed())
  - uncontrolled dict iteration (Python 3.7+ is ordered, but set() is not)
  - BLAS threading non-determinism (set OMP_NUM_THREADS=1 or seed sklearn)

First divergent key: {key}, run1={value1}, run2={value2}
```

**Applies to:** all kinds.

### 3.3 Guards NOT in V1

Considered but deferred for V2 or later:

| Guard | Why deferred |
|-------|--------------|
| TurnoverGuard | Needs multi-day simulation, beyond 500 ms budget |
| MaxDrawdownGuard | Needs full backtest, handled by gate framework |
| PositionConcentrationGuard | Engine risk_manager handles this |
| CorrelationStabilityGuard | Too narrow, only applies to AlphaCombiner |
| IndexAlignmentGuard | Subsumed by existing shape contract tests |
| MonotonicDateGuard | Panda DatetimeIndex handles this at data layer |

### 3.4 Bypass Mechanism

**Design decision:** no per-file bypass flag. Rationale from first principles: if a user can bypass guards for their own file, the framework becomes advisory. Guards should be **hard** for the code they apply to.

**Escape hatch:** if a guard produces a false positive, the user files an issue. We fix the guard, not add a bypass. This mirrors how pytest works — if a test is wrong, fix the test.

**Exception for Tier 2 warnings:** they are already non-blocking by design.

---

## 4. Implementation Details

### 4.1 Module `ez/testing/guards/base.py`

```python
"""Guard framework core — base classes and types."""
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

    The sandbox builds this once per save and reuses it across all guards
    in the suite. Attributes are fully populated for the target kind.
    """
    filename: str                 # 'my_factor.py'
    module_name: str              # 'factors.my_factor'
    file_path: Path               # /.../factors/my_factor.py
    kind: GuardKind
    # Lazy import: only loaded the first time a guard asks for the class.
    # Populated by GuardSuite.run() via _load_user_class() helper.
    user_class: type | None = None
    # Whether the user class is subclassable / instantiable with no args.
    instantiation_error: str | None = None


@dataclass(frozen=True)
class GuardResult:
    """Outcome of a single guard run."""
    guard_name: str
    severity: GuardSeverity
    tier: GuardTier
    message: str                  # Empty on pass.
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

        Must return a GuardResult. Must NEVER raise — wrap internal errors
        as severity=BLOCK with descriptive message.
        """
        raise NotImplementedError

    def applies(self, kind: GuardKind) -> bool:
        return kind in self.applies_to
```

### 4.2 Module `ez/testing/guards/mock_data.py`

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

# Canonical mock data seed (all guards use this to be reproducible).
MOCK_SEED = 42
SHUFFLE_SEED = 7

MOCK_N_DAYS = 200
MOCK_START_DATE = "2024-01-01"
MOCK_SYMBOLS = ("T001", "T002", "T003", "T004", "T005")


@lru_cache(maxsize=1)
def build_mock_panel() -> dict[str, pd.DataFrame]:
    """Returns a dict[symbol → DataFrame] with OHLCV + adj_close.

    200 B-day bars, 5 symbols, deterministic geometric brownian motion.
    Cached so guards reuse the exact same object (faster + avoids
    per-guard mutation bugs — guards MUST NOT mutate this).
    """
    rng = np.random.default_rng(MOCK_SEED)
    dates = pd.date_range(MOCK_START_DATE, periods=MOCK_N_DAYS, freq="B")
    panel: dict[str, pd.DataFrame] = {}
    for sym in MOCK_SYMBOLS:
        # GBM: r_t ~ N(mu, sigma)
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
            "adj_close": price,   # Clean mock: no splits/dividends.
            "volume": volume,
        }, index=dates)
    return panel


def build_shuffled_panel(cutoff_idx: int) -> dict[str, pd.DataFrame]:
    """Returns a copy of mock panel with rows > cutoff_idx shuffled.

    Used by LookaheadGuard: if user code output at date = dates[cutoff_idx]
    differs between this and build_mock_panel(), it's reading future data.

    `cutoff_idx` itself stays in place. Rows [cutoff_idx + 1, N) are permuted.
    """
    rng = np.random.default_rng(SHUFFLE_SEED)
    base = build_mock_panel()
    shuffled: dict[str, pd.DataFrame] = {}
    for sym, df in base.items():
        future_part = df.iloc[cutoff_idx + 1:].copy()
        perm = rng.permutation(len(future_part))
        future_part.iloc[:] = future_part.iloc[perm].values
        full = pd.concat([df.iloc[: cutoff_idx + 1], future_part])
        full.index = df.index   # Keep original date index (we only shuffle values).
        shuffled[sym] = full
    return shuffled


def target_date_at(idx: int) -> datetime:
    """Returns the date at position idx in the mock panel (for deterministic guard targets)."""
    dates = pd.date_range(MOCK_START_DATE, periods=MOCK_N_DAYS, freq="B")
    return dates[idx].to_pydatetime()
```

### 4.3 Module `ez/testing/guards/lookahead.py`

```python
"""LookaheadGuard: detect future data access via shuffle-future test."""
from __future__ import annotations
import math
import time
from typing import Any

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import (
    build_mock_panel, build_shuffled_panel, target_date_at,
    MOCK_N_DAYS,
)

TOLERANCE = 1e-9
CUTOFF_IDX = 150   # Runs on date at idx=150, shuffles rows at idx > 150.


def _compare_scalar(a: float, b: float) -> float:
    if a is None or b is None:
        return math.inf if a != b else 0.0
    if math.isnan(a) and math.isnan(b):
        return 0.0
    if math.isnan(a) or math.isnan(b):
        return math.inf
    return abs(a - b)


def _compare_dict(a: dict, b: dict) -> tuple[float, str]:
    """Returns (max_abs_diff, key_of_max_diff). Empty dicts → 0."""
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
        # Single-symbol factor; run on first symbol.
        sym = next(iter(panel))
        df = panel[sym]
        # Factor.compute(df, end_date=None) returns pd.Series.
        # Mask to data <= target_date, mirroring engine usage.
        mask = df.index <= target_date
        series = inst.compute(df.loc[mask])
        if series is None or len(series) == 0:
            return None
        return float(series.iloc[-1])
    if kind == "cross_factor":
        result = inst.compute(panel, target_date)
        if result is None:
            return {}
        return {k: float(v) for k, v in result.items() if v is not None}
    if kind == "strategy":
        sym = next(iter(panel))
        df = panel[sym]
        mask = df.index <= target_date
        # Strategy.generate_signals(df) → SignalList-like (simplify to last signal).
        sigs = inst.generate_signals(df.loc[mask])
        # Canonicalize to scalar for comparison: final action/signal only.
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
        return {k: float(v) for k, v in result.items()}
    if kind == "ml_alpha":
        result = inst.compute(panel, target_date)
        if result is None:
            return {}
        # MLAlpha.compute returns pd.Series (index = symbols).
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
                message=f"LookaheadGuard: could not load user class. "
                        f"Reason: {context.instantiation_error or 'unknown'}",
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
                message=f"LookaheadGuard: user code raised during execution: {type(e).__name__}: {e}",
                runtime_ms=(time.perf_counter() - t0) * 1000,
            )

        # Compare.
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
            # String / unknown — exact equality.
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
                    f"This is a strong signal that the code reads future data."
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

### 4.4 Module `ez/testing/guards/nan_inf.py`

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
    """Returns positions (integer offsets) of NaN/Inf beyond warmup."""
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
    """Returns keys of NaN/Inf in a weight/factor dict."""
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
                message=f"NaNInfGuard: could not load user class. "
                        f"Reason: {context.instantiation_error or 'unknown'}",
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
        target = target_date_at(MOCK_N_DAYS - 1)   # Last date = full output.
        try:
            if context.kind == "factor":
                sym = next(iter(panel))
                out = inst.compute(panel[sym])
                bad = _scan_series(out, warmup)
                bad_desc = [int(i) for i in bad]
            elif context.kind == "cross_factor":
                out = inst.compute(panel, target)
                bad_desc = _scan_dict(dict(out) if out is not None else {})
            elif context.kind == "strategy":
                sym = next(iter(panel))
                out = inst.generate_signals(panel[sym])
                if isinstance(out, pd.Series):
                    bad_desc = [int(i) for i in _scan_series(out, warmup)]
                else:
                    bad_desc = []   # Non-series strategies skip scan.
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
                    f"First positions/keys: {sample}. "
                    f"Common causes: division by zero, log of negative, "
                    f"unpropagated intermediate NaN."
                ),
                details={"bad_positions": [str(x) for x in bad_desc], "warmup": warmup},
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

### 4.5 Module `ez/testing/guards/weight_sum.py`

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
                message=f"WeightSumGuard: could not load user class. "
                        f"Reason: {context.instantiation_error or 'unknown'}",
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
                    message=f"WeightSumGuard: user code raised at date {target.date()}: {type(e).__name__}: {e}",
                    runtime_ms=(time.perf_counter() - t0) * 1000,
                )
            if w is None:
                continue
            s = sum(float(v) for v in w.values())
            if s > UPPER or s < LOWER:
                violations.append({
                    "date": str(target.date()),
                    "sum": s,
                    "weights_preview": {k: round(float(v), 6) for k, v in list(w.items())[:5]},
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
                    f"at {len(violations)} date(s). First violation: "
                    f"date={first['date']}, sum={first['sum']:.6f}. "
                    f"A-share long-only strategies must have 0 <= sum(w) <= 1."
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

### 4.6 Module `ez/testing/guards/non_negative.py`

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
                message=f"NonNegativeWeightsGuard: could not load user class. "
                        f"Reason: {context.instantiation_error or 'unknown'}",
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
                    break   # One per date is enough.
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
                    f"A-share long-only strategies require all individual weights >= 0."
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

### 4.7 Module `ez/testing/guards/determinism.py`

```python
"""DeterminismGuard: two runs with identical input must produce identical output."""
from __future__ import annotations
import math
import time
import pandas as pd

from .base import Guard, GuardContext, GuardResult, GuardSeverity
from .mock_data import build_mock_panel, target_date_at, MOCK_N_DAYS

TOLERANCE = 1e-12


def _canonicalize(output) -> str:
    """Produce a deterministic string for comparison."""
    if output is None:
        return "<None>"
    if isinstance(output, (int, float)):
        return f"{float(output):.15e}"
    if isinstance(output, pd.Series):
        return output.to_json()
    if isinstance(output, dict):
        return str(sorted((str(k), f"{float(v):.15e}") for k, v in output.items() if v is not None))
    return str(output)


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
                message=f"DeterminismGuard: could not load user class. "
                        f"Reason: {context.instantiation_error or 'unknown'}",
            )
        panel = build_mock_panel()
        target = target_date_at(MOCK_N_DAYS - 1)
        try:
            # Fresh instance per run (matches engine semantics).
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
                    f"different output. Common causes: unseeded RNG, uncontrolled set() "
                    f"iteration, BLAS threading for ML models. Seed your RNG explicitly."
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


def _invoke(inst, kind: str, panel: dict, target):
    """Shared invoker across guards — matches LookaheadGuard._run_user_code signatures."""
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
        return {str(k): float(v) for k, v in (out or {}).items()}
    if kind == "ml_alpha":
        out = inst.compute(panel, target)
        return {str(k): float(v) for k, v in (out or {}).items() if v is not None}
    return None
```

### 4.8 Module `ez/testing/guards/suite.py`

```python
"""GuardSuite: orchestrates multiple guards and collects results."""
from __future__ import annotations
import importlib.util
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .base import Guard, GuardContext, GuardResult, GuardSeverity, GuardKind
from .lookahead import LookaheadGuard
from .nan_inf import NaNInfGuard
from .weight_sum import WeightSumGuard
from .non_negative import NonNegativeWeightsGuard
from .determinism import DeterminismGuard


def default_guards() -> list[Guard]:
    return [
        LookaheadGuard(),
        NaNInfGuard(),
        WeightSumGuard(),
        NonNegativeWeightsGuard(),
        DeterminismGuard(),
    ]


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
        """JSON-serializable summary for API response."""
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
                # Guards MUST NOT raise. If they do, treat as block with
                # a clear message so we can find and fix the guard.
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


def load_user_class(file_path: Path, module_name: str, kind: GuardKind) -> tuple[type | None, str | None]:
    """Import the user file and return (class, error_message).

    Returns (None, error) if file cannot be imported or no target class found.
    This runs in the SAME process as the sandbox (after hot-reload has already
    succeeded for the contract test), so the user class is safe to instantiate.
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

    # Find target class by kind.
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

### 4.9 Sandbox Integration

**Strategy:** add a single `_run_guards()` helper in `ez/agent/sandbox.py` that all three existing save flows call. The helper rolls back the file on Tier 1 block, exactly mirroring contract test rollback.

**File: `ez/agent/sandbox.py` — new helper (add near the top of the file, after existing imports):**

```python
# V2.19.0: guard framework integration.
from ez.testing.guards import (
    GuardSuite, GuardContext, SuiteResult, load_user_class,
)


def _run_guards(
    filename: str,
    kind: str,
    target_dir: Path,
) -> SuiteResult:
    """Run the guard suite against a just-saved user file.

    Called by all save flows AFTER contract test passes and BEFORE
    hot-reload. If the suite blocks, the caller rolls back the file.
    """
    stem = filename.replace(".py", "")
    module_name = f"{target_dir.name}.{stem}"
    file_path = target_dir / filename
    user_class, err = load_user_class(file_path, module_name, kind)  # type: ignore[arg-type]
    context = GuardContext(
        filename=filename,
        module_name=module_name,
        file_path=file_path,
        kind=kind,    # type: ignore[arg-type]
        user_class=user_class,
        instantiation_error=err,
    )
    suite = GuardSuite()
    return suite.run(context)
```

**Hook point 1 — `save_and_validate_strategy` (around line 429, between contract test success and hot-reload):**

```python
    # [existing] Run contract test in subprocess with timeout
    test_result = _run_contract_test(safe_name)
    if not test_result["passed"]:
        # [existing rollback]
        ...

    # [NEW] V2.19.0 guard framework.
    guard_result = _run_guards(safe_name, "strategy", _STRATEGIES_DIR)
    if guard_result.blocked:
        # Guards imported the module under `strategies.{stem}`, which fired
        # `__init_subclass__` and registered the user's class. We MUST clean
        # the registry + sys.modules before writing the backup back — otherwise
        # the registry holds a pointer to a class that no longer matches the
        # file on disk.
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
            # Re-register the backup so we don't leave the user with a missing strategy.
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

    # [existing] Hot-reload ...
    ...

    return {
        "success": True,
        "errors": [],
        "path": f"strategies/{safe_name}",
        "test_output": test_result["output"],
        "guard_result": guard_result.to_payload(),
    }
```

**Same pattern for Hook 3** (portfolio/cross_factor/ml_alpha): on guard block, clean the appropriate registry (`PortfolioStrategy._registry` / `CrossSectionalFactor._registry` / etc.) using `_get_all_registries_for_kind(kind)`, then rollback file, then re-register the backup via `_reload_portfolio_code` best-effort.

**Hook point 2 — factor save flow (around line 711, between subprocess validation success and `_reload_factor_code`):**

Straight-line rollback (no exception). We check guards AFTER subprocess validation is confirmed `registered` non-empty, BEFORE `_reload_factor_code`. This keeps rollback logic in one place and avoids exception-ordering hazards with the existing `except Exception as e:` cleanup block.

```python
            # [existing] if not registered: raise ValueError("No Factor subclass found in code")
            # [existing guard placement: after registered check, before reload]

            # V2.19.0 guard framework.
            guard_result = _run_guards(safe_name, "factor", target_dir)
            if guard_result.blocked:
                # Rollback (inline — same pattern as the `except Exception` block below).
                # V2.12.2 codex: clean BOTH dicts to avoid zombie entries.
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
                    # Best-effort re-register the backup so user doesn't lose a working factor.
                    try:
                        _reload_factor_code(safe_name, target_dir)
                    except Exception as restore_err:
                        logger.warning(
                            "Factor rollback succeeded but re-register failed: %s",
                            restore_err,
                        )
                else:
                    target.unlink(missing_ok=True)
                return {
                    "success": False,
                    "errors": [f"Guard failed: {blk.guard_name}: {blk.message}"
                               for blk in guard_result.blocks],
                    "guard_result": guard_result.to_payload(),
                }

            # [existing] hot-reload the actual factor implementation
            if not _frozen_inprocess:
                _reload_factor_code(safe_name, target_dir)
                live = [k for k, v in Factor._registry.items() if v.__module__ == module_name]
                if not live:
                    raise ValueError(
                        f"Factor hot-reload succeeded but registry has no entries for {module_name}"
                    )
        except Exception as e:
            # [existing cleanup block — unchanged]
            ...
        return {"success": True, "path": str(target), "test_output": f"Factor saved. Registered: {registered}",
                "guard_result": guard_result.to_payload()}
```

**No new exception type needed.** The inline rollback pattern mirrors the existing `except Exception as e:` block and uses the same `backup` variable that is already in scope.

**Hook point 3 — portfolio / cross_factor / ml_alpha save flow (around line 786, between `_run_portfolio_contract_test` success and `_reload_portfolio_code`):**

```python
    # [existing] test_result = _run_portfolio_contract_test(safe_name, kind, target_dir)
    # [existing] if not test_result["passed"]: rollback + return

    # [NEW] Guard framework.
    guard_result = _run_guards(safe_name, kind, target_dir)
    if guard_result.blocked:
        # Guards imported the module under `{dirname}.{stem}`, registering the
        # user's class via __init_subclass__. Clean all relevant registries
        # (portfolio_strategy / cross_factor / ml_alpha all share the dual-dict
        # registry pattern per V2.12.2). Use the sandbox-local helper
        # `_sandbox_registries_for_kind` (NOT the one in ez/api/routes/code.py,
        # which would be a layer violation).
        stem = safe_name.replace(".py", "")
        module_name = f"{target_dir.name}.{stem}"
        with _reload_lock:
            for reg in _sandbox_registries_for_kind(kind):
                dirty = [k for k, v in reg.items() if v.__module__ == module_name]
                for k in dirty:
                    reg.pop(k, None)
            if module_name in sys.modules:
                del sys.modules[module_name]
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
            "errors": [f"Guard failed: {blk.guard_name}: {blk.message}"
                       for blk in guard_result.blocks],
            "test_output": test_result["output"],
            "guard_result": guard_result.to_payload(),
        }

    # [existing] hot-reload
    try:
        _reload_portfolio_code(safe_name, kind, target_dir)
    ...

    return {
        "success": True,
        "errors": [],
        "path": f"{target_dir.name}/{safe_name}",
        "test_output": test_result["output"],
        "guard_result": guard_result.to_payload(),
    }
```

**New helper `_sandbox_registries_for_kind` (module-private, added near the top of `sandbox.py`):**

```python
def _sandbox_registries_for_kind(kind: str) -> list[dict]:
    """Return all registry dicts that __init_subclass__ would populate for a kind.

    Mirrors `_get_all_registries_for_kind` in ez/api/routes/code.py but lives
    in the agent layer to avoid a layer violation (agent cannot import from api).
    Returns empty list for unknown kinds — caller will no-op.
    """
    if kind == "strategy":
        from ez.strategy.base import Strategy
        return [Strategy._registry]
    if kind == "factor":
        from ez.factor.base import Factor
        return [Factor._registry, Factor._registry_by_key]
    if kind in ("cross_factor", "ml_alpha"):
        from ez.portfolio.cross_sectional_factor import CrossSectionalFactor
        return [CrossSectionalFactor._registry, CrossSectionalFactor._registry_by_key]
    if kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy
        return [PortfolioStrategy._registry, PortfolioStrategy._registry_by_key]
    return []
```

**Why duplicate instead of import:** per the dependency flow in `CLAUDE.md`, `ez/agent/` must not import from `ez/api/`. The two helpers are small (10 lines each) and the shared logic is trivial. We could alternatively move `_get_all_registries_for_kind` down to `ez/agent/sandbox.py` or a new `ez/_registry_utils.py` module, but V1 keeps the duplication to minimize blast radius. The test `test_sandbox_registries_parity` (see Section 5.3 Test #11) asserts the two helpers return byte-identical structures.

**Threading / module reload hazard:** `load_user_class` uses `importlib.util.spec_from_file_location` with a **unique `module_name`** (`{dirname}.{stem}`). This is the same pattern the existing in-process fallback uses at sandbox.py:689-692. The module is inserted into `sys.modules` under this canonical key, which means it will be picked up by the subsequent `_reload_user_strategy()` / `_reload_factor_code()` / `_reload_portfolio_code()` call without re-parsing. No module-name collision is introduced because the names are already canonical for these directories.

### 4.10 API Changes

**`ez/api/routes/code.py` (`POST /api/code/save`)** — no structural change. The route already returns whatever dict `save_and_validate_code()` produces. The new `guard_result` key simply flows through. **Zero code changes to the route.**

**Backwards compatibility:** clients that don't know about `guard_result` see a new key but ignore it. Success/failure semantics are unchanged (`res.ok` still maps to `success=True` or HTTP 4xx).

### 4.11 Frontend Changes

**File: `web/src/components/CodeEditor.tsx`**

**State additions (top of component, after existing `testOutput` state):**

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

const [guardReport, setGuardReport] = useState<GuardReport | null>(null)
```

**In `save()` handler — capture `guard_result` from both success and error branches:**

```tsx
const save = async (overwrite = false) => {
  // ...existing code...
  try {
    const res = await api('/save', {...})
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
      // ...existing auto-retry logic...
      setStatus('保存失败')
      setErrors(errs)
      if (detail.test_output) setTestOutput(detail.test_output)
      if (detail.guard_result) setGuardReport(detail.guard_result)   // NEW
    }
  } catch (e: unknown) {
    setStatus(`Error: ${e instanceof Error ? e.message : String(e)}`)
  } finally { setSaving(false) }
}
```

**Reset on file load (add to existing file-load paths):**

```tsx
// In loadFile(), after setFilename/setCode: setGuardReport(null)
// In "new file" button handlers: setGuardReport(null)
```

**Status bar extension (replace lines 570-576):**

```tsx
{(status || errors.length > 0 || guardReport) && (
  <div className="px-3 py-1 text-xs border-b"
       style={{
         borderColor: 'var(--border)',
         backgroundColor: (errors.length || guardReport?.blocked) ? '#7f1d1d20'
                        : (guardReport?.n_warnings ? '#92400e20' : '#14532d20'),
       }}>
    {status && <div style={{ color: errors.length ? '#ef4444' : '#22c55e' }}>{status}</div>}
    {errors.map((e, i) => <div key={i} style={{ color: '#ef4444' }}>{e}</div>)}
    {guardReport && (
      <div className="flex items-center gap-2 mt-1">
        <span style={{ color: 'var(--text-secondary)' }}>代码守卫:</span>
        {guardReport.guards.map((g, i) => (
          <span key={i}
                title={g.message}
                style={{
                  color: g.severity === 'block' ? '#ef4444'
                       : g.severity === 'warn' ? '#f59e0b'
                       : '#22c55e',
                  fontWeight: 600,
                }}>
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

**Test output panel extension (extend lines 640-649 to also show guard details):**

```tsx
{(testOutput || (guardReport && (guardReport.n_blocks > 0 || guardReport.n_warnings > 0))) && (
  <div className="border-t overflow-auto"
       style={{ borderColor: 'var(--border)', maxHeight: '240px', backgroundColor: 'var(--bg-primary)' }}>
    <div className="flex justify-between items-center px-3 py-1">
      <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
        合约测试 + 代码守卫输出
      </span>
      <button onClick={() => { setTestOutput(''); setGuardReport(null) }}
              className="text-xs px-1.5 rounded hover:opacity-80"
              style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>✕</button>
    </div>
    {testOutput && (
      <pre className="px-3 pb-2 text-xs whitespace-pre-wrap"
           style={{ color: 'var(--text-primary)' }}>{testOutput}</pre>
    )}
    {guardReport && guardReport.guards
        .filter(g => g.severity !== 'pass')
        .map((g, i) => (
          <div key={i} className="px-3 pb-2 text-xs"
               style={{ color: g.severity === 'block' ? '#ef4444' : '#f59e0b' }}>
            <div style={{ fontWeight: 600 }}>
              [{g.severity.toUpperCase()}] {g.name} ({g.runtime_ms.toFixed(0)} ms)
            </div>
            <div style={{ whiteSpace: 'pre-wrap' }}>{g.message}</div>
          </div>
        ))}
  </div>
)}
```

**Zero new files. Zero new components. Minimal surface area.**

---

## 5. Testing Strategy

### 5.1 Test Directory Structure

```
tests/test_guards/                                  NEW test package
├── __init__.py
├── conftest.py                                     # Shared fixtures
├── test_guard_base.py                              # GuardContext, GuardResult, SuiteResult
├── test_mock_data.py                               # Mock data determinism
├── test_lookahead_guard.py                         # LookaheadGuard unit tests
├── test_nan_inf_guard.py                           # NaNInfGuard unit tests
├── test_weight_sum_guard.py                        # WeightSumGuard unit tests
├── test_non_negative_guard.py                      # NonNegativeWeightsGuard unit tests
├── test_determinism_guard.py                       # DeterminismGuard unit tests
├── test_guard_suite.py                             # GuardSuite orchestration
├── test_sandbox_integration.py                     # Sandbox save-flow integration
└── golden_bugs/                                    # Real historical bugs as regression tests
    ├── test_v1_dynamic_ef_lookahead.py             # v1 Dynamic EF lookahead bug
    └── test_mlalpha_purge_lookahead.py             # MLAlpha timedelta purge bug
```

### 5.2 Unit Tests (per guard)

**For each guard:** 5 test cases minimum:
1. **Clean pass** — good code passes the guard.
2. **Bug detected** — deliberately buggy code triggers the expected severity.
3. **Runtime bound** — guard completes in < 100 ms on mock data.
4. **User code raises** — guard handles it gracefully (returns block with message, no crash).
5. **Edge case** — e.g., warmup NaN allowed, empty dict handled, None output handled.

**LookaheadGuard extra tests (6 total):**
- Pass: factor using only `data.iloc[:-10].mean()` (past only)
- Block: factor using `data["close"].iloc[-1]` when called with full data (uses `t`, that's fine), BUT block if it does `data["close"].shift(-1)` (actual future read)
- Block: `CrossSectionalFactor` that accesses `panel[sym].loc[future_date]`
- Block: `PortfolioStrategy.generate_weights` that does `panel[sym].iloc[150:]` (whole future)
- Pass: `MLAlpha` with a Ridge that uses the proper `_build_training_panel` purge path
- Block: same MLAlpha but with `feature_fn` that calls `df.shift(-1)`

**NaNInfGuard extra tests (6 total):**
- Pass: clean factor, warmup NaNs allowed
- Block: `Factor` that does `np.log(data["close"] - data["close"])` → all zeros → log = -inf
- Block: `CrossSectionalFactor` that returns `{sym: np.nan}` unconditionally
- Block: `PortfolioStrategy` that returns `{sym: 1.0 / 0.0}`
- Pass: `MLAlpha` in warmup (returns empty dict, not block)
- Edge: output is empty dict → no violation (handled at GuardSuite level as pass)

**WeightSumGuard extra tests (5 total):**
- Pass: weights summing to 1.0 exactly
- Pass: weights summing to 0.5 (cash-heavy, still valid)
- Block: weights summing to 1.5 (over-leverage)
- Block: weights summing to -0.1 (net short)
- Block: different behavior at different dates (detect date-dependent bugs)

**NonNegativeWeightsGuard extra tests (4 total):**
- Pass: all weights ≥ 0
- Warn: one negative weight
- Warn: negative at only one of 5 target dates
- Pass: tolerance at -1e-10 (below threshold 1e-9)

**DeterminismGuard extra tests (5 total):**
- Pass: deterministic code (clean Ridge + seed)
- Warn: code using `random.random()` without seed
- Warn: code returning `dict(set([...]))` (set iteration non-deterministic)
- Pass: code with explicit `np.random.default_rng(42)`
- Edge: output is `None` in both runs → pass (both canonicalize to `<None>`)

### 5.3 Integration Tests

**File: `tests/test_guards/test_sandbox_integration.py`**

Tests the full `save_and_validate_code()` flow with guard integration. Uses real `_STRATEGIES_DIR` / `_FACTORS_DIR` / etc. via tmp_path fixtures.

**Test matrix (10 tests):**

| # | Kind | Bug | Expected |
|---|------|-----|----------|
| 1 | strategy | None (clean) | success=True, guard_result with all passes |
| 2 | factor | None (clean) | success=True, guard_result with all passes |
| 3 | factor | NaN in output | success=False, guard_result.blocked=True, file rolled back |
| 4 | portfolio_strategy | sum > 1.5 | success=False, blocked, file rolled back |
| 5 | portfolio_strategy | negative weight | success=True, guard_result.warnings[0] present |
| 6 | cross_factor | lookahead (reads future) | success=False, blocked |
| 7 | ml_alpha | clean Ridge | success=True, passes |
| 8 | strategy | unseeded random.random() | success=True, DeterminismGuard warns |
| 9 | strategy | guard itself raises | success=False, blocked with "guard bug" message |
| 10 | strategy | Overwrite a clean file with a buggy one | original file restored, success=False |
| 11 | N/A | `_sandbox_registries_for_kind` vs `_get_all_registries_for_kind` parity | Both return the same registry dict objects for each kind |
| 12 | strategy | Guard block on new file (no backup) | File deleted, registry cleaned |
| 13 | strategy | Guard block on overwrite | Original file restored, original class back in registry |
| 14 | factor | Guard block on overwrite | Original factor restored, `_registry` + `_registry_by_key` cleaned |

### 5.4 Golden Bug Tests

**File: `tests/test_guards/golden_bugs/test_v1_dynamic_ef_lookahead.py`**

Reproduces the v1 Dynamic EF look-ahead bug as a regression defense. The bug: weight optimizer used `close[t]` while trading used `close[t-1]`, reading one day into the future. We encode it as a minimal `CrossSectionalFactor` that exhibits the same pattern and verify `LookaheadGuard` catches it.

```python
"""Golden bug 1: v1 Dynamic EF lookahead.

Historical context (see validation/phase_o_nested_oos.py and
validation/report_charts/降回撤研究_v5.md):
  The original v1 Dynamic EF implementation computed weights using
  prices from date t, but the 'trading' happened at t+1. This silently
  inflated Sharpe by ~0.4 in backtest and would have destroyed live
  capital. Codex caught it during round-2 review.

This test encodes a minimal reproduction as a CrossSectionalFactor and
asserts LookaheadGuard blocks it.
"""
from __future__ import annotations
import pandas as pd
from datetime import datetime

from ez.testing.guards import (
    GuardContext, LookaheadGuard, build_mock_panel, target_date_at,
    GuardSeverity,
)


class _V1DynamicEFBugRepro:
    """Minimal reproduction of the v1 Dynamic EF lookahead bug.

    BUG: the 'weight optimizer' reads future prices via `panel[sym].iloc[target_idx+1]`.
    """
    warmup_period = 0

    def compute(self, panel: dict, target_date: datetime) -> dict:
        result = {}
        for sym, df in panel.items():
            # BUG: use future price (this is the lookahead).
            target_idx = df.index.get_indexer([target_date], method="nearest")[0]
            if target_idx + 1 < len(df):
                future_price = df["close"].iloc[target_idx + 1]  # ← future data
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
        file_path=None,
        kind="cross_factor",
        user_class=_V1DynamicEFBugRepro,
    )
    result = guard.check(ctx)
    assert result.severity == GuardSeverity.BLOCK
    assert "future data" in result.message.lower() or "shuffled" in result.message.lower()
```

**File: `tests/test_guards/golden_bugs/test_mlalpha_purge_lookahead.py`**

Reproduces the MLAlpha Phase 1 round 1 `timedelta(days=N)` calendar purge bug. The bug: labels crossed weekends because purge used calendar days instead of trading days.

```python
"""Golden bug 2: MLAlpha timedelta purge lookahead.

Historical context: MLAlpha V1 round 1 used `timedelta(days=N)` for
label purge, which lets labels cross weekends and point at prediction
windows. Fixed in round 2 by using positional iloc[:-purge_bars]. This
test encodes a minimal factor that reads future data in a way the old
purge would have missed and asserts LookaheadGuard catches it.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from ez.testing.guards import GuardContext, LookaheadGuard, GuardSeverity


class _CalendarPurgeLookaheadRepro:
    """Minimal reproduction: factor that uses `close` 5 calendar days later,
    which passes a 5-day calendar purge but contains up to 2 trading days
    of future information (due to weekends)."""
    warmup_period = 0

    def compute(self, panel: dict, target_date: datetime) -> dict:
        result = {}
        for sym, df in panel.items():
            # Ask for the close 5 CALENDAR days later (bug).
            target_plus_5cal = target_date + timedelta(days=5)
            mask = df.index >= target_plus_5cal
            if mask.any():
                future_close = float(df.loc[mask, "close"].iloc[0])
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
        file_path=None,
        kind="cross_factor",
        user_class=_CalendarPurgeLookaheadRepro,
    )
    result = guard.check(ctx)
    assert result.severity == GuardSeverity.BLOCK
```

### 5.5 Performance Tests

**File: `tests/test_guards/test_guard_suite.py::test_runtime_budget_under_500ms`**

```python
def test_runtime_budget_under_500ms():
    """Full suite on a trivial clean factor must complete in < 500 ms."""
    class _CleanFactor:
        warmup_period = 20
        def compute(self, df):
            return df["close"].rolling(20).mean()

    ctx = GuardContext(
        filename="clean.py",
        module_name="test_clean",
        file_path=None,
        kind="factor",
        user_class=_CleanFactor,
    )
    suite = GuardSuite()
    result = suite.run(ctx)
    assert not result.blocked
    assert result.total_runtime_ms < 500, f"Suite too slow: {result.total_runtime_ms} ms"
```

### 5.6 Test Count Estimate

| Module | Tests |
|--------|-------|
| test_guard_base.py | 6 |
| test_mock_data.py | 4 |
| test_lookahead_guard.py | 6 |
| test_nan_inf_guard.py | 6 |
| test_weight_sum_guard.py | 5 |
| test_non_negative_guard.py | 4 |
| test_determinism_guard.py | 5 |
| test_guard_suite.py | 5 (including runtime bound) |
| test_sandbox_integration.py | 14 |
| golden_bugs/test_v1_dynamic_ef_lookahead.py | 1 |
| golden_bugs/test_mlalpha_purge_lookahead.py | 1 |
| **Total** | **57** |

Target: 2265 → 2322 (+57). (Baseline 2265 = current V2.18.1 test count per CLAUDE.md.)

---

## 6. Acceptance Criteria

### 6.1 Correctness

- [ ] All 5 guards implemented and exported from `ez.testing.guards`.
- [ ] `GuardSuite` default includes all 5.
- [ ] Sandbox integration at all 3 hook points (strategy, factor, portfolio flow).
- [ ] Tier 1 block causes file rollback matching contract-test rollback semantics.
- [ ] Tier 2 warn surfaces in `guard_result.n_warnings` without blocking.
- [ ] `guard_result` payload present in `save_and_validate_code()` return dict on both success and failure paths.
- [ ] Frontend `CodeEditor.tsx` surfaces guard results in status bar + test output panel.
- [ ] 57 new tests pass. Existing 2265 tests unaffected.
- [ ] Both golden bug tests trigger expected block verdict.

### 6.2 Performance

- [ ] `GuardSuite.run()` completes in < 500 ms on mock data for any kind.
- [ ] Individual guard `runtime_ms` < 150 ms on mock data.
- [ ] `build_mock_panel()` uses `@lru_cache` — rebuild cost measured once per process.

### 6.3 Defensive Coding

- [ ] All guards handle `user_class is None` gracefully (return block/warn with explanatory message).
- [ ] All guards wrap user code invocation in `try/except` (never crash the sandbox).
- [ ] `GuardSuite.run()` wraps every guard in `try/except` (guard bugs don't crash the sandbox).
- [ ] `load_user_class()` handles import failure gracefully (returns `(None, error_message)`).

### 6.4 Non-Regression

- [ ] `pytest tests/` passes 2322 / 2322 (was 2265).
- [ ] `scripts/benchmark.py` runtime not regressed > 2 %.
- [ ] `./scripts/start.sh` boots cleanly, web UI renders without console errors.

### 6.5 Documentation

- [ ] `ez/testing/guards/__init__.py` docstring summarizes the 5 guards and their tiers.
- [ ] `CLAUDE.md` top-level version section adds V2.19.0 entry.
- [ ] `web/CLAUDE.md` and `ez/agent/CLAUDE.md` mention guard integration where relevant.

---

## 7. Non-Goals / Out of Scope

- **Multi-period / multi-regime guards.** V1 uses a single mock data fixture. Regime-specific tests are V2.
- **User-configurable guard thresholds.** V1 ships with fixed tolerances. Configurability is a V2 feature once we see false positive rate data.
- **Per-file bypass flag.** Rejected by design (Section 3.4).
- **New UI components.** All frontend changes are extensions to existing `CodeEditor.tsx` panels.
- **New API routes.** `POST /api/code/save` return dict is extended; no new routes.
- **Runtime guards** (executed during live backtest). V1 is save-time only. V2 may add runtime verification hooks.
- **C++ guard implementations.** Python is fast enough for the 500 ms budget on mock data.
- **Guard result persistence to DB.** Guard results are ephemeral (per-save). Not persisted.
- **Historical re-scan.** V1 does not retroactively run guards on already-saved files. The user can trigger a re-scan by re-saving (no-op write + save).
- **Support for `StrategyEnsemble` code files.** Ensemble has no dedicated save path; its sub-strategies are guarded individually when saved.

---

## 8. Risk Analysis

### 8.1 False Positive Risk

| Guard | FP scenario | Mitigation |
|-------|-------------|------------|
| LookaheadGuard | Legitimate stochastic factor (e.g., random features) shows differences in shuffled run | Deterministic mock data + `np.random.default_rng(42)` seed + `DeterminismGuard` checks separately. If factor is inherently stochastic, document that it may conflict with LookaheadGuard. V1 accepts this as known limitation. |
| NaNInfGuard | Factor that legitimately NaNs at the last bar (e.g., rolling window with `center=True`) | Honor `warmup_period`. If factor sets `warmup_period=0` but is actually noisy, that's a user bug. |
| WeightSumGuard | Strategy that intentionally leaves cash | Sum < 1 is fine. Only sum > 1.001 or < -0.001 blocks. |
| NonNegativeWeightsGuard | Raw alpha before clipping | Warn-only. User can ignore. |
| DeterminismGuard | ML model with BLAS threading non-determinism | Warn-only. User advised to set `OMP_NUM_THREADS=1`. |

**First-principles approach:** start strict, loosen based on real feedback. Tolerances (1e-9, 1e-12) are tight because we'd rather reject false positives manually than ship a look-the-other-way guard.

### 8.2 False Negative Risk

**LookaheadGuard cannot detect:**
- Code that reads future data but happens to produce the same output at `t=150` (extremely unlikely — would require outputting a constant)
- Code that reads future data only at `t > 150` (we only test at `t = 150`)

**Mitigation:** future work can randomize the cutoff index, but V1 uses fixed `CUTOFF_IDX = 150` for determinism and speed. The more important hit rate is the well-known bug patterns, which this covers.

**NaNInfGuard cannot detect:**
- Output that is finite on the mock data but NaN on real data (coincidentally clean mock)

**Mitigation:** mock data has both positive and negative returns, volatile periods, etc. Not perfect but covers division-by-zero and log-negative patterns.

### 8.3 Performance Risk

**Worst case:** complex MLAlpha with expensive `feature_fn` runs 2x (determinism) + 2x (lookahead) = 4x on full 200 bars + 1x for NaNInf = 5 invocations. At ~50 ms per invocation → 250 ms. Within budget but tight.

**Mitigation:** `build_mock_panel()` uses `@lru_cache` so the panel is built once per process. Individual guards short-circuit on guard-bug exceptions. If budget exceeded, V2 will add an optional `fast_mode=True` flag that runs only Tier 1 guards.

### 8.4 Integration Risk

**Sandbox save path is hot.** Every user save goes through it. A guard bug crashes the save flow.

**Mitigation:**
- `GuardSuite.run()` wraps every guard in `try/except` (guard bugs become "guard bug, not user bug" block, not a crash).
- Integration tests verify sandbox rollback semantics match contract-test semantics exactly.
- Rollback is atomic: file written back to original (or deleted) BEFORE return.

### 8.5 Backwards Compatibility

**API:** return dict gets a new key `guard_result`. Old clients ignore it.

**Frontend:** new UI elements are additive — if `guardReport === null`, status bar renders as before.

**Disk state:** no new files saved to disk on failure (rollback is clean).

**Registry state:** guards run AFTER contract test and BEFORE hot-reload. If guard blocks, hot-reload does not run, so registry is unchanged. No zombie state possible.

---

## 9. Rollout Plan

### 9.1 Phases

**Phase 1 — Framework + 1 guard:** implement `base.py`, `suite.py`, `mock_data.py`, `lookahead.py`, integrate hook 1 (strategy), add tests. Ship as internal preview.

**Phase 2 — Remaining 4 guards:** `nan_inf.py`, `weight_sum.py`, `non_negative.py`, `determinism.py`, hook 2 (factor), hook 3 (portfolio/cross_factor/ml_alpha), full test coverage.

**Phase 3 — Frontend + docs:** `CodeEditor.tsx` extensions, `CLAUDE.md` updates, `web/CLAUDE.md` updates, `docs/superpowers/plans/2026-04-11-guard-framework-implementation.md` updates with any in-flight changes.

**Phase 4 — Golden bug tests:** the two historical bug regression tests, final `pytest` + benchmark run.

**Phase 5 — Ship:** V2.19.0 tag, code review, release.

### 9.2 Checkpoints

- After Phase 1: guard tests run, no regression in existing 2229 tests.
- After Phase 2: full 53 new tests pass. `scripts/benchmark.py` runtime budget met.
- After Phase 3: frontend manual smoke test — create strategy, introduce each bug class, verify UI.
- After Phase 4: golden bug tests pass. `superpowers:code-reviewer` review.
- After Phase 5: V2.19.0 tag.

### 9.3 Revert Plan

If a guard produces high false positive rate in practice, revert via:
1. Remove the problematic guard class from `default_guards()` in `suite.py`.
2. Its tests become skipped (via `pytest.skip`).
3. The guard stays in the module for future fix / V2 rework.

No database migration, no API change, no state corruption risk. Revert is a single commit.

---

## 10. Work Estimate

| Area | Lines | Tests | Sessions |
|------|-------|-------|----------|
| Core framework (base, suite, mock_data) | ~400 | 10 | 0.5 |
| LookaheadGuard | ~250 | 6 | 0.5 |
| NaNInfGuard | ~180 | 6 | 0.3 |
| WeightSumGuard | ~150 | 5 | 0.3 |
| NonNegativeWeightsGuard | ~120 | 4 | 0.2 |
| DeterminismGuard | ~180 | 5 | 0.3 |
| Sandbox integration (3 hooks + `_sandbox_registries_for_kind`) | ~180 | 14 | 0.5 |
| Frontend | ~100 | 0 | 0.3 |
| Golden bug tests | ~150 | 2 | 0.2 |
| Docs (CLAUDE.md updates) | ~80 | 0 | 0.1 |
| Review + polish | — | — | 0.5 |
| **Total** | **~1,790** | **57** | **~4 sessions** |

---

## 11. Open Questions (None Required for Approval)

The following will be decided during implementation:

1. **Exact tolerance for `_compare_scalar`** — 1e-9 matches codebase norm but might be tight for floating-point summation of many terms. Can be loosened during Phase 1 if observed in tests.
2. **Whether `SuiteResult.to_payload()` truncates `details["output_a_sample"]`** — currently hardcoded 300 chars. Safe for JSON payload size.
3. **Guard order in `default_guards()`** — currently block-first, warn-last. This means if Lookahead blocks, Determinism still runs (warn level gets measured). Alternative: short-circuit on first block. V1 keeps full run for maximum signal.
4. **Ml_alpha NaN edge case** — MLAlpha warmup period is effectively `max(train_window, feature_warmup_days)` but the Guard only reads `getattr(inst, 'warmup_period', 0)`. For ml_alpha kind the NaNInfGuard scans dicts (cross-sectional output), not the series, so warmup is not in scope. Verified in Section 4.4 `_scan_dict` path.

None of these change the public contract.

---

## 12. References

- Sandbox save flow: `ez/agent/sandbox.py:383-456` (strategy), `:624-751` (factor), `:755-801` (portfolio/cross_factor/ml_alpha)
- Existing contract tests: `ez/agent/sandbox.py:812-834` (portfolio shape check with `>=0` and `<=1.001`)
- Frontend save: `web/src/components/CodeEditor.tsx:322-358`
- Frontend status panel: `web/src/components/CodeEditor.tsx:570-576`
- Frontend test output panel: `web/src/components/CodeEditor.tsx:639-649`
- Historical lookahead bugs: `validation/report_charts/降回撤研究_v5.md` (v1 Dynamic EF), `CLAUDE.md` V2.13 Phase 1 round 1 (MLAlpha purge)
- Guard framework design precedent: none internally; inspired by pytest `@pytest.mark` + pre-commit hook patterns

---

**End of spec.**
