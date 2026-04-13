"""DuckDB implementation of DataStore.

[CORE] — interface frozen. Implementation details may change.

V2.18: Parquet-first query. Both query_kline() and query_kline_batch()
check data/cache/{market}_{period}.parquet before DuckDB tables.
Date-range guard via manifest.json prevents staleness loops.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from ez.data.provider import DataStore
from ez.errors import ValidationError
from ez.types import Bar

logger = logging.getLogger(__name__)

# Whitelist of valid period values — used to prevent SQL injection
_VALID_PERIODS = frozenset(("daily", "weekly", "monthly"))


def _safe_table(period: str) -> str:
    """Return sanitized table name or raise on invalid period."""
    if period not in _VALID_PERIODS:
        raise ValidationError(f"Invalid period '{period}'. Must be one of: {', '.join(sorted(_VALID_PERIODS))}")
    return f"kline_{period}"


def _rows_to_bars(rows: list) -> list[Bar]:
    """Convert DB/parquet rows to Bar objects."""
    return [
        Bar(time=r[0], symbol=r[1], market=r[2], open=r[3], high=r[4],
            low=r[5], close=r[6], adj_close=r[7], volume=int(r[8]))
        for r in rows
    ]


class DuckDBStore(DataStore):
    """DuckDB-backed data store."""

    PERIODS = ("daily", "weekly", "monthly")
    _PARQUET_GRACE_DAYS = 7  # C4: date-range grace for weekends/holidays

    def __init__(self, db_path: str = "data/ez_trading.db"):
        import os
        import sys as _sys
        # EZ_DATA_DIR overrides default data location (used in packaged builds)
        data_dir = os.environ.get("EZ_DATA_DIR")
        if data_dir:
            p = Path(data_dir) / "ez_trading.db"
        else:
            p = Path(db_path)
            if not p.is_absolute():
                project_root = Path(__file__).resolve().parent.parent.parent
                p = project_root / p
        p.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(p))
        self._init_tables()

        # Parquet cache directory resolution:
        # 1. EZ_DATA_DIR/cache  (env override)
        # 2. sys._MEIPASS/data/cache  (PyInstaller frozen)
        # 3. <project_root>/data/cache  (development)
        if data_dir:
            self._cache_dir: Path | None = Path(data_dir) / "cache"
        elif getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
            self._cache_dir = Path(_sys._MEIPASS) / "data" / "cache"
        else:
            project_root = Path(__file__).resolve().parent.parent.parent
            self._cache_dir = project_root / "data" / "cache"
        if not self._cache_dir.is_dir():
            self._cache_dir = None

        # Manifest cache (lazy-loaded on first parquet access)
        self._manifest: dict | None = None
        self._manifest_loaded = False

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

    # ── Parquet cache helpers ─────────────────────────────────────────

    def _load_manifest(self) -> dict | None:
        """Load and cache manifest.json from parquet cache directory."""
        if self._manifest_loaded:
            return self._manifest
        self._manifest_loaded = True
        if not self._cache_dir:
            return None
        manifest_path = self._cache_dir / "manifest.json"
        if manifest_path.exists():
            try:
                self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Failed to read parquet manifest: %s", manifest_path)
        return self._manifest

    def get_data_hash(self) -> str | None:
        """Return a short (12-char) hash summarizing current parquet cache state.

        V2.16.2 round 4: used for backtest reproducibility. The hash is
        a SHA-256 prefix of the sorted file->md5 mapping plus the
        manifest date_range. If a user re-runs the same spec after the
        parquet cache was rebuilt, the hash changes -> results may differ.
        Downstream callers (e.g. portfolio /run) embed this in
        run_config to detect silent drift between runs.

        Returns None if manifest absent or lacks file md5s — caller
        should treat that as "unknown data state".
        """
        manifest = self._load_manifest()
        if not manifest:
            return None
        files = manifest.get("files") or {}
        md5_map = {
            name: (info or {}).get("md5")
            for name, info in files.items()
            if isinstance(info, dict) and info.get("md5")
        }
        if not md5_map:
            return None
        date_range = manifest.get("date_range") or {}
        payload = {
            "files": dict(sorted(md5_map.items())),
            "date_range": {
                "start": date_range.get("start"),
                "end": date_range.get("end"),
            },
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def _find_parquet_cache(self, market: str, period: str, end_date: date | None = None) -> str | None:
        """Return parquet path if file exists and date range is valid.

        C4 date-range guard: if requested end_date exceeds manifest's
        date_range.end + 7 days, skip parquet to prevent staleness loop.
        """
        if not self._cache_dir:
            return None
        p = self._cache_dir / f"{market}_{period}.parquet"
        if not p.exists():
            return None

        # Date-range guard (C4)
        if end_date is not None:
            manifest = self._load_manifest()
            if manifest:
                try:
                    manifest_end = date.fromisoformat(manifest["date_range"]["end"])
                    if end_date > manifest_end + timedelta(days=self._PARQUET_GRACE_DAYS):
                        return None  # Request exceeds parquet coverage
                except (KeyError, ValueError):
                    pass  # No valid date_range in manifest — allow parquet

        return str(p)

    # ── Query methods ─────────────────────────────────────────────────

    def query_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        # 1. Parquet cache (highest priority)
        parquet_path = self._find_parquet_cache(market, period, end_date)
        if parquet_path:
            rows = self._conn.execute(
                "SELECT time, symbol, market, open, high, low, close, adj_close, volume "
                "FROM read_parquet(?) "
                "WHERE symbol = ? AND time >= ? AND time <= ? ORDER BY time",
                [parquet_path, symbol,
                 datetime.combine(start_date, datetime.min.time()),
                 datetime.combine(end_date, datetime.max.time())],
            ).fetchall()
            if rows:
                return _rows_to_bars(rows)

        # 2. DuckDB table (existing behavior)
        table = _safe_table(period)
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE symbol=? AND market=? AND time>=? AND time<=? ORDER BY time",
            [symbol, market, datetime.combine(start_date, datetime.min.time()),
             datetime.combine(end_date, datetime.max.time())],
        ).fetchall()
        return _rows_to_bars(rows)

    def query_kline_batch(
        self, symbols: list[str], market: str, period: str,
        start_date: date, end_date: date,
    ) -> dict[str, list[Bar]]:
        """Batch query: parquet first (C3), then DuckDB for missing symbols."""
        if not symbols:
            return {}

        start_ts = datetime.combine(start_date, datetime.min.time())
        end_ts = datetime.combine(end_date, datetime.max.time())
        result: dict[str, list[Bar]] = {s: [] for s in symbols}
        remaining = list(symbols)

        # 1. Parquet cache
        parquet_path = self._find_parquet_cache(market, period, end_date)
        if parquet_path:
            placeholders = ",".join(["?"] * len(symbols))
            rows = self._conn.execute(
                f"SELECT time, symbol, market, open, high, low, close, adj_close, volume "
                f"FROM read_parquet(?) WHERE symbol IN ({placeholders}) "
                f"AND time >= ? AND time <= ? ORDER BY symbol, time",
                [parquet_path, *symbols, start_ts, end_ts],
            ).fetchall()
            found_syms: set[str] = set()
            for r in rows:
                bar = Bar(time=r[0], symbol=r[1], market=r[2], open=r[3], high=r[4],
                          low=r[5], close=r[6], adj_close=r[7], volume=int(r[8]))
                if bar.symbol in result:
                    result[bar.symbol].append(bar)
                    found_syms.add(bar.symbol)
            remaining = [s for s in symbols if s not in found_syms]

        if not remaining:
            return result

        # 2. DuckDB table for remaining symbols
        table = _safe_table(period)
        placeholders = ",".join(["?"] * len(remaining))
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE symbol IN ({placeholders}) AND market=? "
            f"AND time>=? AND time<=? ORDER BY symbol, time",
            [*remaining, market, start_ts, end_ts],
        ).fetchall()
        for r in rows:
            bar = Bar(time=r[0], symbol=r[1], market=r[2], open=r[3], high=r[4],
                      low=r[5], close=r[6], adj_close=r[7], volume=int(r[8]))
            if bar.symbol in result:
                result[bar.symbol].append(bar)
        return result

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
        # DuckDB executemany doesn't support SQL functions anywhere in the statement.
        # Use executemany for bulk upsert, then a single UPDATE for timestamps.
        self._conn.executemany(
            """INSERT INTO symbols (ts_code, name, area, industry)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (ts_code) DO UPDATE SET
                 name=EXCLUDED.name, area=EXCLUDED.area,
                 industry=EXCLUDED.industry""",
            params,
        )
        # Refresh updated_at for all affected rows
        codes = [p[0] for p in params]
        if codes:
            placeholders = ",".join(["?"] * len(codes))
            self._conn.execute(
                f"UPDATE symbols SET updated_at=CURRENT_TIMESTAMP WHERE ts_code IN ({placeholders})",
                codes,
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
