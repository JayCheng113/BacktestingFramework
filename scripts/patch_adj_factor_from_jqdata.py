#!/usr/bin/env python3
"""Patch DuckDB kline_daily: fix close/OHLC to raw (fq=None) values.

The jqdata 2025 parquet snapshot was captured with fq='pre' (前复权).
That means close/open/high/low are forward-adjusted, NOT raw prices.
adj_close is already correct (= pre-adjusted close).

This script fetches fq=None OHLC from jqdatasdk API and patches the DB
so that close/open/high/low contain raw (unadjusted) prices while
adj_close retains the pre-adjusted value.

Usage:
    export JQDATA_USERNAME="your_phone"
    export JQDATA_PASSWORD="your_password"
    python scripts/patch_adj_factor_from_jqdata.py [--batch-size 50] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd


def _to_jq_code(ts_code: str) -> str:
    if ts_code.endswith(".SZ"):
        return ts_code.replace(".SZ", ".XSHE")
    if ts_code.endswith(".SH"):
        return ts_code.replace(".SH", ".XSHG")
    if ts_code.endswith(".BJ"):
        return ts_code.replace(".BJ", ".XBJE")
    return ts_code


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="data/ez_trading.db")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--throttle", type=float, default=0.35, help="Seconds between API calls")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbols", default="", help="Comma-separated subset (default: all 2025+ symbols)")
    args = parser.parse_args()

    username = os.environ.get("JQDATA_USERNAME", "")
    password = os.environ.get("JQDATA_PASSWORD", "")
    if not username or not password:
        print("ERROR: set JQDATA_USERNAME and JQDATA_PASSWORD environment variables")
        sys.exit(1)

    try:
        import jqdatasdk as jq
    except ImportError:
        print("ERROR: pip install jqdatasdk")
        sys.exit(1)

    jq.auth(username, password)
    print("jqdatasdk auth OK")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found")
        sys.exit(1)

    conn = duckdb.connect(str(db_path))

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM kline_daily WHERE time >= '2025-01-01' ORDER BY symbol"
            ).fetchall()
        ]

    print(f"Patching adj_close for {len(symbols)} symbols...")

    patched = skipped = failed = 0
    total_rows = 0

    for i, sym in enumerate(symbols):
        jq_code = _to_jq_code(sym)
        try:
            df_raw = jq.get_price(
                jq_code,
                start_date="2025-01-07",
                end_date="2026-01-14",
                frequency="daily",
                fields=["open", "high", "low", "close", "pre_close"],
                fq=None,
                skip_paused=False,
            )
            if df_raw is None or df_raw.empty or "close" not in df_raw.columns:
                skipped += 1
                if (i + 1) % 100 == 0:
                    print(f"  [{i+1}/{len(symbols)}] {sym}: no data, skipped")
                continue

            df_raw = df_raw.dropna(subset=["close"])
            if df_raw.empty:
                skipped += 1
                continue

            df_raw["trade_date"] = pd.to_datetime(df_raw.index).strftime("%Y-%m-%d")
            df_raw["symbol"] = sym

            if args.dry_run:
                patched += 1
                total_rows += len(df_raw)
                if (i + 1) % 200 == 0 or i < 3:
                    print(f"  [{i+1}/{len(symbols)}] {sym}: would patch {len(df_raw)} rows raw OHLC")
            else:
                conn.execute("""
                    UPDATE kline_daily
                    SET open = u.open,
                        high = u.high,
                        low = u.low,
                        close = u.close
                    FROM df_raw u
                    WHERE kline_daily.symbol = u.symbol
                      AND CAST(kline_daily.time AS DATE) = CAST(u.trade_date AS DATE)
                """)
                patched += 1
                total_rows += len(df_raw)

            if (i + 1) % 200 == 0:
                print(f"  [{i+1}/{len(symbols)}] progress: {patched} patched, {failed} failed, {len(df_raw)} rows")

        except Exception as e:
            failed += 1
            if (i + 1) % 100 == 0 or failed <= 5:
                print(f"  [{i+1}/{len(symbols)}] {sym}: FAILED — {e}")

        if args.throttle > 0:
            time.sleep(args.throttle)

    conn.close()
    print(f"\n{'='*60}")
    print(f"DONE: {patched} patched / {skipped} skipped / {failed} failed / {total_rows} total rows updated to raw OHLC")
    if args.dry_run:
        print("(dry-run — no actual DB changes)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
