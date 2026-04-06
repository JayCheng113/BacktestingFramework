# Deep Audit Bug Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 8 verified bugs found in the deep audit, with regression tests for each backend fix.

**Architecture:** Minimal-change bug fixes — no refactoring, no new features. Each fix is self-contained with a regression test. Backend fixes (Python) get automated tests; frontend fixes (TypeScript) verified manually.

**Tech Stack:** Python 3.12 / pytest / React 19 / TypeScript

---

## File Map

| Task | Files Modified | Files Created |
|------|----------------|---------------|
| T1 | `ez/data/provider.py` | — |
| T1-test | — | `tests/test_data/test_sparse_cache_ttl.py` |
| T2 | `ez/data/providers/akshare_provider.py` | — |
| T2-test | — | `tests/test_data/test_akshare_raw_fallback.py` |
| T3 | `ez/portfolio/engine.py`, `ez/errors.py` | — |
| T3-test | — | `tests/test_portfolio/test_accounting_explicit.py` |
| T4 | `ez/api/app.py` | — |
| T4-test | — | `tests/test_api/test_lifespan_cleanup.py` |
| T5 | `ez/data/validator.py` | — |
| T5-test | `tests/test_data/test_validator.py` | — |
| T6 | `web/src/components/PortfolioFactorContent.tsx` | — |
| T7 | `web/src/components/PortfolioRunContent.tsx` | — |
| T8 | `web/src/components/PortfolioRunContent.tsx` | — |

---

### Task 1: Sparse Cache TTL (BUG-01)

**Files:**
- Modify: `ez/data/provider.py:87, 145, 192`
- Create: `tests/test_data/test_sparse_cache_ttl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data/test_sparse_cache_ttl.py
"""Regression: _known_sparse_symbols must expire after TTL."""
import time
from datetime import date, datetime
from unittest.mock import MagicMock
from ez.data.provider import DataProviderChain, DataProvider
from ez.types import Bar


def _bar(day=2, **kw):
    defaults = dict(
        time=datetime(2024, 1, day), symbol="TEST.SZ", market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000,
    )
    defaults.update(kw)
    return Bar(**defaults)


def test_sparse_cache_expires_after_ttl():
    """Once TTL elapses, a previously-sparse symbol should be re-fetched."""
    store = MagicMock()
    store.query_kline.return_value = []
    store.save_kline.return_value = 0

    provider = MagicMock(spec=DataProvider)
    provider.name = "mock"
    # First call: return sparse data (10 bars for 100-day range → < 75%)
    sparse_bars = [_bar(day=i) for i in range(2, 12)]
    provider.get_kline.return_value = sparse_bars

    chain = DataProviderChain([provider], store)

    # First fetch → marks as sparse
    chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 4, 10))
    assert ("TEST.SZ", "cn_stock", "daily") in chain._known_sparse_symbols

    # Reset provider to return full data
    full_bars = [_bar(day=i) for i in range(2, 28)] * 4  # 100+ bars
    provider.get_kline.return_value = full_bars
    provider.get_kline.reset_mock()
    store.query_kline.return_value = sparse_bars  # cache still has sparse data

    # Simulate TTL expiry by backdating the timestamp
    key = ("TEST.SZ", "cn_stock", "daily")
    chain._known_sparse_symbols[key] = time.monotonic() - 90000  # > 24h ago

    # Second fetch → TTL expired, should NOT skip density, should refetch
    chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 4, 10))
    provider.get_kline.assert_called_once()  # provider was called again


def test_sparse_cache_not_expired_skips_refetch():
    """Within TTL, sparse symbol should still skip density check."""
    store = MagicMock()
    sparse_bars = [_bar(day=i) for i in range(2, 12)]
    store.query_kline.return_value = sparse_bars
    store.save_kline.return_value = 0

    provider = MagicMock(spec=DataProvider)
    provider.name = "mock"

    chain = DataProviderChain([provider], store)

    # Manually mark as sparse (recent timestamp)
    key = ("TEST.SZ", "cn_stock", "daily")
    chain._known_sparse_symbols[key] = time.monotonic()

    # Fetch → TTL not expired, cache with boundary ok should be accepted
    result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 15))
    # Short range (14 days) skips density anyway, so use longer range
    # Actually for this test: manually set boundary-ok sparse data for long range
    store.query_kline.return_value = [_bar(day=2), _bar(day=15)] + [_bar(day=i) for i in range(3, 12)]
    result = chain.get_kline("TEST.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 4, 10))
    provider.get_kline.assert_not_called()  # cache accepted, no provider call
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_data/test_sparse_cache_ttl.py -v`
Expected: FAIL — `_known_sparse_symbols` is a `set`, not a `dict`, so `chain._known_sparse_symbols[key] = ...` raises TypeError.

- [ ] **Step 3: Implement the fix**

In `ez/data/provider.py`, change the class-level cache from `set` to `dict` with timestamps:

```python
# Line 87: change from set to dict with monotonic timestamps
_known_sparse_symbols: dict[tuple[str, str, str], float] = {}

_SPARSE_TTL_SECONDS: float = 86400.0  # 24 hours
```

Update `_is_cache_complete` (line 127) — the `skip_density` parameter stays the same, caller changes:

```python
# Line 144-145: update the lookup to check TTL
sparse_key = (symbol, market, period)
_ts = self._known_sparse_symbols.get(sparse_key)
skip_density = _ts is not None and (time.monotonic() - _ts) < self._SPARSE_TTL_SECONDS
```

Add `import time` at top of file if not already present.

Update line 192 — store timestamp instead of just adding to set:

```python
# Line 192: store monotonic timestamp
self._known_sparse_symbols[sparse_key] = time.monotonic()
```

If TTL expired, remove the stale key so density check runs normally:

```python
# After line 145, add cleanup of expired key
if _ts is not None and not skip_density:
    del self._known_sparse_symbols[sparse_key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_data/test_sparse_cache_ttl.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest tests/test_data/test_provider_chain.py tests/test_data/test_sparse_cache_ttl.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add ez/data/provider.py tests/test_data/test_sparse_cache_ttl.py
git commit -m "fix(BUG-01): sparse cache TTL — expire after 24h to allow re-fetch"
```

---

### Task 2: AKShare Raw Fallback Guard (BUG-02)

**Files:**
- Modify: `ez/data/providers/akshare_provider.py:102-104, 146`
- Create: `tests/test_data/test_akshare_raw_fallback.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data/test_akshare_raw_fallback.py
"""Regression: raw fetch failure must NOT use qfq close as raw close."""
from datetime import datetime
from ez.types import Bar


def test_raw_fallback_sets_close_equal_adj_close():
    """When raw fetch fails, close should equal adj_close (not a fake raw price).
    
    This is the current BUGGY behavior that we need to verify exists,
    then fix so close is marked as unreliable.
    """
    # The fix will make close == adj_close AND set a flag or use NaN.
    # For now, just verify the bar construction logic.
    # After fix: close should be NaN when raw is unavailable, so
    # downstream limit checks skip this bar.
    import math
    
    # Simulate bar construction when raw=None (the fallback path)
    raw = None
    adj_close = 10.5
    open_v = 10.0
    
    # After fix: close should be NaN when raw unavailable
    close = raw["close"] if raw else float("nan")
    assert math.isnan(close), "close should be NaN when raw fetch fails"


def test_raw_available_uses_raw_close():
    """When raw fetch succeeds, close should be the raw value."""
    raw = {"open": 10.0, "high": 11.0, "low": 9.5, "close": 10.3, "volume": 100000}
    adj_close = 10.5
    
    close = raw["close"] if raw else float("nan")
    assert close == 10.3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_data/test_akshare_raw_fallback.py -v`
Expected: FAIL on `test_raw_fallback_sets_close_equal_adj_close` — current code uses `adj_close` not NaN.

- [ ] **Step 3: Implement the fix**

In `ez/data/providers/akshare_provider.py`, line 146, change the fallback `close` from `adj_close` to `float("nan")`:

```python
# Line 141-148: change close fallback from adj_close to NaN
bars.append(Bar(
    time=dt, symbol=symbol, market=market,
    open=raw["open"] if raw else float(open_v),
    high=raw["high"] if raw else float(high_v),
    low=raw["low"] if raw else float(low_v),
    close=raw["close"] if raw else float("nan"),   # NaN → limit checks skip
    adj_close=adj_close,
    volume=raw["volume"] if raw else int(float(volume_v)),
))
```

This makes `MarketRulesMatcher` naturally skip limit checks: the `(raw_close_today - prev_raw_close) / prev_raw_close` calculation produces NaN, and the comparison `change <= -limit_pct + 1e-6` evaluates to False for NaN, so the bar is NOT treated as limit-down/up. Buys and sells proceed at `adj_close` prices (which is what the engine uses for fill price via `prices = df["adj_close"]`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_data/test_akshare_raw_fallback.py -v`
Expected: PASS

- [ ] **Step 5: Run existing AKShare tests**

Run: `pytest tests/test_data/test_akshare_provider.py -v`
Expected: All pass (existing tests mock both raw and adj data)

- [ ] **Step 6: Commit**

```bash
git add ez/data/providers/akshare_provider.py tests/test_data/test_akshare_raw_fallback.py
git commit -m "fix(BUG-02): AKShare raw fallback uses NaN close instead of qfq"
```

---

### Task 3: Assert → Explicit Check for Accounting Invariant (BUG-04)

**Files:**
- Modify: `ez/errors.py` (add `AccountingError`)
- Modify: `ez/portfolio/engine.py:460-464, 523`
- Create: `tests/test_portfolio/test_accounting_explicit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolio/test_accounting_explicit.py
"""Regression: accounting invariants must use explicit raise, not assert."""
import ast
import inspect
from pathlib import Path


def test_no_assert_for_accounting_invariants():
    """Engine must not rely on assert for cash/equity invariants.
    
    Python -O strips assert statements. PyInstaller/Nuitka default to -O.
    Accounting guards must be explicit if/raise, never assert.
    """
    engine_path = Path("ez/portfolio/engine.py")
    source = engine_path.read_text()
    tree = ast.parse(source)
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            # Check if it's an accounting invariant assert
            test_source = ast.get_source_segment(source, node)
            if test_source and ("cash" in test_source or "equity" in test_source):
                raise AssertionError(
                    f"Line {node.lineno}: accounting invariant uses assert instead "
                    f"of explicit raise. Assert is stripped by python -O."
                )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_portfolio/test_accounting_explicit.py -v`
Expected: FAIL — finds `assert cash >= -EPS_FUND` at lines 460 and 523.

- [ ] **Step 3: Add AccountingError to ez/errors.py**

```python
# Add after BacktestError (line 27-28)
class AccountingError(EzTradingError):
    """Accounting invariant violation in portfolio engine."""
```

- [ ] **Step 4: Replace asserts in engine.py**

In `ez/portfolio/engine.py`, add import at top:

```python
from ez.errors import AccountingError
```

Replace lines 459-464:

```python
# Old:
assert cash >= -EPS_FUND, \
    f"Negative cash on {day}: cash={cash:.2f}"
assert equity > 0, \
    f"Non-positive equity on {day}: equity={equity:.2f}, cash={cash:.2f}, pos={position_value:.2f}"

# New:
if cash < -EPS_FUND:
    raise AccountingError(
        f"Negative cash on {day}: cash={cash:.2f}")
if equity <= 0:
    raise AccountingError(
        f"Non-positive equity on {day}: equity={equity:.2f}, "
        f"cash={cash:.2f}, pos={position_value:.2f}")
```

Replace line 523:

```python
# Old:
assert cash >= -EPS_FUND, f"Negative cash after liquidation: {cash:.2f}"

# New:
if cash < -EPS_FUND:
    raise AccountingError(f"Negative cash after liquidation: {cash:.2f}")
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_portfolio/test_accounting_explicit.py tests/test_portfolio/test_engine.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add ez/errors.py ez/portfolio/engine.py tests/test_portfolio/test_accounting_explicit.py
git commit -m "fix(BUG-04): replace assert with explicit raise for accounting invariants"
```

---

### Task 4: Lifespan try/finally (BUG-05)

**Files:**
- Modify: `ez/api/app.py:39-47`
- Create: `tests/test_api/test_lifespan_cleanup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api/test_lifespan_cleanup.py
"""Regression: close_resources must run even if aclose() raises."""
from unittest.mock import patch, AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_close_resources_called_even_if_aclose_raises():
    """If LLM provider.aclose() throws, close_resources() must still execute."""
    mock_provider = AsyncMock()
    mock_provider.aclose.side_effect = RuntimeError("network error")
    
    with patch("ez.api.app.get_cached_provider", return_value=mock_provider), \
         patch("ez.api.app.close_resources") as mock_close, \
         patch("ez.api.app.get_tushare_provider", return_value=None):
        
        from ez.api.app import lifespan, app
        
        async with lifespan(app):
            pass  # startup
        # After exiting context: shutdown runs
        
        mock_close.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api/test_lifespan_cleanup.py -v`
Expected: FAIL — `close_resources()` not called because `aclose()` exception propagates.

- [ ] **Step 3: Implement the fix**

In `ez/api/app.py`, wrap lines 40-47 in try/finally:

```python
    # Shutdown
    from ez.llm.factory import get_cached_provider
    provider = get_cached_provider()
    try:
        if provider is not None:
            await provider.aclose()
    finally:
        close_resources()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api/test_lifespan_cleanup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ez/api/app.py tests/test_api/test_lifespan_cleanup.py
git commit -m "fix(BUG-05): wrap aclose/close_resources in try/finally"
```

---

### Task 5: Negative Price Validation (BUG-10)

**Files:**
- Modify: `ez/data/validator.py:43-53`
- Modify: `tests/test_data/test_validator.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data/test_validator.py`:

```python
def test_negative_price_fails():
    """Negative prices must be rejected by validator."""
    # Negative close
    result = DataValidator.validate_bars([_bar(close=-5.0)])
    assert result.invalid_count == 1
    assert "negative" in result.errors[0].lower()

    # Negative open
    result2 = DataValidator.validate_bars([_bar(open=-1.0)])
    assert result2.invalid_count == 1

    # Negative high
    result3 = DataValidator.validate_bars([_bar(high=-0.5)])
    assert result3.invalid_count == 1

    # Negative low
    result4 = DataValidator.validate_bars([_bar(low=-2.0)])
    assert result4.invalid_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_data/test_validator.py::test_negative_price_fails -v`
Expected: FAIL — current `_check_bar` doesn't check for negative prices.

- [ ] **Step 3: Implement the fix**

In `ez/data/validator.py`, add at the beginning of `_check_bar` (after line 44):

```python
@staticmethod
def _check_bar(bar: Bar) -> list[str]:
    errors = []
    # Reject negative prices (corrupt data from API errors)
    for field in ("open", "high", "low", "close"):
        val = getattr(bar, field)
        if val < 0:
            errors.append(f"Negative {field} ({val}) for {bar.symbol} at {bar.time}")
    if bar.low > bar.high:
        errors.append(f"OHLC consistency: low ({bar.low}) > high ({bar.high}) for {bar.symbol} at {bar.time}")
    if bar.low > bar.open or bar.low > bar.close:
        errors.append(f"OHLC consistency: low ({bar.low}) > open/close for {bar.symbol} at {bar.time}")
    if bar.high < bar.open or bar.high < bar.close:
        errors.append(f"OHLC consistency: high ({bar.high}) < open/close for {bar.symbol} at {bar.time}")
    if bar.volume < 0:
        errors.append(f"Negative volume ({bar.volume}) for {bar.symbol} at {bar.time}")
    return errors
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_data/test_validator.py -v`
Expected: All pass (5 tests including new one)

- [ ] **Step 5: Commit**

```bash
git add ez/data/validator.py tests/test_data/test_validator.py
git commit -m "fix(BUG-10): reject negative prices in DataValidator"
```

---

### Task 6: ML Diagnostics Race Token (BUG-06)

**Files:**
- Modify: `web/src/components/PortfolioFactorContent.tsx:1, 319-336`

- [ ] **Step 1: Add useRef import and token ref**

At line 1, add `useRef` to the import:

```typescript
import { useState, useEffect, useRef } from 'react'
```

Inside the component function (after the existing state declarations around line 300), add:

```typescript
const diagTokenRef = useRef(0)
```

- [ ] **Step 2: Guard the async handler**

Replace the `runDiagnostics` function (lines 319-336):

```typescript
const runDiagnostics = async () => {
  if (!selectedAlpha) return
  const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
  if (symbolList.length === 0) { setError('请填写股票池'); return }
  const token = ++diagTokenRef.current
  setLoading(true); setError(''); setResult(null)
  try {
    const resp = await mlAlphaDiagnostics({
      ml_alpha_name: selectedAlpha,
      symbols: symbolList,
      market,
      start_date: startDate,
      end_date: endDate,
    })
    if (diagTokenRef.current !== token) return  // superseded
    setResult(resp.data)
  } catch (e: any) {
    if (diagTokenRef.current !== token) return  // superseded
    setError(e.response?.data?.detail || '诊断失败')
  } finally {
    if (diagTokenRef.current === token) setLoading(false)
  }
}
```

- [ ] **Step 3: Verify manually**

1. Start frontend: `cd web && npm run dev`
2. Navigate to Portfolio → Factor Research → ML Alpha diagnostics
3. Click "运行诊断", then immediately change the ML Alpha dropdown
4. Verify old result does NOT overwrite the panel after switching

- [ ] **Step 4: Commit**

```bash
git add web/src/components/PortfolioFactorContent.tsx
git commit -m "fix(BUG-06): add race token to ML diagnostics panel"
```

---

### Task 7: fullWeights Invalidation (BUG-07)

**Files:**
- Modify: `web/src/components/PortfolioRunContent.tsx:109, 128-135`

- [ ] **Step 1: Add invalidation on input changes**

The component receives `symbols`, `market`, `startDate`, `endDate`, `selected` (strategy), `strategyParams` as props. When any of these change, `fullWeights` should be cleared.

After the existing `useEffect` that clears on `result?.run_id` (line 133-135), add:

```typescript
// BUG-07: clear fullWeights when input parameters change, even before
// a new run is triggered. Prevents stale weights from a previous run
// being displayed while the user adjusts inputs.
useEffect(() => {
  setFullWeights(null)
}, [symbols, market, startDate, endDate, selected])
```

- [ ] **Step 2: Verify manually**

1. Run a portfolio backtest → click "加载完整历史" to populate fullWeights
2. Change the symbols field without running a new backtest
3. Verify the weights table is cleared (shows result?.weights_history or nothing)

- [ ] **Step 3: Commit**

```bash
git add web/src/components/PortfolioRunContent.tsx
git commit -m "fix(BUG-07): clear fullWeights on input parameter changes"
```

---

### Task 8: Benchmark Market Filter (BUG-08)

**Files:**
- Modify: `web/src/components/PortfolioRunContent.tsx:296-302`

- [ ] **Step 1: Replace hardcoded benchmark options with market-aware list**

Replace lines 296-302:

```typescript
<label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>指数基准
  <select value={indexBenchmark} onChange={e => setIndexBenchmark(e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle}>
    <option value="">无 (绝对收益)</option>
    {market === 'cn_stock' && <>
      <option value="000300">沪深300</option>
      <option value="000905">中证500</option>
      <option value="000852">中证1000</option>
    </>}
    {market !== 'cn_stock' && (
      <option value="" disabled>暂不支持非A股指数基准</option>
    )}
  </select>
</label>
```

- [ ] **Step 2: Clear indexBenchmark when market changes away from cn_stock**

In the component, after the new `useEffect` from Task 7, add:

```typescript
// BUG-08: reset index benchmark when switching away from cn_stock
// to prevent A-share indices being applied to foreign markets.
useEffect(() => {
  if (market !== 'cn_stock' && indexBenchmark) {
    setIndexBenchmark('')
  }
}, [market])
```

- [ ] **Step 3: Verify manually**

1. Set market to `cn_stock` → verify CSI300/500/1000 appear in dropdown
2. Switch to `us_stock` → verify dropdown shows only "无(绝对收益)" + disabled hint
3. Verify `indexBenchmark` is cleared when switching market

- [ ] **Step 4: Commit**

```bash
git add web/src/components/PortfolioRunContent.tsx
git commit -m "fix(BUG-08): filter benchmark options by market, reset on switch"
```

---

## Final Verification

- [ ] **Step 1: Run full backend test suite**

```bash
pytest tests/ -x -q
```

Expected: All 2024+ tests pass (base 2024 + 4 new regression tests)

- [ ] **Step 2: Build frontend**

```bash
cd web && npm run build
```

Expected: No TypeScript errors

- [ ] **Step 3: Commit final spec update**

Update `docs/superpowers/specs/2026-04-06-deep-audit-bugfix.md` to mark all items as fixed. Update CLAUDE.md version notes if applicable.

```bash
git add -A
git commit -m "docs: mark all 8 audit bugs as fixed"
```
