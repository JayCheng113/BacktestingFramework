"""V2.15 A2: DeploymentStore — DuckDB persistence for deployment specs, records, and snapshots.

Follows the pattern established by ez/portfolio/portfolio_store.py:
- DuckDB connection passed in constructor
- _sanitize_nans for JSON safety
- CREATE TABLE IF NOT EXISTS for schema init
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone

import threading

import duckdb

from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec


def _sanitize_nans(obj):
    """Replace NaN/Inf with None in nested dicts/lists."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_nans(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nans(v) for v in obj]
    return obj


def _to_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime is UTC-aware, or return None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_ts(val) -> datetime | None:
    """Parse a DuckDB timestamp value to a UTC-aware datetime, or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    # String fallback
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


VALID_STATUSES = frozenset({"pending", "approved", "running", "paused", "stopped", "error"})


class DeploymentStore:
    """Persist deployment specs, records, and daily snapshots to DuckDB."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn
        self._lock = threading.Lock()  # DuckDB single-connection not thread-safe
        self._init_tables()

    def _init_tables(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS deployment_specs (
                    spec_id     VARCHAR PRIMARY KEY,
                    spec_json   TEXT NOT NULL,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS deployment_records (
                    deployment_id       VARCHAR PRIMARY KEY,
                    spec_id             VARCHAR NOT NULL,
                    name                VARCHAR NOT NULL,
                    status              VARCHAR DEFAULT 'pending',
                    stop_reason         VARCHAR DEFAULT '',
                    source_run_id       VARCHAR,
                    code_commit         VARCHAR,
                    gate_verdict        TEXT,
                    last_processed_date DATE,
                    consecutive_errors  INTEGER DEFAULT 0,
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    approved_at         TIMESTAMP,
                    started_at          TIMESTAMP,
                    stopped_at          TIMESTAMP
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS deployment_snapshots (
                    deployment_id   VARCHAR NOT NULL,
                    snapshot_date   DATE NOT NULL,
                    equity          DOUBLE NOT NULL,
                    cash            DOUBLE NOT NULL,
                    holdings        TEXT NOT NULL,
                    weights         TEXT NOT NULL,
                    prev_returns    TEXT DEFAULT '{}',
                    trades          TEXT DEFAULT '[]',
                    risk_events     TEXT DEFAULT '[]',
                    rebalanced      BOOLEAN DEFAULT FALSE,
                    execution_ms    DOUBLE,
                    error           TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (deployment_id, snapshot_date)
                )
            """)

    # -- Spec methods ------------------------------------------------------

    def save_spec(self, spec: DeploymentSpec) -> None:
        """INSERT OR IGNORE — idempotent by spec_id."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO deployment_specs (spec_id, spec_json) VALUES (?, ?)",
                [spec.spec_id, spec.to_json()],
            )

    def get_spec(self, spec_id: str) -> DeploymentSpec | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT spec_json FROM deployment_specs WHERE spec_id = ?",
                [spec_id],
            ).fetchone()
            if not row:
                return None
            return DeploymentSpec.from_json(row[0])

    # -- Record methods ----------------------------------------------------

    def save_record(self, record: DeploymentRecord) -> None:
        """INSERT a new deployment record."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO deployment_records
                   (deployment_id, spec_id, name, status, stop_reason, source_run_id,
                    code_commit, gate_verdict, created_at, approved_at, started_at, stopped_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    record.deployment_id,
                    record.spec_id,
                    record.name,
                    record.status,
                    record.stop_reason,
                    record.source_run_id,
                    record.code_commit,
                    record.gate_verdict,
                    _to_utc(record.created_at),
                    _to_utc(record.approved_at),
                    _to_utc(record.started_at),
                    _to_utc(record.stopped_at),
                ],
            )

    def get_record(self, deployment_id: str) -> DeploymentRecord | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT deployment_id, spec_id, name, status, stop_reason,
                          source_run_id, code_commit, gate_verdict,
                          created_at, approved_at, started_at, stopped_at
                   FROM deployment_records WHERE deployment_id = ?""",
                [deployment_id],
            ).fetchone()
            if not row:
                return None
            return DeploymentRecord(
                deployment_id=row[0],
                spec_id=row[1],
                name=row[2],
                status=row[3] or "pending",
                stop_reason=row[4] or "",
                source_run_id=row[5],
                code_commit=row[6],
                gate_verdict=row[7],
                created_at=_parse_ts(row[8]) or datetime.now(timezone.utc),
                approved_at=_parse_ts(row[9]),
                started_at=_parse_ts(row[10]),
                stopped_at=_parse_ts(row[11]),
            )

    def list_deployments(self, status: str | None = None) -> list[DeploymentRecord]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    """SELECT deployment_id, spec_id, name, status, stop_reason,
                              source_run_id, code_commit, gate_verdict,
                              created_at, approved_at, started_at, stopped_at
                       FROM deployment_records WHERE status = ?
                       ORDER BY created_at DESC""",
                    [status],
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT deployment_id, spec_id, name, status, stop_reason,
                              source_run_id, code_commit, gate_verdict,
                              created_at, approved_at, started_at, stopped_at
                       FROM deployment_records ORDER BY created_at DESC""",
                ).fetchall()
            return [
                DeploymentRecord(
                    deployment_id=r[0],
                    spec_id=r[1],
                    name=r[2],
                    status=r[3] or "pending",
                    stop_reason=r[4] or "",
                    source_run_id=r[5],
                    code_commit=r[6],
                    gate_verdict=r[7],
                    created_at=_parse_ts(r[8]) or datetime.now(timezone.utc),
                    approved_at=_parse_ts(r[9]),
                    started_at=_parse_ts(r[10]),
                    stopped_at=_parse_ts(r[11]),
                )
                for r in rows
            ]

    def update_status(self, deployment_id: str, status: str, stop_reason: str = "") -> None:
        """Update deployment status. Sets timestamp columns based on new status."""
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid deployment status: {status!r}")
        with self._lock:
            now = datetime.now(timezone.utc)
            if status == "approved":
                self._conn.execute(
                    "UPDATE deployment_records SET status = ?, stop_reason = ?, approved_at = ? WHERE deployment_id = ?",
                    [status, stop_reason, now, deployment_id],
                )
            elif status == "running":
                self._conn.execute(
                    "UPDATE deployment_records SET status = ?, stop_reason = ?, started_at = ? WHERE deployment_id = ?",
                    [status, stop_reason, now, deployment_id],
                )
            elif status in ("stopped", "paused", "error"):
                self._conn.execute(
                    "UPDATE deployment_records SET status = ?, stop_reason = ?, stopped_at = ? WHERE deployment_id = ?",
                    [status, stop_reason, now, deployment_id],
                )
            else:
                self._conn.execute(
                    "UPDATE deployment_records SET status = ?, stop_reason = ? WHERE deployment_id = ?",
                    [status, stop_reason, deployment_id],
                )

    # -- Snapshot methods --------------------------------------------------

    def save_daily_snapshot(self, deployment_id: str, snapshot_date: date, result: dict) -> None:
        """Save one day's execution result. Also updates last_processed_date atomically."""
        sanitized = _sanitize_nans(result)
        with self._lock:
            self._conn.begin()
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO deployment_snapshots
                       (deployment_id, snapshot_date, equity, cash, holdings, weights,
                        prev_returns, trades, risk_events, rebalanced, execution_ms, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        deployment_id,
                        snapshot_date,
                        sanitized.get("equity", 0.0),
                        sanitized.get("cash", 0.0),
                        json.dumps(sanitized.get("holdings", {}), ensure_ascii=False),
                        json.dumps(sanitized.get("weights", {}), ensure_ascii=False),
                        json.dumps(sanitized.get("prev_returns", {}), ensure_ascii=False),
                        json.dumps(sanitized.get("trades", []), ensure_ascii=False),
                        json.dumps(sanitized.get("risk_events", []), ensure_ascii=False),
                        bool(sanitized.get("rebalanced", False)),
                        sanitized.get("execution_ms"),
                        sanitized.get("error"),
                    ],
                )
                # Update last_processed_date in the record
                self._conn.execute(
                    "UPDATE deployment_records SET last_processed_date = ? WHERE deployment_id = ?",
                    [snapshot_date, deployment_id],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_latest_snapshot(self, deployment_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT deployment_id, snapshot_date, equity, cash, holdings, weights,
                          prev_returns, trades, risk_events, rebalanced, execution_ms, error
                   FROM deployment_snapshots
                   WHERE deployment_id = ?
                   ORDER BY snapshot_date DESC LIMIT 1""",
                [deployment_id],
            ).fetchone()
            if not row:
                return None
            return self._row_to_snapshot(row)

    def get_all_snapshots(self, deployment_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT deployment_id, snapshot_date, equity, cash, holdings, weights,
                          prev_returns, trades, risk_events, rebalanced, execution_ms, error
                   FROM deployment_snapshots
                   WHERE deployment_id = ?
                   ORDER BY snapshot_date ASC""",
                [deployment_id],
            ).fetchall()
            return [self._row_to_snapshot(r) for r in rows]

    def get_last_processed_date(self, deployment_id: str) -> date | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_processed_date FROM deployment_records WHERE deployment_id = ?",
                [deployment_id],
            ).fetchone()
            if not row or row[0] is None:
                return None
            val = row[0]
            if isinstance(val, date):
                return val
            # String fallback
            if isinstance(val, str):
                return date.fromisoformat(val)
            return None

    # -- Error tracking ----------------------------------------------------

    def save_error(self, deployment_id: str, snapshot_date: date, error: str) -> None:
        """Record an error for this date WITHOUT writing a zero-asset snapshot.

        Only advances last_processed_date and writes error text.
        Does NOT create/overwrite a snapshot row with equity=0/cash=0 —
        that would corrupt _restore_full_state on next resume.
        """
        with self._lock:
            self._conn.begin()
            try:
                # Check if a normal snapshot already exists for this date
                existing = self._conn.execute(
                    "SELECT 1 FROM deployment_snapshots WHERE deployment_id = ? AND snapshot_date = ?",
                    [deployment_id, snapshot_date],
                ).fetchone()
                if existing:
                    # Update error column on existing snapshot (don't overwrite equity/cash)
                    self._conn.execute(
                        "UPDATE deployment_snapshots SET error = ? WHERE deployment_id = ? AND snapshot_date = ?",
                        [error, deployment_id, snapshot_date],
                    )
                # else: no snapshot for this date — that's fine, _restore_full_state
                # will use the latest successful snapshot

                # Always advance last_processed_date (so scheduler doesn't re-attempt)
                self._conn.execute(
                    "UPDATE deployment_records SET last_processed_date = ? WHERE deployment_id = ?",
                    [snapshot_date, deployment_id],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def increment_error_count(self, deployment_id: str) -> int:
        """Increment consecutive_errors and return the new count."""
        with self._lock:
            self._conn.execute(
                """UPDATE deployment_records
                   SET consecutive_errors = consecutive_errors + 1
                   WHERE deployment_id = ?""",
                [deployment_id],
            )
            row = self._conn.execute(
                "SELECT consecutive_errors FROM deployment_records WHERE deployment_id = ?",
                [deployment_id],
            ).fetchone()
            return row[0] if row else 0

    def reset_error_count(self, deployment_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE deployment_records SET consecutive_errors = 0 WHERE deployment_id = ?",
                [deployment_id],
            )

    def get_error_count(self, deployment_id: str) -> int:
        """Return the current consecutive_errors count for a deployment."""
        with self._lock:
            row = self._conn.execute(
                "SELECT consecutive_errors FROM deployment_records WHERE deployment_id = ?",
                [deployment_id],
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def update_gate_verdict(self, deployment_id: str, verdict_json: str) -> None:
        """Persist the serialized gate verdict JSON for a deployment."""
        with self._lock:
            self._conn.execute(
                "UPDATE deployment_records SET gate_verdict = ? WHERE deployment_id = ?",
                [verdict_json, deployment_id],
            )

    # -- Internal helpers --------------------------------------------------

    @staticmethod
    def _row_to_snapshot(row) -> dict:
        """Convert a snapshot query row to a dict with parsed JSON fields."""
        return _sanitize_nans({
            "deployment_id": row[0],
            "snapshot_date": row[1],
            "equity": row[2],
            "cash": row[3],
            "holdings": json.loads(row[4]) if row[4] else {},
            "weights": json.loads(row[5]) if row[5] else {},
            "prev_returns": json.loads(row[6]) if row[6] else {},
            "trades": json.loads(row[7]) if row[7] else [],
            "risk_events": json.loads(row[8]) if row[8] else [],
            "rebalanced": bool(row[9]) if row[9] is not None else False,
            "execution_ms": row[10],
            "error": row[11],
        })

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
