#!/usr/bin/env python3
"""Bulk data download + cross-validation + parquet output for ez-trading.

Downloads A-share stocks, ETFs, and indices from Tushare + BaoStock,
cross-validates against AKShare, and writes parquet files only if
the validation gate passes (0 errors).

Usage:
    python scripts/build_data_cache.py                        # Full A-share + ETF, 5 years
    python scripts/build_data_cache.py --etf-only             # Seed data (ETF pool only)
    python scripts/build_data_cache.py --no-verify            # Skip cross-validation
    python scripts/build_data_cache.py --symbols 000001.SZ,510300.SH
    python scripts/build_data_cache.py --start 2020-01-01 --end 2025-12-31
    python scripts/build_data_cache.py --periods daily,weekly,monthly
    python scripts/build_data_cache.py --exclude-symbols 162411.SZ
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────

SEED_ETFS = sorted([
    "159531.SZ", "159813.SZ", "159851.SZ", "159852.SZ", "159869.SZ",
    "159915.SZ", "159985.SZ", "162411.SZ", "510300.SH", "510500.SH",
    "510880.SH", "512010.SH", "512660.SH", "512690.SH", "512980.SH",
    "513100.SH", "513260.SH", "513600.SH", "513660.SH", "513880.SH",
    "515100.SH", "515220.SH", "515700.SH", "515880.SH", "518880.SH",
])

INDEX_SYMBOLS = ["000300.SH", "000905.SH", "000852.SH", "000001.SH", "399006.SZ"]

TUSHARE_API_URL = "https://api.tushare.pro"
TUSHARE_RATE_DELAY = 0.15       # 500/min rate limit, 0.15s between calls
TUSHARE_MAX_RETRIES = 3
TUSHARE_BACKOFF_BASE = 1.0      # seconds, doubles on each retry

# Cross-validation thresholds (in percentage points of daily return)
CV_ERROR_THRESHOLD = 1.0        # > 1pp = ERROR
CV_WARNING_THRESHOLD = 0.1      # > 0.1pp = WARNING

# Output
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
ROW_GROUP_SIZE = 100_000
PARQUET_COMPRESSION = "snappy"

# Parquet column order (matches Bar dataclass)
PARQUET_COLUMNS = [
    "time", "symbol", "market",
    "open", "high", "low", "close", "adj_close",
    "volume",
]


# ── Tushare HTTP client ──────────────────────────────────────────────

class TushareClient:
    """Minimal Tushare Pro HTTP client. No SDK dependency."""

    def __init__(self, token: str, timeout: int = 30):
        import httpx
        self._token = token
        self._client = httpx.Client(timeout=timeout)
        self._last_call_time: float = 0.0

    def close(self):
        self._client.close()

    def call(
        self, api_name: str, params: dict, fields: str,
    ) -> dict | None:
        """Call Tushare API with rate-limit throttling + exponential backoff.

        Returns {"fields": [...], "items": [[...], ...]} or None if empty.
        """
        last_error: Exception | None = None

        for attempt in range(TUSHARE_MAX_RETRIES):
            self._throttle()
            payload = {
                "api_name": api_name,
                "token": self._token,
                "params": params,
                "fields": fields,
            }
            try:
                resp = self._client.post(TUSHARE_API_URL, json=payload)
                resp.raise_for_status()
                body = resp.json()
            except Exception as e:
                raise RuntimeError(f"Tushare HTTP error ({api_name}): {e}") from e

            code = body.get("code", -1)
            if code == 0:
                data = body.get("data")
                if not data or not data.get("items"):
                    return None
                return data

            msg = body.get("msg", "unknown error")
            if code == 2002 and attempt < TUSHARE_MAX_RETRIES - 1:
                wait = TUSHARE_BACKOFF_BASE * (2 ** attempt)
                print(f"  [RATE] Tushare rate limited ({api_name}), "
                      f"retry {attempt + 1}/{TUSHARE_MAX_RETRIES} in {wait:.1f}s: {msg}")
                time.sleep(wait)
                last_error = RuntimeError(f"Tushare rate limited ({api_name}): {msg}")
                continue

            raise RuntimeError(f"Tushare API error ({api_name}, code={code}): {msg}")

        if last_error:
            raise last_error
        return None

    def _throttle(self):
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < TUSHARE_RATE_DELAY:
            time.sleep(TUSHARE_RATE_DELAY - elapsed)
        self._last_call_time = time.monotonic()


# ── Utility functions ─────────────────────────────────────────────────

def _date_to_ts(d: date) -> str:
    """date -> 'YYYYMMDD' for Tushare."""
    return d.strftime("%Y%m%d")


def _ts_to_datetime(s: str) -> datetime:
    """'YYYYMMDD' -> datetime."""
    return datetime.strptime(s, "%Y%m%d")


def _is_etf(symbol: str) -> bool:
    """Check if symbol is an ETF (51xxxx.SH, 15xxxx.SZ, 16xxxx.SZ)."""
    code = symbol.split(".")[0] if "." in symbol else symbol
    return code.startswith(("51", "15", "16"))


def _load_token() -> str:
    """Load Tushare token from env var or .env file."""
    token = os.environ.get("TUSHARE_TOKEN", "")
    if token:
        return token

    # Try .env file in project root
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN=") and not line.startswith("#"):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return ""


def _md5(path: Path) -> str:
    """Compute MD5 hex digest of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _print_header(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def _print_step(msg: str):
    print(f"\n--- {msg}")


# ── Step 1: Download stocks from Tushare ──────────────────────────────

def download_stocks(
    client: TushareClient,
    trading_days: list[str],
    exclude_symbols: set[str],
) -> pd.DataFrame:
    """Download all A-share stock daily data by trade_date batch.

    Each API call fetches ALL stocks for a single trade_date.
    Returns DataFrame with columns matching PARQUET_COLUMNS.
    """
    _print_step(f"Downloading stocks: {len(trading_days)} trading days")
    all_rows: list[dict] = []
    total = len(trading_days)

    for i, td in enumerate(trading_days):
        if (i + 1) % 50 == 0 or i == 0 or i == total - 1:
            print(f"  [{i+1}/{total}] trade_date={td}")

        data = client.call(
            api_name="daily",
            params={"trade_date": td},
            fields="ts_code,trade_date,open,high,low,close,vol,amount",
        )
        if not data:
            continue

        fields = data["fields"]
        idx = {f: j for j, f in enumerate(fields)}
        for row in data["items"]:
            sym = row[idx["ts_code"]]
            if sym in exclude_symbols:
                continue
            # Skip ETFs (they use BaoStock path)
            if _is_etf(sym):
                continue
            try:
                vol_raw = row[idx["vol"]]
                volume = int(float(vol_raw) * 100) if vol_raw is not None else 0
                all_rows.append({
                    "trade_date_str": td,
                    "symbol": sym,
                    "open": float(row[idx["open"]]),
                    "high": float(row[idx["high"]]),
                    "low": float(row[idx["low"]]),
                    "close": float(row[idx["close"]]),
                    "volume": volume,
                })
            except (ValueError, TypeError, KeyError):
                continue

    print(f"  Total stock rows: {len(all_rows):,}")
    if not all_rows:
        return pd.DataFrame(columns=PARQUET_COLUMNS)

    df = pd.DataFrame(all_rows)
    df["time"] = pd.to_datetime(df["trade_date_str"], format="%Y%m%d")
    df["market"] = "cn_stock"
    df.drop(columns=["trade_date_str"], inplace=True)
    return df


# ── Step 2: Download adj_factor and compute adj_close ─────────────────

def download_adj_factors(
    client: TushareClient,
    trading_days: list[str],
    symbols_needed: set[str],
) -> pd.DataFrame:
    """Download adj_factor for all stocks by trade_date batch.

    Returns DataFrame with columns: symbol, trade_date_str, adj_factor.
    """
    _print_step(f"Downloading adj_factor: {len(trading_days)} trading days")
    all_rows: list[dict] = []
    total = len(trading_days)

    for i, td in enumerate(trading_days):
        if (i + 1) % 50 == 0 or i == 0 or i == total - 1:
            print(f"  [{i+1}/{total}] trade_date={td}")

        data = client.call(
            api_name="adj_factor",
            params={"trade_date": td},
            fields="ts_code,trade_date,adj_factor",
        )
        if not data:
            continue

        fields = data["fields"]
        idx = {f: j for j, f in enumerate(fields)}
        for row in data["items"]:
            sym = row[idx["ts_code"]]
            if sym not in symbols_needed:
                continue
            try:
                af = float(row[idx["adj_factor"]])
                assert af > 0, f"adj_factor <= 0 for {sym} on {td}: {af}"
                all_rows.append({
                    "symbol": sym,
                    "trade_date_str": td,
                    "adj_factor": af,
                })
            except (ValueError, TypeError, KeyError, AssertionError) as e:
                print(f"  [WARN] adj_factor skip: {e}")
                continue

    print(f"  Total adj_factor rows: {len(all_rows):,}")
    if not all_rows:
        return pd.DataFrame(columns=["symbol", "trade_date_str", "adj_factor"])
    return pd.DataFrame(all_rows)


def merge_adj_close(df_stocks: pd.DataFrame, df_adj: pd.DataFrame) -> pd.DataFrame:
    """Compute adj_close = close * adj_factor / latest_factor (per symbol).

    Edge case: symbols with no adj_factor → fallback adj_close = close.
    """
    _print_step("Computing adj_close")
    if df_stocks.empty:
        df_stocks["adj_close"] = pd.Series(dtype=float)
        return df_stocks

    # Need trade_date_str for merge key
    if "trade_date_str" not in df_stocks.columns:
        df_stocks["trade_date_str"] = df_stocks["time"].dt.strftime("%Y%m%d")

    if df_adj.empty:
        print("  [WARN] No adj_factor data. Using adj_close = close for all stocks.")
        df_stocks["adj_close"] = df_stocks["close"]
        return df_stocks

    # latest_factor = per-symbol max adj_factor
    latest_factors = df_adj.groupby("symbol")["adj_factor"].max().rename("latest_factor")

    # Merge adj_factor onto stock data
    df_merged = df_stocks.merge(
        df_adj[["symbol", "trade_date_str", "adj_factor"]],
        on=["symbol", "trade_date_str"],
        how="left",
    )

    # Merge latest_factor
    df_merged = df_merged.merge(latest_factors, on="symbol", how="left")

    # Compute adj_close; fallback to close if adj_factor is NaN
    has_adj = df_merged["adj_factor"].notna() & df_merged["latest_factor"].notna()
    df_merged["adj_close"] = df_merged["close"]  # default fallback
    df_merged.loc[has_adj, "adj_close"] = (
        df_merged.loc[has_adj, "close"]
        * df_merged.loc[has_adj, "adj_factor"]
        / df_merged.loc[has_adj, "latest_factor"]
    ).round(4)

    # Count symbols without adj_factor
    syms_no_adj = set(df_merged.loc[~has_adj, "symbol"].unique())
    if syms_no_adj:
        print(f"  [WARN] {len(syms_no_adj)} symbols have no adj_factor "
              f"(fallback adj_close=close). First 10: {sorted(syms_no_adj)[:10]}")

    n_adj = has_adj.sum()
    print(f"  Rows with adj_factor: {n_adj:,} / {len(df_merged):,}")

    # Drop helper columns
    df_merged.drop(columns=["adj_factor", "latest_factor", "trade_date_str"],
                   inplace=True, errors="ignore")
    return df_merged


    # NOTE: BaoStock does NOT support ETF K-line data. query_all_stock returns
    # 0 ETF codes (51/15/16 prefix), query_history_k_data_plus returns 0 rows.
    # BaoStock function removed — Tushare fund_daily + fund_adj is the primary
    # ETF source, AKShare is the fallback.


# ── Step 3b: Download ETFs from AKShare (fallback when BaoStock fails) ──

def download_etfs_akshare(
    etf_symbols: list[str],
    start_date: str,
    end_date: str,
    exclude_symbols: set[str],
) -> pd.DataFrame:
    """Download ETF data from AKShare (raw + qfq) as BaoStock fallback.

    BaoStock query_history_k_data_plus does NOT support ETF codes (51/15/16).
    AKShare fund_etf_hist_em works for all ETFs.
    """
    try:
        import akshare as ak
    except ImportError:
        print("  [ERROR] akshare not installed. pip install akshare")
        return pd.DataFrame(columns=PARQUET_COLUMNS)

    _print_step(f"Downloading {len(etf_symbols)} ETFs from AKShare (BaoStock fallback)")
    all_rows: list[dict] = []
    sanity_failures: list[str] = []

    for sym in etf_symbols:
        if sym in exclude_symbols:
            print(f"  [SKIP] {sym} (excluded)")
            continue
        code = sym.split(".")[0]
        try:
            import time as _time
            _time.sleep(0.6)  # AKShare throttle

            # Raw (unadjusted)
            df_raw = ak.fund_etf_hist_em(symbol=code, adjust="",
                                          start_date=start_date.replace("-", ""),
                                          end_date=end_date.replace("-", ""))
            _time.sleep(0.6)

            # Forward-adjusted (qfq)
            df_qfq = ak.fund_etf_hist_em(symbol=code, adjust="qfq",
                                          start_date=start_date.replace("-", ""),
                                          end_date=end_date.replace("-", ""))

            if df_raw is None or df_raw.empty:
                print(f"  [WARN] No data for {sym}")
                continue

            # Sanity check: raw vs qfq returns within 1bp
            if df_qfq is not None and not df_qfq.empty and len(df_raw) > 5:
                raw_rets = df_raw["收盘"].astype(float).pct_change().dropna().values
                qfq_rets = df_qfq["收盘"].astype(float).pct_change().dropna().values
                min_len = min(len(raw_rets), len(qfq_rets))
                if min_len > 0:
                    max_diff = float(np.nanmax(np.abs(raw_rets[:min_len] - qfq_rets[:min_len])))
                    if max_diff > 0.0001:
                        sanity_failures.append(f"{sym}: raw/qfq diff={max_diff:.6f}")

            for _, row in df_raw.iterrows():
                dt_str = str(row["日期"]).replace("-", "")
                raw_close = float(row["收盘"])
                # Find matching qfq close
                adj_close = raw_close
                if df_qfq is not None and not df_qfq.empty:
                    qfq_match = df_qfq[df_qfq["日期"].astype(str) == str(row["日期"])]
                    if not qfq_match.empty:
                        adj_close = round(float(qfq_match.iloc[0]["收盘"]), 4)

                all_rows.append({
                    "time": pd.Timestamp(str(row["日期"])),
                    "symbol": sym,
                    "market": "cn_stock",
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                    "close": raw_close,
                    "adj_close": adj_close,
                    "volume": int(float(row["成交量"])),
                })
            print(f"  {sym} → {len(df_raw)} rows")
        except Exception as e:
            print(f"  [ERROR] {sym}: {e}")

    if sanity_failures:
        print(f"  [WARN] raw/qfq sanity issues: {sanity_failures}")

    print(f"  Total ETF rows: {len(all_rows):,}")
    if not all_rows:
        return pd.DataFrame(columns=PARQUET_COLUMNS)
    return pd.DataFrame(all_rows)


# ── Step 3c: Download ETFs from Tushare fund_daily ────────────────────

def download_etfs_tushare(
    client: "TushareClient",
    etf_symbols: list[str],
    start_date: date,
    end_date: date,
    exclude_symbols: set[str],
) -> pd.DataFrame:
    """Download ETF data from Tushare fund_daily + fund_adj for correct adj_close.

    ETFs can have distributions (dividends), so close != adj_close.
    Uses fund_adj API for forward-adjustment: adj_close = close * factor / latest_factor.
    """
    _print_step(f"Downloading {len(etf_symbols)} ETFs from Tushare fund_daily + fund_adj")
    all_rows: list[dict] = []
    ts_start = _date_to_ts(start_date)
    ts_end = _date_to_ts(end_date)

    for sym in etf_symbols:
        if sym in exclude_symbols:
            print(f"  [SKIP] {sym} (excluded)")
            continue
        # Fetch OHLCV
        data = client.call(
            api_name="fund_daily",
            params={"ts_code": sym, "start_date": ts_start, "end_date": ts_end},
            fields="ts_code,trade_date,open,high,low,close,vol,amount",
        )
        if data is None or not data.get("items"):
            print(f"  [WARN] No data for {sym}")
            continue

        # Fetch adj_factor for this ETF
        adj_data = client.call(
            api_name="fund_adj",
            params={"ts_code": sym, "start_date": ts_start, "end_date": ts_end},
            fields="ts_code,trade_date,adj_factor",
        )
        adj_map: dict[str, float] = {}
        if adj_data and adj_data.get("items"):
            adj_fields = adj_data["fields"]
            adj_idx = {f: i for i, f in enumerate(adj_fields)}
            for arow in adj_data["items"]:
                try:
                    af = float(arow[adj_idx["adj_factor"]])
                    if af > 0:
                        adj_map[arow[adj_idx["trade_date"]]] = af
                except (ValueError, TypeError):
                    continue
        latest_factor = max(adj_map.values()) if adj_map else 1.0

        fields = data["fields"]
        idx = {f: i for i, f in enumerate(fields)}
        count = 0
        for row in data["items"]:
            try:
                close_val = float(row[idx["close"]])
                trade_date = row[idx["trade_date"]]
                vol_raw = row[idx.get("vol", idx.get("amount", -1))]
                volume = int(float(vol_raw) * 100) if vol_raw is not None else 0

                # Forward-adjusted close using fund_adj
                if trade_date in adj_map and latest_factor > 0:
                    adj_close = round(close_val * adj_map[trade_date] / latest_factor, 4)
                else:
                    adj_close = close_val

                all_rows.append({
                    "time": pd.Timestamp(_ts_to_datetime(trade_date)),
                    "symbol": sym,
                    "market": "cn_stock",
                    "open": float(row[idx["open"]]),
                    "high": float(row[idx["high"]]),
                    "low": float(row[idx["low"]]),
                    "close": close_val,
                    "adj_close": adj_close,
                    "volume": volume,
                })
                count += 1
            except (ValueError, KeyError, TypeError):
                continue
        print(f"  {sym} → {count} rows (adj_factor: {len(adj_map)} entries)")

    print(f"  Total ETF rows: {len(all_rows):,}")
    if not all_rows:
        return pd.DataFrame(columns=PARQUET_COLUMNS)
    return pd.DataFrame(all_rows)


# ── Step 4: Download indices from Tushare ─────────────────────────────

def download_indices(
    client: TushareClient,
    index_symbols: list[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Download index daily data from Tushare index_daily.

    IMPORTANT: Stores with market='cn_stock' (not 'cn_index') because
    _ensure_benchmark() queries with the portfolio's market string.
    """
    _print_step(f"Downloading {len(index_symbols)} indices from Tushare")
    all_rows: list[dict] = []

    ts_start = _date_to_ts(start_date)
    ts_end = _date_to_ts(end_date)

    for sym in index_symbols:
        print(f"  Fetching index {sym} ...")
        data = client.call(
            api_name="index_daily",
            params={
                "ts_code": sym,
                "start_date": ts_start,
                "end_date": ts_end,
            },
            fields="ts_code,trade_date,open,high,low,close,vol",
        )
        if not data:
            print(f"    [WARN] No data for index {sym}")
            continue

        fields = data["fields"]
        idx = {f: j for j, f in enumerate(fields)}
        count = 0
        for row in data["items"]:
            try:
                td = row[idx["trade_date"]]
                dt = _ts_to_datetime(td)
                vol_raw = row[idx["vol"]]
                volume = int(float(vol_raw) * 100) if vol_raw is not None else 0
                close = float(row[idx["close"]])
                all_rows.append({
                    "time": dt,
                    "symbol": sym,
                    "market": "cn_stock",  # NOT cn_index — see docstring
                    "open": float(row[idx["open"]]),
                    "high": float(row[idx["high"]]),
                    "low": float(row[idx["low"]]),
                    "close": close,
                    "adj_close": close,  # indices have no adjustment
                    "volume": volume,
                })
                count += 1
            except (ValueError, TypeError, KeyError, IndexError):
                continue
        print(f"    rows: {count}")

    print(f"  Total index rows: {len(all_rows):,}")
    if not all_rows:
        return pd.DataFrame(columns=PARQUET_COLUMNS)
    return pd.DataFrame(all_rows)


# ── Step 5: Cross-validation against AKShare ──────────────────────────

def sanitize_adj_close(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """V2.18.1: 修复 Tushare fund_adj 的 adj_close 异常 (Type A).

    检测和修复两类 factor 异常:
    - 规则 C: 历史初期 factor≈1.0 的连续段, 用后续稳定 factor 反向修复
      (典型 case: 159901.SZ 2018-01 之前 factor=1.0, 之后降到 0.488)
    - 规则 B-adj: adj_close daily return > 15% 但 raw_close < 10% 变动
      (factor 异常, 非真实市场事件). 用 raw ret 修复 adj_close.
      (典型 case: 512890.SH 2020-09-18 factor spike 到 1.0)

    不处理 Type B (raw 和 adj 同步大变动) — 可能是真实涨停/分红/拆分, 或者
    Tushare 对 raw_close 本身的 bug. 后者需要外部数据源 (如 Tencent qfq) 重建,
    由 validation/fix_adj_close_v2.py 离线处理.

    修复后会对异常未清除的 symbol 发 warning, 交由 cross-validation 最终判断.

    Returns:
        (df_sanitized, stats) — stats 包含 rule_c_fixed, rule_b_fixed, flagged
    """
    _print_step("Sanitizing adj_close anomalies (V2.18.1)")
    if df.empty:
        return df, {"rule_c_fixed": 0, "rule_b_fixed": 0, "flagged": 0}

    df = df.sort_values(["symbol", "time"]).reset_index(drop=True)
    new_adj = df["adj_close"].values.copy()
    stats = {"rule_c_fixed": 0, "rule_b_fixed": 0, "flagged": 0,
             "flagged_syms": []}

    for sym, g in df.groupby("symbol"):
        idx = g.index.values
        close = g["close"].values
        adj = new_adj[idx].copy()
        n = len(adj)
        if n < 10:
            continue

        # 规则 C: 历史初期 factor=1 连续段
        factor = np.where(close > 0, adj / close, 0.0)
        for i in range(1, n - 5):
            if factor[i - 1] > 0.95 and 0.1 < factor[i] < 0.95:
                post = factor[i:i + 5]
                post = post[post > 0]
                if len(post) >= 3 and post.std() < 0.05:
                    true_factor = float(factor[i])
                    n_fixed_c = 0
                    for j in range(i):
                        if close[j] > 0 and factor[j] > 0.95:
                            adj[j] = close[j] * true_factor
                            n_fixed_c += 1
                    stats["rule_c_fixed"] += n_fixed_c
                    break  # 每个 symbol 只修一次历史初期

        # 规则 B-adj: factor 异常 (adj 大变动 + raw 小变动)
        for i in range(1, n):
            if adj[i - 1] <= 0 or adj[i] <= 0:
                continue
            if close[i - 1] <= 0 or close[i] <= 0:
                continue
            ret_in = (adj[i] - adj[i - 1]) / adj[i - 1]
            if abs(ret_in) < 0.15:
                continue
            raw_ret = (close[i] - close[i - 1]) / close[i - 1]
            # Type A: adj 大变动, raw 小变动
            if abs(raw_ret) < 0.1:
                adj[i] = adj[i - 1] * (1 + raw_ret)
                stats["rule_b_fixed"] += 1

        new_adj[idx] = adj

        # 检查修复后是否还有 > 50% 的 adj_close daily return (可能是 Type B)
        final_ret = np.diff(adj) / (adj[:-1] + 1e-9)
        if np.any(np.abs(final_ret) > 0.5):
            stats["flagged"] += 1
            stats["flagged_syms"].append(sym)

    df["adj_close"] = new_adj

    print(f"  规则 C (历史 factor): {stats['rule_c_fixed']:,} 日修复")
    print(f"  规则 B-adj (factor spike): {stats['rule_b_fixed']:,} 日修复")
    if stats["flagged"] > 0:
        print(f"  [WARN] {stats['flagged']} 个 symbol 仍有 > 50% 单日异常, "
              f"可能需要 Tencent 重建:")
        for s in stats["flagged_syms"][:10]:
            print(f"    {s}")
        if len(stats["flagged_syms"]) > 10:
            print(f"    ... {len(stats['flagged_syms']) - 10} more")
        print(f"  运行 `python validation/fix_adj_close_v2.py` 做 Tencent 重建")
    else:
        print(f"  ✓ 所有 symbol 修复后无 > 50% adj_close 异常")

    return df, stats


def cross_validate(
    df: pd.DataFrame,
    start_date: date,
    end_date: date,
    sample_stocks: int = 20,
    sample_etfs: int = 10,
    exclude_symbols: set[str] | None = None,
) -> dict:
    """Compare daily returns between primary data and AKShare.

    Samples a subset of stocks and ETFs to avoid overwhelming AKShare.
    Returns validation report dict.
    """
    _print_step("Cross-validating against AKShare")
    exclude_symbols = exclude_symbols or set()

    try:
        import akshare as ak
    except ImportError:
        print("  [WARN] akshare not installed. Skipping cross-validation.")
        return {"skipped": True, "reason": "akshare not installed",
                "errors": [], "warnings": [], "clean": []}

    if df.empty:
        print("  [WARN] No data to validate.")
        return {"skipped": True, "reason": "empty dataset",
                "errors": [], "warnings": [], "clean": []}

    # Pick sample symbols: some stocks + some ETFs + all indices
    all_symbols = df["symbol"].unique()
    stocks = [s for s in all_symbols if not _is_etf(s) and s not in INDEX_SYMBOLS
              and s not in exclude_symbols]
    etfs = [s for s in all_symbols if _is_etf(s) and s not in exclude_symbols]
    indices = [s for s in all_symbols if s in INDEX_SYMBOLS]

    rng = np.random.default_rng(42)
    sampled_stocks = sorted(rng.choice(stocks, size=min(sample_stocks, len(stocks)),
                                       replace=False)) if stocks else []
    sampled_etfs = sorted(rng.choice(etfs, size=min(sample_etfs, len(etfs)),
                                     replace=False)) if etfs else []
    sample_syms = list(sampled_stocks) + list(sampled_etfs) + indices
    print(f"  Validating {len(sample_syms)} symbols "
          f"({len(sampled_stocks)} stocks + {len(sampled_etfs)} ETFs + {len(indices)} indices)")

    errors: list[dict] = []
    warnings: list[dict] = []
    clean: list[str] = []

    ts_start = start_date.strftime("%Y%m%d")
    ts_end = end_date.strftime("%Y%m%d")

    for i, sym in enumerate(sample_syms):
        if (i + 1) % 5 == 0 or i == 0:
            print(f"  [{i+1}/{len(sample_syms)}] Validating {sym} ...")

        # Get primary data returns
        sym_df = df[df["symbol"] == sym].sort_values("time")
        if len(sym_df) < 2:
            print(f"    [SKIP] {sym}: fewer than 2 rows")
            continue

        primary_ret = sym_df.set_index("time")["adj_close"].pct_change().dropna()
        if primary_ret.empty:
            continue

        # Fetch AKShare data
        time.sleep(0.6)  # AKShare rate limit
        code = sym.split(".")[0] if "." in sym else sym
        is_etf_sym = _is_etf(sym)
        is_index_sym = sym in INDEX_SYMBOLS

        try:
            if is_index_sym:
                # AKShare index
                index_map = {
                    "000300.SH": "000300", "000905.SH": "000905",
                    "000852.SH": "000852", "000001.SH": "000001",
                    "399006.SZ": "399006",
                }
                ak_code = index_map.get(sym, code)
                df_ak = ak.stock_zh_index_daily_em(symbol=ak_code)
            elif is_etf_sym:
                df_ak = ak.fund_etf_hist_em(
                    symbol=code, period="daily",
                    start_date=ts_start, end_date=ts_end, adjust="qfq",
                )
            else:
                df_ak = ak.stock_zh_a_hist(
                    symbol=code, period="daily",
                    start_date=ts_start, end_date=ts_end, adjust="qfq",
                )
        except Exception as e:
            print(f"    [WARN] AKShare fetch failed for {sym}: {e}")
            continue

        if df_ak is None or df_ak.empty:
            print(f"    [WARN] AKShare returned no data for {sym}")
            continue

        # Parse AKShare close and compute returns
        try:
            date_col = "日期" if "日期" in df_ak.columns else "date"
            close_col = "收盘" if "收盘" in df_ak.columns else "close"
            df_ak[date_col] = pd.to_datetime(df_ak[date_col])
            ak_ret = df_ak.set_index(date_col)[close_col].astype(float).pct_change().dropna()
        except Exception as e:
            print(f"    [WARN] AKShare parse failed for {sym}: {e}")
            continue

        # Align dates
        common = primary_ret.index.intersection(ak_ret.index)
        if len(common) < 2:
            print(f"    [WARN] {sym}: only {len(common)} common dates")
            continue

        p_ret = primary_ret.loc[common]
        a_ret = ak_ret.loc[common]
        diffs = (p_ret - a_ret).abs() * 100  # convert to percentage points

        max_diff = diffs.max()
        mean_diff = diffs.mean()
        error_days = int((diffs > CV_ERROR_THRESHOLD).sum())
        warn_days = int((diffs > CV_WARNING_THRESHOLD).sum())

        entry = {
            "symbol": sym,
            "common_days": int(len(common)),
            "max_diff_pp": round(float(max_diff), 4),
            "mean_diff_pp": round(float(mean_diff), 6),
            "error_days": error_days,
            "warning_days": warn_days,
        }

        if error_days > 0:
            errors.append(entry)
            print(f"    [ERROR] {sym}: {error_days} days with > {CV_ERROR_THRESHOLD}pp diff "
                  f"(max={max_diff:.4f}pp)")
        elif warn_days > 0:
            warnings.append(entry)
            print(f"    [WARNING] {sym}: {warn_days} days with > {CV_WARNING_THRESHOLD}pp diff "
                  f"(max={max_diff:.4f}pp)")
        else:
            clean.append(sym)

    report = {
        "timestamp": datetime.now().isoformat(),
        "sample_size": len(sample_syms),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "clean_count": len(clean),
        "errors": errors,
        "warnings": warnings,
        "clean": clean,
        "thresholds": {
            "error_pp": CV_ERROR_THRESHOLD,
            "warning_pp": CV_WARNING_THRESHOLD,
        },
    }

    print(f"\n  Validation summary:")
    print(f"    ERRORS:   {len(errors)}")
    print(f"    WARNINGS: {len(warnings)}")
    print(f"    CLEAN:    {len(clean)}")

    return report


# ── Step 6: Derive weekly/monthly from daily ──────────────────────────

def derive_period(df_daily: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample daily OHLCV to weekly (W-FRI) or monthly (ME).

    Uses standard OHLCV aggregation: first open, max high, min low,
    last close/adj_close, sum volume.
    """
    freq_label = "weekly" if "W" in freq else "monthly"
    _print_step(f"Deriving {freq_label} from daily ({freq})")

    if df_daily.empty:
        return pd.DataFrame(columns=PARQUET_COLUMNS)

    results = []
    grouped = df_daily.groupby(["symbol", "market"])

    for (sym, mkt), group in grouped:
        g = group.set_index("time").sort_index()
        resampled = g.resample(freq).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "adj_close": "last",
            "volume": "sum",
        }).dropna(subset=["close"])

        resampled["symbol"] = sym
        resampled["market"] = mkt
        resampled = resampled.reset_index()
        results.append(resampled)

    if not results:
        return pd.DataFrame(columns=PARQUET_COLUMNS)

    df_out = pd.concat(results, ignore_index=True)
    print(f"  {freq_label} rows: {len(df_out):,}")
    return df_out


# ── Step 7: Write parquet ─────────────────────────────────────────────

def write_parquet(df: pd.DataFrame, path: Path, label: str) -> dict:
    """Write DataFrame to parquet (sorted by symbol+time, snappy compression).

    Returns metadata dict with row count, file size, md5.
    """
    if df.empty:
        print(f"  [SKIP] {label}: no data")
        return {"rows": 0, "file_size": 0, "md5": ""}

    # Ensure correct column order
    for col in PARQUET_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"Missing column {col} in {label} DataFrame")
    df = df[PARQUET_COLUMNS].copy()

    # Sort by (symbol, time)
    df = df.sort_values(["symbol", "time"]).reset_index(drop=True)

    # Ensure types
    df["time"] = pd.to_datetime(df["time"])
    df["volume"] = df["volume"].astype(int)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(
        path,
        engine="pyarrow",
        compression=PARQUET_COMPRESSION,
        row_group_size=ROW_GROUP_SIZE,
        index=False,
    )

    file_size = path.stat().st_size
    md5 = _md5(path)
    print(f"  Written: {path.name} ({len(df):,} rows, {file_size/1024/1024:.1f} MB, md5={md5[:12]}...)")

    return {
        "rows": len(df),
        "file_size": file_size,
        "md5": md5,
    }


# ── Step 8: Write manifest.json ──────────────────────────────────────

def write_manifest(
    output_dir: Path,
    file_meta: dict[str, dict],
    start_date: date,
    end_date: date,
    df_daily: pd.DataFrame,
    args: argparse.Namespace,
    validation_report: dict,
) -> Path:
    """Write manifest.json with build metadata."""
    _print_step("Writing manifest.json")

    n_stocks = 0
    n_etfs = 0
    n_indices = 0
    if not df_daily.empty:
        syms = df_daily["symbol"].unique()
        n_indices = sum(1 for s in syms if s in INDEX_SYMBOLS)
        n_etfs = sum(1 for s in syms if _is_etf(s) and s not in INDEX_SYMBOLS)
        n_stocks = len(syms) - n_etfs - n_indices

    manifest = {
        "version": "1.0",
        "build_timestamp": datetime.now().isoformat(),
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "symbols": {
            "stocks": n_stocks,
            "etfs": n_etfs,
            "indices": n_indices,
            "total": n_stocks + n_etfs + n_indices,
        },
        "files": file_meta,
        "mode": "etf_only" if args.etf_only else ("custom" if args.symbols else "full"),
        "excluded_symbols": sorted(args.exclude_symbols.split(",")) if args.exclude_symbols else [],
        "validation": {
            "skipped": validation_report.get("skipped", False),
            "error_count": validation_report.get("error_count", 0),
            "warning_count": validation_report.get("warning_count", 0),
            "warnings": [w["symbol"] for w in validation_report.get("warnings", [])],
        },
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Written: {manifest_path}")
    return manifest_path


# ── Step 9: Get trading days from Tushare ─────────────────────────────

def get_trading_days(client: TushareClient, start_date: date, end_date: date) -> list[str]:
    """Fetch SSE trading calendar and return list of YYYYMMDD strings."""
    _print_step("Fetching trading calendar")
    data = client.call(
        api_name="trade_cal",
        params={
            "exchange": "SSE",
            "start_date": _date_to_ts(start_date),
            "end_date": _date_to_ts(end_date),
            "is_open": "1",
        },
        fields="cal_date",
    )
    if not data:
        raise RuntimeError("Failed to fetch trading calendar from Tushare")

    days = sorted(row[0] for row in data["items"])
    print(f"  Trading days: {len(days)} ({days[0]} to {days[-1]})")
    return days


# ── Main ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build parquet data cache for ez-trading platform.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--etf-only", action="store_true",
        help="Only download seed ETF pool + indices (faster, for initial setup).",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Skip cross-validation against AKShare.",
    )
    parser.add_argument(
        "--symbols", type=str, default="",
        help="Comma-separated list of specific symbols to download.",
    )
    parser.add_argument(
        "--start", type=str, default="",
        help="Start date (YYYY-MM-DD). Default: 5 years ago.",
    )
    parser.add_argument(
        "--end", type=str, default="",
        help="End date (YYYY-MM-DD). Default: yesterday.",
    )
    parser.add_argument(
        "--periods", type=str, default="daily,weekly,monthly",
        help="Comma-separated periods to generate (daily,weekly,monthly).",
    )
    parser.add_argument(
        "--exclude-symbols", type=str, default="",
        help="Comma-separated symbols to exclude (known-bad data).",
    )
    parser.add_argument(
        "--output-dir", type=str, default="",
        help="Output directory. Default: data/cache/",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    _print_header("ez-trading Data Cache Builder")

    # ── Parse arguments ───────────────────────────────────────────
    start_date = (
        date.fromisoformat(args.start)
        if args.start
        else date.today() - timedelta(days=5 * 365)
    )
    end_date = (
        date.fromisoformat(args.end)
        if args.end
        else date.today() - timedelta(days=1)
    )
    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    exclude_set = set(s.strip() for s in args.exclude_symbols.split(",") if s.strip())
    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR

    print(f"  Date range:  {start_date} to {end_date}")
    print(f"  Periods:     {periods}")
    print(f"  Mode:        {'ETF-only' if args.etf_only else ('custom' if args.symbols else 'full')}")
    if exclude_set:
        print(f"  Excluded:    {sorted(exclude_set)}")
    print(f"  Output:      {output_dir}")

    # ── Load Tushare token ────────────────────────────────────────
    token = _load_token()
    if not token:
        print("\n[FATAL] TUSHARE_TOKEN not found. Set it via:")
        print("  export TUSHARE_TOKEN=your_token_here")
        print("  or add TUSHARE_TOKEN=... to .env file")
        sys.exit(1)

    client = TushareClient(token)

    try:
        # ── Get trading calendar ──────────────────────────────────
        trading_days = get_trading_days(client, start_date, end_date)

        # ── Decide which symbols to download ──────────────────────
        custom_symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else []

        # ── Download stocks ───────────────────────────────────────
        if args.etf_only:
            # ETF-only mode: no stocks
            df_stocks = pd.DataFrame(columns=PARQUET_COLUMNS)
            print("\n  [ETF-ONLY] Skipping stock download.")
        elif custom_symbols:
            # Custom symbols: download only specified non-ETF symbols
            stock_syms = [s for s in custom_symbols if not _is_etf(s) and s not in INDEX_SYMBOLS]
            if stock_syms:
                # For custom symbols, we still use trade_date batch but filter
                df_stocks = download_stocks(client, trading_days, exclude_set)
                # Filter to only requested symbols
                df_stocks = df_stocks[df_stocks["symbol"].isin(stock_syms)]
            else:
                df_stocks = pd.DataFrame(columns=PARQUET_COLUMNS)
        else:
            # Full mode: download all stocks
            df_stocks = download_stocks(client, trading_days, exclude_set)

        # ── Download adj_factors and compute adj_close ────────────
        if not df_stocks.empty:
            symbols_in_stocks = set(df_stocks["symbol"].unique())
            df_adj = download_adj_factors(client, trading_days, symbols_in_stocks)
            df_stocks = merge_adj_close(df_stocks, df_adj)
        else:
            print("\n  [SKIP] No stocks to compute adj_close for.")

        # ── Download ETFs from BaoStock ───────────────────────────
        if args.etf_only:
            etf_list = SEED_ETFS
        elif custom_symbols:
            etf_list = [s for s in custom_symbols if _is_etf(s)]
        else:
            etf_list = SEED_ETFS  # Full mode also includes seed ETFs

        if etf_list:
            # Tushare fund_daily primary for ETFs (same data source as stocks)
            if client:
                df_etfs = download_etfs_tushare(
                    client, etf_list, start_date, end_date, exclude_set)
            else:
                df_etfs = pd.DataFrame(columns=PARQUET_COLUMNS)
            # AKShare fallback if Tushare returned nothing
            if df_etfs.empty:
                df_etfs = download_etfs_akshare(
                    etf_list,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    exclude_set,
                )
        else:
            df_etfs = pd.DataFrame(columns=PARQUET_COLUMNS)

        # ── Download indices ──────────────────────────────────────
        df_indices = download_indices(client, INDEX_SYMBOLS, start_date, end_date)

        # ── Combine all daily data ────────────────────────────────
        _print_step("Combining all daily data")
        frames = [f for f in [df_stocks, df_etfs, df_indices] if not f.empty]
        if not frames:
            print("\n[FATAL] No data downloaded. Check credentials and date range.")
            sys.exit(1)

        df_daily = pd.concat(frames, ignore_index=True)

        # Drop trade_date_str if still present
        if "trade_date_str" in df_daily.columns:
            df_daily.drop(columns=["trade_date_str"], inplace=True)

        # Deduplicate (symbol + time)
        before = len(df_daily)
        df_daily = df_daily.drop_duplicates(subset=["symbol", "time"], keep="last")
        if len(df_daily) < before:
            print(f"  Removed {before - len(df_daily):,} duplicate rows")

        n_syms = df_daily["symbol"].nunique()
        print(f"  Combined: {len(df_daily):,} rows, {n_syms:,} symbols")

        # ── V2.18.1: Sanitize adj_close anomalies ─────────────────────
        df_daily, sanitize_stats = sanitize_adj_close(df_daily)

        # ── Coverage completeness check (hard fail on missing required symbols) ──
        present_syms = set(df_daily["symbol"].unique())
        required_etfs = set(etf_list) - exclude_set if etf_list else set()
        required_indices = set(INDEX_SYMBOLS)
        missing_etfs = required_etfs - present_syms
        missing_indices = required_indices - present_syms
        if missing_etfs or missing_indices:
            print(f"\n[FATAL] Coverage incomplete — required symbols missing from downloaded data:")
            if missing_etfs:
                print(f"  Missing ETFs ({len(missing_etfs)}): {sorted(missing_etfs)}")
            if missing_indices:
                print(f"  Missing indices ({len(missing_indices)}): {sorted(missing_indices)}")
            print(f"  Fix: check API credentials/network, or --exclude-symbols to skip known-bad")
            sys.exit(1)
        print(f"  Coverage: all {len(required_etfs)} ETFs + {len(required_indices)} indices present ✓")

        # ── Cross-validation ──────────────────────────────────────
        validation_report: dict
        if args.no_verify:
            print("\n  [SKIP] Cross-validation (--no-verify)")
            validation_report = {
                "skipped": True, "reason": "--no-verify flag",
                "errors": [], "warnings": [], "clean": [],
                "error_count": 0, "warning_count": 0,
            }
        else:
            validation_report = cross_validate(
                df_daily, start_date, end_date,
                exclude_symbols=exclude_set,
            )

        # ── Always write validation_report.json ───────────────────
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "validation_report.json"
        report_path.write_text(
            json.dumps(validation_report, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n  Validation report: {report_path}")

        # ── Hard gate: errors > 0 → abort ─────────────────────────
        error_count = validation_report.get("error_count", 0)
        if error_count > 0:
            print(f"\n[FATAL] Cross-validation found {error_count} ERROR(s). "
                  f"No parquet files written.")
            print(f"  Use --exclude-symbols to skip known-bad symbols, "
                  f"or --no-verify to bypass.")
            sys.exit(1)

        # ── Write parquet files ───────────────────────────────────
        _print_header("Writing parquet files")
        file_meta: dict[str, dict] = {}

        if "daily" in periods:
            meta = write_parquet(
                df_daily,
                output_dir / "cn_stock_daily.parquet",
                "daily",
            )
            file_meta["cn_stock_daily.parquet"] = meta

        if "weekly" in periods:
            df_weekly = derive_period(df_daily, "W-FRI")
            meta = write_parquet(
                df_weekly,
                output_dir / "cn_stock_weekly.parquet",
                "weekly",
            )
            file_meta["cn_stock_weekly.parquet"] = meta

        if "monthly" in periods:
            df_monthly = derive_period(df_daily, "ME")
            meta = write_parquet(
                df_monthly,
                output_dir / "cn_stock_monthly.parquet",
                "monthly",
            )
            file_meta["cn_stock_monthly.parquet"] = meta

        # ── Write manifest.json ───────────────────────────────────
        write_manifest(
            output_dir, file_meta, start_date, end_date,
            df_daily, args, validation_report,
        )

        # ── Summary ───────────────────────────────────────────────
        _print_header("BUILD COMPLETE")
        print(f"  Output:     {output_dir}")
        print(f"  Date range: {start_date} to {end_date}")
        print(f"  Symbols:    {n_syms:,}")
        for fname, meta in file_meta.items():
            if meta["rows"] > 0:
                print(f"  {fname}: {meta['rows']:,} rows, "
                      f"{meta['file_size']/1024/1024:.1f} MB")

        warn_count = validation_report.get("warning_count", 0)
        if warn_count > 0:
            print(f"\n  WARNINGS: {warn_count} symbols had minor return discrepancies.")
            print(f"  See {report_path} for details.")

        print()

    finally:
        client.close()


if __name__ == "__main__":
    main()
