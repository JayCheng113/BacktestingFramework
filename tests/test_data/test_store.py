from datetime import date, datetime
import pytest
from ez.types import Bar
from ez.data.store import DuckDBStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = DuckDBStore(db_path)
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
