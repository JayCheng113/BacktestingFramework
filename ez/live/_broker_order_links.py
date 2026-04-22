"""BrokerOrderLinkRepository — broker order link persistence extracted from DeploymentStore.

Owns all CRUD and upsert logic for the ``deployment_broker_order_links`` table.
Shares the DuckDB connection and ``threading.RLock`` with the parent
``DeploymentStore`` so transactions span both stores atomically.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

import duckdb

from ez.live.broker import BrokerExecutionReport
from ez.live.events import (
    DeploymentEvent,
    EventType,
    broker_order_status_rank,
    normalize_broker_order_status,
)

# Module-level helpers that stay in deployment_store.py — imported at call-time
# to avoid circular imports (this module is imported *by* deployment_store).
# We use a lazy-import helper so the top-level module load order stays clean.

_ds_helpers: dict[str, Any] = {}


def _get_helpers():
    """Lazy-import helpers from deployment_store to break the import cycle."""
    if not _ds_helpers:
        from ez.live.deployment_store import (
            _parse_ts,
            _runtime_event_payload,
            _to_utc,
        )

        _ds_helpers["_to_utc"] = _to_utc
        _ds_helpers["_parse_ts"] = _parse_ts
        _ds_helpers["_runtime_event_payload"] = _runtime_event_payload
    return _ds_helpers


class BrokerOrderLinkRepository:
    """Broker order link persistence — delegates from DeploymentStore."""

    def __init__(self, conn: duckdb.DuckDBPyConnection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    # -- Public query methods ------------------------------------------------

    def get_broker_order_link(
        self,
        deployment_id: str,
        *,
        broker_type: str,
        client_order_id: str,
    ) -> dict | None:
        h = _get_helpers()
        _parse_ts = h["_parse_ts"]
        with self._lock:
            row = self._conn.execute(
                """SELECT deployment_id, broker_type, client_order_id, broker_order_id, symbol,
                          account_id, latest_report_id, latest_status, last_report_ts
                   FROM deployment_broker_order_links
                   WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?""",
                [deployment_id, broker_type, client_order_id],
            ).fetchone()
            if not row:
                return None
            return {
                "deployment_id": row[0],
                "broker_type": row[1],
                "client_order_id": row[2],
                "broker_order_id": row[3] or "",
                "symbol": row[4] or "",
                "account_id": row[5] or "",
                "latest_report_id": row[6] or "",
                "latest_status": row[7] or "",
                "last_report_ts": _parse_ts(row[8]),
            }

    def find_broker_order_link(
        self,
        deployment_id: str,
        *,
        broker_type: str,
        broker_order_id: str,
        account_id: str = "",
    ) -> dict | None:
        h = _get_helpers()
        _parse_ts = h["_parse_ts"]
        with self._lock:
            normalized_account_id = str(account_id or "").strip()
            if normalized_account_id:
                row = self._conn.execute(
                    """SELECT deployment_id, broker_type, client_order_id, broker_order_id, symbol,
                              account_id, latest_report_id, latest_status, last_report_ts
                       FROM deployment_broker_order_links
                       WHERE deployment_id = ? AND broker_type = ? AND broker_order_id = ?
                         AND (account_id = ? OR account_id = '')
                       ORDER BY CASE WHEN account_id = ? THEN 0 ELSE 1 END,
                                last_report_ts DESC NULLS LAST, updated_at DESC, client_order_id ASC""",
                    [deployment_id, broker_type, broker_order_id, normalized_account_id, normalized_account_id],
                ).fetchone()
            else:
                row = self._conn.execute(
                    """SELECT deployment_id, broker_type, client_order_id, broker_order_id, symbol,
                              account_id, latest_report_id, latest_status, last_report_ts
                       FROM deployment_broker_order_links
                       WHERE deployment_id = ? AND broker_type = ? AND broker_order_id = ?
                       ORDER BY last_report_ts DESC NULLS LAST, updated_at DESC, client_order_id ASC""",
                    [deployment_id, broker_type, broker_order_id],
                ).fetchone()
            if not row:
                return None
            return {
                "deployment_id": row[0],
                "broker_type": row[1],
                "client_order_id": row[2],
                "broker_order_id": row[3] or "",
                "symbol": row[4] or "",
                "account_id": row[5] or "",
                "latest_report_id": row[6] or "",
                "latest_status": row[7] or "",
                "last_report_ts": _parse_ts(row[8]),
            }

    def list_broker_order_links_by_broker_order_id(
        self,
        deployment_id: str,
        *,
        broker_type: str,
        broker_order_id: str,
        account_id: str = "",
    ) -> list[dict]:
        h = _get_helpers()
        _parse_ts = h["_parse_ts"]
        with self._lock:
            normalized_account_id = str(account_id or "").strip()
            if normalized_account_id:
                rows = self._conn.execute(
                    """SELECT deployment_id, broker_type, client_order_id, broker_order_id, symbol,
                              account_id, latest_report_id, latest_status, last_report_ts, updated_at
                       FROM deployment_broker_order_links
                       WHERE deployment_id = ? AND broker_type = ? AND broker_order_id = ?
                         AND (account_id = ? OR account_id = '')
                       ORDER BY CASE WHEN account_id = ? THEN 0 ELSE 1 END,
                                last_report_ts DESC NULLS LAST, updated_at DESC, client_order_id ASC""",
                    [
                        deployment_id,
                        broker_type,
                        broker_order_id,
                        normalized_account_id,
                        normalized_account_id,
                    ],
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT deployment_id, broker_type, client_order_id, broker_order_id, symbol,
                              account_id, latest_report_id, latest_status, last_report_ts, updated_at
                       FROM deployment_broker_order_links
                       WHERE deployment_id = ? AND broker_type = ? AND broker_order_id = ?
                       ORDER BY last_report_ts DESC NULLS LAST, updated_at DESC, client_order_id ASC""",
                    [deployment_id, broker_type, broker_order_id],
                ).fetchall()
            return [
                {
                    "deployment_id": row[0],
                    "broker_type": row[1],
                    "client_order_id": row[2],
                    "broker_order_id": row[3] or "",
                    "symbol": row[4] or "",
                    "account_id": row[5] or "",
                    "latest_report_id": row[6] or "",
                    "latest_status": row[7] or "",
                    "last_report_ts": _parse_ts(row[8]),
                    "updated_at": _parse_ts(row[9]),
                }
                for row in rows
            ]

    def list_broker_order_links(
        self,
        deployment_id: str,
        *,
        broker_type: str | None = None,
    ) -> list[dict]:
        h = _get_helpers()
        _parse_ts = h["_parse_ts"]
        with self._lock:
            if broker_type:
                rows = self._conn.execute(
                    """SELECT deployment_id, broker_type, client_order_id, broker_order_id, symbol,
                              account_id, latest_report_id, latest_status, last_report_ts
                       FROM deployment_broker_order_links
                       WHERE deployment_id = ? AND broker_type = ?
                       ORDER BY updated_at ASC, client_order_id ASC""",
                    [deployment_id, broker_type],
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT deployment_id, broker_type, client_order_id, broker_order_id, symbol,
                              account_id, latest_report_id, latest_status, last_report_ts
                       FROM deployment_broker_order_links
                       WHERE deployment_id = ?
                       ORDER BY updated_at ASC, client_order_id ASC""",
                    [deployment_id],
                ).fetchall()
            return [
                {
                    "deployment_id": row[0],
                    "broker_type": row[1],
                    "client_order_id": row[2],
                    "broker_order_id": row[3] or "",
                    "symbol": row[4] or "",
                    "account_id": row[5] or "",
                    "latest_report_id": row[6] or "",
                    "latest_status": row[7] or "",
                    "last_report_ts": _parse_ts(row[8]),
                }
                for row in rows
            ]

    # -- Private upsert methods (called within store transactions) -----------

    def _upsert_broker_execution_event_link_locked(
        self,
        event: DeploymentEvent,
    ) -> None:
        payload = event.payload or {}
        broker_type = str(payload.get("broker_type", "") or "").strip()
        client_order_id = str(event.client_order_id or "").strip()
        report_id = str(payload.get("report_id", "") or "").strip()
        if not broker_type or not client_order_id or not report_id:
            return
        self._upsert_broker_order_links_locked(
            deployment_id=event.deployment_id,
            reports=[
                BrokerExecutionReport(
                    report_id=report_id,
                    broker_type=broker_type,
                    as_of=event.event_ts,
                    client_order_id=client_order_id,
                    broker_order_id=str(payload.get("broker_order_id", "") or "").strip(),
                    symbol=str(payload.get("symbol", "") or "").strip(),
                    side=str(payload.get("side", "") or "").strip(),
                    status=str(payload.get("status", "") or "").strip(),
                    filled_shares=int(payload.get("filled_shares", 0) or 0),
                    remaining_shares=int(payload.get("remaining_shares", 0) or 0),
                    avg_price=float(payload.get("avg_price", 0.0) or 0.0),
                    message=str(payload.get("message", "") or ""),
                    raw_payload=dict(payload.get("raw_payload") or {})
                    if isinstance(payload.get("raw_payload"), dict)
                    else None,
                    account_id=str(payload.get("account_id", "") or "").strip(),
                )
            ],
        )

    def _upsert_broker_submit_ack_link_locked(
        self,
        event: DeploymentEvent,
    ) -> None:
        h = _get_helpers()
        _to_utc = h["_to_utc"]
        _runtime_event_payload = h["_runtime_event_payload"]
        payload = event.payload or {}
        broker_type = str(payload.get("broker_type", "") or "").strip()
        runtime_payload = _runtime_event_payload(event)
        client_order_id = str(
            runtime_payload.get("client_order_id", "")
            or runtime_payload.get("order_remark", "")
            or ""
        ).strip()
        broker_order_id = str(
            runtime_payload.get("order_sysid", "")
            or runtime_payload.get("order_id", "")
            or ""
        ).strip()
        account_id = str(runtime_payload.get("account_id", "") or "").strip()
        if not broker_type or not client_order_id or not broker_order_id:
            return
        self._upsert_broker_order_links_locked(
            deployment_id=event.deployment_id,
            reports=[
                BrokerExecutionReport(
                    report_id=(
                        f"submit_ack:{broker_order_id}:"
                        f"{_to_utc(event.event_ts).isoformat() if _to_utc(event.event_ts) else ''}"
                    ),
                    broker_type=broker_type,
                    as_of=event.event_ts,
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    symbol=str(runtime_payload.get("stock_code", "") or "").strip(),
                    side=str(
                        runtime_payload.get("side", "")
                        or runtime_payload.get("offset_flag", "")
                        or ""
                    ).strip(),
                    status="reported",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=0.0,
                    message=str(
                        runtime_payload.get("error_msg", "")
                        or runtime_payload.get("status_msg", "")
                        or ""
                    ),
                    raw_payload=dict(runtime_payload),
                    account_id=account_id,
                )
            ],
        )

    def _has_later_cancel_failed_runtime_locked(
        self,
        *,
        deployment_id: str,
        broker_type: str,
        client_order_id: str,
        broker_order_id: str,
        not_before: datetime | None,
    ) -> bool:
        h = _get_helpers()
        _to_utc = h["_to_utc"]
        _parse_ts = h["_parse_ts"]
        rows = self._conn.execute(
            """SELECT event_ts, client_order_id, payload_json
               FROM deployment_events
               WHERE deployment_id = ? AND event_type = ?
               ORDER BY event_ts DESC, event_id DESC""",
            [deployment_id, EventType.BROKER_RUNTIME_RECORDED.value],
        ).fetchall()
        not_before_ts = _to_utc(not_before) if not_before is not None else None
        for event_ts_raw, stored_client_order_id, payload_json in rows:
            event_ts = _to_utc(_parse_ts(event_ts_raw))
            if (
                not_before_ts is not None
                and event_ts is not None
                and event_ts < not_before_ts
            ):
                break
            try:
                payload = json.loads(payload_json) if payload_json else {}
            except Exception:
                payload = {}
            if str(payload.get("broker_type", "") or "") != broker_type:
                continue
            runtime_payload = payload.get("payload")
            if not isinstance(runtime_payload, dict):
                runtime_payload = {}
            runtime_kind = str(payload.get("runtime_kind", "") or "")
            cancel_result = runtime_payload.get("cancel_result")
            error_msg = str(
                runtime_payload.get("error_msg", "")
                or runtime_payload.get("status_msg", "")
                or ""
            )
            is_cancel_failed = runtime_kind == "cancel_error" or (
                runtime_kind == "cancel_order_stock_async_response"
                and (
                    cancel_result not in {"", None, 0, "0"}
                    or bool(error_msg)
                )
            )
            if not is_cancel_failed:
                continue
            payload_client_order_id = str(
                runtime_payload.get("client_order_id", "")
                or runtime_payload.get("order_remark", "")
                or stored_client_order_id
                or ""
            ).strip()
            payload_broker_order_id = str(
                runtime_payload.get("order_sysid", "")
                or runtime_payload.get("broker_order_id", "")
                or runtime_payload.get("order_id", "")
                or ""
            ).strip()
            if client_order_id and payload_client_order_id == client_order_id:
                return True
            if broker_order_id and payload_broker_order_id == broker_order_id:
                return True
        return False

    def _upsert_broker_cancel_requested_link_locked(
        self,
        event: DeploymentEvent,
    ) -> None:
        h = _get_helpers()
        _to_utc = h["_to_utc"]
        _parse_ts = h["_parse_ts"]
        payload = event.payload or {}
        broker_type = str(payload.get("broker_type", "") or "")
        client_order_id = str(event.client_order_id or "").strip()
        broker_order_id = str(payload.get("broker_order_id", "") or "").strip()
        symbol = str(payload.get("symbol", "") or "")
        account_id = str(payload.get("account_id", "") or "").strip()
        if not broker_type or not client_order_id:
            return

        before = self._conn.execute(
            """SELECT broker_order_id, symbol, account_id, latest_report_id, latest_status, last_report_ts
               FROM deployment_broker_order_links
               WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?""",
            [event.deployment_id, broker_type, client_order_id],
        ).fetchone()
        previous_broker_order_id = str(before[0] or "") if before else ""
        previous_symbol = str(before[1] or "") if before else ""
        previous_account_id = str(before[2] or "") if before else ""
        previous_report_id = str(before[3] or "") if before else ""
        previous_status = normalize_broker_order_status(
            str(before[4] or "") if before else ""
        )
        previous_report_ts = (
            _to_utc(_parse_ts(before[5]))
            if before and before[5] is not None
            else None
        )
        if previous_status in {
            "partially_canceled",
            "filled",
            "canceled",
            "junk",
            "order_error",
        }:
            return
        if self._has_later_cancel_failed_runtime_locked(
            deployment_id=event.deployment_id,
            broker_type=broker_type,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id or previous_broker_order_id,
            not_before=event.event_ts,
        ):
            return
        if previous_status in {"partially_filled", "partially_filled_cancel_pending"}:
            target_status = "partially_filled_cancel_pending"
        elif previous_status == "reported_cancel_pending":
            target_status = "reported_cancel_pending"
        else:
            target_status = "reported_cancel_pending"
        target_rank = broker_order_status_rank(target_status)
        current_rank = broker_order_status_rank(previous_status)
        if before is not None and current_rank > target_rank:
            return
        now = datetime.now(timezone.utc)

        self._conn.execute(
            """INSERT OR REPLACE INTO deployment_broker_order_links
               (deployment_id, broker_type, client_order_id, broker_order_id, symbol, account_id,
                latest_report_id, latest_status, last_report_ts, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                    (SELECT created_at FROM deployment_broker_order_links
                     WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?),
                    ?
               ), ?)""",
            [
                event.deployment_id,
                broker_type,
                client_order_id,
                broker_order_id or previous_broker_order_id,
                symbol or previous_symbol,
                account_id or previous_account_id,
                previous_report_id,
                target_status if before is None or target_rank >= current_rank else previous_status,
                previous_report_ts,
                event.deployment_id,
                broker_type,
                client_order_id,
                now,
                now,
            ],
        )

    def _upsert_broker_cancel_ack_link_locked(
        self,
        event: DeploymentEvent,
    ) -> None:
        h = _get_helpers()
        _to_utc = h["_to_utc"]
        _parse_ts = h["_parse_ts"]
        _runtime_event_payload = h["_runtime_event_payload"]
        payload = event.payload or {}
        broker_type = str(payload.get("broker_type", "") or "")
        runtime_payload = _runtime_event_payload(event)
        client_order_id = str(
            runtime_payload.get("client_order_id", "")
            or runtime_payload.get("order_remark", "")
            or ""
        ).strip()
        broker_order_id = str(
            runtime_payload.get("order_sysid", "")
            or runtime_payload.get("broker_order_id", "")
            or runtime_payload.get("order_id", "")
            or ""
        ).strip()
        account_id = str(runtime_payload.get("account_id", "") or "").strip()
        if not broker_type:
            return

        if client_order_id:
            before = self._conn.execute(
                """SELECT client_order_id, broker_order_id, symbol, account_id, latest_report_id, latest_status, last_report_ts
                   FROM deployment_broker_order_links
                   WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?""",
                [event.deployment_id, broker_type, client_order_id],
            ).fetchone()
        elif broker_order_id:
            matches = self.list_broker_order_links_by_broker_order_id(
                event.deployment_id,
                broker_type=broker_type,
                broker_order_id=broker_order_id,
                account_id=account_id,
            )
            before = None
            if len(matches) == 1:
                match = matches[0]
                before = (
                    match.get("client_order_id", ""),
                    match.get("broker_order_id", ""),
                    match.get("symbol", ""),
                    match.get("account_id", ""),
                    match.get("latest_report_id", ""),
                    match.get("latest_status", ""),
                    match.get("last_report_ts"),
                )
        else:
            return
        if before is None:
            resolved_client_order_id = client_order_id
            if not resolved_client_order_id:
                return
            target_status = "reported_cancel_pending"
            now = datetime.now(timezone.utc)
            self._conn.execute(
                """INSERT OR REPLACE INTO deployment_broker_order_links
                   (deployment_id, broker_type, client_order_id, broker_order_id, symbol, account_id,
                    latest_report_id, latest_status, last_report_ts, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                        (SELECT created_at FROM deployment_broker_order_links
                         WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?),
                        ?
                   ), ?)""",
                [
                    event.deployment_id,
                    broker_type,
                    resolved_client_order_id,
                    broker_order_id,
                    str(runtime_payload.get("stock_code", "") or "").strip(),
                    account_id,
                    "",
                    target_status,
                    None,
                    event.deployment_id,
                    broker_type,
                    resolved_client_order_id,
                    now,
                    now,
                ],
            )
            return

        resolved_client_order_id = str(before[0] or "").strip()
        previous_broker_order_id = str(before[1] or "").strip()
        previous_symbol = str(before[2] or "").strip()
        previous_account_id = str(before[3] or "").strip()
        previous_report_id = str(before[4] or "").strip()
        previous_status = normalize_broker_order_status(str(before[5] or ""))
        previous_report_ts = (
            _to_utc(_parse_ts(before[6]))
            if before[6] is not None
            else None
        )
        if previous_status in {
            "partially_canceled",
            "filled",
            "canceled",
            "junk",
            "order_error",
        }:
            return
        if previous_status in {"partially_filled", "partially_filled_cancel_pending"}:
            target_status = "partially_filled_cancel_pending"
        else:
            target_status = "reported_cancel_pending"
        target_rank = broker_order_status_rank(target_status)
        current_rank = broker_order_status_rank(previous_status)
        if current_rank > target_rank:
            return

        now = datetime.now(timezone.utc)
        self._conn.execute(
            """INSERT OR REPLACE INTO deployment_broker_order_links
               (deployment_id, broker_type, client_order_id, broker_order_id, symbol, account_id,
                latest_report_id, latest_status, last_report_ts, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                    (SELECT created_at FROM deployment_broker_order_links
                     WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?),
                    ?
               ), ?)""",
            [
                event.deployment_id,
                broker_type,
                resolved_client_order_id,
                broker_order_id or previous_broker_order_id,
                str(runtime_payload.get("stock_code", "") or "").strip() or previous_symbol,
                account_id or previous_account_id,
                previous_report_id,
                target_status if target_rank >= current_rank else previous_status,
                previous_report_ts,
                event.deployment_id,
                broker_type,
                resolved_client_order_id,
                now,
                now,
            ],
        )

    def _upsert_broker_cancel_failed_link_locked(
        self,
        event: DeploymentEvent,
    ) -> None:
        h = _get_helpers()
        _runtime_event_payload = h["_runtime_event_payload"]
        payload = event.payload or {}
        broker_type = str(payload.get("broker_type", "") or "")
        runtime_payload = _runtime_event_payload(event)
        client_order_id = str(
            runtime_payload.get("client_order_id", "")
            or runtime_payload.get("order_remark", "")
            or ""
        ).strip()
        broker_order_id = str(
            runtime_payload.get("order_sysid", "")
            or runtime_payload.get("broker_order_id", "")
            or runtime_payload.get("order_id", "")
            or ""
        ).strip()
        account_id = str(runtime_payload.get("account_id", "") or "").strip()
        if not broker_type:
            return
        if client_order_id:
            before = self._conn.execute(
                """SELECT client_order_id, latest_status
                   FROM deployment_broker_order_links
                   WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?""",
                [event.deployment_id, broker_type, client_order_id],
            ).fetchone()
        elif broker_order_id:
            matches = self.list_broker_order_links_by_broker_order_id(
                event.deployment_id,
                broker_type=broker_type,
                broker_order_id=broker_order_id,
                account_id=account_id,
            )
            before = None
            if len(matches) == 1:
                match = matches[0]
                before = (
                    match.get("client_order_id", ""),
                    match.get("latest_status", ""),
                )
        else:
            return
        if before is None:
            return

        resolved_client_order_id = str(before[0] or "")
        previous_status = normalize_broker_order_status(str(before[1] or ""))
        if previous_status == "reported_cancel_pending":
            target_status = "reported"
        elif previous_status == "partially_filled_cancel_pending":
            target_status = "partially_filled"
        else:
            return

        self._conn.execute(
            """UPDATE deployment_broker_order_links
               SET latest_status = ?, updated_at = ?
               WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?""",
            [
                target_status,
                datetime.now(timezone.utc),
                event.deployment_id,
                broker_type,
                resolved_client_order_id,
            ],
        )

    def _upsert_broker_order_links_locked(
        self,
        *,
        deployment_id: str,
        reports: list[BrokerExecutionReport],
    ) -> int:
        h = _get_helpers()
        _to_utc = h["_to_utc"]
        _parse_ts = h["_parse_ts"]
        updated = 0
        now = datetime.now(timezone.utc)
        for report in reports:
            client_order_id = str(report.client_order_id or "")
            if not client_order_id:
                continue
            normalized_status = normalize_broker_order_status(
                report.status,
                filled_shares=report.filled_shares,
                remaining_shares=report.remaining_shares,
            )
            before = self._conn.execute(
                """SELECT broker_order_id, symbol, account_id, latest_report_id, latest_status, last_report_ts
                   FROM deployment_broker_order_links
                   WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?""",
                [deployment_id, report.broker_type, client_order_id],
            ).fetchone()
            previous_broker_order_id = str(before[0] or "") if before else ""
            previous_symbol = str(before[1] or "") if before else ""
            previous_account_id = str(before[2] or "") if before else ""
            previous_report_id = str(before[3] or "") if before else ""
            previous_status = normalize_broker_order_status(
                str(before[4] or "") if before else ""
            )
            previous_report_ts = (
                _to_utc(_parse_ts(before[5]))
                if before and before[5] is not None
                else None
            )
            incoming_report_ts = _to_utc(report.as_of)
            if previous_status == "reported_cancel_pending" and normalized_status == "partially_filled":
                normalized_status = "partially_filled_cancel_pending"
            elif previous_status == "partially_filled_cancel_pending" and normalized_status == "partially_filled":
                normalized_status = "partially_filled_cancel_pending"
            preserve_cancel_pending_metadata = (
                previous_status == "reported_cancel_pending"
                and normalized_status == "reported"
            )
            should_advance = (
                before is None
                or preserve_cancel_pending_metadata
                or broker_order_status_rank(normalized_status)
                > broker_order_status_rank(previous_status)
                or (
                    broker_order_status_rank(normalized_status)
                    == broker_order_status_rank(previous_status)
                    and (
                        previous_report_ts is None
                        or incoming_report_ts > previous_report_ts
                        or incoming_report_ts == previous_report_ts
                    )
                )
            )
            latest_report_id = report.report_id if should_advance else previous_report_id
            latest_status = (
                previous_status
                if preserve_cancel_pending_metadata and should_advance
                else normalized_status if should_advance else previous_status
            )
            last_report_ts = incoming_report_ts if should_advance else previous_report_ts
            self._conn.execute(
                """INSERT OR REPLACE INTO deployment_broker_order_links
                   (deployment_id, broker_type, client_order_id, broker_order_id, symbol, account_id,
                    latest_report_id, latest_status, last_report_ts, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                        (SELECT created_at FROM deployment_broker_order_links
                         WHERE deployment_id = ? AND broker_type = ? AND client_order_id = ?),
                        ?
                   ), ?)""",
                [
                    deployment_id,
                    report.broker_type,
                    client_order_id,
                    report.broker_order_id or previous_broker_order_id,
                    report.symbol or previous_symbol,
                    str(getattr(report, "account_id", "") or "") or previous_account_id,
                    latest_report_id,
                    latest_status,
                    last_report_ts,
                    deployment_id,
                    report.broker_type,
                    client_order_id,
                    now,
                    now,
                ],
            )
            if before is None or previous_report_id != latest_report_id:
                updated += 1
        return updated
