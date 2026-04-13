"""V2.17 round 7 regression: parquet cache must not silently truncate.

Real bug exposed during first paper-trading verification deployment:
- parquet cache manifest_end = 2026-04-08
- Deploy script requested end_date = 2026-04-13 (5 days after)
- Prior behaviour: V2.18 C4 guard allowed parquet use when
  `end_date <= manifest_end + 7 days` (grace period).
- Result: parquet returned bars only up to 2026-04-08, the last 5 days
  were silently missing. Strategy ran on stale signals.

Fix: guard changed to strict — `end_date > manifest_end` → skip
parquet, let the provider chain (DB → fetch) stitch complete data.

Test: synthetic parquet with manifest_end = day N; query up to day N+3;
verify `_find_parquet_cache` returns None so caller falls back to
complete data sources.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ez.data.store import DuckDBStore


def _make_store_with_parquet(tmp_path: Path, manifest_end_iso: str) -> DuckDBStore:
    """Build a store pointing at an isolated parquet cache dir."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Minimal valid parquet file — content doesn't matter, only existence
    df = pd.DataFrame({
        "time": [pd.Timestamp("2026-01-01")],
        "symbol": ["X"], "market": ["cn_stock"],
        "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
        "adj_close": [1.0], "volume": [1],
    })
    df.to_parquet(cache_dir / "cn_stock_daily.parquet", index=False)

    (cache_dir / "manifest.json").write_text(json.dumps({
        "date_range": {"start": "2015-01-01", "end": manifest_end_iso},
        "files": {"cn_stock_daily.parquet": {"md5": "x"}},
    }), encoding="utf-8")

    store = DuckDBStore(db_path=str(tmp_path / "test.db"))
    store._cache_dir = cache_dir
    store._manifest = None
    store._manifest_loaded = False
    return store


def test_parquet_skipped_when_end_after_manifest(tmp_path) -> None:
    """V2.17 r7 critical: end_date > manifest_end must skip parquet.
    Prior grace-period bug let truncated data through silently."""
    store = _make_store_with_parquet(tmp_path, manifest_end_iso="2026-04-08")
    # Request end_date 5 days AFTER manifest_end
    path = store._find_parquet_cache("cn_stock", "daily", end_date=date(2026, 4, 13))
    assert path is None, (
        "parquet returned for end_date past manifest_end — this causes "
        "silent data truncation (strategies run on stale signals). "
        "Fix: _find_parquet_cache must skip parquet when end_date > manifest_end."
    )


def test_parquet_used_when_end_equals_manifest(tmp_path) -> None:
    """Boundary: end_date == manifest_end is fine (parquet has that bar)."""
    store = _make_store_with_parquet(tmp_path, manifest_end_iso="2026-04-08")
    path = store._find_parquet_cache("cn_stock", "daily", end_date=date(2026, 4, 8))
    assert path is not None
    assert "cn_stock_daily.parquet" in path


def test_parquet_used_when_end_before_manifest(tmp_path) -> None:
    """Historical backtest (end < manifest_end): parquet is perfect."""
    store = _make_store_with_parquet(tmp_path, manifest_end_iso="2026-04-08")
    path = store._find_parquet_cache("cn_stock", "daily", end_date=date(2024, 12, 31))
    assert path is not None


def test_parquet_skipped_for_single_day_past_manifest(tmp_path) -> None:
    """Even 1 day past manifest must skip — no grace tolerance."""
    store = _make_store_with_parquet(tmp_path, manifest_end_iso="2026-04-08")
    path = store._find_parquet_cache("cn_stock", "daily", end_date=date(2026, 4, 9))
    assert path is None


def test_parquet_used_when_end_date_is_none(tmp_path) -> None:
    """Caller didn't supply end_date — guard is inactive."""
    store = _make_store_with_parquet(tmp_path, manifest_end_iso="2026-04-08")
    path = store._find_parquet_cache("cn_stock", "daily", end_date=None)
    assert path is not None


def test_parquet_used_when_no_manifest(tmp_path) -> None:
    """Parquet exists but no manifest — allow (can't check freshness)."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # parquet file present; manifest.json MISSING
    pd.DataFrame({
        "time": [pd.Timestamp("2026-01-01")], "symbol": ["X"],
        "market": ["cn_stock"], "open": [1.0], "high": [1.0], "low": [1.0],
        "close": [1.0], "adj_close": [1.0], "volume": [1],
    }).to_parquet(cache_dir / "cn_stock_daily.parquet", index=False)

    store = DuckDBStore(db_path=str(tmp_path / "t.db"))
    store._cache_dir = cache_dir
    store._manifest = None
    store._manifest_loaded = False
    path = store._find_parquet_cache("cn_stock", "daily", end_date=date(2099, 12, 31))
    assert path is not None  # No manifest = can't verify, allow by default
