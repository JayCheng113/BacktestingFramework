import json
from datetime import date, datetime

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ez.types import Bar
from ez.data.store import DuckDBStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = DuckDBStore(db_path)
    s._cache_dir = None  # Isolate from real parquet cache on developer machines
    s._manifest_loaded = True
    yield s
    s.close()


@pytest.fixture
def sample_bar():
    return Bar(
        time=datetime(2024, 1, 2), symbol="000001.SZ", market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000,
    )


def test_save_and_query(store, sample_bar):
    saved = store.save_kline([sample_bar], "daily")
    assert saved == 1
    result = store.query_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result) == 1
    assert result[0].symbol == "000001.SZ"
    assert result[0].adj_close == 10.15


def test_save_duplicate_ignored(store, sample_bar):
    store.save_kline([sample_bar], "daily")
    saved_again = store.save_kline([sample_bar], "daily")
    assert saved_again == 0
    result = store.query_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result) == 1


def test_has_data(store, sample_bar):
    assert store.has_data("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31)) is False
    store.save_kline([sample_bar], "daily")
    assert store.has_data("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31)) is True


def test_query_empty(store):
    result = store.query_kline("NONE", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert result == []


def test_invalid_period_rejected(store, sample_bar):
    """SQL injection via period parameter must be blocked."""
    from ez.errors import ValidationError
    with pytest.raises(ValidationError, match="Invalid period"):
        store.query_kline("SYM", "mkt", "evil; DROP TABLE kline_daily--", date(2024, 1, 1), date(2024, 1, 31))
    with pytest.raises(ValidationError, match="Invalid period"):
        store.save_kline([sample_bar], "5min")
    with pytest.raises(ValidationError, match="Invalid period"):
        store.has_data("SYM", "mkt", "../etc/passwd", date(2024, 1, 1), date(2024, 1, 31))


def test_context_manager(tmp_path):
    """DuckDBStore supports with-statement."""
    db_path = str(tmp_path / "ctx.db")
    with DuckDBStore(db_path) as store:
        assert store.has_data("X", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31)) is False


def test_save_and_query_symbols(store):
    symbols = [
        {"ts_code": "000001.SZ", "name": "Ping An Bank", "area": "Shenzhen", "industry": "Banking"},
        {"ts_code": "510300.SH", "name": "CSI 300 ETF", "area": "Huatai", "industry": "ETF"},
    ]
    saved = store.save_symbols(symbols)
    assert saved == 2
    assert store.symbols_count() == 2

    results = store.query_symbols("000001")
    assert len(results) == 1
    assert results[0]["name"] == "Ping An Bank"

    results = store.query_symbols("ETF")
    assert len(results) == 1
    assert results[0]["ts_code"] == "510300.SH"

    results = store.query_symbols("")  # all
    assert len(results) == 2


def test_save_symbols_upsert(store):
    store.save_symbols([{"ts_code": "000001.SZ", "name": "Old Name", "area": "", "industry": ""}])
    store.save_symbols([{"ts_code": "000001.SZ", "name": "New Name", "area": "SZ", "industry": "Bank"}])
    assert store.symbols_count() == 1
    r = store.query_symbols("000001")
    assert r[0]["name"] == "New Name"


# ── Parquet cache tests (V2.18) ──────────────────────────────────────

def _write_test_parquet(path, bars: list[Bar], manifest_end: str = "2024-12-31"):
    """Helper: write bars to parquet + manifest.json."""
    records = [{
        "time": b.time, "symbol": b.symbol, "market": b.market,
        "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]
    df = pd.DataFrame(records).sort_values(["symbol", "time"]).reset_index(drop=True)
    pq.write_table(pa.Table.from_pandas(df), str(path), row_group_size=100_000)
    # Write manifest
    manifest = {"date_range": {"start": "2024-01-01", "end": manifest_end}}
    (path.parent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_parquet_priority(store, tmp_path):
    """Parquet data is returned before DuckDB table data."""
    parquet_bar = Bar(
        time=datetime(2024, 1, 2), symbol="000001.SZ", market="cn_stock",
        open=99.0, high=99.5, low=98.0, close=99.2, adj_close=99.0, volume=9999,
    )
    db_bar = Bar(
        time=datetime(2024, 1, 2), symbol="000001.SZ", market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000,
    )
    parquet_path = tmp_path / "cache" / "cn_stock_daily.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    _write_test_parquet(parquet_path, [parquet_bar])

    store.save_kline([db_bar], "daily")
    store._cache_dir = parquet_path.parent
    store._manifest_loaded = False  # reset manifest cache

    result = store.query_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result) == 1
    assert result[0].adj_close == 99.0  # from parquet, not DuckDB's 10.15


def test_parquet_missing_fallback(store):
    """When no parquet cache exists, falls through to DuckDB."""
    bar = Bar(time=datetime(2024, 1, 2), symbol="000001.SZ", market="cn_stock",
              open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000)
    store.save_kline([bar], "daily")
    store._cache_dir = None
    result = store.query_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result) == 1
    assert result[0].adj_close == 10.15


def test_parquet_symbol_not_found_fallback(store, tmp_path):
    """Symbol not in parquet → falls through to DuckDB."""
    parquet_bar = Bar(time=datetime(2024, 1, 2), symbol="OTHER.SZ", market="cn_stock",
                      open=5.0, high=5.5, low=4.8, close=5.2, adj_close=5.0, volume=100)
    db_bar = Bar(time=datetime(2024, 1, 2), symbol="000001.SZ", market="cn_stock",
                 open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000)
    parquet_path = tmp_path / "cache" / "cn_stock_daily.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    _write_test_parquet(parquet_path, [parquet_bar])

    store.save_kline([db_bar], "daily")
    store._cache_dir = parquet_path.parent
    store._manifest_loaded = False

    result = store.query_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result) == 1
    assert result[0].adj_close == 10.15  # from DuckDB


def test_parquet_batch_priority(store, tmp_path):
    """query_kline_batch returns parquet data, skips DuckDB for found symbols."""
    bars = [
        Bar(time=datetime(2024, 1, 2), symbol="A.SZ", market="cn_stock",
            open=10, high=11, low=9, close=10, adj_close=10.0, volume=100),
        Bar(time=datetime(2024, 1, 3), symbol="A.SZ", market="cn_stock",
            open=11, high=12, low=10, close=11, adj_close=11.0, volume=200),
        Bar(time=datetime(2024, 1, 2), symbol="B.SZ", market="cn_stock",
            open=20, high=21, low=19, close=20, adj_close=20.0, volume=300),
    ]
    parquet_path = tmp_path / "cache" / "cn_stock_daily.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    _write_test_parquet(parquet_path, bars)

    store._cache_dir = parquet_path.parent
    store._manifest_loaded = False
    result = store.query_kline_batch(
        ["A.SZ", "B.SZ"], "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result["A.SZ"]) == 2
    assert len(result["B.SZ"]) == 1
    assert result["A.SZ"][0].adj_close == 10.0
    # DuckDB table should be empty (nothing saved there)
    db_count = store._conn.execute("SELECT COUNT(*) FROM kline_daily").fetchone()[0]
    assert db_count == 0


def test_parquet_batch_partial(store, tmp_path):
    """Batch: symbols in parquet served from parquet, missing fall to DuckDB."""
    pq_bar = Bar(time=datetime(2024, 1, 2), symbol="PQ.SZ", market="cn_stock",
                 open=50, high=55, low=48, close=52, adj_close=50.0, volume=500)
    db_bar = Bar(time=datetime(2024, 1, 2), symbol="000001.SZ", market="cn_stock",
                 open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000)
    parquet_path = tmp_path / "cache" / "cn_stock_daily.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    _write_test_parquet(parquet_path, [pq_bar])

    store.save_kline([db_bar], "daily")
    store._cache_dir = parquet_path.parent
    store._manifest_loaded = False

    result = store.query_kline_batch(
        ["PQ.SZ", "000001.SZ"], "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result["PQ.SZ"]) == 1
    assert result["PQ.SZ"][0].adj_close == 50.0  # from parquet
    assert len(result["000001.SZ"]) == 1
    assert result["000001.SZ"][0].adj_close == 10.15  # from DuckDB


def test_parquet_date_range_guard(store, tmp_path):
    """C4: request beyond manifest date_range.end + 7d skips parquet."""
    bar = Bar(time=datetime(2024, 6, 1), symbol="X.SZ", market="cn_stock",
              open=10, high=11, low=9, close=10, adj_close=10.0, volume=100)
    parquet_path = tmp_path / "cache" / "cn_stock_daily.parquet"
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    _write_test_parquet(parquet_path, [bar], manifest_end="2024-06-30")

    store._cache_dir = parquet_path.parent
    store._manifest_loaded = False

    # Request within range → parquet
    r1 = store.query_kline("X.SZ", "cn_stock", "daily", date(2024, 5, 1), date(2024, 6, 30))
    assert len(r1) == 1

    # Request way beyond range → skip parquet (falls to DuckDB, empty)
    store._manifest_loaded = False
    r2 = store.query_kline("X.SZ", "cn_stock", "daily", date(2024, 5, 1), date(2025, 1, 15))
    assert len(r2) == 0  # skipped parquet, DuckDB empty


def test_parquet_frozen_mode(tmp_path, monkeypatch):
    """In frozen mode, cache dir resolves to sys._MEIPASS/data/cache."""
    meipass = tmp_path / "meipass"
    cache = meipass / "data" / "cache"
    cache.mkdir(parents=True)

    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(meipass), raising=False)
    monkeypatch.delenv("EZ_DATA_DIR", raising=False)

    store = DuckDBStore(str(tmp_path / "test.db"))
    assert store._cache_dir == cache
    store.close()
