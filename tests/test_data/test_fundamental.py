"""Tests for FundamentalStore (V2.11): tables, PIT queries, preload, data quality."""
from datetime import date

import duckdb
import pytest


@pytest.fixture
def store():
    """FundamentalStore with in-memory DuckDB."""
    conn = duckdb.connect(":memory:")
    # Create symbols table (required for industry cache)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            ts_code VARCHAR PRIMARY KEY,
            name VARCHAR,
            area VARCHAR,
            industry VARCHAR,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO symbols VALUES ('000001.SZ', '平安银行', '深圳', '银行', CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO symbols VALUES ('600519.SH', '贵州茅台', '贵州', '食品饮料', CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO symbols VALUES ('000858.SZ', '五粮液', '四川', '食品饮料', CURRENT_TIMESTAMP)")

    from ez.data.fundamental import FundamentalStore
    s = FundamentalStore(conn)
    return s


class TestFundamentalStoreBasic:
    def test_save_and_query_daily_basic(self, store):
        records = [
            {"symbol": "000001.SZ", "trade_date": date(2024, 1, 2), "pe_ttm": 8.5, "pb": 0.8, "total_mv": 300000},
            {"symbol": "000001.SZ", "trade_date": date(2024, 1, 3), "pe_ttm": 8.6, "pb": 0.81, "total_mv": 305000},
        ]
        saved = store.save_daily_basic(records)
        assert saved == 2
        assert store.has_daily_basic("000001.SZ", date(2024, 1, 2), date(2024, 1, 3))
        assert not store.has_daily_basic("000001.SZ", date(2025, 1, 1), date(2025, 12, 31))

    def test_save_and_query_fina_indicator(self, store):
        records = [
            {"symbol": "000001.SZ", "ann_date": date(2024, 4, 28), "end_date": date(2024, 3, 31),
             "roe": 12.5, "roa": 1.2, "grossprofit_margin": 45.0, "debt_to_assets": 88.0},
            {"symbol": "000001.SZ", "ann_date": date(2024, 8, 30), "end_date": date(2024, 6, 30),
             "roe": 13.0, "roa": 1.3, "grossprofit_margin": 46.0, "debt_to_assets": 87.5},
        ]
        saved = store.save_fina_indicator(records)
        assert saved == 2
        assert store.has_fina_indicator("000001.SZ")
        assert not store.has_fina_indicator("NONEXIST.SZ")

    def test_upsert_idempotent(self, store):
        record = [{"symbol": "000001.SZ", "trade_date": date(2024, 1, 2), "pe_ttm": 8.5}]
        store.save_daily_basic(record)
        store.save_daily_basic(record)  # duplicate
        count = store._conn.execute(
            "SELECT COUNT(*) FROM fundamental_daily WHERE symbol='000001.SZ'"
        ).fetchone()[0]
        assert count == 1


class TestPreloadAndPIT:
    def test_preload_daily_and_query(self, store):
        records = [
            {"symbol": "000001.SZ", "trade_date": date(2024, 1, 2), "pe_ttm": 8.5, "pb": 0.8},
            {"symbol": "000001.SZ", "trade_date": date(2024, 1, 3), "pe_ttm": 8.6, "pb": 0.81},
            {"symbol": "600519.SH", "trade_date": date(2024, 1, 2), "pe_ttm": 35.0, "pb": 12.0},
        ]
        store.save_daily_basic(records)
        store.preload(["000001.SZ", "600519.SH"], date(2024, 1, 1), date(2024, 1, 5))

        val = store.get_daily_basic_at("000001.SZ", date(2024, 1, 3))
        assert val is not None
        assert val["pe_ttm"] == 8.6

        val2 = store.get_daily_basic_at("600519.SH", date(2024, 1, 2))
        assert val2 is not None
        assert val2["pe_ttm"] == 35.0

    def test_daily_basic_fallback_to_recent(self, store):
        """Querying a weekend/holiday should fall back to last trading day."""
        records = [{"symbol": "000001.SZ", "trade_date": date(2024, 1, 5), "pe_ttm": 8.5}]  # Friday
        store.save_daily_basic(records)
        store.preload(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 8))

        # Saturday query should fall back to Friday
        val = store.get_daily_basic_at("000001.SZ", date(2024, 1, 6))
        assert val is not None
        assert val["pe_ttm"] == 8.5

    def test_daily_basic_returns_none_if_too_far(self, store):
        records = [{"symbol": "000001.SZ", "trade_date": date(2024, 1, 2), "pe_ttm": 8.5}]
        store.save_daily_basic(records)
        store.preload(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 15))

        # 10 days later — beyond 5-day fallback
        val = store.get_daily_basic_at("000001.SZ", date(2024, 1, 15))
        assert val is None

    def test_fina_pit_correct_order(self, store):
        """PIT: returns most recent report announced before query date."""
        records = [
            {"symbol": "000001.SZ", "ann_date": date(2024, 4, 28), "end_date": date(2024, 3, 31),
             "roe": 12.5, "revenue_yoy": 15.0},
            {"symbol": "000001.SZ", "ann_date": date(2024, 8, 30), "end_date": date(2024, 6, 30),
             "roe": 13.0, "revenue_yoy": 18.0},
        ]
        store.save_fina_indicator(records)
        store.preload(["000001.SZ"], date(2024, 1, 1), date(2024, 12, 31))

        # Before Q1 announced: no data
        val = store.get_fina_pit("000001.SZ", date(2024, 4, 1))
        assert val is None

        # After Q1 announced but before H1: get Q1 data
        val = store.get_fina_pit("000001.SZ", date(2024, 5, 15))
        assert val is not None
        assert val["roe"] == 12.5

        # After H1 announced: get H1 data (more recent)
        val = store.get_fina_pit("000001.SZ", date(2024, 9, 15))
        assert val is not None
        assert val["roe"] == 13.0

    def test_fina_pit_no_lookahead(self, store):
        """Critical: PIT must NOT return data announced in the future."""
        records = [
            {"symbol": "000001.SZ", "ann_date": date(2024, 8, 30), "end_date": date(2024, 6, 30),
             "roe": 13.0},
        ]
        store.save_fina_indicator(records)
        store.preload(["000001.SZ"], date(2024, 1, 1), date(2024, 12, 31))

        # Before announcement: must return None
        val = store.get_fina_pit("000001.SZ", date(2024, 7, 15))
        assert val is None, "PIT violation: returned data not yet announced"


class TestIndustryCache:
    def test_industry_from_symbols_table(self, store):
        store.preload(["000001.SZ", "600519.SH"], date(2024, 1, 1), date(2024, 1, 2))
        assert store.get_industry("000001.SZ") == "银行"
        assert store.get_industry("600519.SH") == "食品饮料"
        assert store.get_industry("NONEXIST.SZ") is None

    def test_get_all_industries(self, store):
        store.preload(["000001.SZ", "600519.SH", "000858.SZ"], date(2024, 1, 1), date(2024, 1, 2))
        industries = store.get_all_industries()
        assert "000001.SZ" in industries
        assert "600519.SH" in industries


class TestDataQualityReport:
    def test_report_structure(self, store):
        records = [
            {"symbol": "000001.SZ", "trade_date": date(2024, 1, d), "pe_ttm": 8.5}
            for d in range(2, 20)
        ]
        store.save_daily_basic(records)
        store.preload(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 31))

        report = store.data_quality_report(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 31))
        assert len(report) == 1
        r = report[0]
        assert r["symbol"] == "000001.SZ"
        assert r["daily_count"] == 18
        assert r["daily_coverage_pct"] > 0
        assert "has_fina" in r
        assert "industry" in r
