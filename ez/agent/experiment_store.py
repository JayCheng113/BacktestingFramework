"""B5: ExperimentStore — DuckDB persistence for experiment data.

Manages its own tables (runs, gate_verdicts) in the same DuckDB file
as market data, but does NOT modify the core DataStore schema.

Tables:
  - experiment_runs: one row per run (spec + metrics + gate result)
  - experiment_specs: one row per unique spec_id (dedup key)
"""
from __future__ import annotations

import json
from datetime import datetime

import duckdb


class ExperimentStore:
    """Persist and query experiment runs in DuckDB."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_specs (
                spec_id VARCHAR PRIMARY KEY,
                strategy_name VARCHAR NOT NULL,
                strategy_params VARCHAR,  -- JSON
                symbol VARCHAR NOT NULL,
                market VARCHAR NOT NULL,
                period VARCHAR DEFAULT 'daily',
                start_date DATE,
                end_date DATE,
                initial_capital DOUBLE DEFAULT 100000,
                commission_rate DOUBLE DEFAULT 0.0003,
                min_commission DOUBLE DEFAULT 5.0,
                slippage_rate DOUBLE DEFAULT 0.0,
                run_backtest BOOLEAN DEFAULT TRUE,
                run_wfo BOOLEAN DEFAULT TRUE,
                wfo_n_splits INTEGER DEFAULT 5,
                wfo_train_ratio DOUBLE DEFAULT 0.7,
                tags VARCHAR DEFAULT '[]',  -- JSON array
                description VARCHAR DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_runs (
                run_id VARCHAR PRIMARY KEY,
                spec_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                duration_ms DOUBLE,
                code_commit VARCHAR,
                sharpe_ratio DOUBLE,
                total_return DOUBLE,
                max_drawdown DOUBLE,
                trade_count INTEGER DEFAULT 0,
                win_rate DOUBLE,
                profit_factor DOUBLE,
                p_value DOUBLE,
                is_significant BOOLEAN DEFAULT FALSE,
                oos_sharpe DOUBLE,
                overfitting_score DOUBLE,
                gate_passed BOOLEAN DEFAULT FALSE,
                gate_summary VARCHAR,
                gate_reasons VARCHAR,  -- JSON
                error VARCHAR
            )
        """)

    def save_spec(self, spec_dict: dict) -> None:
        """Upsert a RunSpec (idempotent on spec_id)."""
        self._conn.execute("""
            INSERT OR REPLACE INTO experiment_specs (
                spec_id, strategy_name, strategy_params, symbol, market,
                period, start_date, end_date, initial_capital,
                commission_rate, min_commission, slippage_rate,
                run_backtest, run_wfo, wfo_n_splits, wfo_train_ratio,
                tags, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            spec_dict["spec_id"],
            spec_dict["strategy_name"],
            json.dumps(spec_dict["strategy_params"]),
            spec_dict["symbol"],
            spec_dict["market"],
            spec_dict["period"],
            spec_dict["start_date"],
            spec_dict["end_date"],
            spec_dict["initial_capital"],
            spec_dict["commission_rate"],
            spec_dict["min_commission"],
            spec_dict["slippage_rate"],
            spec_dict["run_backtest"],
            spec_dict["run_wfo"],
            spec_dict["wfo_n_splits"],
            spec_dict["wfo_train_ratio"],
            json.dumps(spec_dict.get("tags", [])),
            spec_dict.get("description", ""),
        ])

    def save_run(self, report_dict: dict) -> None:
        """Insert a run result. Raises on duplicate run_id."""
        self._conn.execute("""
            INSERT INTO experiment_runs (
                run_id, spec_id, status, created_at, duration_ms, code_commit,
                sharpe_ratio, total_return, max_drawdown, trade_count,
                win_rate, profit_factor, p_value, is_significant,
                oos_sharpe, overfitting_score,
                gate_passed, gate_summary, gate_reasons, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            report_dict["run_id"],
            report_dict["spec_id"],
            report_dict["status"],
            report_dict["created_at"],
            report_dict["duration_ms"],
            report_dict["code_commit"],
            report_dict["sharpe_ratio"],
            report_dict["total_return"],
            report_dict["max_drawdown"],
            report_dict["trade_count"],
            report_dict["win_rate"],
            report_dict["profit_factor"],
            report_dict["p_value"],
            report_dict["is_significant"],
            report_dict["oos_sharpe"],
            report_dict["overfitting_score"],
            report_dict["gate_passed"],
            report_dict["gate_summary"],
            json.dumps(report_dict.get("gate_reasons", [])),
            report_dict["error"],
        ])

    def find_by_spec_id(self, spec_id: str) -> list[dict]:
        """Find all runs for a given spec_id."""
        rows = self._conn.execute(
            "SELECT * FROM experiment_runs WHERE spec_id = ? ORDER BY created_at DESC",
            [spec_id],
        ).fetchdf()
        return rows.to_dict("records") if len(rows) > 0 else []

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List recent runs."""
        rows = self._conn.execute(
            "SELECT r.*, s.strategy_name, s.symbol, s.market "
            "FROM experiment_runs r "
            "JOIN experiment_specs s ON r.spec_id = s.spec_id "
            "ORDER BY r.created_at DESC LIMIT ? OFFSET ?",
            [limit, offset],
        ).fetchdf()
        return rows.to_dict("records") if len(rows) > 0 else []

    def get_run(self, run_id: str) -> dict | None:
        """Get a single run by run_id."""
        rows = self._conn.execute(
            "SELECT r.*, s.strategy_name, s.symbol, s.market, s.strategy_params "
            "FROM experiment_runs r "
            "JOIN experiment_specs s ON r.spec_id = s.spec_id "
            "WHERE r.run_id = ?",
            [run_id],
        ).fetchdf()
        if len(rows) == 0:
            return None
        return rows.to_dict("records")[0]

    def count_by_spec_id(self, spec_id: str) -> int:
        """Count runs for a spec (for idempotency check)."""
        result = self._conn.execute(
            "SELECT COUNT(*) FROM experiment_runs WHERE spec_id = ?",
            [spec_id],
        ).fetchone()
        return result[0] if result else 0
