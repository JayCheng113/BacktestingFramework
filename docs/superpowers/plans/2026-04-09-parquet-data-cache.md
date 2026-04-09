# Parquet Data Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-built parquet data warehouse so backtests run entirely from local data, with cross-validation gate ensuring data quality.

**Architecture:** `DuckDBStore` checks local parquet files before querying its own tables or calling APIs. A standalone build script downloads from Tushare (stocks) + BaoStock (ETFs) + Tushare (indices), cross-validates against AKShare, and writes parquet only if the validation gate passes. Weekly/monthly are derived from daily. Seed ETF data ships with releases.

**Tech Stack:** DuckDB (native `read_parquet()`), pandas, pyarrow (parquet I/O), baostock (optional, ETF data), existing tushare/akshare providers.

---

## Hard Constraints (Must be enforced in implementation)

### C1: Cross-validation is a HARD GATE, not a report

| Level | Threshold (daily return diff) | Action |
|-------|------|--------|
| ERROR | > 1pp | **`sys.exit(1)`. No parquet written. Build fails.** |
| WARNING | > 0.1pp | Continue. Flag in manifest `"warnings"` list. Print yellow in build output. |
| CLEAN | ≤ 0.1pp | Pass silently. |

`validation_report.json` is always written (for debugging), but parquet files are ONLY written after `error_count == 0`. `--exclude-symbols` is the escape hatch for known-bad symbols (e.g., 162411.SZ).

### C2: Offline scope is explicit per build mode

| Mode | Scope | Offline guarantee | API fallback |
|------|-------|-------------------|--------------|
| `--etf-only` (seed) | 25 preset ETFs + 5 indices | **Yes — these symbols fully offline** | Other symbols still fall back to API at runtime |
| Full build (default) | All A-shares + ETFs + indices | **Yes — entire cn_stock daily is offline** | Only truly new symbols (IPO after build date) fall back |

Benchmark symbols (000300.SH, 000905.SH, etc.) are explicitly included in BOTH modes via `INDEX_SYMBOLS` constant in the build script. `_ensure_benchmark()` in portfolio routes goes through `store.query_kline()` which checks parquet first.

### C3: `query_kline_batch()` is THE primary entry point — must be parquet-first

The portfolio backtest hot path is: `_fetch_data()` (ez/api/routes/portfolio.py:561) → `chain.get_kline_batch()` (ez/data/provider.py:223) → `store.query_kline_batch()` (ez/data/store.py:90).

Task 2 rewrites `query_kline_batch()` to check parquet FIRST with a single `read_parquet()` + `WHERE symbol IN (...)`. Symbols found in parquet are returned immediately; only missing symbols fall through to DuckDB tables. This is not optional — without this change, all portfolio backtests, parameter searches, and walk-forward validations bypass the parquet cache entirely.

---

## Task 1: Parquet-first query — single path

**Files:**
- Modify: `ez/data/store.py:26-88`
- Test: `tests/test_data/test_store.py`

- [ ] **Step 1: Write 3 failing tests** — parquet priority, missing fallback, symbol-not-found fallback

Tests create a parquet file with different adj_close values than DuckDB. Assert parquet data wins when present; DuckDB data wins when parquet is absent or doesn't contain the symbol.

Key test: `test_parquet_priority` — parquet bar has adj_close=99.0, DuckDB has 10.15. Result must be 99.0.

- [ ] **Step 2: Run tests — verify FAIL** (`_cache_dir` doesn't exist yet)

- [ ] **Step 3: Implement `_cache_dir` init + `_find_parquet_cache()` + parquet-first in `query_kline()`**

In `__init__`: resolve `_cache_dir` via EZ_DATA_DIR → `sys._MEIPASS` (frozen) → `project_root/data/cache`. Set to `None` if directory doesn't exist.

`_find_parquet_cache(market, period)`: returns `str` path to `{cache_dir}/{market}_{period}.parquet` if file exists, else `None`.

`query_kline()`: if parquet found, execute `SELECT ... FROM read_parquet(?) WHERE symbol=? AND time>=? AND time<=?`. If rows returned, build Bar list and return. Otherwise fall through to existing DuckDB query unchanged.

- [ ] **Step 4: Run tests — ALL PASS**

- [ ] **Step 5: Commit**

---

## Task 2: Parquet-first query — batch path (C3: portfolio main path)

**Files:**
- Modify: `ez/data/store.py:90-112`
- Test: `tests/test_data/test_store.py`

- [ ] **Step 1: Write 2 failing tests** — batch all from parquet, batch partial (some parquet + some DuckDB)

`test_parquet_batch_priority`: 2 symbols both in parquet → both returned from parquet, DuckDB table stays empty.
`test_parquet_batch_partial`: symbol PQ.SZ in parquet, 000001.SZ in DuckDB only → PQ.SZ from parquet (adj_close=50.0), 000001.SZ from DuckDB (adj_close=10.15).

- [ ] **Step 2: Run tests — verify FAIL**

- [ ] **Step 3: Rewrite `query_kline_batch()`**

New flow:
1. Check parquet: `SELECT ... FROM read_parquet(?) WHERE symbol IN (...) AND time>=? AND time<=?`
2. Track `found_syms` from parquet results
3. `remaining = [s for s in symbols if s not in found_syms]`
4. If no remaining: return parquet results immediately (zero DuckDB queries)
5. Otherwise: run existing DuckDB query for remaining symbols only
6. Merge and return

This ensures `_fetch_data()` → `chain.get_kline_batch()` → `store.query_kline_batch()` hits parquet first.

- [ ] **Step 4: Run tests — ALL PASS**

- [ ] **Step 5: Commit**

---

## Task 3: Build script with hard gate (C1 + C2)

**Files:**
- Create: `scripts/build_data_cache.py` (~400 lines)
- Modify: `pyproject.toml` (add `data-cache` optional dep)

- [ ] **Step 1: Add `baostock` to pyproject.toml optional deps**

```toml
data-cache = ["baostock>=0.8"]
all = ["tushare>=1.4", "akshare>=1.14", "scikit-learn>=1.5", "lightgbm>=4.0", "xgboost>=2.0", "baostock>=0.8"]
```

- [ ] **Step 2: Create `scripts/build_data_cache.py`**

Core components:

**Constants:**
- `SEED_ETFS`: 25 ETF symbols from QMT preset strategies
- `INDEX_SYMBOLS`: 000300.SH, 000905.SH, 000852.SH, 000001.SH, 399006.SZ (C2: benchmark coverage)
- `BAOSTOCK_ABORT_LIMIT = 90_000` / `BAOSTOCK_WARN_LIMIT = 50_000`
- `ERROR_THRESHOLD = 0.01` / `WARNING_THRESHOLD = 0.001`

**Tushare functions:**
- `fetch_tushare_daily_by_dates(token, start, end)` — iterate trade_date, 1 call = all stocks
- `fetch_tushare_adj_factors(token, start, end)` — iterate trade_date, 1 call = all factors
- `merge_adj_close(daily_df, adj_df)` — `adj_close = close × factor / latest_factor`
- `fetch_tushare_index(token, codes, start, end)` — per-index fetch, `adj_close = close`

**BaoStock functions:**
- `_baostock_check_limit()` — global counter, warn at 50K, abort at 90K
- `fetch_baostock_etf(symbols, start, end)` — dual fetch per symbol: raw (`adjustflag=3`) + qfq (`adjustflag=2`)

**Cross-validation (C1: hard gate):**
- `cross_validate(primary_df, symbols, start, end, exclude)` — compare daily returns vs AKShare
- Returns dict with `gate_passed: bool`
- **Main enforces gate:**
  ```python
  if not report["gate_passed"]:
      print(f"❌ GATE FAILED: {error_count} symbols with >1pp return discrepancy")
      sys.exit(1)  # No parquet written
  ```

**Parquet writing:**
- `build_parquet(df, market, period, output_dir)` — normalize columns, sort by (symbol, time), write with `row_group_size=100_000`
- `derive_weekly_monthly(daily_path, market, output_dir, periods)` — resample daily to W-FRI / ME

**Main flow:**
1. Parse args (--etf-only, --symbols, --start, --end, --no-verify, --exclude-symbols, --periods)
2. Download data per mode (C2: etf-only vs full)
3. Cross-validate if not --no-verify and not --etf-only (C1: gate)
4. **Only write parquet after gate passes** (C1)
5. Derive weekly/monthly from daily
6. Write manifest.json + validation_report.json

- [ ] **Step 3: Verify `--help` works**

Run: `python scripts/build_data_cache.py --help`

- [ ] **Step 4: Commit**

---

## Task 4: Release packaging + seed data directory

**Files:**
- Modify: `.github/workflows/build-release.yml`
- Create: `data/cache/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Create cache dir + gitkeep**
- [ ] **Step 2: Add `--add-data "data/cache${SEP}data/cache"` to PyInstaller**
- [ ] **Step 3: Add `data/cache/*.parquet` and `data/cache/*.json` to .gitignore, keep .gitkeep**
- [ ] **Step 4: Commit**

---

## Task 5: Integration tests

**Files:**
- Create: `tests/test_data/test_parquet_cache.py`

Tests:
1. `test_batch_all_from_parquet` — all symbols served from parquet, DuckDB table empty
2. `test_index_benchmark_from_parquet` — 000300.SH found in parquet (C2)
3. `test_weekly_derived_from_daily` — weekly matches resample
4. `test_frozen_mode_path` — monkeypatch `sys.frozen` + `sys._MEIPASS`, verify cache_dir resolves

- [ ] **Step 1: Write tests**
- [ ] **Step 2: Run + verify pass**
- [ ] **Step 3: Run full suite — 2252+ passed**
- [ ] **Step 4: Commit**

---

## Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md` (V2.18 entry + header version bump)
- Modify: `ez/data/CLAUDE.md` (V2.18 status + files table)

- [ ] **Step 1: Update docs**
- [ ] **Step 2: Commit**

---

## Spec Coverage Matrix

| Spec Requirement | Task | Hard Constraint |
|------------------|------|-----------------|
| Parquet-first query_kline() | T1 | |
| Parquet-first query_kline_batch() | T2 | **C3** |
| Tushare daily by trade_date batch | T3 | |
| adj_close = close × factor / latest | T3 | |
| BaoStock ETF (raw + qfq) | T3 | |
| Index benchmark in parquet | T3 | **C2** |
| Cross-validation gate (return-based) | T3 | **C1** |
| ERROR → sys.exit(1), no parquet | T3 | **C1** |
| WARNING → continue, flag manifest | T3 | **C1** |
| --exclude-symbols escape hatch | T3 | |
| Weekly/monthly derived from daily | T3 | |
| manifest.json | T3 | |
| row_group_size=100_000 | T3 | |
| BaoStock 90K safety | T3 | |
| Release --add-data | T4 | |
| Frozen mode (sys._MEIPASS) | T1 | |
| Offline scope: seed vs full | T3 | **C2** |
| Integration tests | T5 | |
| CLAUDE.md update | T6 | |
