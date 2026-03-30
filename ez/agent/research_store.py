"""V2.8: Persistence for research tasks and iterations."""
from __future__ import annotations

import logging
from datetime import datetime

import duckdb

logger = logging.getLogger(__name__)


class ResearchStore:
    """DuckDB persistence for autonomous research tasks."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS research_tasks (
                task_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP,
                completed_at TIMESTAMP,
                stop_reason TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                error TEXT DEFAULT ''
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS research_iterations (
                task_id TEXT NOT NULL,
                iteration INTEGER NOT NULL,
                hypotheses TEXT DEFAULT '[]',
                strategies_tried INTEGER DEFAULT 0,
                strategies_passed INTEGER DEFAULT 0,
                best_sharpe DOUBLE DEFAULT 0.0,
                analysis TEXT DEFAULT '{}',
                spec_ids TEXT DEFAULT '[]',
                created_at TIMESTAMP,
                PRIMARY KEY (task_id, iteration)
            )
        """)

    def save_task(self, task: dict) -> None:
        self._conn.execute(
            """INSERT INTO research_tasks (task_id, goal, config, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            [task["task_id"], task["goal"], task.get("config", "{}"),
             task.get("status", "pending"), task.get("created_at", datetime.now().isoformat())],
        )

    def update_task_status(
        self, task_id: str, status: str,
        stop_reason: str = "", summary: str = "", error: str = "",
    ) -> None:
        completed_at = datetime.now().isoformat() if status in ("completed", "cancelled", "failed") else None
        self._conn.execute(
            """UPDATE research_tasks
               SET status=?, stop_reason=?, summary=?, error=?, completed_at=?
               WHERE task_id=?""",
            [status, stop_reason, summary, error, completed_at, task_id],
        )

    def save_iteration(self, iteration: dict) -> None:
        self._conn.execute(
            """INSERT INTO research_iterations
               (task_id, iteration, hypotheses, strategies_tried, strategies_passed,
                best_sharpe, analysis, spec_ids, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [iteration["task_id"], iteration["iteration"],
             iteration.get("hypotheses", "[]"),
             iteration.get("strategies_tried", 0),
             iteration.get("strategies_passed", 0),
             iteration.get("best_sharpe", 0.0),
             iteration.get("analysis", "{}"),
             iteration.get("spec_ids", "[]"),
             iteration.get("created_at", datetime.now().isoformat())],
        )

    def get_task(self, task_id: str) -> dict | None:
        rows = self._conn.execute(
            "SELECT * FROM research_tasks WHERE task_id=?", [task_id]
        ).fetchall()
        if not rows:
            return None
        cols = [d[0] for d in self._conn.description]
        return dict(zip(cols, rows[0]))

    def list_tasks(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM research_tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [limit, offset],
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_iterations(self, task_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM research_iterations WHERE task_id=? ORDER BY iteration",
            [task_id],
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def close(self) -> None:
        """Close the DuckDB connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
