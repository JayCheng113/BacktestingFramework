"""DuckDB implementation of DataStore.

[CORE] — interface frozen. Implementation details may change.
"""
from __future__ import annotations

from datetime import date, datetime

import duckdb

from ez.data.provider import DataStore
from ez.errors import ValidationError
from ez.types import Bar

# Whitelist of valid period values — used to prevent SQL injection
_VALID_PERIODS = frozenset(("daily", "weekly", "monthly"))


def _safe_table(period: str) -> str:
    """Return sanitized table name or raise on invalid period."""
    if period not in _VALID_PERIODS:
        raise ValidationError(f"Invalid period '{period}'. Must be one of: {', '.join(sorted(_VALID_PERIODS))}")
    return f"kline_{period}"


class DuckDBStore(DataStore):
    """DuckDB-backed data store."""

    PERIODS = ("daily", "weekly", "monthly")

    def __init__(self, db_path: str = "data/ez_trading.db"):
        self._conn = duckdb.connect(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        for period in self.PERIODS:
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS kline_{period} (
                    time TIMESTAMP NOT NULL,
                    symbol VARCHAR NOT NULL,
                    market VARCHAR NOT NULL,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    adj_close DOUBLE,
                    volume BIGINT,
                    PRIMARY KEY (symbol, market, time)
                )
            """)
        # Symbol directory table (stocks + ETFs)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                ts_code VARCHAR PRIMARY KEY,
                name VARCHAR,
                area VARCHAR,
                industry VARCHAR,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def query_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        table = _safe_table(period)
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE symbol=? AND market=? AND time>=? AND time<=? ORDER BY time",
            [symbol, market, datetime.combine(start_date, datetime.min.time()),
             datetime.combine(end_date, datetime.max.time())],
        ).fetchall()
        return [
            Bar(time=r[0], symbol=r[1], market=r[2], open=r[3], high=r[4],
                low=r[5], close=r[6], adj_close=r[7], volume=int(r[8]))
            for r in rows
        ]

    def save_kline(self, bars: list[Bar], period: str) -> int:
        if not bars:
            return 0
        table = _safe_table(period)
        count_before = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        params = [
            [b.time, b.symbol, b.market, b.open, b.high, b.low, b.close, b.adj_close, b.volume]
            for b in bars
        ]
        self._conn.executemany(
            f"""INSERT INTO {table}
                (time, symbol, market, open, high, low, close, adj_close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING""",
            params,
        )
        count_after = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return count_after - count_before

    def has_data(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> bool:
        table = _safe_table(period)
        count = self._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE symbol=? AND market=? AND time>=? AND time<=?",
            [symbol, market, datetime.combine(start_date, datetime.min.time()),
             datetime.combine(end_date, datetime.max.time())],
        ).fetchone()[0]
        return count > 0

    # ── Symbol directory ────────────────────────────────────────────

    def save_symbols(self, symbols: list[dict]) -> int:
        """Upsert symbol directory (stocks + ETFs). Returns count saved."""
        if not symbols:
            return 0
        params = [
            [s.get("ts_code", ""), s.get("name", ""), s.get("area", ""), s.get("industry", "")]
            for s in symbols if s.get("ts_code")
        ]
        self._conn.executemany(
            """INSERT INTO symbols (ts_code, name, area, industry, updated_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT (ts_code) DO UPDATE SET
                 name=EXCLUDED.name, area=EXCLUDED.area,
                 industry=EXCLUDED.industry, updated_at=CURRENT_TIMESTAMP""",
            params,
        )
        return len(params)

    def query_symbols(self, keyword: str = "", limit: int = 50) -> list[dict]:
        """Search symbols by code or name. Empty keyword returns all."""
        if keyword:
            rows = self._conn.execute(
                "SELECT ts_code, name, area, industry FROM symbols "
                "WHERE ts_code ILIKE ? OR name ILIKE ? LIMIT ?",
                [f"%{keyword}%", f"%{keyword}%", limit],
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT ts_code, name, area, industry FROM symbols LIMIT ?", [limit],
            ).fetchall()
        return [{"ts_code": r[0], "name": r[1], "area": r[2], "industry": r[3]} for r in rows]

    def symbols_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
