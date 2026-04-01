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


class TestLRUEviction:
    """Test LRU cache eviction for FundamentalStore."""

    def _set_access_time(self, store, symbol: str, t: float):
        """Directly set LRU timestamp — avoids fragile sleep-based ordering."""
        store._symbol_access_time[symbol] = t

    def test_evicts_oldest_symbols_first(self, store):
        """Oldest-accessed symbols should be evicted when cache exceeds threshold."""
        from ez.data.fundamental import FundamentalStore

        original_max = FundamentalStore._MAX_DAILY_CACHE
        FundamentalStore._MAX_DAILY_CACHE = 10
        try:
            # Load A (8 entries) and B (5 entries) into DB
            store.save_daily_basic([
                {"symbol": "000001.SZ", "trade_date": date(2024, 1, d), "pe_ttm": 8.0}
                for d in range(2, 10)
            ])
            store.save_daily_basic([
                {"symbol": "600519.SH", "trade_date": date(2024, 1, d), "pe_ttm": 35.0}
                for d in range(2, 7)
            ])
            # Preload both, then set A older than B
            store.preload(["000001.SZ", "600519.SH"], date(2024, 1, 1), date(2024, 1, 15))
            assert len(store._daily_cache) == 13  # no eviction yet (protected)
            self._set_access_time(store, "000001.SZ", 1.0)  # old
            self._set_access_time(store, "600519.SH", 2.0)  # new

            # Trigger eviction by preloading B again (total 13 > 10)
            store.preload(["600519.SH"], date(2024, 1, 1), date(2024, 1, 15))

            # A (oldest, unprotected) evicted; B (protected) retained
            assert store.get_daily_basic_at("000001.SZ", date(2024, 1, 2)) is None
            assert store.get_daily_basic_at("600519.SH", date(2024, 1, 2)) is not None
        finally:
            FundamentalStore._MAX_DAILY_CACHE = original_max

    def test_read_refreshes_access_time(self, store):
        """Reading a symbol should refresh its LRU timestamp, protecting it from eviction."""
        from ez.data.fundamental import FundamentalStore

        original_max = FundamentalStore._MAX_DAILY_CACHE
        # Units: daily + industry. A(3d+1i=4)+B(5d+1i=6)+C(3d+1i=4)=14
        # threshold=12, target=9. 14>12 → evict B(6)→remaining=8<=9
        FundamentalStore._MAX_DAILY_CACHE = 12
        try:
            store.save_daily_basic([
                {"symbol": "000001.SZ", "trade_date": date(2024, 1, d), "pe_ttm": 8.0}
                for d in range(2, 5)
            ])
            store.save_daily_basic([
                {"symbol": "600519.SH", "trade_date": date(2024, 1, d), "pe_ttm": 35.0}
                for d in range(2, 7)
            ])
            store.save_daily_basic([
                {"symbol": "000858.SZ", "trade_date": date(2024, 1, d), "pe_ttm": 50.0}
                for d in range(2, 5)
            ])
            # Load all three
            store.preload(["000001.SZ", "600519.SH"], date(2024, 1, 1), date(2024, 1, 15))
            self._set_access_time(store, "000001.SZ", 1.0)  # A: old
            self._set_access_time(store, "600519.SH", 2.0)  # B: middle

            # Read A → refreshes its timestamp above B
            store.get_daily_basic_at("000001.SZ", date(2024, 1, 3))
            assert store._symbol_access_time["000001.SZ"] > store._symbol_access_time["600519.SH"]

            # Preload C → total 11 > 10, B is now oldest → evicted
            store.preload(["000858.SZ"], date(2024, 1, 1), date(2024, 1, 15))

            assert store.get_daily_basic_at("600519.SH", date(2024, 1, 2)) is None  # B evicted
            assert store.get_daily_basic_at("000001.SZ", date(2024, 1, 2)) is not None  # A survived
            assert store.get_daily_basic_at("000858.SZ", date(2024, 1, 2)) is not None  # C protected
        finally:
            FundamentalStore._MAX_DAILY_CACHE = original_max

    def test_eviction_cleans_all_caches(self, store):
        """Eviction should clean daily, fina, industry, and access_time consistently."""
        from ez.data.fundamental import FundamentalStore

        original_max = FundamentalStore._MAX_DAILY_CACHE
        FundamentalStore._MAX_DAILY_CACHE = 8
        try:
            store.save_daily_basic([
                {"symbol": "000001.SZ", "trade_date": date(2024, 1, d), "pe_ttm": 8.0}
                for d in range(2, 8)
            ])
            store.save_fina_indicator([
                {"symbol": "000001.SZ", "ann_date": date(2024, 4, 28),
                 "end_date": date(2024, 3, 31), "roe": 12.5},
            ])
            store.save_daily_basic([
                {"symbol": "600519.SH", "trade_date": date(2024, 1, d), "pe_ttm": 35.0}
                for d in range(2, 6)
            ])
            # Preload A, set it old
            store.preload(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 15))
            assert store.get_industry("000001.SZ") == "银行"
            self._set_access_time(store, "000001.SZ", 1.0)

            # Preload B → total 10 > 8, A (old, unprotected) evicted
            store.preload(["600519.SH"], date(2024, 1, 1), date(2024, 1, 15))

            assert store.get_daily_basic_at("000001.SZ", date(2024, 1, 2)) is None
            assert store.get_fina_pit("000001.SZ", date(2024, 5, 1)) is None
            assert store.get_industry("000001.SZ") is None
            assert "000001.SZ" not in store._symbol_access_time
        finally:
            FundamentalStore._MAX_DAILY_CACHE = original_max

    def test_eviction_respects_75pct_target(self, store):
        """After eviction, cache size should be at or below 75% of max."""
        from ez.data.fundamental import FundamentalStore

        original_max = FundamentalStore._MAX_DAILY_CACHE
        FundamentalStore._MAX_DAILY_CACHE = 20
        try:
            for i, (sym, pe) in enumerate([("000001.SZ", 8.0), ("600519.SH", 35.0), ("000858.SZ", 50.0)]):
                store.save_daily_basic([
                    {"symbol": sym, "trade_date": date(2024, 1, d), "pe_ttm": pe}
                    for d in range(2, 10)
                ])
            # Preload each separately, staggering timestamps
            store.preload(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 15))
            self._set_access_time(store, "000001.SZ", 1.0)
            store.preload(["600519.SH"], date(2024, 1, 1), date(2024, 1, 15))
            self._set_access_time(store, "600519.SH", 2.0)
            store.preload(["000858.SZ"], date(2024, 1, 1), date(2024, 1, 15))
            # 24 > 20 → evict oldest until <= 15

            assert len(store._daily_cache) <= 15
        finally:
            FundamentalStore._MAX_DAILY_CACHE = original_max

    def test_current_request_never_self_evicted(self, store):
        """A large preload batch must never evict its own symbols. (Issue #1)"""
        from ez.data.fundamental import FundamentalStore

        original_max = FundamentalStore._MAX_DAILY_CACHE
        # threshold=5, target=3. Load 8 entries for 2 symbols → exceeds threshold
        # but both are in the current request → must NOT self-evict
        FundamentalStore._MAX_DAILY_CACHE = 5
        try:
            store.save_daily_basic([
                {"symbol": "000001.SZ", "trade_date": date(2024, 1, d), "pe_ttm": 8.0}
                for d in range(2, 6)
            ])
            store.save_daily_basic([
                {"symbol": "600519.SH", "trade_date": date(2024, 1, d), "pe_ttm": 35.0}
                for d in range(2, 6)
            ])
            # Total 8 > 5 after preload, but both symbols are protected
            store.preload(["000001.SZ", "600519.SH"], date(2024, 1, 1), date(2024, 1, 15))

            # Both symbols MUST still be in cache
            assert store.get_daily_basic_at("000001.SZ", date(2024, 1, 2)) is not None
            assert store.get_daily_basic_at("600519.SH", date(2024, 1, 2)) is not None
            assert len(store._daily_cache) == 8
        finally:
            FundamentalStore._MAX_DAILY_CACHE = original_max

    def test_fina_only_symbol_evicted(self, store):
        """Symbols with only fina/industry data (no daily) must be eviction candidates. (Issue #2)"""
        from ez.data.fundamental import FundamentalStore

        original_max = FundamentalStore._MAX_DAILY_CACHE
        FundamentalStore._MAX_DAILY_CACHE = 6
        try:
            # Sym A: fina + industry only (no daily entries in DB)
            store.save_fina_indicator([
                {"symbol": "000001.SZ", "ann_date": date(2024, 4, 28),
                 "end_date": date(2024, 3, 31), "roe": 12.5},
            ])
            # end must be >= ann_date so fina data actually loads
            store.preload(["000001.SZ"], date(2024, 1, 1), date(2024, 12, 31))
            self._set_access_time(store, "000001.SZ", 1.0)
            assert "000001.SZ" in store._fina_cache
            assert "000001.SZ" in store._industry_cache

            # Sym B: 8 daily entries → triggers eviction
            store.save_daily_basic([
                {"symbol": "600519.SH", "trade_date": date(2024, 1, d), "pe_ttm": 35.0}
                for d in range(2, 10)
            ])
            store.preload(["600519.SH"], date(2024, 1, 1), date(2024, 12, 31))

            # A (fina-only, oldest) should be cleaned from all caches
            assert "000001.SZ" not in store._fina_cache
            assert "000001.SZ" not in store._industry_cache
            assert "000001.SZ" not in store._symbol_access_time
        finally:
            FundamentalStore._MAX_DAILY_CACHE = original_max


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
