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
from typing import Any

import threading

import duckdb

from ez.live.broker import BrokerExecutionReport
from ez.live.events import (
    DeploymentEvent,
    EventType,
    broker_order_status_rank,
    normalize_broker_order_status,
)
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
    """Normalize datetimes to naive UTC before DuckDB TIMESTAMP persistence.

    DuckDB TIMESTAMP columns are timezone-naive. Persisting an aware datetime
    can round-trip through the local timezone, which breaks later comparisons
    against fresh UTC timestamps. Storing naive UTC keeps ordering stable.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


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


def _runtime_event_payload(event: DeploymentEvent) -> dict[str, Any]:
    payload = event.payload or {}
    runtime_payload = payload.get("payload")
    return dict(runtime_payload) if isinstance(runtime_payload, dict) else {}


def _runtime_event_is_cancel_failed(event: DeploymentEvent) -> bool:
    if event.event_type != EventType.BROKER_RUNTIME_RECORDED:
        return False
    payload = event.payload or {}
    runtime_kind = str(payload.get("runtime_kind", "") or "")
    runtime_payload = _runtime_event_payload(event)
    if runtime_kind == "cancel_error":
        return True
    if runtime_kind != "cancel_order_stock_async_response":
        return False
    cancel_result = runtime_payload.get("cancel_result")
    error_msg = str(
        runtime_payload.get("error_msg", "")
        or runtime_payload.get("status_msg", "")
        or ""
    )
    return cancel_result not in {"", None, 0, "0"} or bool(error_msg)


def _runtime_event_is_cancel_submit_ack(event: DeploymentEvent) -> bool:
    if event.event_type != EventType.BROKER_RUNTIME_RECORDED:
        return False
    payload = event.payload or {}
    runtime_kind = str(payload.get("runtime_kind", "") or "")
    if runtime_kind != "cancel_order_stock_async_response":
        return False
    runtime_payload = _runtime_event_payload(event)
    cancel_result = runtime_payload.get("cancel_result")
    error_msg = str(
        runtime_payload.get("error_msg", "")
        or runtime_payload.get("status_msg", "")
        or ""
    )
    if cancel_result not in {"", None, 0, "0"} or bool(error_msg):
        return False
    return bool(
        str(
            runtime_payload.get("order_sysid", "")
            or runtime_payload.get("broker_order_id", "")
            or runtime_payload.get("order_id", "")
            or ""
        ).strip()
    )


def _runtime_event_is_order_submit_ack(event: DeploymentEvent) -> bool:
    if event.event_type != EventType.BROKER_RUNTIME_RECORDED:
        return False
    payload = event.payload or {}
    runtime_kind = str(payload.get("runtime_kind", "") or "")
    if runtime_kind != "order_stock_async_response":
        return False
    runtime_payload = _runtime_event_payload(event)
    return bool(
        str(
            runtime_payload.get("order_id", "")
            or runtime_payload.get("order_sysid", "")
            or ""
        ).strip()
    )


VALID_STATUSES = frozenset({"pending", "approved", "running", "paused", "stopped", "error"})


class DeploymentStore:
    """Persist deployment specs, records, and daily snapshots to DuckDB."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn
        self._lock = threading.RLock()  # RLock: cancel-ack upsert re-enters during append_event
        self._init_tables()

        from ez.live._broker_order_links import BrokerOrderLinkRepository
        self._broker_links = BrokerOrderLinkRepository(self._conn, self._lock)

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
                    liquidation     BOOLEAN DEFAULT FALSE,
                    execution_ms    DOUBLE,
                    error           TEXT,
                    strategy_state  BLOB,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (deployment_id, snapshot_date)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS deployment_events (
                    event_id         VARCHAR PRIMARY KEY,
                    deployment_id    VARCHAR NOT NULL,
                    event_type       VARCHAR NOT NULL,
                    event_ts         TIMESTAMP NOT NULL,
                    client_order_id  VARCHAR NOT NULL,
                    payload_json     TEXT NOT NULL,
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS deployment_broker_order_links (
                    deployment_id    VARCHAR NOT NULL,
                    broker_type      VARCHAR NOT NULL,
                    client_order_id  VARCHAR NOT NULL,
                    broker_order_id  VARCHAR DEFAULT '',
                    symbol           VARCHAR DEFAULT '',
                    account_id       VARCHAR DEFAULT '',
                    latest_report_id VARCHAR DEFAULT '',
                    latest_status    VARCHAR DEFAULT '',
                    last_report_ts   TIMESTAMP,
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (deployment_id, broker_type, client_order_id)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS deployment_broker_sync_cursors (
                    deployment_id  VARCHAR NOT NULL,
                    broker_type    VARCHAR NOT NULL,
                    cursor_json    TEXT NOT NULL,
                    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (deployment_id, broker_type)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS deployment_broker_state_projections (
                    deployment_id   VARCHAR NOT NULL,
                    broker_type     VARCHAR NOT NULL,
                    projection_json TEXT NOT NULL,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (deployment_id, broker_type)
                )
            """)
            # V2.17 migration: add strategy_state column if missing (existing DBs)
            try:
                self._conn.execute(
                    "ALTER TABLE deployment_snapshots ADD COLUMN strategy_state BLOB"
                )
            except Exception:
                pass  # Column already exists — OK
            try:
                self._conn.execute(
                    "ALTER TABLE deployment_snapshots ADD COLUMN liquidation BOOLEAN DEFAULT FALSE"
                )
            except Exception:
                pass  # Column already exists — OK
            try:
                self._conn.execute(
                    "ALTER TABLE deployment_broker_order_links ADD COLUMN symbol VARCHAR DEFAULT ''"
                )
            except Exception:
                pass  # Column already exists — OK
            try:
                self._conn.execute(
                    "ALTER TABLE deployment_broker_order_links ADD COLUMN account_id VARCHAR DEFAULT ''"
                )
            except Exception:
                pass  # Column already exists — OK

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

    def save_daily_snapshot(
        self,
        deployment_id: str,
        snapshot_date: date,
        result: dict,
        strategy_state: bytes | None = None,
    ) -> None:
        """Save one day's execution result. Also updates last_processed_date atomically.

        V2.17: optional `strategy_state` bytes (pickle blob) persists the
        strategy instance across process restarts. Enables MLAlpha to
        keep its trained sklearn model, StrategyEnsemble to keep its
        hypothetical-return ledger, and custom user strategies to
        preserve `self.*` fields. Passing None keeps the column NULL
        (graceful degrade if caller can't pickle).
        """
        sanitized = _sanitize_nans(result)
        with self._lock:
            self._conn.begin()
            try:
                self._write_snapshot_locked(
                    deployment_id=deployment_id,
                    snapshot_date=snapshot_date,
                    sanitized=sanitized,
                    strategy_state=strategy_state,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def save_snapshot_with_events(
        self,
        deployment_id: str,
        snapshot_date: date,
        snapshot_payload: dict,
        events: list[DeploymentEvent],
        broker_order_links: list[BrokerExecutionReport] | None = None,
        *,
        strategy_state: bytes | None = None,
    ) -> None:
        """Atomically persist OMS events + snapshot row + broker-order link upsert.

        Single DuckDB transaction — BEGIN / COMMIT / ROLLBACK covers all three
        writes. If any step fails, the entire batch is rolled back, including
        any broker-order link upsert that was already issued earlier in the
        transaction.

        This is the preferred path for live execution ticks, stop-with-liquidate,
        and any caller that mixes events + snapshots. Prevents the pathological
        case where events commit but the snapshot row write fails afterwards,
        leaving orphan events that confuse `_build_replay_baseline` during
        recovery.
        """
        sanitized = _sanitize_nans(snapshot_payload)
        with self._lock:
            self._conn.begin()
            try:
                if events:
                    self._append_events_locked(events)
                if broker_order_links:
                    self._broker_links._upsert_broker_order_links_locked(
                        deployment_id=deployment_id,
                        reports=broker_order_links,
                    )
                self._write_snapshot_locked(
                    deployment_id=deployment_id,
                    snapshot_date=snapshot_date,
                    sanitized=sanitized,
                    strategy_state=strategy_state,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def save_execution_result(
        self,
        *,
        deployment_id: str,
        snapshot_date: date,
        result: dict,
        events: list[DeploymentEvent],
        broker_reports: list[BrokerExecutionReport] | None = None,
        strategy_state: bytes | None = None,
    ) -> None:
        """Backward-compat wrapper around `save_snapshot_with_events`.

        Existing callers should continue to work unchanged. New callers should
        prefer `save_snapshot_with_events` — the argument names match the
        internal concepts (snapshot / events / broker-order links) more
        directly.
        """
        self.save_snapshot_with_events(
            deployment_id,
            snapshot_date,
            result,
            events,
            broker_reports,
            strategy_state=strategy_state,
        )

    def save_broker_sync_result(
        self,
        *,
        deployment_id: str,
        events: list[DeploymentEvent],
        broker_reports: list[BrokerExecutionReport] | None = None,
    ) -> None:
        """Atomically persist broker-side sync artifacts without writing a snapshot."""
        if not events and not broker_reports:
            return
        with self._lock:
            self._conn.begin()
            try:
                if events:
                    self._append_events_locked(events)
                if broker_reports:
                    self._broker_links._upsert_broker_order_links_locked(
                        deployment_id=deployment_id,
                        reports=broker_reports,
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_latest_snapshot(self, deployment_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT deployment_id, snapshot_date, equity, cash, holdings, weights,
                          prev_returns, trades, risk_events, rebalanced, liquidation,
                          execution_ms, error
                   FROM deployment_snapshots
                   WHERE deployment_id = ?
                   ORDER BY snapshot_date DESC LIMIT 1""",
                [deployment_id],
            ).fetchone()
            if not row:
                return None
            return self._row_to_snapshot(row)

    def get_latest_strategy_state(self, deployment_id: str) -> bytes | None:
        """V2.17: fetch the latest snapshot's strategy pickle blob.

        Used by Scheduler._start_engine to restore strategy internals
        (trained ML models, ensemble ledgers, user-defined state)
        across process restart. Returns None if the latest snapshot has
        no state (e.g., strategy was unpicklable, persistence opted
        out, or deployment has not completed a tick yet). Older blobs
        must not be reused once a newer snapshot explicitly stored NULL.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT strategy_state
                   FROM deployment_snapshots
                   WHERE deployment_id = ?
                   ORDER BY snapshot_date DESC LIMIT 1""",
                [deployment_id],
            ).fetchone()
            if not row or row[0] is None:
                return None
            return bytes(row[0]) if not isinstance(row[0], bytes) else row[0]

    def get_all_snapshots(self, deployment_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT deployment_id, snapshot_date, equity, cash, holdings, weights,
                          prev_returns, trades, risk_events, rebalanced, liquidation,
                          execution_ms, error
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

    # -- Event methods -----------------------------------------------------

    def append_event(self, event: DeploymentEvent) -> None:
        """Append one event. Idempotent by deterministic event_id."""
        self.append_events([event])

    def append_events(self, events: list[DeploymentEvent]) -> int:
        """Append events with INSERT OR IGNORE semantics.

        Returns the number of newly inserted rows.
        """
        if not events:
            return 0
        with self._lock:
            self._conn.begin()
            try:
                inserted = self._append_events_locked(events)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return inserted

    def get_events(self, deployment_id: str) -> list[DeploymentEvent]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT event_id, deployment_id, event_type, event_ts,
                          client_order_id, payload_json
                   FROM deployment_events
                   WHERE deployment_id = ?
                   ORDER BY event_ts ASC, event_id ASC""",
                [deployment_id],
            ).fetchall()
            return [
                DeploymentEvent.from_dict(
                    {
                        "event_id": row[0],
                        "deployment_id": row[1],
                        "event_type": row[2],
                        "event_ts": row[3],
                        "client_order_id": row[4],
                        "payload": json.loads(row[5]) if row[5] else {},
                    }
                )
                for row in rows
            ]

    def get_latest_event(
        self,
        deployment_id: str,
        *,
        event_type: EventType | str,
    ) -> DeploymentEvent | None:
        event_type_value = (
            event_type.value
            if isinstance(event_type, EventType)
            else str(event_type)
        )
        with self._lock:
            row = self._conn.execute(
                """SELECT event_id, deployment_id, event_type, event_ts,
                          client_order_id, payload_json
                   FROM deployment_events
                   WHERE deployment_id = ? AND event_type = ?
                   ORDER BY event_ts DESC, event_id DESC
                   LIMIT 1""",
                [deployment_id, event_type_value],
            ).fetchone()
            if not row:
                return None
            return DeploymentEvent.from_dict(
                {
                    "event_id": row[0],
                    "deployment_id": row[1],
                    "event_type": row[2],
                    "event_ts": row[3],
                    "client_order_id": row[4],
                    "payload": json.loads(row[5]) if row[5] else {},
                }
            )

    def get_recent_events(
        self,
        deployment_id: str,
        *,
        event_type: EventType | str,
        limit: int = 10,
    ) -> list[DeploymentEvent]:
        event_type_value = (
            event_type.value
            if isinstance(event_type, EventType)
            else str(event_type)
        )
        limit = max(int(limit), 1)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT event_id, deployment_id, event_type, event_ts,
                           client_order_id, payload_json
                    FROM deployment_events
                    WHERE deployment_id = ? AND event_type = ?
                    ORDER BY event_ts DESC, event_id DESC
                    LIMIT {limit}""",
                [deployment_id, event_type_value],
            ).fetchall()
            return [
                DeploymentEvent.from_dict(
                    {
                        "event_id": row[0],
                        "deployment_id": row[1],
                        "event_type": row[2],
                        "event_ts": row[3],
                        "client_order_id": row[4],
                        "payload": json.loads(row[5]) if row[5] else {},
                    }
                )
                for row in rows
            ]

    def get_latest_event_ts(
        self,
        deployment_id: str,
        *,
        event_type: EventType | str | None = None,
    ) -> datetime | None:
        with self._lock:
            if event_type is None:
                row = self._conn.execute(
                    """SELECT event_ts
                       FROM deployment_events
                       WHERE deployment_id = ?
                       ORDER BY event_ts DESC, event_id DESC
                       LIMIT 1""",
                    [deployment_id],
                ).fetchone()
            else:
                event_type_value = (
                    event_type.value
                    if isinstance(event_type, EventType)
                    else str(event_type)
                )
                row = self._conn.execute(
                    """SELECT event_ts
                       FROM deployment_events
                       WHERE deployment_id = ? AND event_type = ?
                       ORDER BY event_ts DESC, event_id DESC
                       LIMIT 1""",
                    [deployment_id, event_type_value],
                ).fetchone()
            return _parse_ts(row[0]) if row else None

    def get_latest_runtime_event(
        self,
        deployment_id: str,
        *,
        kind: str | None = None,
        prefix: str | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> DeploymentEvent | None:
        """Return the latest broker runtime event matching a precise runtime kind selector."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT event_id, deployment_id, event_type, event_ts,
                          client_order_id, payload_json
                   FROM deployment_events
                   WHERE deployment_id = ? AND event_type = ?
                   ORDER BY event_ts DESC, event_id DESC""",
                [deployment_id, EventType.BROKER_RUNTIME_RECORDED.value],
            ).fetchall()
            accepted_kinds = set(kinds or ())
            for row in rows:
                payload = json.loads(row[5]) if row[5] else {}
                runtime_kind = str(payload.get("runtime_kind", "") or "")
                if kind is not None and runtime_kind != kind:
                    continue
                if prefix is not None and not runtime_kind.startswith(prefix):
                    continue
                if accepted_kinds and runtime_kind not in accepted_kinds:
                    continue
                return DeploymentEvent.from_dict(
                    {
                        "event_id": row[0],
                        "deployment_id": row[1],
                        "event_type": row[2],
                        "event_ts": row[3],
                        "client_order_id": row[4],
                        "payload": payload,
                    }
                )
            return None

    def get_latest_risk_event(
        self,
        deployment_id: str,
        *,
        event_name: str,
    ) -> dict | None:
        """Return the latest structured risk_event payload with the requested event name."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT payload_json
                   FROM deployment_events
                   WHERE deployment_id = ? AND event_type = ?
                   ORDER BY event_ts DESC, event_id DESC""",
                [deployment_id, EventType.RISK_RECORDED.value],
            ).fetchall()
            for row in rows:
                payload = json.loads(row[0]) if row[0] else {}
                risk_event = payload.get("risk_event") if isinstance(payload.get("risk_event"), dict) else None
                if isinstance(risk_event, dict) and risk_event.get("event") == event_name:
                    return risk_event
            return None

    def get_broker_sync_cursor(
        self,
        deployment_id: str,
        *,
        broker_type: str,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT cursor_json
                   FROM deployment_broker_sync_cursors
                   WHERE deployment_id = ? AND broker_type = ?""",
                [deployment_id, broker_type],
            ).fetchone()
            if not row or not row[0]:
                return None
            try:
                parsed = json.loads(row[0])
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None

    def upsert_broker_sync_cursor(
        self,
        deployment_id: str,
        *,
        broker_type: str,
        cursor_state: dict[str, object] | None,
    ) -> None:
        if cursor_state is None:
            return
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO deployment_broker_sync_cursors
                   (deployment_id, broker_type, cursor_json, updated_at)
                   VALUES (?, ?, ?, ?)""",
                [
                    deployment_id,
                    broker_type,
                    json.dumps(_sanitize_nans(cursor_state), ensure_ascii=False),
                    _to_utc(datetime.now(timezone.utc)),
                ],
            )

    def get_broker_state_projection(
        self,
        deployment_id: str,
        *,
        broker_type: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if broker_type:
                row = self._conn.execute(
                    """SELECT projection_json
                       FROM deployment_broker_state_projections
                       WHERE deployment_id = ? AND broker_type = ?""",
                    [deployment_id, broker_type],
                ).fetchone()
            else:
                row = self._conn.execute(
                    """SELECT projection_json
                       FROM deployment_broker_state_projections
                       WHERE deployment_id = ?
                       ORDER BY updated_at DESC, broker_type ASC
                       LIMIT 1""",
                    [deployment_id],
                ).fetchone()
            if not row or not row[0]:
                return None
            try:
                parsed = json.loads(row[0])
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None

    def upsert_broker_state_projection(
        self,
        deployment_id: str,
        *,
        broker_type: str,
        projection: dict[str, Any] | None,
    ) -> None:
        if not broker_type or projection is None:
            return
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO deployment_broker_state_projections
                   (deployment_id, broker_type, projection_json, updated_at)
                   VALUES (?, ?, ?, ?)""",
                [
                    deployment_id,
                    broker_type,
                    json.dumps(_sanitize_nans(projection), ensure_ascii=False),
                    _to_utc(datetime.now(timezone.utc)),
                ],
            )

    def get_broker_order_link(
        self,
        deployment_id: str,
        *,
        broker_type: str,
        client_order_id: str,
    ) -> dict | None:
        return self._broker_links.get_broker_order_link(
            deployment_id, broker_type=broker_type, client_order_id=client_order_id,
        )

    def find_broker_order_link(
        self,
        deployment_id: str,
        *,
        broker_type: str,
        broker_order_id: str,
        account_id: str = "",
    ) -> dict | None:
        return self._broker_links.find_broker_order_link(
            deployment_id,
            broker_type=broker_type,
            broker_order_id=broker_order_id,
            account_id=account_id,
        )

    def list_broker_order_links_by_broker_order_id(
        self,
        deployment_id: str,
        *,
        broker_type: str,
        broker_order_id: str,
        account_id: str = "",
    ) -> list[dict]:
        return self._broker_links.list_broker_order_links_by_broker_order_id(
            deployment_id,
            broker_type=broker_type,
            broker_order_id=broker_order_id,
            account_id=account_id,
        )

    def list_broker_order_links(
        self,
        deployment_id: str,
        *,
        broker_type: str | None = None,
    ) -> list[dict]:
        return self._broker_links.list_broker_order_links(
            deployment_id, broker_type=broker_type,
        )

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

    def _append_events_locked(self, events: list[DeploymentEvent]) -> int:
        inserted = 0
        for event in events:
            before = self._conn.execute(
                "SELECT 1 FROM deployment_events WHERE event_id = ?",
                [event.event_id],
            ).fetchone()
            self._conn.execute(
                """INSERT OR IGNORE INTO deployment_events
                   (event_id, deployment_id, event_type, event_ts,
                    client_order_id, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    event.event_id,
                    event.deployment_id,
                    event.event_type.value,
                    _to_utc(event.event_ts),
                    event.client_order_id,
                    json.dumps(_sanitize_nans(event.payload), ensure_ascii=False),
                ],
            )
            if before is None:
                inserted += 1
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED:
                self._broker_links._upsert_broker_execution_event_link_locked(event)
            elif _runtime_event_is_order_submit_ack(event):
                self._broker_links._upsert_broker_submit_ack_link_locked(event)
            elif _runtime_event_is_cancel_submit_ack(event):
                self._broker_links._upsert_broker_cancel_ack_link_locked(event)
            elif event.event_type == EventType.BROKER_CANCEL_REQUESTED:
                self._broker_links._upsert_broker_cancel_requested_link_locked(event)
            elif _runtime_event_is_cancel_failed(event):
                self._broker_links._upsert_broker_cancel_failed_link_locked(event)
        return inserted

    def _write_snapshot_locked(
        self,
        *,
        deployment_id: str,
        snapshot_date: date,
        sanitized: dict,
        strategy_state: bytes | None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO deployment_snapshots
               (deployment_id, snapshot_date, equity, cash, holdings, weights,
                prev_returns, trades, risk_events, rebalanced, liquidation,
                execution_ms, error,
                strategy_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                bool(sanitized.get("liquidation", False)),
                sanitized.get("execution_ms"),
                sanitized.get("error"),
                strategy_state,
            ],
        )
        self._conn.execute(
            "UPDATE deployment_records SET last_processed_date = ? WHERE deployment_id = ?",
            [snapshot_date, deployment_id],
        )

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
            "liquidation": bool(row[10]) if row[10] is not None else False,
            "execution_ms": row[11],
            "error": row[12],
        })

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
