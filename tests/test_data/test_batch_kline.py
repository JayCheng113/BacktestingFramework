"""Tests for batch kline query (V2.12.1 S1)."""
from datetime import date

import duckdb
import pytest

from ez.types import Bar
from ez.data.store import DuckDBStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = DuckDBStore(db_path)
    # Insert test data
    from datetime import datetime
    bars = [
        Bar(time=datetime(2024, 1, 2), symbol="A", market="cn_stock",
            open=10, high=11, low=9, close=10.5, adj_close=10.5, volume=1000),
        Bar(time=datetime(2024, 1, 3), symbol="A", market="cn_stock",
            open=10.5, high=11.5, low=10, close=11, adj_close=11, volume=1100),
        Bar(time=datetime(2024, 1, 2), symbol="B", market="cn_stock",
            open=20, high=22, low=19, close=21, adj_close=21, volume=2000),
    ]
    s.save_kline(bars, "daily")
    return s


class TestBatchKline:
    def test_batch_returns_same_as_individual(self, store):
        """Batch query must return identical results to per-symbol queries."""
        start, end = date(2024, 1, 1), date(2024, 1, 5)
        individual_a = store.query_kline("A", "cn_stock", "daily", start, end)
        individual_b = store.query_kline("B", "cn_stock", "daily", start, end)

        batch = store.query_kline_batch(["A", "B"], "cn_stock", "daily", start, end)
        assert len(batch["A"]) == len(individual_a)
        assert len(batch["B"]) == len(individual_b)
        for i in range(len(individual_a)):
            assert batch["A"][i].close == individual_a[i].close
            assert batch["A"][i].time == individual_a[i].time

    def test_batch_with_missing_symbols(self, store):
        """Symbols without data should return empty lists."""
        batch = store.query_kline_batch(
            ["A", "NONEXIST"], "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 5))
        assert len(batch["A"]) == 2
        assert batch["NONEXIST"] == []

    def test_empty_symbols_returns_empty(self, store):
        batch = store.query_kline_batch([], "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 5))
        assert batch == {}
