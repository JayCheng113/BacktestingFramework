"""B5: ExperimentStore — DuckDB persistence for experiment data.

Manages its own tables in the same DuckDB file as market data,
but does NOT modify the core DataStore schema.

Tables:
  - experiment_specs: immutable spec records (INSERT ON CONFLICT DO NOTHING)
  - experiment_runs: one row per run attempt
  - completed_specs: PRIMARY KEY constraint enforces at most one completed
    run per spec_id (atomic idempotency without explicit transactions)
"""
from __future__ import annotations

import json
import math

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
                strategy_params VARCHAR,
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
                use_market_rules BOOLEAN DEFAULT FALSE,
                t_plus_1 BOOLEAN DEFAULT TRUE,
                price_limit_pct DOUBLE DEFAULT 0.1,
                lot_size INTEGER DEFAULT 100,
                tags VARCHAR DEFAULT '[]',
                description VARCHAR DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_runs (
                run_id VARCHAR PRIMARY KEY,
                spec_id VARCHAR NOT NULL REFERENCES experiment_specs(spec_id),
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
                gate_reasons VARCHAR,
                error VARCHAR
            )
        """)
        # Atomic idempotency: PK constraint ensures at most one completed
        # run per spec_id. No explicit transactions needed.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS completed_specs (
                spec_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                completed_at TIMESTAMPTZ NOT NULL
            )
        """)
        # V2.6 migration: add market_rules columns if missing
        try:
            self._conn.execute("SELECT use_market_rules FROM experiment_specs LIMIT 0")
        except duckdb.Error:
            for col, defn in [
                ("use_market_rules", "BOOLEAN DEFAULT FALSE"),
                ("t_plus_1", "BOOLEAN DEFAULT TRUE"),
                ("price_limit_pct", "DOUBLE DEFAULT 0.1"),
                ("lot_size", "INTEGER DEFAULT 100"),
            ]:
                try:
                    self._conn.execute(f"ALTER TABLE experiment_specs ADD COLUMN {col} {defn}")
                except duckdb.Error:
                    pass  # column already exists

        # Backfill: only on first init (completed_specs empty) to avoid
        # unnecessary full-table scan on every startup.
        count = self._conn.execute("SELECT COUNT(*) FROM completed_specs").fetchone()[0]
        if count == 0:
            self._conn.execute("""
                INSERT INTO completed_specs (spec_id, run_id, completed_at)
                SELECT spec_id, run_id, created_at
                FROM experiment_runs
                WHERE status = 'completed'
                ON CONFLICT (spec_id) DO NOTHING
            """)

    def save_spec(self, spec_dict: dict) -> None:
        """Insert spec if not exists. Immutable — never overwrites existing specs."""
        self._conn.execute("""
            INSERT INTO experiment_specs (
                spec_id, strategy_name, strategy_params, symbol, market,
                period, start_date, end_date, initial_capital,
                commission_rate, min_commission, slippage_rate,
                run_backtest, run_wfo, wfo_n_splits, wfo_train_ratio,
                use_market_rules, t_plus_1, price_limit_pct, lot_size,
                tags, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (spec_id) DO NOTHING
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
            spec_dict.get("use_market_rules", False),
            spec_dict.get("t_plus_1", True),
            spec_dict.get("price_limit_pct", 0.1),
            spec_dict.get("lot_size", 100),
            json.dumps(spec_dict.get("tags", [])),
            spec_dict.get("description", ""),
        ])

    def save_run(self, report_dict: dict) -> None:
        """Insert a run result. Raises on duplicate run_id or missing spec."""
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

    def save_completed_run(self, report_dict: dict) -> bool:
        """Save a completed run with atomic idempotency.

        Uses completed_specs PK constraint: if a completed run already
        exists for this spec_id, the INSERT fails and we return False.
        No explicit transactions — the constraint is the lock.

        Non-completed runs (failed) are always saved without restriction.
        """
        spec_id = report_dict["spec_id"]

        if report_dict["status"] != "completed":
            self.save_run(report_dict)
            return True

        # Step 1: Claim the spec_id lock via PK constraint.
        # Only treat unique-key violations as "duplicate" (return False).
        # Other DuckDB errors (connection issues, etc.) must propagate.
        try:
            self._conn.execute(
                "INSERT INTO completed_specs (spec_id, run_id, completed_at) VALUES (?, ?, ?)",
                [spec_id, report_dict["run_id"], report_dict["created_at"]],
            )
        except duckdb.Error as e:
            msg = str(e).lower()
            if "duplicate" in msg or "unique" in msg or "constraint" in msg or "primary key" in msg:
                return False
            raise

        # Step 2: Write the actual run. If this fails, roll back the lock
        # to avoid a "dirty" completed_specs entry with no matching run.
        try:
            self.save_run(report_dict)
        except Exception:
            try:
                self._conn.execute(
                    "DELETE FROM completed_specs WHERE spec_id = ?", [spec_id],
                )
            except Exception:
                pass  # rollback failed — original error is more important
            raise

        return True

    def get_completed_run_id(self, spec_id: str) -> str | None:
        """Return the run_id of the completed run for this spec_id, or None."""
        result = self._conn.execute(
            "SELECT run_id FROM completed_specs WHERE spec_id = ?", [spec_id],
        ).fetchone()
        return result[0] if result else None

    @staticmethod
    def _clean_rows(records: list[dict]) -> list[dict]:
        """Clean DuckDB output for JSON compliance: NaN→None, JSON strings→objects."""
        json_fields = ("gate_reasons", "strategy_params", "tags")
        for row in records:
            for k, v in row.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    row[k] = None
                elif k in json_fields and isinstance(v, str):
                    try:
                        row[k] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        pass
        return records

    def find_by_spec_id(self, spec_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM experiment_runs WHERE spec_id = ? ORDER BY created_at DESC",
            [spec_id],
        ).fetchdf()
        return self._clean_rows(rows.to_dict("records")) if len(rows) > 0 else []

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self._conn.execute(
            "SELECT r.*, s.strategy_name, s.symbol, s.market, s.strategy_params, s.start_date, s.end_date "
            "FROM experiment_runs r "
            "JOIN experiment_specs s ON r.spec_id = s.spec_id "
            "ORDER BY r.created_at DESC LIMIT ? OFFSET ?",
            [limit, offset],
        ).fetchdf()
        return self._clean_rows(rows.to_dict("records")) if len(rows) > 0 else []

    def get_run(self, run_id: str) -> dict | None:
        rows = self._conn.execute(
            "SELECT r.*, s.strategy_name, s.symbol, s.market, s.strategy_params, s.start_date, s.end_date "
            "FROM experiment_runs r "
            "JOIN experiment_specs s ON r.spec_id = s.spec_id "
            "WHERE r.run_id = ?",
            [run_id],
        ).fetchdf()
        if len(rows) == 0:
            return None
        return self._clean_rows(rows.to_dict("records"))[0]

    def count_by_spec_id(self, spec_id: str) -> int:
        result = self._conn.execute(
            "SELECT COUNT(*) FROM experiment_runs WHERE spec_id = ?",
            [spec_id],
        ).fetchone()
        return result[0] if result else 0

    def delete_run(self, run_id: str) -> bool:
        """Delete a single run by run_id. Also cleans completed_specs if applicable."""
        row = self._conn.execute(
            "SELECT spec_id FROM experiment_runs WHERE run_id = ?", [run_id],
        ).fetchone()
        if not row:
            return False
        spec_id = row[0]
        # Delete run first
        self._conn.execute("DELETE FROM experiment_runs WHERE run_id = ?", [run_id])
        # Clean orphan completed_specs + spec if no runs remain
        remaining = self._conn.execute(
            "SELECT COUNT(*) FROM experiment_runs WHERE spec_id = ?", [spec_id],
        ).fetchone()[0]
        if remaining == 0:
            self._conn.execute("DELETE FROM completed_specs WHERE spec_id = ?", [spec_id])
            self._conn.execute("DELETE FROM experiment_specs WHERE spec_id = ?", [spec_id])
        return True

    def cleanup_old_runs(self, keep_last: int = 200) -> int:
        """Delete oldest runs beyond keep_last. Returns count deleted."""
        total = self._conn.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0]
        if total <= keep_last:
            return 0
        to_delete = total - keep_last
        run_ids = self._conn.execute(
            "SELECT run_id FROM experiment_runs ORDER BY created_at ASC LIMIT ?",
            [to_delete],
        ).fetchall()
        deleted = 0
        for (run_id,) in run_ids:
            if self.delete_run(run_id):
                deleted += 1
        return deleted

    def close(self) -> None:
        self._conn.close()
