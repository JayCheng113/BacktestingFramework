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

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
