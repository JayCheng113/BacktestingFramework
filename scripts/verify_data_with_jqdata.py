#!/usr/bin/env python3
"""Cross-source data verification: Tushare provider chain vs JQData.

Standalone script — no FastAPI / web dependency.

Usage:
    python scripts/verify_data_with_jqdata.py --symbols 000001.SZ,600000.SH,510300.SH \
        --start 2025-06-01 --end 2025-12-31

Free JQData account window: roughly 15 months ago to 3 months ago.
Adjust --start / --end to stay within that window.

Exit code:
    0  — all symbols passed
    1  — at least one symbol has FAIL-level drift
    2  — argument / setup error
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("verify_jqdata")


# ── Tolerance constants ──────────────────────────────────────────────

PRICE_REL_TOL = 0.01       # 1% relative tolerance for OHLCV / adj_close / raw_close
FACTOR_ABS_TOL = 1e-4       # absolute tolerance for factor
LIMIT_PRICE_TOL = 0.015     # 1.5% tolerance for limit price comparison


# ── Helpers ──────────────────────────────────────────────────────────

def _rel_diff(a: float, b: float) -> float:
    """Relative difference: |a - b| / max(|a|, |b|, 1e-6)."""
    denom = max(abs(a), abs(b), 1e-6)
    return abs(a - b) / denom


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# ── Fetch from Tushare chain ────────────────────────────────────────

def fetch_tushare_df(symbol: str, start: date, end: date) -> pd.DataFrame | None:
    """Fetch daily bars from the existing provider chain and return a DataFrame."""
    try:
        from ez.api.deps import get_chain
        chain = get_chain()
        bars = chain.get_kline(symbol, "cn_stock", "daily", start, end)
        if not bars:
            return None
        df = pd.DataFrame([{
            "date": b.time.date() if hasattr(b.time, "date") else b.time,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,       # raw close in our convention
            "adj_close": b.adj_close,
            "volume": b.volume,
        } for b in bars])
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.error("Tushare chain fetch failed for %s: %s", symbol, e)
        return None


# ── Fetch from JQData ───────────────────────────────────────────────

def fetch_jqdata_df(symbol: str, start: date, end: date) -> pd.DataFrame | None:
    """Fetch daily bars from JQDataProvider and return a DataFrame."""
    try:
        from ez.data.providers.jqdata_provider import JQDataProvider
        p = JQDataProvider()
        df = p.get_daily(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if df is None or df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.error("JQData fetch failed for %s: %s", symbol, e)
        return None


# ── Comparison ──────────────────────────────────────────────────────

def compare_symbol(
    symbol: str,
    ts_df: pd.DataFrame,
    jq_df: pd.DataFrame,
    *,
    verbose: bool = False,
) -> dict:
    """Compare Tushare vs JQData DataFrames for one symbol.

    Returns a result dict:
        status: "OK" | "WARN(...)" | "FAIL(...)"
        details: list of per-date drift lines
        date_coverage: dict with ts_only, jq_only, common counts
    """
    result = {
        "symbol": symbol,
        "status": "OK",
        "details": [],
        "date_coverage": {},
        "field_max_drift": {},
    }

    # Normalize date columns
    ts_dates = set(ts_df["date"].dt.strftime("%Y-%m-%d"))
    jq_dates = set(jq_df["date"].dt.strftime("%Y-%m-%d"))
    common_dates = sorted(ts_dates & jq_dates)
    ts_only = sorted(ts_dates - jq_dates)
    jq_only = sorted(jq_dates - ts_dates)

    result["date_coverage"] = {
        "common": len(common_dates),
        "ts_only": len(ts_only),
        "jq_only": len(jq_only),
        "ts_only_dates": ts_only[:5],
        "jq_only_dates": jq_only[:5],
    }

    if not common_dates:
        result["status"] = "FAIL(no_common_dates)"
        return result

    # Merge on date
    ts_df = ts_df.copy()
    jq_df = jq_df.copy()
    ts_df["date_str"] = ts_df["date"].dt.strftime("%Y-%m-%d")
    jq_df["date_str"] = jq_df["date"].dt.strftime("%Y-%m-%d")
    ts_indexed = ts_df.set_index("date_str")
    jq_indexed = jq_df.set_index("date_str")

    # Fields to compare: (ts_col, jq_col, tolerance, is_factor)
    comparisons = [
        ("open",      "open",      PRICE_REL_TOL,  False),
        ("high",      "high",      PRICE_REL_TOL,  False),
        ("low",       "low",       PRICE_REL_TOL,  False),
        ("close",     "raw_close", PRICE_REL_TOL,  False),   # our close = raw close
        ("adj_close", "adj_close", PRICE_REL_TOL,  False),
    ]

    # Track max drift per field
    warn_fields: set[str] = set()
    fail_fields: set[str] = set()

    for ts_col, jq_col, tol, is_factor in comparisons:
        if ts_col not in ts_indexed.columns or jq_col not in jq_indexed.columns:
            continue

        max_drift = 0.0
        drift_dates = []

        for d in common_dates:
            if d not in ts_indexed.index or d not in jq_indexed.index:
                continue
            ts_row = ts_indexed.loc[d]
            jq_row = jq_indexed.loc[d]

            # Handle potential duplicates by taking first row
            ts_val = float(ts_row[ts_col].iloc[0]) if hasattr(ts_row[ts_col], "iloc") else float(ts_row[ts_col])
            jq_val = float(jq_row[jq_col].iloc[0]) if hasattr(jq_row[jq_col], "iloc") else float(jq_row[jq_col])

            if np.isnan(ts_val) or np.isnan(jq_val):
                continue

            if is_factor:
                drift = abs(ts_val - jq_val)
            else:
                drift = _rel_diff(ts_val, jq_val)

            max_drift = max(max_drift, drift)
            if drift > tol:
                drift_dates.append((d, ts_val, jq_val, drift))

        field_name = ts_col
        result["field_max_drift"][field_name] = round(max_drift, 6)

        if drift_dates:
            if max_drift > tol * 5:
                fail_fields.add(field_name)
            else:
                warn_fields.add(field_name)

            if verbose:
                for d, tv, jv, dr in drift_dates[:10]:
                    result["details"].append(
                        f"  {d} {field_name}: ts={tv:.4f} jq={jv:.4f} drift={dr:.4%}"
                    )

    # Factor comparison (if jq has factor column)
    if "factor" in jq_indexed.columns:
        max_factor_drift = 0.0
        for d in common_dates:
            if d not in jq_indexed.index:
                continue
            jq_row = jq_indexed.loc[d]
            jq_factor = float(jq_row["factor"].iloc[0]) if hasattr(jq_row["factor"], "iloc") else float(jq_row["factor"])
            if np.isnan(jq_factor):
                continue
            # We don't have factor in ts_df directly, so just record jq's factor range
            max_factor_drift = max(max_factor_drift, abs(jq_factor))
        result["field_max_drift"]["factor_range"] = round(max_factor_drift, 6)

    # Date coverage warnings
    if ts_only:
        warn_fields.add("date_coverage")

    # Final status
    if fail_fields:
        result["status"] = f"FAIL({','.join(sorted(fail_fields))})"
    elif warn_fields:
        result["status"] = f"WARN({','.join(sorted(warn_fields))})"
    else:
        result["status"] = "OK"

    return result


# ── Volume comparison (separate because units may differ) ───────────

def compare_volume(ts_df: pd.DataFrame, jq_df: pd.DataFrame, common_dates: list[str]) -> dict:
    """Check volume alignment — Tushare is in shares, JQData is in shares."""
    ts_indexed = ts_df.set_index(ts_df["date"].dt.strftime("%Y-%m-%d"))
    jq_indexed = jq_df.set_index(jq_df["date"].dt.strftime("%Y-%m-%d"))

    diffs = []
    for d in common_dates:
        if d not in ts_indexed.index or d not in jq_indexed.index:
            continue
        ts_row = ts_indexed.loc[d]
        jq_row = jq_indexed.loc[d]
        ts_vol = float(ts_row["volume"].iloc[0]) if hasattr(ts_row["volume"], "iloc") else float(ts_row["volume"])
        jq_vol = float(jq_row["volume"].iloc[0]) if hasattr(jq_row["volume"], "iloc") else float(jq_row["volume"])
        if ts_vol > 0 and jq_vol > 0:
            drift = _rel_diff(ts_vol, jq_vol)
            if drift > 0.01:
                diffs.append((d, ts_vol, jq_vol, drift))

    return {"volume_drift_days": len(diffs), "volume_diffs_sample": diffs[:5]}


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Cross-source data verification: Tushare provider chain vs JQData.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/verify_data_with_jqdata.py --symbols 000001.SZ,600000.SH --start 2025-06-01 --end 2025-12-31
    python scripts/verify_data_with_jqdata.py --symbols 510300.SH --start 2025-06-01 --end 2025-12-31 --verbose

Environment variables required:
    JQDATA_USERNAME  — JQData (聚宽) phone number
    JQDATA_PASSWORD  — JQData password
    TUSHARE_TOKEN    — Tushare Pro API token (for provider chain)

Note: Free JQData accounts can only access data from ~15 months ago to ~3 months ago.
        """,
    )
    parser.add_argument(
        "--symbols", required=True,
        help="Comma-separated Tushare-format symbols (e.g. 000001.SZ,600000.SH)",
    )
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-date drift details")

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        logger.error("No symbols provided")
        sys.exit(2)

    try:
        start = _parse_date(args.start)
        end = _parse_date(args.end)
    except ValueError as e:
        logger.error("Invalid date format: %s", e)
        sys.exit(2)

    if start >= end:
        logger.error("start must be before end")
        sys.exit(2)

    # ── Run comparison ───────────────────────────────────────────────

    results: list[dict] = []
    has_fail = False

    for sym in symbols:
        logger.info("=" * 60)
        logger.info("Comparing %s  [%s ~ %s]", sym, start, end)

        ts_df = fetch_tushare_df(sym, start, end)
        jq_df = fetch_jqdata_df(sym, start, end)

        if ts_df is None:
            logger.warning("  Tushare: no data")
            results.append({"symbol": sym, "status": "FAIL(no_ts_data)"})
            has_fail = True
            continue
        if jq_df is None:
            logger.warning("  JQData: no data")
            results.append({"symbol": sym, "status": "FAIL(no_jq_data)"})
            has_fail = True
            continue

        logger.info("  Tushare: %d bars, JQData: %d bars", len(ts_df), len(jq_df))

        r = compare_symbol(sym, ts_df, jq_df, verbose=args.verbose)
        results.append(r)

        # Log coverage
        cov = r["date_coverage"]
        logger.info("  Date coverage: %d common, %d ts-only, %d jq-only",
                     cov["common"], cov["ts_only"], cov["jq_only"])
        if cov["ts_only"] > 0:
            logger.info("    TS-only sample: %s", cov.get("ts_only_dates", []))
        if cov["jq_only"] > 0:
            logger.info("    JQ-only sample: %s", cov.get("jq_only_dates", []))

        # Log max drift per field
        for field, drift in r.get("field_max_drift", {}).items():
            logger.info("  Max drift %-10s: %.4f%%", field, drift * 100)

        # Log details
        for line in r.get("details", []):
            logger.info(line)

        # Volume comparison
        ts_dates = set(ts_df["date"].dt.strftime("%Y-%m-%d"))
        jq_dates = set(jq_df["date"].dt.strftime("%Y-%m-%d"))
        common = sorted(ts_dates & jq_dates)
        vol_info = compare_volume(ts_df, jq_df, common)
        if vol_info["volume_drift_days"] > 0:
            logger.info("  Volume drift on %d dates", vol_info["volume_drift_days"])
            for d, tv, jv, dr in vol_info["volume_diffs_sample"]:
                logger.info("    %s: ts=%d jq=%d drift=%.2f%%", d, int(tv), int(jv), dr * 100)

        # Status
        status = r["status"]
        if status.startswith("FAIL"):
            has_fail = True
        logger.info("  Status: %s", status)

    # ── Summary ──────────────────────────────────────────────────────

    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("-" * 60)
    ok_count = sum(1 for r in results if r["status"] == "OK")
    warn_count = sum(1 for r in results if r["status"].startswith("WARN"))
    fail_count = sum(1 for r in results if r["status"].startswith("FAIL"))

    for r in results:
        logger.info("  %-12s %s", r["symbol"], r["status"])

    logger.info("-" * 60)
    logger.info("  Total: %d  OK: %d  WARN: %d  FAIL: %d",
                len(results), ok_count, warn_count, fail_count)
    logger.info("  Pass rate: %.0f%%", 100 * ok_count / max(len(results), 1))

    sys.exit(1 if has_fail else 0)


if __name__ == "__main__":
    main()
