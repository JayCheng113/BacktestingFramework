"""V2.16.2 round 4: data reproducibility hash.

DuckDBStore.get_data_hash() summarizes the parquet cache manifest so
portfolio /run can embed it in run_config. When the parquet cache is
rebuilt (fresh build, ETF adj_factor fix, etc.), the hash changes and
cross-run comparison can surface "results may differ due to data drift".

Contract:
- Stable under identical manifest content (deterministic)
- Changes when file md5s change
- Changes when date_range changes
- None when manifest absent (not cached)
- None when manifest has no file md5s
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ez.data.store import DuckDBStore


def _make_store(tmp_path: Path, manifest: dict | None = None) -> DuckDBStore:
    """Build a DuckDBStore pointing at an isolated tmp cache dir.

    DuckDBStore auto-resolves cache_dir from EZ_DATA_DIR env or from
    project defaults; we override via the internal `_cache_dir` attr
    after construction. The DB path is isolated to tmp_path so tests
    don't touch real data/ez_trading.db.
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (cache_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )
    db_path = tmp_path / "test.db"
    store = DuckDBStore(db_path=str(db_path))
    # Override auto-resolved cache_dir with our isolated one
    store._cache_dir = cache_dir
    store._manifest = None
    store._manifest_loaded = False
    return store


def test_hash_stable_under_identical_manifest(tmp_path) -> None:
    """Same manifest -> same hash every call (no stray randomness)."""
    m = {
        "version": "1.0",
        "build_timestamp": "2026-04-10T05:00:00",
        "date_range": {"start": "2015-01-01", "end": "2026-04-08"},
        "files": {
            "cn_stock_daily.parquet": {"md5": "abc123"},
            "cn_stock_weekly.parquet": {"md5": "def456"},
        },
    }
    store = _make_store(tmp_path, m)
    h1 = store.get_data_hash()
    h2 = store.get_data_hash()
    assert h1 == h2
    assert h1 is not None
    assert len(h1) == 12


def test_hash_changes_when_md5_changes(tmp_path) -> None:
    m1 = {
        "date_range": {"start": "2015-01-01", "end": "2026-04-08"},
        "files": {"cn_stock_daily.parquet": {"md5": "abc123"}},
    }
    m2 = {
        "date_range": {"start": "2015-01-01", "end": "2026-04-08"},
        "files": {"cn_stock_daily.parquet": {"md5": "xyz999"}},  # changed
    }
    s1 = _make_store(tmp_path / "a", m1)
    s2 = _make_store(tmp_path / "b", m2)
    assert s1.get_data_hash() != s2.get_data_hash()


def test_hash_changes_when_date_range_changes(tmp_path) -> None:
    """New parquet build extended the date range -> hash changes even
    if existing md5s happen to match (parquet build timestamp differs)."""
    m1 = {
        "date_range": {"start": "2015-01-01", "end": "2026-04-08"},
        "files": {"cn_stock_daily.parquet": {"md5": "abc123"}},
    }
    m2 = {
        "date_range": {"start": "2015-01-01", "end": "2026-05-01"},  # extended
        "files": {"cn_stock_daily.parquet": {"md5": "abc123"}},
    }
    s1 = _make_store(tmp_path / "a", m1)
    s2 = _make_store(tmp_path / "b", m2)
    assert s1.get_data_hash() != s2.get_data_hash()


def test_hash_order_independent_on_files(tmp_path) -> None:
    """File dict insertion order must not affect the hash.
    Manifest.json is written by a script — order is not guaranteed."""
    m1 = {
        "date_range": {"start": "2015", "end": "2026"},
        "files": {
            "a.parquet": {"md5": "aaa"},
            "b.parquet": {"md5": "bbb"},
        },
    }
    m2 = {
        "date_range": {"start": "2015", "end": "2026"},
        "files": {
            "b.parquet": {"md5": "bbb"},  # reversed order
            "a.parquet": {"md5": "aaa"},
        },
    }
    s1 = _make_store(tmp_path / "a", m1)
    s2 = _make_store(tmp_path / "b", m2)
    assert s1.get_data_hash() == s2.get_data_hash()


def test_hash_none_when_no_manifest(tmp_path) -> None:
    store = _make_store(tmp_path, manifest=None)
    assert store.get_data_hash() is None


def test_hash_none_when_files_section_empty(tmp_path) -> None:
    """Manifest exists but no file md5s (legacy or partial build)."""
    m = {
        "date_range": {"start": "2015", "end": "2026"},
        "files": {},
    }
    store = _make_store(tmp_path, m)
    assert store.get_data_hash() is None


def test_hash_ignores_metadata_fields(tmp_path) -> None:
    """Fields like build_timestamp / version / symbol counts must not
    be in the hash, otherwise every rebuild changes the hash even when
    underlying data is byte-identical (e.g., reproducibility test)."""
    m1 = {
        "version": "1.0",
        "build_timestamp": "2026-04-10T05:00:00",
        "symbols": {"etfs": 309, "total": 314},
        "date_range": {"start": "2015", "end": "2026"},
        "files": {"cn_stock_daily.parquet": {"md5": "abc123"}},
    }
    m2 = {
        "version": "2.0",                              # different
        "build_timestamp": "2026-04-11T05:00:00",      # different
        "symbols": {"etfs": 310, "total": 315},         # different
        "date_range": {"start": "2015", "end": "2026"},
        "files": {"cn_stock_daily.parquet": {"md5": "abc123"}},
    }
    s1 = _make_store(tmp_path / "a", m1)
    s2 = _make_store(tmp_path / "b", m2)
    # Same hash — the metadata changes are not reproducibility-relevant
    assert s1.get_data_hash() == s2.get_data_hash()


def test_portfolio_run_config_has_data_hash(tmp_path, monkeypatch) -> None:
    """Integration: _get_current_data_hash in portfolio route returns
    the store's hash. A portfolio /run save includes `_data_hash` in
    run_config."""
    # Set up a fake store with a known manifest
    m = {
        "date_range": {"start": "2015", "end": "2026"},
        "files": {"cn_stock_daily.parquet": {"md5": "zzz"}},
    }
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    fake_store = DuckDBStore(db_path=str(tmp_path / "t.db"))
    fake_store._cache_dir = cache_dir
    fake_store._manifest = None
    fake_store._manifest_loaded = False

    # Patch get_store to return our fake
    import ez.api.deps as deps_mod
    monkeypatch.setattr(deps_mod, "get_store", lambda: fake_store)

    from ez.api.routes.portfolio import _get_current_data_hash
    h = _get_current_data_hash()
    assert h == fake_store.get_data_hash()
    assert h is not None and len(h) == 12
