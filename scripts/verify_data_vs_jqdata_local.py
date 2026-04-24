#!/usr/bin/env python3
"""Cross-source verification: local Tushare/parquet cache vs JQData parquet snapshots.

Reads JQData parquet files from a local directory (one file per symbol),
loads the same symbols/dates from our data provider chain, and compares
raw OHLCV + pre_close field-by-field.

Usage:
    python scripts/verify_data_vs_jqdata_local.py \
        --jqdata-dir /Users/zcheng256/auto-rep/benchmarks/validation/jqdata_2025 \
        [--symbols 000001.SZ,600000.SH]  # default: all files in jqdata-dir \
        [--tolerance 0.015]              # relative tolerance (default 1.5%) \
        [--verbose]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def _load_jqdata(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str).str[:8]
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def _load_tushare(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """Load from DuckDB kline_daily or parquet cache."""
    try:
        import duckdb
        db_path = Path(__file__).resolve().parent.parent / "data" / "ez_trading.db"
        if not db_path.exists():
            db_path = Path(__file__).resolve().parent.parent / "data" / "ez_trading.duckdb"
        if not db_path.exists():
            return _load_from_parquet_cache(symbol, start, end)
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            df = conn.execute(
                "SELECT * FROM kline_daily WHERE symbol = ? AND time >= ? AND time <= ? ORDER BY time",
                [symbol, f"{start[:4]}-{start[4:6]}-{start[6:]}", f"{end[:4]}-{end[4:6]}-{end[6:]}"],
            ).fetchdf()
        finally:
            conn.close()
        if df.empty:
            return _load_from_parquet_cache(symbol, start, end)
        if "time" in df.columns:
            df["trade_date"] = pd.to_datetime(df["time"]).dt.strftime("%Y%m%d")
        return df
    except Exception as e:
        print(f"  [WARN] DuckDB load failed for {symbol}: {e}")
        return _load_from_parquet_cache(symbol, start, end)


def _load_from_parquet_cache(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        cache_path = Path(__file__).resolve().parent.parent / "data" / "cache" / "cn_stock_daily.parquet"
        if not cache_path.exists():
            return None
        df = pd.read_parquet(cache_path, filters=[("symbol", "=", symbol)])
        if df.empty:
            return None
        if "time" in df.columns:
            df["trade_date"] = pd.to_datetime(df["time"]).dt.strftime("%Y%m%d")
        return df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
    except Exception as e:
        print(f"  [WARN] Parquet cache load failed for {symbol}: {e}")
        return None


_COMPARE_FIELDS = {
    "open":      ("open",      "open"),
    "high":      ("high",      "high"),
    "low":       ("low",       "low"),
    "close":     ("close",     "close"),
    "volume":    ("volume",    "vol"),
    "amount":    ("amount",    "amount"),
    "pre_close": ("pre_close", "pre_close"),
}


def _compare_symbol(
    symbol: str,
    jq_df: pd.DataFrame,
    ts_df: pd.DataFrame,
    tolerance: float,
    verbose: bool,
) -> dict:
    jq = jq_df.copy()
    ts = ts_df.copy()

    if "trade_date" not in ts.columns and "date" in ts.columns:
        ts["trade_date"] = pd.to_datetime(ts["date"]).dt.strftime("%Y%m%d")
    elif "trade_date" in ts.columns:
        ts["trade_date"] = ts["trade_date"].astype(str).str[:8]

    jq_dates = set(jq["trade_date"].unique())
    ts_dates = set(ts["trade_date"].unique())
    common = sorted(jq_dates & ts_dates)
    only_jq = sorted(jq_dates - ts_dates)
    only_ts = sorted(ts_dates - jq_dates)

    if not common:
        return {
            "symbol": symbol,
            "status": "NO_OVERLAP",
            "common_dates": 0,
            "only_jq": len(only_jq),
            "only_ts": len(only_ts),
            "field_drifts": {},
        }

    jq_m = jq[jq["trade_date"].isin(common)].set_index("trade_date").sort_index()
    ts_m = ts[ts["trade_date"].isin(common)].set_index("trade_date").sort_index()

    field_drifts: dict[str, list] = {}
    worst_pct = 0.0

    for label, (jq_col, ts_col) in _COMPARE_FIELDS.items():
        if jq_col not in jq_m.columns:
            continue
        ts_actual_col = ts_col if ts_col in ts_m.columns else label
        if ts_actual_col not in ts_m.columns:
            field_drifts[label] = [{"reason": "missing_in_tushare"}]
            continue

        jq_vals = pd.to_numeric(jq_m[jq_col], errors="coerce")
        ts_vals = pd.to_numeric(ts_m[ts_actual_col], errors="coerce")

        denom = jq_vals.abs().clip(lower=1e-6)
        pct = ((jq_vals - ts_vals).abs() / denom)
        bad = pct[pct > tolerance].dropna()

        if not bad.empty:
            worst_pct = max(worst_pct, float(bad.max()))
            details = []
            for dt in bad.index[:5]:
                details.append({
                    "date": dt,
                    "jq": float(jq_vals.loc[dt]),
                    "ts": float(ts_vals.loc[dt]),
                    "pct": f"{float(pct.loc[dt]):.4%}",
                })
            if len(bad) > 5:
                details.append({"note": f"... and {len(bad)-5} more"})
            field_drifts[label] = details

    status = "OK" if not field_drifts else ("WARN" if worst_pct < 0.05 else "FAIL")

    return {
        "symbol": symbol,
        "status": status,
        "common_dates": len(common),
        "only_jq": len(only_jq),
        "only_ts": len(only_ts),
        "field_drifts": field_drifts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jqdata-dir", required=True, help="Directory with {symbol}.parquet files")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols (default: all in jqdata-dir)")
    parser.add_argument("--tolerance", type=float, default=0.015, help="Relative tolerance (default 1.5%%)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=50, help="Max symbols to check (default 50)")
    args = parser.parse_args()

    jqdir = Path(args.jqdata_dir)
    if not jqdir.is_dir():
        print(f"ERROR: {jqdir} is not a directory")
        sys.exit(1)

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = sorted(p.stem for p in jqdir.glob("*.parquet") if p.stem != "snapshot_manifest")

    if args.max_symbols and len(symbols) > args.max_symbols:
        import random
        random.seed(42)
        symbols = sorted(random.sample(symbols, args.max_symbols))
        print(f"Sampling {args.max_symbols} symbols (seed=42)")

    ok = warn = fail = skip = 0
    drift_report: list[dict] = []

    for i, sym in enumerate(symbols):
        pq_path = jqdir / f"{sym}.parquet"
        if not pq_path.exists():
            print(f"[{i+1}/{len(symbols)}] {sym}: SKIP (no jqdata file)")
            skip += 1
            continue

        jq_df = _load_jqdata(pq_path)
        start = str(jq_df["trade_date"].min())
        end = str(jq_df["trade_date"].max())

        ts_df = _load_tushare(sym, start, end)
        if ts_df is None or len(ts_df) == 0:
            print(f"[{i+1}/{len(symbols)}] {sym}: SKIP (no tushare data)")
            skip += 1
            continue

        result = _compare_symbol(sym, jq_df, ts_df, args.tolerance, args.verbose)

        tag = result["status"]
        extra = ""
        if result["field_drifts"]:
            fields = list(result["field_drifts"].keys())
            extra = f" drift={fields}"
        if result["only_jq"] or result["only_ts"]:
            extra += f" dates(+jq={result['only_jq']},+ts={result['only_ts']})"

        print(f"[{i+1}/{len(symbols)}] {sym}: {tag}  (common={result['common_dates']}{extra})")

        if tag == "OK":
            ok += 1
        elif tag == "WARN":
            warn += 1
            drift_report.append(result)
        elif tag == "FAIL":
            fail += 1
            drift_report.append(result)
        else:
            skip += 1

        if args.verbose and result["field_drifts"]:
            for field, details in result["field_drifts"].items():
                for d in details:
                    if isinstance(d, dict) and "date" in d:
                        print(f"    {field} {d['date']}: jq={d['jq']:.4f} ts={d['ts']:.4f} ({d['pct']})")
                    elif isinstance(d, dict) and "note" in d:
                        print(f"    {field} {d['note']}")
                    elif isinstance(d, dict) and "reason" in d:
                        print(f"    {field}: {d['reason']}")

    print(f"\n{'='*60}")
    print(f"SUMMARY: {ok} OK / {warn} WARN / {fail} FAIL / {skip} SKIP  (tolerance={args.tolerance:.1%})")
    print(f"{'='*60}")

    if drift_report:
        print(f"\nDrift details ({len(drift_report)} symbols):")
        for r in drift_report[:20]:
            print(f"  {r['symbol']}: {list(r['field_drifts'].keys())}")

    sys.exit(1 if fail > 0 else 0)


if __name__ == "__main__":
    main()
