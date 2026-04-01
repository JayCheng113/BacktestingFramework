"""Fundamental data store — DuckDB tables + PIT query + preload cache.

[EXTENSION] — new in V2.11.

Stores daily_basic (PE/PB/MV/turnover) and fina_indicator (ROE/ROA/margins/growth)
with Point-in-Time alignment via ann_date.

Data fetch is delegated to TushareDataProvider; this module owns storage + query.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import threading

import duckdb

logger = logging.getLogger(__name__)


class FundamentalStore:
    """Fundamental data: DuckDB storage + in-memory preload for fast compute().

    Tables managed:
      - fundamental_daily: PE/PB/PS/MV/turnover (daily frequency)
      - fina_indicator: pre-computed financial ratios with ann_date (quarterly, PIT)
    Industry classification reuses the existing ``symbols`` table's industry column.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn
        self._init_tables()
        # In-memory caches populated by preload(). Protected by _cache_lock for thread safety.
        self._daily_cache: dict[tuple[str, date], dict] = {}
        self._fina_cache: dict[str, list[dict]] = {}  # symbol -> sorted by end_date desc
        self._industry_cache: dict[str, str] = {}
        self._cache_lock = threading.Lock()
        # LRU tracking: symbol -> last access monotonic timestamp
        self._symbol_access_time: dict[str, float] = {}

    # ── Schema ────────────────────────────────────────────────────────

    def _init_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamental_daily (
                symbol    VARCHAR NOT NULL,
                trade_date DATE    NOT NULL,
                pe        DOUBLE,
                pe_ttm    DOUBLE,
                pb        DOUBLE,
                ps        DOUBLE,
                ps_ttm    DOUBLE,
                dv_ratio  DOUBLE,
                turnover_rate   DOUBLE,
                turnover_rate_f DOUBLE,
                volume_ratio    DOUBLE,
                total_share DOUBLE,
                float_share DOUBLE,
                total_mv    DOUBLE,
                circ_mv     DOUBLE,
                PRIMARY KEY (symbol, trade_date)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS fina_indicator (
                symbol     VARCHAR NOT NULL,
                ann_date   DATE    NOT NULL,
                end_date   DATE    NOT NULL,
                roe        DOUBLE,
                roe_waa    DOUBLE,
                roa        DOUBLE,
                grossprofit_margin DOUBLE,
                netprofit_margin   DOUBLE,
                debt_to_assets     DOUBLE,
                current_ratio      DOUBLE,
                quick_ratio        DOUBLE,
                revenue_yoy  DOUBLE,
                profit_yoy   DOUBLE,
                roe_yoy      DOUBLE,
                eps          DOUBLE,
                dt_eps       DOUBLE,
                PRIMARY KEY (symbol, end_date)
            )
        """)
        # Index for PIT queries: WHERE symbol IN (...) AND ann_date <= ?
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fina_ann_date ON fina_indicator(symbol, ann_date)
        """)

    # ── Data ingestion ────────────────────────────────────────────────

    def save_daily_basic(self, records: list[dict]) -> int:
        """Insert daily basic records (skip duplicates). Returns actual new rows inserted.

        Uses ON CONFLICT DO NOTHING: daily_basic values (PE/PB/MV) are published once per
        trade_date and never revised. Unlike fina_indicator (which may have restated ann_date),
        daily_basic is immutable after publication.

        Note: count uses SELECT COUNT before/after. DuckDB single-connection mode prevents
        concurrent writes, so the count is accurate in practice.
        """
        if not records:
            return 0
        count_before = self._conn.execute("SELECT COUNT(*) FROM fundamental_daily").fetchone()[0]
        params = []
        for r in records:
            params.append([
                r.get("symbol") or r.get("ts_code", ""),
                r.get("trade_date"),
                r.get("pe"), r.get("pe_ttm"), r.get("pb"),
                r.get("ps"), r.get("ps_ttm"), r.get("dv_ratio"),
                r.get("turnover_rate"), r.get("turnover_rate_f"),
                r.get("volume_ratio"),
                r.get("total_share"), r.get("float_share"),
                r.get("total_mv"), r.get("circ_mv"),
            ])
        self._conn.executemany("""
            INSERT INTO fundamental_daily
                (symbol, trade_date, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio,
                 turnover_rate, turnover_rate_f, volume_ratio,
                 total_share, float_share, total_mv, circ_mv)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
        """, params)
        count_after = self._conn.execute("SELECT COUNT(*) FROM fundamental_daily").fetchone()[0]
        return count_after - count_before

    def save_fina_indicator(self, records: list[dict]) -> int:
        """Upsert fina_indicator records. Returns actual number of new rows inserted."""
        if not records:
            return 0
        count_before = self._conn.execute("SELECT COUNT(*) FROM fina_indicator").fetchone()[0]
        params = []
        for r in records:
            params.append([
                r.get("symbol") or r.get("ts_code", ""),
                r.get("ann_date"), r.get("end_date"),
                r.get("roe"), r.get("roe_waa"), r.get("roa"),
                r.get("grossprofit_margin"), r.get("netprofit_margin"),
                r.get("debt_to_assets"),
                r.get("current_ratio"), r.get("quick_ratio"),
                r.get("revenue_yoy"), r.get("profit_yoy"), r.get("roe_yoy"),
                r.get("eps"), r.get("dt_eps"),
            ])
        self._conn.executemany("""
            INSERT INTO fina_indicator
                (symbol, ann_date, end_date, roe, roe_waa, roa,
                 grossprofit_margin, netprofit_margin, debt_to_assets,
                 current_ratio, quick_ratio,
                 revenue_yoy, profit_yoy, roe_yoy, eps, dt_eps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (symbol, end_date) DO UPDATE SET
                roe=EXCLUDED.roe, roe_waa=EXCLUDED.roe_waa,
                roa=EXCLUDED.roa, grossprofit_margin=EXCLUDED.grossprofit_margin,
                netprofit_margin=EXCLUDED.netprofit_margin, debt_to_assets=EXCLUDED.debt_to_assets,
                current_ratio=EXCLUDED.current_ratio, quick_ratio=EXCLUDED.quick_ratio,
                revenue_yoy=EXCLUDED.revenue_yoy, profit_yoy=EXCLUDED.profit_yoy,
                roe_yoy=EXCLUDED.roe_yoy, eps=EXCLUDED.eps, dt_eps=EXCLUDED.dt_eps
        """, params)
        count_after = self._conn.execute("SELECT COUNT(*) FROM fina_indicator").fetchone()[0]
        return count_after - count_before

    def has_daily_basic(self, symbol: str, start: date, end: date, min_coverage: float = 0.5) -> bool:
        """Check if daily basic data has sufficient coverage for this symbol.

        Note: only checks count-based coverage ratio. A partial fetch that was interrupted
        may have low coverage. Users can clear data and re-fetch if coverage is insufficient.
        """
        count = self._conn.execute(
            "SELECT COUNT(*) FROM fundamental_daily WHERE symbol=? AND trade_date>=? AND trade_date<=?",
            [symbol, start, end],
        ).fetchone()[0]
        if count == 0:
            return False
        # Estimate expected trading days
        days_span = (end - start).days
        expected = max(1, int(days_span * 245 / 365))
        return count >= expected * min_coverage

    def has_fina_indicator(self, symbol: str) -> bool:
        """Check if any fina_indicator data exists for this symbol."""
        count = self._conn.execute(
            "SELECT COUNT(*) FROM fina_indicator WHERE symbol=?", [symbol],
        ).fetchone()[0]
        return count > 0

    # ── Preload (bulk DuckDB → memory) ────────────────────────────────

    # Approximate cache budget in "entry units":
    # daily row = 1, fina row = 1, industry row = 1.
    # Keep the old name for backward compatibility with tests/docs.
    _MAX_DAILY_CACHE = 500_000
    _EVICT_TARGET_RATIO = 0.75  # evict down to 75% of max to avoid thrashing

    def _touch_symbols(self, symbols: list[str]) -> None:
        """Update LRU access timestamp for symbols."""
        t = time.monotonic()
        for sym in symbols:
            self._symbol_access_time[sym] = t

    def _cache_units(self) -> int:
        """Approximate total cache size across daily/fina/industry caches."""
        return (
            len(self._daily_cache)
            + sum(len(rows) for rows in self._fina_cache.values())
            + len(self._industry_cache)
        )

    def _cleanup_ghost_access_time(self) -> int:
        """Drop access timestamps for symbols no longer present in any cache."""
        live_syms = (
            set(s for s, _ in self._daily_cache)
            | set(self._fina_cache)
            | set(self._industry_cache)
        )
        ghost_syms = [s for s in self._symbol_access_time if s not in live_syms]
        for sym in ghost_syms:
            del self._symbol_access_time[sym]
        return len(ghost_syms)

    def _evict_lru(self, protect: set[str] | None = None) -> None:
        """Evict least recently used symbols until cache is below target size.

        Args:
            protect: Symbols that must NOT be evicted (current request's working set).
        """
        total_units = self._cache_units()
        if total_units <= self._MAX_DAILY_CACHE:
            return
        target = int(self._MAX_DAILY_CACHE * self._EVICT_TARGET_RATIO)
        protect = protect or set()
        # Count daily_cache entries per symbol (single pass)
        sym_counts: dict[str, int] = {}
        for sym, _ in self._daily_cache:
            sym_counts[sym] = sym_counts.get(sym, 0) + 1
        all_syms = set(sym_counts) | set(self._fina_cache) | set(self._industry_cache)

        def _symbol_units(sym: str) -> int:
            return (
                sym_counts.get(sym, 0)
                + len(self._fina_cache.get(sym, ()))
                + (1 if sym in self._industry_cache else 0)
            )

        # Sort eviction candidates by access time ascending (oldest first).
        # Protected symbols (current request) are excluded from eviction.
        candidates = sorted(
            (s for s in all_syms if s not in protect),
            key=lambda s: self._symbol_access_time.get(s, 0.0),
        )
        evict_syms: set[str] = set()
        remaining = total_units
        for sym in candidates:
            if remaining <= target:
                break
            units = _symbol_units(sym)
            if units <= 0:
                continue
            evict_syms.add(sym)
            remaining -= units
        # Single-pass rebuild for daily_cache; pop for fina/industry/access_time
        if evict_syms:
            self._daily_cache = {k: v for k, v in self._daily_cache.items() if k[0] not in evict_syms}
            for sym in evict_syms:
                self._fina_cache.pop(sym, None)
                self._industry_cache.pop(sym, None)
                self._symbol_access_time.pop(sym, None)
        ghost_count = self._cleanup_ghost_access_time()
        if evict_syms or ghost_count:
            logger.info(
                "LRU evicted %d symbols (%d ghost cleaned), cache_units≈%d",
                len(evict_syms),
                ghost_count,
                remaining,  # approximate; avoids redundant _cache_units() scan
            )

    def preload(self, symbols: list[str], start: date, end: date) -> None:
        """Bulk-load fundamental data into memory for fast compute() lookups.

        Thread-safe: uses _cache_lock to prevent concurrent corruption.
        LRU eviction when cache exceeds _MAX_DAILY_CACHE.
        Must be called before using get_daily_basic_at() or get_fina_pit().
        """
        with self._cache_lock:
            protect = set(symbols)
            self._touch_symbols(symbols)
            self._preload_daily(symbols, start, end)
            self._preload_fina(symbols, end)
            self._preload_industry(symbols)
            # Evict AFTER loading so the size check reflects the true post-load state.
            # Current request's symbols are protected from eviction.
            self._evict_lru(protect=protect)

    def _preload_daily(self, symbols: list[str], start: date, end: date) -> None:
        # Additive: don't clear — avoids concurrent request cache pollution
        if not symbols:
            return
        placeholders = ",".join(["?"] * len(symbols))
        rows = self._conn.execute(f"""
            SELECT symbol, trade_date, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio,
                   turnover_rate, turnover_rate_f, volume_ratio,
                   total_share, float_share, total_mv, circ_mv
            FROM fundamental_daily
            WHERE symbol IN ({placeholders}) AND trade_date >= ? AND trade_date <= ?
            ORDER BY symbol, trade_date
        """, [*symbols, start, end]).fetchall()
        for r in rows:
            key = (r[0], r[1] if isinstance(r[1], date) else r[1].date() if hasattr(r[1], 'date') else r[1])
            self._daily_cache[key] = {
                "pe": r[2], "pe_ttm": r[3], "pb": r[4],
                "ps": r[5], "ps_ttm": r[6], "dv_ratio": r[7],
                "turnover_rate": r[8], "turnover_rate_f": r[9],
                "volume_ratio": r[10],
                "total_share": r[11], "float_share": r[12],
                "total_mv": r[13], "circ_mv": r[14],
            }

    def _preload_fina(self, symbols: list[str], end: date) -> None:
        # Additive but per-symbol rebuild: replace requested symbols only, leave others intact
        if not symbols:
            return
        placeholders = ",".join(["?"] * len(symbols))
        rows = self._conn.execute(f"""
            SELECT symbol, ann_date, end_date, roe, roe_waa, roa,
                   grossprofit_margin, netprofit_margin, debt_to_assets,
                   current_ratio, quick_ratio,
                   revenue_yoy, profit_yoy, roe_yoy, eps, dt_eps
            FROM fina_indicator
            WHERE symbol IN ({placeholders}) AND ann_date <= ?
            ORDER BY symbol, end_date DESC
        """, [*symbols, end]).fetchall()
        # Clear only the requested symbols' entries (not ALL symbols)
        for sym in symbols:
            self._fina_cache[sym] = []
        for r in rows:
            sym = r[0]
            ann = r[1] if isinstance(r[1], date) else r[1].date() if hasattr(r[1], 'date') else r[1]
            rec = {
                "ann_date": ann,
                "end_date": r[2] if isinstance(r[2], date) else r[2].date() if hasattr(r[2], 'date') else r[2],
                "roe": r[3], "roe_waa": r[4], "roa": r[5],
                "grossprofit_margin": r[6], "netprofit_margin": r[7],
                "debt_to_assets": r[8],
                "current_ratio": r[9], "quick_ratio": r[10],
                "revenue_yoy": r[11], "profit_yoy": r[12], "roe_yoy": r[13],
                "eps": r[14], "dt_eps": r[15],
            }
            self._fina_cache[sym].append(rec)
        # Remove empty entries to avoid ghost symbols in LRU accounting
        for sym in symbols:
            if sym in self._fina_cache and not self._fina_cache[sym]:
                del self._fina_cache[sym]

    def _preload_industry(self, symbols: list[str]) -> None:
        # Additive: don't clear — merge new entries
        if not symbols:
            return
        placeholders = ",".join(["?"] * len(symbols))
        rows = self._conn.execute(f"""
            SELECT ts_code, industry FROM symbols WHERE ts_code IN ({placeholders})
        """, symbols).fetchall()
        for r in rows:
            if r[0] and r[1]:
                self._industry_cache[r[0]] = r[1]

    # ── Point-in-time queries (from preloaded cache) ──────────────────
    # Threading model for LRU:
    # - Writes (preload + eviction) are serialized by _cache_lock.
    # - Reads below are NOT locked. dict.get() and dict[k]=float are GIL-atomic.
    # - Concurrency caveat: a read may update _symbol_access_time AFTER eviction
    #   has already decided to evict that symbol (based on a point-in-time snapshot).
    #   This is benign: the reader already holds the returned data, and the stale
    #   access_time entry ("ghost") is cleaned up on the next eviction cycle.
    #   Full LRU correctness across threads would require locking reads, which is
    #   too expensive for the hot path (~12.5k calls/backtest). The current design
    #   is best-effort for cross-thread recency; within a single request (preload →
    #   compute), the protected-set mechanism guarantees no self-eviction.

    def get_daily_basic_at(self, symbol: str, trade_date: date) -> dict | None:
        """Get daily basic data for exact date. Returns None if unavailable.

        Falls back to most recent available date within 5 trading days.
        Updates LRU access time on hit.
        """
        d = trade_date
        for _ in range(6):  # try up to 5 days back (weekends/holidays)
            val = self._daily_cache.get((symbol, d))
            if val is not None:
                self._symbol_access_time[symbol] = time.monotonic()
                return val
            d -= timedelta(days=1)
        return None

    def get_fina_pit(self, symbol: str, as_of: date) -> dict | None:
        """Get latest financial indicator announced on or before as_of (PIT).

        Correctness: finds the report with the latest end_date among all reports
        whose ann_date <= as_of. Does NOT assume end_date and ann_date are
        monotonically ordered (handles late filings and restatements).
        Updates LRU access time on hit.
        """
        reports = self._fina_cache.get(symbol)
        if not reports:
            return None
        # Filter to reports announced by as_of, pick the one with latest end_date
        valid = [r for r in reports if r["ann_date"] <= as_of]
        if not valid:
            return None
        self._symbol_access_time[symbol] = time.monotonic()
        return max(valid, key=lambda r: r["end_date"])

    def get_industry(self, symbol: str) -> str | None:
        """Get industry name for symbol (from symbols table cache)."""
        val = self._industry_cache.get(symbol)
        if val is not None:
            self._symbol_access_time[symbol] = time.monotonic()
        return val

    def get_all_industries(self) -> dict[str, str]:
        """Get all symbol → industry mappings from cache."""
        return dict(self._industry_cache)

    # ── Data fetch orchestration ──────────────────────────────────────

    def ensure_data(
        self,
        symbols: list[str],
        start: date,
        end: date,
        provider=None,
        need_fina: bool = False,
    ) -> dict:
        """Ensure fundamental data is available. Fetches from Tushare if missing.

        Args:
            symbols: Stock symbols to fetch
            start, end: Date range
            provider: TushareDataProvider instance (None = skip fetching)
            need_fina: Whether fina_indicator data is needed

        Returns:
            dict with fetch status: {"daily_fetched": int, "fina_fetched": int, "errors": [...]}
        """
        status = {"daily_fetched": 0, "fina_fetched": 0, "errors": []}

        if provider is None:
            logger.warning("No Tushare provider available, skipping fundamental data fetch")
            return status

        # 1. Fetch daily_basic for symbols missing data
        for sym in symbols:
            if not self.has_daily_basic(sym, start, end):
                try:
                    records = provider.get_daily_basic(sym, start, end)
                    if records:
                        # Ensure symbol field is set
                        for r in records:
                            r["symbol"] = sym
                        saved = self.save_daily_basic(records)
                        status["daily_fetched"] += saved
                except Exception as e:
                    logger.warning("Failed to fetch daily_basic for %s: %s", sym, e)
                    status["errors"].append(f"daily_basic/{sym}: {e}")

        # 2. Fetch fina_indicator if needed
        if need_fina:
            for sym in symbols:
                if not self.has_fina_indicator(sym):
                    try:
                        records = provider.get_fina_indicator(sym, start, end)
                        if records:
                            for r in records:
                                r["symbol"] = sym
                            saved = self.save_fina_indicator(records)
                            status["fina_fetched"] += saved
                    except Exception as e:
                        logger.warning("Failed to fetch fina_indicator for %s: %s", sym, e)
                        status["errors"].append(f"fina_indicator/{sym}: {e}")

        return status

    # ── Data quality ──────────────────────────────────────────────────

    def data_quality_report(self, symbols: list[str], start: date, end: date) -> list[dict]:
        """Generate data quality report for each symbol.

        Returns list of dicts with: symbol, daily_count, daily_missing_pct,
        has_fina, industry.
        """
        # Estimate expected trading days (approx 245 per year)
        days_span = (end - start).days
        expected = max(1, int(days_span * 245 / 365))

        report = []
        for sym in symbols:
            daily_count = self._conn.execute(
                "SELECT COUNT(*) FROM fundamental_daily WHERE symbol=? AND trade_date>=? AND trade_date<=?",
                [sym, start, end],
            ).fetchone()[0]
            fina_count = self._conn.execute(
                "SELECT COUNT(*) FROM fina_indicator WHERE symbol=?", [sym],
            ).fetchone()[0]
            industry = self._industry_cache.get(sym) or ""

            report.append({
                "symbol": sym,
                "daily_count": daily_count,
                "daily_expected": expected,
                "daily_coverage_pct": round(100 * daily_count / expected, 1) if expected > 0 else 0,
                "fina_reports": fina_count,
                "has_fina": fina_count > 0,
                "industry": industry,
            })
        return report
