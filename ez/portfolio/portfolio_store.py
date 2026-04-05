"""V2.9 P7: PortfolioStore — DuckDB persistence for portfolio backtest runs."""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime

import duckdb


def _sanitize_nans(obj):
    """Replace NaN/Inf with None in nested dicts/lists."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_nans(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nans(v) for v in obj]
    return obj


class PortfolioStore:
    """Persist portfolio backtest results to DuckDB."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn
        self._init_tables()

    def _init_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_runs (
                run_id          VARCHAR PRIMARY KEY,
                strategy_name   VARCHAR,
                strategy_params TEXT,
                symbols         TEXT,
                start_date      VARCHAR,
                end_date        VARCHAR,
                freq            VARCHAR,
                initial_cash    DOUBLE,
                metrics         TEXT,
                equity_curve    TEXT,
                trade_count     INTEGER,
                rebalance_count INTEGER,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                rebalance_weights TEXT,
                trades          TEXT,
                config          TEXT,
                warnings        TEXT,
                dates           TEXT
            )
        """)
        # Migration: add columns for existing DBs. V2.12.2 codex: config +
        # warnings added so past runs retain optimizer/risk_control/
        # index_benchmark/tracking_error plus the warnings the user saw at
        # run time. `dates` added so the compare-chart can align equity
        # curves by real trading days instead of the synthetic bar index —
        # prior version only stored equity values, so runs with different
        # date ranges, frequencies, or holiday gaps appeared aligned on the
        # x-axis but were actually shifted relative to each other.
        for col, typ in [
            ("rebalance_weights", "TEXT"),
            ("trades", "TEXT"),
            ("config", "TEXT"),
            ("warnings", "TEXT"),
            ("dates", "TEXT"),
        ]:
            try:
                self._conn.execute(f"ALTER TABLE portfolio_runs ADD COLUMN {col} {typ}")
            except Exception:
                pass  # column already exists

    def save_run(self, data: dict) -> str:
        run_id = data.get("run_id") or uuid.uuid4().hex[:12]
        self._conn.execute(
            """INSERT OR IGNORE INTO portfolio_runs
               (run_id, strategy_name, strategy_params, symbols, start_date, end_date,
                freq, initial_cash, metrics, equity_curve, trade_count, rebalance_count,
                rebalance_weights, trades, config, warnings, dates)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                run_id,
                data.get("strategy_name", ""),
                json.dumps(data.get("strategy_params", {}), ensure_ascii=False),
                json.dumps(data.get("symbols", []), ensure_ascii=False),
                data.get("start_date", ""),
                data.get("end_date", ""),
                data.get("freq", "monthly"),
                data.get("initial_cash", 1_000_000),
                json.dumps(data.get("metrics", {}), ensure_ascii=False),
                json.dumps(data.get("equity_curve", []), ensure_ascii=False),
                data.get("trade_count", 0),
                data.get("rebalance_count", 0),
                json.dumps(data.get("rebalance_weights", []), ensure_ascii=False),
                json.dumps(data.get("trades", []), ensure_ascii=False),
                json.dumps(data.get("config", {}), ensure_ascii=False),
                json.dumps(data.get("warnings", []), ensure_ascii=False),
                json.dumps(data.get("dates", []), ensure_ascii=False),
            ],
        )
        return run_id

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self._conn.execute(
            """SELECT run_id, strategy_name, start_date, end_date, freq,
                      metrics, trade_count, rebalance_count, created_at
               FROM portfolio_runs ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            [limit, offset],
        ).fetchall()
        result = []
        for r in rows:
            metrics = _sanitize_nans(json.loads(r[5])) if r[5] else {}
            result.append({
                "run_id": r[0], "strategy_name": r[1],
                "start_date": r[2], "end_date": r[3], "freq": r[4],
                "metrics": metrics, "trade_count": r[6],
                "rebalance_count": r[7], "created_at": str(r[8]) if r[8] else None,
            })
        return result

    def get_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            """SELECT run_id, strategy_name, strategy_params, symbols,
                      start_date, end_date, freq, initial_cash,
                      metrics, equity_curve, trade_count, rebalance_count, created_at,
                      rebalance_weights, trades, config, warnings, dates
               FROM portfolio_runs WHERE run_id = ?""", [run_id],
        ).fetchone()
        if not row:
            return None
        cols = ["run_id", "strategy_name", "strategy_params", "symbols",
                "start_date", "end_date", "freq", "initial_cash",
                "metrics", "equity_curve", "trade_count", "rebalance_count", "created_at",
                "rebalance_weights", "trades", "config", "warnings", "dates"]
        d = dict(zip(cols, row))
        for key in ("strategy_params", "symbols", "metrics", "equity_curve",
                     "rebalance_weights", "trades", "config", "warnings", "dates"):
            if d.get(key) and isinstance(d[key], str):
                d[key] = _sanitize_nans(json.loads(d[key]))
        if d["created_at"]:
            d["created_at"] = str(d["created_at"])
        return d

    def delete_run(self, run_id: str) -> bool:
        before = self._conn.execute("SELECT COUNT(*) FROM portfolio_runs WHERE run_id = ?", [run_id]).fetchone()[0]
        if before == 0:
            return False
        self._conn.execute("DELETE FROM portfolio_runs WHERE run_id = ?", [run_id])
        return True

    def close(self):
        if self._conn:
            self._conn.close()
