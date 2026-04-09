# Parquet Data Cache Layer

## Goal

Replace on-demand API fetching with a pre-built, verified, local parquet data warehouse. Backtests run entirely from local data. Cross-validation across multiple data sources catches quality issues upfront.

## Problem

1. **QMT divergence**: Tushare 2000 points cannot access `fund_daily` (needs 5000). ETF data falls back to AKShare/Tencent with different adjustment methods — likely root cause of QMT backtest differences.
2. **API dependency**: Every backtest may trigger network calls. Rate limits slow parameter searches.
3. **No verification**: Data from a single source is trusted blindly. No cross-checking.
4. **No bulk download**: 5000+ symbols fetched one-by-one on first use.

## Architecture

```
Build time (scripts/build_data_cache.py):

  Step 1: Tushare daily (by trade_date, batch all stocks) ──→ raw OHLCV DataFrame
  Step 2: Tushare adj_factor (by trade_date, batch)        ──→ per-symbol factor map
  Step 3: Merge: adj_close = close × factor[date] / factor[latest]
  Step 4: BaoStock ETF (per symbol, raw + qfq)             ──→ append to DataFrame
  Step 5: Sort by (symbol, time) → write parquet (row_group_size=100_000)
  Step 6: Weekly/Monthly: resample from daily (not separate API calls)

  Cross-validation (parallel):
  AKShare + Tencent + BaoStock (sample 50 symbols) ──→ compare daily RETURNS
  → validation_report.json

Runtime query priority:
  Parquet local cache → DuckDB runtime cache → API provider chain
```

## File Layout

```
data/cache/
├── cn_stock_daily.parquet           # All A-shares + ETFs + indices, sorted by (symbol, time)
├── cn_stock_weekly.parquet          # Derived from daily (resample)
├── cn_stock_monthly.parquet         # Derived from daily (resample)
├── manifest.json                    # Build metadata: date, range, sources, symbol count
├── validation_report.json           # Cross-source comparison results
│
├── cn_stock_1min/                   # Future: minute-level, Hive partitioned
│   ├── symbol=000001.SZ/
│   │   └── data.parquet
│   └── ...
├── cn_stock_tick/                   # Future: tick-level, Hive partitioned
│   └── ...
```

**Current scope: daily + weekly + monthly only.** Minute/tick directories are reserved in the schema but not implemented.

## Parquet Schema

Identical to existing DuckDB `kline_daily` table:

| Column | Type | Notes |
|--------|------|-------|
| time | TIMESTAMP | Trading day (or bar timestamp for minute+) |
| symbol | VARCHAR | e.g. "000001.SZ", "510300.SH" |
| market | VARCHAR | "cn_stock" |
| open | DOUBLE | |
| high | DOUBLE | |
| low | DOUBLE | |
| close | DOUBLE | Raw (unadjusted) close |
| adj_close | DOUBLE | Forward-adjusted close = close × factor / latest_factor |
| volume | BIGINT | |

**Write options:**
- Sorted by `(symbol, time)` within each file
- `row_group_size=100_000` — ensures ~60+ row groups for full A-share, enabling fine-grained predicate pushdown (single-symbol query reads ~1 row group instead of scanning all)
- Compression: snappy (default, good balance of speed vs size)

## Data Sources & Download Strategy

### adj_close Computation (Critical for Data Correctness)

Tushare `daily` API returns **raw** OHLCV (no adj_close). Forward-adjusted close must be computed:

```
adj_close = close × adj_factor[date] / latest_factor[symbol]
```

Where `latest_factor` = max adj_factor across all dates for that symbol.

**Two-pass approach:**
1. Fetch `adj_factor` for the most recent trading day → `latest_factor` per symbol
2. For each historical day, join with daily raw data to compute adj_close

### Source Priority

| Data | Primary Source | Method | Rate | Notes |
|------|---------------|--------|------|-------|
| A-share daily OHLCV | Tushare `daily` | By trade_date (1 call = all stocks) | 500/min | ~1250 calls for 5 years |
| Adjustment factors | Tushare `adj_factor` | By trade_date (1 call = all stocks) | 500/min | ~1250 calls (NOT per-symbol) |
| ETF daily OHLCV | BaoStock | Per symbol, `adjustflag=3` (raw) + `adjustflag=2` (qfq) | **≤100K/day** | ~30 ETFs = 60 calls |
| Index daily | Tushare `index_daily` | Per index code | 500/min | 000300.SH, 000905.SH, 000852.SH etc. Stored in same parquet with market="cn_stock" so benchmark queries hit parquet |
| Cross-validation | AKShare + Tencent + BaoStock | Sample 50 symbols | See below | Verification only |

### Rate Limits & Safety

| Provider | Limit | Our Usage | Safety Margin |
|----------|-------|-----------|---------------|
| Tushare | 500 calls/min | ~2500 total (daily + adj_factor) | 0.15s throttle between calls |
| BaoStock | **100,000 calls/day** | ~60 (ETF) + ~100 (validation) = ~160 | 99.8% margin; add counter to abort at 90K |
| AKShare | ~100 calls/min (0.6s throttle) | ~100 (validation) | Existing throttle sufficient |
| Tencent | ~60 calls/min (1s throttle) | ~50 (validation) | Existing throttle sufficient |

**BaoStock safety**: Track call count in script; log warning at 50K; abort at 90K to prevent blacklisting.

### Download Time Estimate (Full A-share, 5 years)

| Step | Calls | Rate | Time |
|------|-------|------|------|
| Tushare daily by trade_date | ~1250 | 500/min | 3 min |
| Tushare adj_factor by trade_date | ~1250 | 500/min | 3 min |
| BaoStock ETF (30 symbols × 2 fetches) | 60 | 100K/day | <1 min |
| Weekly/Monthly (derived from daily) | 0 | — | <10 sec |
| Cross-validation (50 symbols × 3 sources) | ~250 | mixed | 3 min |
| **Total** | **~3810** | | **~10 min** |

Previous estimate was 16 min because adj_factor was per-symbol (5000 calls). By-trade_date batch cuts this to ~10 min.

### ETF Data Gap

Tushare `fund_daily` requires 5000 points (user has 2000). Solution:
- BaoStock provides ETF OHLCV with both raw (`adjustflag=3`) and forward-adjusted (`adjustflag=2`) prices
- Free, registration-free, but **100K calls/day limit** — sufficient for our use
- BaoStock ETF codes: `sh.510300` format (convert from `510300.SH`)

### Weekly/Monthly: Derived from Daily

**Do NOT fetch weekly/monthly from APIs separately.** Different providers define "week" differently (last trading day of ISO week vs Friday close vs...). Instead:

```python
# Weekly: resample daily to week-ending Friday
weekly = daily.resample("W-FRI").agg({
    "open": "first", "high": "max", "low": "min",
    "close": "last", "adj_close": "last", "volume": "sum"
})

# Monthly: resample daily to month-end
monthly = daily.resample("ME").agg({...same...})
```

This guarantees weekly/monthly data is exactly consistent with daily — no cross-source ambiguity.

## Cross-Validation (Build Gate, Not Report)

### Method: Compare Daily Returns, Not Absolute Prices

**Why returns, not prices?** Forward-adjusted prices depend on the reference date. If Source A built adj_close yesterday and Source B built it today (after a dividend), all prices differ systematically. But daily returns are adjustment-independent:

```python
# Per-symbol, per-source: compute daily returns
returns = adj_close.pct_change()

# Compare returns across sources
return_diff = abs(tushare_returns - other_returns)

# Thresholds (on returns, not prices)
if return_diff > 0.01:    # >1pp return difference — ERROR (likely data error)
if return_diff > 0.001:   # >0.1pp — WARNING (possible rounding/timing)
```

### Gate Behavior (Hard Gate, Not Just Logging)

Validation is a **build gate**, not a report. The script enforces:

| Level | Threshold | Action |
|-------|-----------|--------|
| ERROR | return diff > 1pp | **Abort build. Exit code 1. No parquet written.** |
| WARNING | return diff > 0.1pp | Continue, flag in manifest `"warnings"` list |
| CLEAN | diff ≤ 0.1pp | Pass silently |

```python
# build_data_cache.py validation gate
if error_count > 0:
    print(f"GATE FAILED: {error_count} symbols with >1pp return discrepancy")
    print("Symbols:", [e["symbol"] for e in errors])
    print("Fix: investigate data source, or --exclude-symbols to skip problematic symbols")
    sys.exit(1)  # No parquet written

# Only write parquet after gate passes
if error_count == 0:
    write_parquet(df, output_path)
    write_manifest(manifest, warnings)
```

**`--exclude-symbols` escape hatch**: For known problematic symbols (e.g., 162411.SZ LOF with inconsistent pricing across sources), the user can explicitly exclude them:
```bash
python scripts/build_data_cache.py --exclude-symbols 162411.SZ
```
Excluded symbols are logged in `manifest.json` under `"excluded"` with reason.

### Validation Report

Output: `validation_report.json` (always written, even on gate failure — for debugging)
```json
{
  "generated_at": "2026-04-09T10:00:00",
  "gate_passed": false,
  "sources": {"primary": "tushare", "verified": ["akshare", "tencent", "baostock"]},
  "symbols_checked": 50,
  "total_days_compared": 62500,
  "errors": [
    {"symbol": "162411.SZ", "date": "2024-03-15", "tushare_ret": 0.023, "akshare_ret": -0.005, "diff": 0.028, "source": "akshare"}
  ],
  "warnings": [],
  "summary": {"error_count": 3, "warning_count": 12, "clean_count": 35, "error_rate": "0.005%"}
}
```

### Manifest File

Output: `manifest.json`
```json
{
  "built_at": "2026-04-09T10:15:00",
  "builder_version": "0.2.18",
  "date_range": {"start": "2021-01-04", "end": "2026-04-08"},
  "symbols": {"stocks": 4823, "etfs": 30, "indices": 5},
  "sources": {"stocks": "tushare", "etfs": "baostock", "adj_factor": "tushare"},
  "files": {
    "cn_stock_daily.parquet": {"rows": 6250000, "size_mb": 185, "md5": "abc123..."},
    "cn_stock_weekly.parquet": {"rows": 1300000, "size_mb": 42},
    "cn_stock_monthly.parquet": {"rows": 300000, "size_mb": 10}
  },
  "note": "adj_close is forward-adjusted as of built_at date. Rebuild after corporate actions for updated factors."
}
```

## Forward-Adjustment Staleness

**Important:** `adj_close = close × factor / latest_factor` is a point-in-time snapshot. When a stock splits or pays dividends after the parquet is built, ALL historical adj_close values for that stock become stale (the denominator `latest_factor` changes).

**Mitigation:**
- `manifest.json` records `built_at` timestamp
- Script `--update` mode (future) can detect stale factors and rebuild affected symbols
- For research purposes, short-term staleness (days) has negligible impact on strategy signals that use returns or relative rankings

## Integration: store.py Change (Both Single and Batch Paths)

The portfolio backtest main path goes through `DataProviderChain.get_kline_batch()` → `DuckDBStore.query_kline_batch()`. The single-stock path goes through `query_kline()`. Both must check parquet first.

### query_kline() — single symbol

```python
def query_kline(self, symbol, market, period, start_date, end_date):
    # 1. Parquet cache (highest priority, no API call)
    parquet_path = self._find_parquet_cache(market, period)
    if parquet_path:
        df = self._conn.execute("""
            SELECT time, symbol, market, open, high, low, close, adj_close, volume
            FROM read_parquet(?)
            WHERE symbol = ? AND time >= ? AND time <= ?
            ORDER BY time
        """, [str(parquet_path), symbol, start_date, end_date]).fetchdf()
        if not df.empty:
            return self._df_to_bars(df)

    # 2. Existing DuckDB table query (unchanged)
    ...
```

### query_kline_batch() — multi symbol (portfolio main path)

```python
def query_kline_batch(self, symbols, market, period, start_date, end_date):
    # 1. Parquet cache — single read_parquet with WHERE symbol IN (...)
    parquet_path = self._find_parquet_cache(market, period)
    parquet_result: dict[str, list[Bar]] = {}
    parquet_missing: list[str] = list(symbols)

    if parquet_path:
        placeholders = ",".join(["?"] * len(symbols))
        rows = self._conn.execute(
            f"SELECT * FROM read_parquet(?) WHERE symbol IN ({placeholders}) "
            f"AND time >= ? AND time <= ? ORDER BY symbol, time",
            [str(parquet_path), *symbols, start_date, end_date],
        ).fetchall()
        for r in rows:
            bar = Bar(time=r[0], symbol=r[1], ...)
            parquet_result.setdefault(bar.symbol, []).append(bar)
        # Only symbols NOT found in parquet fall through to DuckDB
        parquet_missing = [s for s in symbols if s not in parquet_result]

    if not parquet_missing:
        return parquet_result  # All symbols served from parquet

    # 2. Existing DuckDB query for remaining symbols
    db_result = self._query_kline_batch_from_db(parquet_missing, market, period, ...)
    return {**parquet_result, **db_result}
```

This ensures the portfolio backtest hot path (`_fetch_data()` → `chain.get_kline_batch()` → `store.query_kline_batch()`) hits parquet first with a single SQL query. Symbols not in parquet fall through to DuckDB → API chain as before.

### _find_parquet_cache() path resolution

- Dev mode: `data/cache/{market}_{period}.parquet`
- Frozen mode (PyInstaller): `sys._MEIPASS/data/cache/...` or `EZ_DATA_DIR`

### Benchmark symbol coverage

`_ensure_benchmark()` in portfolio routes calls `chain.get_kline()` for the benchmark symbol. This already passes through `store.query_kline()` which will check parquet first. Index benchmarks (e.g., `000300.SH` CSI 300) are included in the parquet scope — see Data Sources below.

No changes to `DataProviderChain`, API routes, or frontend.

## Memory Consideration

Full A-share daily data: ~5000 symbols × 1250 days = 6.25M rows.
In-memory as pandas DataFrame: ~600-800MB.

This is manageable on most machines (8GB+ RAM). The script:
1. Fetches daily data in chunks (by trade_date) and appends to a list
2. Concatenates into a single DataFrame at the end
3. Sorts by (symbol, time)
4. Writes to parquet (pyarrow handles memory-mapped writing)

If memory is a concern, the script could write intermediate parquet chunks and let DuckDB merge-sort them — but this is an optimization, not needed for initial implementation.

## Release Packaging

### Seed Data (shipped with release)

Only the QMT preset ETF pool (~30 symbols, 5 years):
- `cn_stock_daily.parquet` — ~3MB (seed, ETF-only)
- `cn_stock_weekly.parquet` — ~0.5MB (derived from daily)
- `cn_stock_monthly.parquet` — ~0.1MB (derived from daily)
- `manifest.json` — build metadata

Added to PyInstaller:
```yaml
--add-data "data/cache${SEP}data/cache"
```

### Full Data (user-downloaded)

User runs:
```bash
python scripts/build_data_cache.py --market cn_stock --years 5
```

This replaces the seed parquet files with full A-share data (~200MB).

### Script Modes

```bash
# Default: full A-share + ETF, 5 years
python scripts/build_data_cache.py

# ETF only (for seed data generation in CI)
python scripts/build_data_cache.py --etf-only

# Custom symbol list
python scripts/build_data_cache.py --symbols 000001.SZ,510300.SH

# Custom date range
python scripts/build_data_cache.py --start 2020-01-01 --end 2025-12-31

# Skip cross-validation (faster)
python scripts/build_data_cache.py --no-verify

# Include weekly/monthly (default: daily only)
python scripts/build_data_cache.py --periods daily,weekly,monthly
```

## Future Extensibility (Not Implemented Now)

### Minute-Level Data
- Same schema, Hive-partitioned by symbol: `cn_stock_1min/symbol=XXXXX/data.parquet`
- DuckDB: `read_parquet('data/cache/cn_stock_1min/**/*.parquet', hive_partitioning=true)`
- Download source: BaoStock (free minute data from 1999, but respect 100K/day limit — ~5000 symbols × 2 calls = 10K, well within)
- Size: ~20GB/year for full A-share → must be user-downloaded

### Tick-Level Data
- Extended schema (add bid/ask/trade_type columns)
- Partitioned by symbol + date: `cn_stock_tick/symbol=XXXXX/date=2024-01-02/data.parquet`
- Size: terabytes → external storage, not in-process

### Incremental Update
- `--update` flag: only download new trading days since last build
- Read existing parquet → find max(time) per symbol → fetch delta → merge → rewrite
- Also re-fetch latest adj_factor to update forward-adjustment (handles post-build splits)
- Not needed now (full rebuild takes ~10 minutes)

## New Dependency

```toml
# pyproject.toml [project.optional-dependencies]
data-cache = ["baostock>=0.8"]
```

BaoStock is only used in `scripts/build_data_cache.py`, not in runtime imports. If not installed, the script prints a helpful error message.

## Files Changed

| File | Change | Lines |
|------|--------|-------|
| `scripts/build_data_cache.py` | **New** — bulk download + cross-validation gate + parquet output | ~400 |
| `ez/data/store.py` | **Modify** — `_find_parquet_cache()` + parquet-first in both `query_kline()` AND `query_kline_batch()` | ~40 |
| `.github/workflows/build-release.yml` | **Modify** — add `--add-data data/cache` | 1 |
| `pyproject.toml` | **Modify** — add `baostock` to optional deps | 1 |

## Testing

1. **Unit test**: `test_store_parquet_priority` — `query_kline()` returns parquet data before DuckDB
2. **Unit test**: `test_store_parquet_batch_priority` — `query_kline_batch()` returns parquet data, skips DuckDB for found symbols
3. **Unit test**: `test_store_parquet_batch_partial` — batch: symbols in parquet served from parquet, missing symbols fall through to DuckDB
4. **Unit test**: `test_store_parquet_missing_fallback` — falls through to DuckDB when parquet absent
5. **Unit test**: `test_store_parquet_frozen_mode` — resolves path under `sys._MEIPASS`
6. **Integration test**: `test_build_data_cache_etf_only` — script produces valid parquet with correct schema
7. **Gate test**: `test_cross_validation_gate_blocks_on_error` — script exits non-zero when errors found, no parquet written
8. **Gate test**: `test_cross_validation_gate_passes_warnings` — warnings allowed, parquet written, warnings in manifest
9. **Derivation test**: `test_weekly_monthly_derived_from_daily` — weekly/monthly parquet matches daily resample
10. **Benchmark test**: `test_index_benchmark_served_from_parquet` — index symbols (000300.SH) found in parquet, no API call

## Success Criteria

1. `python scripts/build_data_cache.py --etf-only` completes in <2 minutes
2. Portfolio backtest with preset ETF strategies makes zero API calls when parquet present
3. Cross-validation report identifies known data discrepancies (e.g., 162411.SZ)
4. Release build includes seed parquet, users can backtest ETF strategies offline
5. Full A-share build completes in <12 minutes with 2000-point Tushare
6. BaoStock call count never exceeds 90K in a single script run
7. Weekly/monthly parquet values exactly match daily-derived resample
