"""Fundamental data store — DuckDB tables + PIT query + preload cache.

[EXTENSION] — new in V2.11.

Stores daily_basic (PE/PB/MV/turnover) and fina_indicator (ROE/ROA/margins/growth)
with Point-in-Time alignment via ann_date.

Data fetch is delegated to TushareDataProvider; this module owns storage + query.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

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
        # In-memory caches populated by preload()
        self._daily_cache: dict[tuple[str, date], dict] = {}
        self._fina_cache: dict[str, list[dict]] = {}  # symbol -> sorted by end_date desc
        self._industry_cache: dict[str, str] = {}

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

    # ── Data ingestion ────────────────────────────────────────────────

    def save_daily_basic(self, records: list[dict]) -> int:
        """Insert daily basic records (skip duplicates). Returns actual new rows inserted.

        Uses ON CONFLICT DO NOTHING: daily_basic values (PE/PB/MV) are published once per
        trade_date and never revised. Unlike fina_indicator (which may have restated ann_date),
        daily_basic is immutable after publication.
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
                ann_date=EXCLUDED.ann_date, roe=EXCLUDED.roe, roe_waa=EXCLUDED.roe_waa,
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

    def preload(self, symbols: list[str], start: date, end: date) -> None:
        """Bulk-load fundamental data into memory for fast compute() lookups.

        Must be called before using get_daily_basic_at() or get_fina_pit().
        """
        self._preload_daily(symbols, start, end)
        self._preload_fina(symbols, end)
        self._preload_industry(symbols)

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

    def get_daily_basic_at(self, symbol: str, trade_date: date) -> dict | None:
        """Get daily basic data for exact date. Returns None if unavailable.

        Falls back to most recent available date within 5 trading days.
        """
        d = trade_date
        for _ in range(6):  # try up to 5 days back (weekends/holidays)
            val = self._daily_cache.get((symbol, d))
            if val is not None:
                return val
            d -= timedelta(days=1)
        return None

    def get_fina_pit(self, symbol: str, as_of: date) -> dict | None:
        """Get latest financial indicator announced on or before as_of (PIT).

        Returns the most recent report by end_date whose ann_date <= as_of.
        """
        reports = self._fina_cache.get(symbol)
        if not reports:
            return None
        # Reports are sorted by end_date desc in preload; find first with ann_date <= as_of
        for r in reports:
            if r["ann_date"] <= as_of:
                return r
        return None

    def get_industry(self, symbol: str) -> str | None:
        """Get industry name for symbol (from symbols table cache)."""
        return self._industry_cache.get(symbol)

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
