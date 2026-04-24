from __future__ import annotations

from datetime import date, datetime, timezone

import duckdb
import pytest

from ez.live.broker import BrokerExecutionReport
from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.events import (
    EventType,
    make_broker_cancel_requested_event,
    make_broker_execution_event,
    make_broker_runtime_event,
    make_snapshot_event,
)


def _store() -> DeploymentStore:
    return DeploymentStore(duckdb.connect(":memory:"))


def test_append_events_is_idempotent_by_event_id():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )

    inserted1 = store.append_events([event])
    inserted2 = store.append_events([event])
    events = store.get_events("dep-1")

    assert inserted1 == 1
    assert inserted2 == 0
    assert len(events) == 1
    assert events[0].event_type == EventType.SNAPSHOT_SAVED


def test_get_events_orders_by_time_then_event_id():
    store = _store()
    event1 = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=500_000.0,
        rebalanced=True,
        trade_count=1,
        event_ts=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
    )
    event2 = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 14),
        equity=1_010_000.0,
        cash=510_000.0,
        rebalanced=False,
        trade_count=0,
        event_ts=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
    )

    store.append_events([event2, event1])
    events = store.get_events("dep-1")

    assert [e.payload["snapshot_date"] for e in events] == ["2026-04-13", "2026-04-14"]


def test_get_latest_event_ts_can_filter_by_type():
    store = _store()
    event1 = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=500_000.0,
        rebalanced=True,
        trade_count=1,
        event_ts=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
    )
    event2 = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 14),
        equity=1_010_000.0,
        cash=510_000.0,
        rebalanced=False,
        trade_count=0,
        event_ts=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
    )

    store.append_events([event1, event2])

    latest = store.get_latest_event_ts("dep-1")
    latest_snapshot = store.get_latest_event_ts("dep-1", event_type=EventType.SNAPSHOT_SAVED)

    assert latest is not None
    assert latest_snapshot is not None
    assert latest_snapshot.date().isoformat() == "2026-04-14"


def test_save_execution_result_rolls_back_events_when_snapshot_write_fails():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )

    def _boom(**kwargs):
        raise RuntimeError("snapshot write failed")

    store._write_snapshot_locked = _boom

    with pytest.raises(RuntimeError, match="snapshot write failed"):
        store.save_execution_result(
            deployment_id="dep-1",
            snapshot_date=date(2026, 4, 13),
            result={
                "equity": 1_000_000.0,
                "cash": 1_000_000.0,
                "holdings": {},
                "weights": {},
                "trades": [],
                "risk_events": [],
            },
            events=[event],
        )

    assert store.get_events("dep-1") == []
    assert store.get_all_snapshots("dep-1") == []


def test_save_execution_result_upserts_broker_order_links():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    report = BrokerExecutionReport(
        report_id="qmt:1001:partially_filled:600:400:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="partially_filled",
        filled_shares=600,
        remaining_shares=400,
        avg_price=12.34,
        account_id="acct-1",
    )

    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[event],
        broker_reports=[report],
    )

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )
    assert link is not None
    assert link["broker_order_id"] == "SYS-001"
    assert link["symbol"] == "000001.SZ"
    assert link["account_id"] == "acct-1"
    assert link["latest_report_id"] == report.report_id
    assert link["latest_status"] == "partially_filled"
    reverse = store.find_broker_order_link(
        "dep-1",
        broker_type="qmt",
        broker_order_id="SYS-001",
    )
    assert reverse is not None
    assert reverse["client_order_id"] == "dep-1:2026-04-13:000001.SZ:buy"


def test_find_broker_order_link_scopes_broker_order_id_by_account_id():
    store = _store()
    shadow = BrokerExecutionReport(
        report_id="qmt:SYS-001:reported:0:1000:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-1:shadow",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="reported",
        filled_shares=0,
        remaining_shares=1000,
        avg_price=12.34,
        account_id="acct-shadow",
    )
    real = BrokerExecutionReport(
        report_id="qmt:SYS-001:reported:0:1000:2026-04-13T15:01:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
        client_order_id="dep-1:real",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="reported",
        filled_shares=0,
        remaining_shares=1000,
        avg_price=12.34,
        account_id="acct-real",
    )

    store.save_broker_sync_result(
        deployment_id="dep-1",
        events=[],
        broker_reports=[shadow, real],
    )

    shadow_link = store.find_broker_order_link(
        "dep-1",
        broker_type="qmt",
        broker_order_id="SYS-001",
        account_id="acct-shadow",
    )
    assert shadow_link is not None
    assert shadow_link["client_order_id"] == "dep-1:shadow"

    real_link = store.find_broker_order_link(
        "dep-1",
        broker_type="qmt",
        broker_order_id="SYS-001",
        account_id="acct-real",
    )
    assert real_link is not None
    assert real_link["client_order_id"] == "dep-1:real"

    shadow_matches = store.list_broker_order_links_by_broker_order_id(
        "dep-1",
        broker_type="qmt",
        broker_order_id="SYS-001",
        account_id="acct-shadow",
    )
    assert [link["client_order_id"] for link in shadow_matches] == ["dep-1:shadow"]


def test_append_execution_event_upserts_broker_order_link():
    store = _store()
    event = make_broker_execution_event(
        "dep-1",
        report_id="submit_ack:1001",
        broker_type="qmt",
        report_ts=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="1001",
        symbol="000001.SZ",
        side="buy",
        status="reported",
        filled_shares=0,
        remaining_shares=1000,
        avg_price=12.34,
        message="77",
        raw_payload={"source": "submit_ack", "broker_submit_id": "77"},
    )

    store.append_events([event])

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )
    assert link is not None
    assert link["broker_order_id"] == "1001"
    assert link["latest_report_id"] == "submit_ack:1001"
    assert link["latest_status"] == "reported"


def test_append_order_stock_async_response_upserts_submit_ack_link():
    store = _store()
    event = make_broker_runtime_event(
        "dep-1",
        runtime_event_id="order_stock_async_response|acct-1|77|1001|2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        runtime_kind="order_stock_async_response",
        event_ts=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        payload={
            "_report_kind": "order_stock_async_response",
            "account_id": "acct-1",
            "order_id": 1001,
            "seq": 77,
            "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
        },
    )

    store.append_events([event])

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )
    assert link is not None
    assert link["broker_order_id"] == "1001"
    assert link["latest_status"] == "reported"


def test_append_cancel_order_stock_async_response_creates_cancel_pending_link_when_client_order_id_is_known():
    store = _store()
    event = make_broker_runtime_event(
        "dep-1",
        runtime_event_id="cancel_order_stock_async_response|acct-1|88|SYS-001|2026-04-13T15:01:00+00:00",
        broker_type="qmt",
        runtime_kind="cancel_order_stock_async_response",
        event_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
        payload={
            "_report_kind": "cancel_order_stock_async_response",
            "account_id": "acct-1",
            "order_sysid": "SYS-001",
            "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
            "cancel_result": 0,
            "seq": 88,
        },
    )

    store.append_events([event])

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )
    assert link is not None
    assert link["broker_order_id"] == "SYS-001"
    assert link["latest_status"] == "reported_cancel_pending"


def test_late_report_backfills_metadata_for_cancel_pending_link_bootstrapped_by_ack():
    store = _store()
    ack_event = make_broker_runtime_event(
        "dep-1",
        runtime_event_id="cancel_order_stock_async_response|acct-1|88|SYS-001|2026-04-13T15:01:00+00:00",
        broker_type="qmt",
        runtime_kind="cancel_order_stock_async_response",
        event_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
        payload={
            "_report_kind": "cancel_order_stock_async_response",
            "account_id": "acct-1",
            "order_sysid": "SYS-001",
            "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
            "cancel_result": 0,
            "seq": 88,
        },
    )
    reported = BrokerExecutionReport(
        report_id="qmt:SYS-001:reported:0:1000:2026-04-13T15:01:30+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 1, 30, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="reported",
        filled_shares=0,
        remaining_shares=1000,
        avg_price=12.34,
    )

    store.append_events([ack_event])
    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[],
        broker_reports=[reported],
    )

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )

    assert link is not None
    assert link["latest_status"] == "reported_cancel_pending"
    assert link["latest_report_id"] == reported.report_id
    assert link["last_report_ts"] == reported.as_of


def test_save_execution_result_is_idempotent_for_duplicate_broker_reports():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    report = BrokerExecutionReport(
        report_id="qmt:SYS-001:filled:1000:0:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="filled",
        filled_shares=1_000,
        remaining_shares=0,
        avg_price=12.34,
    )

    for _ in range(2):
        store.save_execution_result(
            deployment_id="dep-1",
            snapshot_date=date(2026, 4, 13),
            result={
                "equity": 1_000_000.0,
                "cash": 1_000_000.0,
                "holdings": {},
                "weights": {},
                "trades": [],
                "risk_events": [],
            },
            events=[event],
            broker_reports=[report],
        )

    events = store.get_events("dep-1")
    links = store.list_broker_order_links("dep-1", broker_type="qmt")

    assert len(events) == 1
    assert len(links) == 1
    assert links[0]["broker_order_id"] == "SYS-001"
    assert links[0]["latest_report_id"] == report.report_id
    assert links[0]["latest_status"] == "filled"


def test_broker_order_links_do_not_regress_to_older_status():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    newer = BrokerExecutionReport(
        report_id="qmt:1001:canceled:600:0:2026-04-13T15:01:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="canceled",
        filled_shares=600,
        remaining_shares=0,
        avg_price=12.34,
    )
    older = BrokerExecutionReport(
        report_id="qmt:1001:partially_filled:600:400:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="partially_filled",
        filled_shares=600,
        remaining_shares=400,
        avg_price=12.34,
    )

    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[event],
        broker_reports=[newer],
    )
    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 14),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[],
        broker_reports=[older],
    )

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )
    assert link is not None
    assert link["latest_status"] == "canceled"
    assert link["latest_report_id"] == newer.report_id


def test_broker_cancel_requested_advances_link_state_without_new_columns():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    report = BrokerExecutionReport(
        report_id="qmt:SYS-001:partially_filled:600:400:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="partially_filled",
        filled_shares=600,
        remaining_shares=400,
        avg_price=12.34,
    )

    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[event],
        broker_reports=[report],
    )
    store.append_event(
        make_broker_cancel_requested_event(
            deployment_id="dep-1",
            broker_type="qmt",
            request_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
            client_order_id="dep-1:2026-04-13:000001.SZ:buy",
            broker_order_id="SYS-001",
            symbol="000001.SZ",
        )
    )

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )

    assert link is not None
    assert link["latest_status"] == "partially_filled_cancel_pending"
    assert link["latest_report_id"] == report.report_id


def test_broker_cancel_failure_runtime_reopens_pending_link():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    report = BrokerExecutionReport(
        report_id="qmt:SYS-001:partially_filled:600:400:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="partially_filled",
        filled_shares=600,
        remaining_shares=400,
        avg_price=12.34,
    )

    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[event],
        broker_reports=[report],
    )
    store.append_event(
        make_broker_cancel_requested_event(
            deployment_id="dep-1",
            broker_type="qmt",
            request_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
            client_order_id="dep-1:2026-04-13:000001.SZ:buy",
            broker_order_id="SYS-001",
            symbol="000001.SZ",
        )
    )
    store.append_event(
        make_broker_runtime_event(
            "dep-1",
            runtime_event_id="cancel_error:SYS-001",
            broker_type="qmt",
            runtime_kind="cancel_error",
            event_ts=datetime(2026, 4, 13, 15, 2, tzinfo=timezone.utc),
            payload={
                "client_order_id": "dep-1:2026-04-13:000001.SZ:buy",
                "order_sysid": "SYS-001",
                "status_msg": "cancel rejected",
            },
        )
    )

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )

    assert link is not None
    assert link["latest_status"] == "partially_filled"
    assert link["latest_report_id"] == report.report_id


def test_later_cancel_requested_does_not_reopen_pending_after_newer_cancel_error():
    store = _store()
    store.append_event(
        make_broker_runtime_event(
            "dep-1",
            runtime_event_id="cancel_error:SYS-001",
            broker_type="qmt",
            runtime_kind="cancel_error",
            event_ts=datetime(2026, 4, 13, 15, 2, tzinfo=timezone.utc),
            payload={
                "client_order_id": "dep-1:2026-04-13:000001.SZ:buy",
                "order_sysid": "SYS-001",
                "status_msg": "cancel rejected",
            },
        )
    )
    store.append_event(
        make_broker_cancel_requested_event(
            deployment_id="dep-1",
            broker_type="qmt",
            request_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
            client_order_id="dep-1:2026-04-13:000001.SZ:buy",
            broker_order_id="SYS-001",
            symbol="000001.SZ",
        )
    )

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )

    assert link is None


def test_terminal_canceled_report_advances_cancel_pending_link_even_with_same_timestamp():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    as_of = datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc)
    partial = BrokerExecutionReport(
        report_id="qmt:SYS-001:partially_filled:600:400:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=as_of,
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="partially_filled",
        filled_shares=600,
        remaining_shares=400,
        avg_price=12.34,
    )
    canceled = BrokerExecutionReport(
        report_id="qmt:SYS-001:canceled:600:0:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=as_of,
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="canceled",
        filled_shares=600,
        remaining_shares=0,
        avg_price=12.34,
    )

    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[event],
        broker_reports=[partial],
    )
    store.append_event(
        make_broker_cancel_requested_event(
            deployment_id="dep-1",
            broker_type="qmt",
            request_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
            client_order_id="dep-1:2026-04-13:000001.SZ:buy",
            broker_order_id="SYS-001",
            symbol="000001.SZ",
        )
    )
    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[],
        broker_reports=[canceled],
    )

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )

    assert link is not None
    assert link["latest_status"] == "canceled"
    assert link["latest_report_id"] == canceled.report_id


def test_broker_order_links_prefer_later_same_timestamp_report_order():
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-1",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    as_of = datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc)
    first = BrokerExecutionReport(
        report_id="z-report",
        broker_type="qmt",
        as_of=as_of,
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="LEGACY-001",
        symbol="000001.SZ",
        side="buy",
        status="partially_filled",
        filled_shares=500,
        remaining_shares=500,
        avg_price=12.34,
    )
    second = BrokerExecutionReport(
        report_id="a-report",
        broker_type="qmt",
        as_of=as_of,
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="partially_filled",
        filled_shares=500,
        remaining_shares=500,
        avg_price=12.34,
    )

    store.save_execution_result(
        deployment_id="dep-1",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[event],
        broker_reports=[first, second],
    )

    link = store.get_broker_order_link(
        "dep-1",
        broker_type="qmt",
        client_order_id="dep-1:2026-04-13:000001.SZ:buy",
    )

    assert link is not None
    assert link["broker_order_id"] == "SYS-001"
    assert link["latest_report_id"] == "a-report"
    assert link["latest_status"] == "partially_filled"
    assert link["last_report_ts"] == as_of


def test_save_daily_snapshot_persists_liquidation_flag():
    store = _store()

    store.save_daily_snapshot(
        "dep-1",
        date(2026, 4, 13),
        {
            "equity": 998_000.0,
            "cash": 998_000.0,
            "holdings": {},
            "weights": {},
            "trades": [{"symbol": "000001.SZ", "side": "sell", "shares": 100}],
            "risk_events": [],
            "rebalanced": False,
            "liquidation": True,
        },
    )

    latest = store.get_latest_snapshot("dep-1")
    snapshots = store.get_all_snapshots("dep-1")

    assert latest["liquidation"] is True
    assert snapshots[0]["liquidation"] is True


def test_update_gate_verdict_persists_to_record():
    store = _store()
    spec = DeploymentSpec(
        strategy_name="TopNRotation",
        strategy_params={"factor": "momentum_rank_20", "top_n": 5},
        symbols=("000001.SZ",),
        market="cn_stock",
        freq="weekly",
        shadow_broker_type="qmt",
        risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
    )
    record = DeploymentRecord(
        spec_id=spec.spec_id,
        name="test-deployment",
        status="running",
    )

    store.save_spec(spec)
    store.save_record(record)
    verdict_json = '{"passed": false, "reason": "risk_blocked"}'

    store.update_gate_verdict(record.deployment_id, verdict_json)

    updated = store.get_record(record.deployment_id)

    assert updated is not None
    assert updated.gate_verdict == verdict_json


def test_append_events_stays_unique_under_repeated_inserts():
    """event_id uniqueness is enforced at schema level — 10 inserts -> 1 row.

    This covers both same-session duplicate callback re-delivery and cross-
    process replay idempotency. The deployment_events table has event_id as
    PRIMARY KEY and _append_events_locked uses INSERT OR IGNORE.
    """
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-unique",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )

    inserted_counts = [store.append_events([event]) for _ in range(10)]

    assert inserted_counts[0] == 1
    assert inserted_counts[1:] == [0] * 9
    assert len(store.get_events("dep-unique")) == 1


def test_save_snapshot_with_events_is_atomic():
    """save_snapshot_with_events commits events + snapshot in a single txn.

    On success: both visible. On snapshot write failure: events rolled back.
    """
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-atomic",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )

    # Success path
    store.save_snapshot_with_events(
        "dep-atomic",
        date(2026, 4, 13),
        {
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        [event],
    )
    assert len(store.get_events("dep-atomic")) == 1
    assert len(store.get_all_snapshots("dep-atomic")) == 1

    # Failure path — snapshot write fails, events rolled back
    failing_event = make_snapshot_event(
        deployment_id="dep-atomic-2",
        business_date=date(2026, 4, 14),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )

    def _boom(**kwargs):
        raise RuntimeError("snapshot failed mid-transaction")

    store._write_snapshot_locked = _boom

    with pytest.raises(RuntimeError, match="snapshot failed mid-transaction"):
        store.save_snapshot_with_events(
            "dep-atomic-2",
            date(2026, 4, 14),
            {
                "equity": 0.0,
                "cash": 0.0,
                "holdings": {},
                "weights": {},
                "trades": [],
                "risk_events": [],
            },
            [failing_event],
        )

    assert store.get_events("dep-atomic-2") == []
    assert store.get_all_snapshots("dep-atomic-2") == []


def test_save_snapshot_with_events_rolls_back_broker_order_links():
    """If the snapshot write fails, the broker-order link upsert must also
    be rolled back — otherwise a phantom link could survive without a
    matching event/snapshot pair.
    """
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-rollback",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    report = BrokerExecutionReport(
        report_id="qmt:SYS-001:filled:1000:0:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-rollback:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-001",
        symbol="000001.SZ",
        side="buy",
        status="filled",
        filled_shares=1_000,
        remaining_shares=0,
        avg_price=12.34,
    )

    def _boom(**kwargs):
        raise RuntimeError("snapshot failed with broker link pending")

    store._write_snapshot_locked = _boom

    with pytest.raises(RuntimeError, match="snapshot failed with broker link pending"):
        store.save_snapshot_with_events(
            "dep-rollback",
            date(2026, 4, 13),
            {
                "equity": 1_000_000.0,
                "cash": 1_000_000.0,
                "holdings": {},
                "weights": {},
                "trades": [],
                "risk_events": [],
            },
            [event],
            [report],
        )

    assert store.get_events("dep-rollback") == []
    assert store.get_all_snapshots("dep-rollback") == []
    assert store.list_broker_order_links("dep-rollback", broker_type="qmt") == []


def test_save_execution_result_delegates_to_save_snapshot_with_events():
    """Backward-compat wrapper preserves atomicity: the old API still writes
    events + snapshot + broker links in a single transaction.
    """
    store = _store()
    event = make_snapshot_event(
        deployment_id="dep-compat",
        business_date=date(2026, 4, 13),
        equity=1_000_000.0,
        cash=1_000_000.0,
        rebalanced=False,
        trade_count=0,
    )
    report = BrokerExecutionReport(
        report_id="qmt:SYS-010:filled:500:0:2026-04-13T15:00:00+00:00",
        broker_type="qmt",
        as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
        client_order_id="dep-compat:2026-04-13:000001.SZ:buy",
        broker_order_id="SYS-010",
        symbol="000001.SZ",
        side="buy",
        status="filled",
        filled_shares=500,
        remaining_shares=0,
        avg_price=12.0,
    )

    store.save_execution_result(
        deployment_id="dep-compat",
        snapshot_date=date(2026, 4, 13),
        result={
            "equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "holdings": {},
            "weights": {},
            "trades": [],
            "risk_events": [],
        },
        events=[event],
        broker_reports=[report],
    )

    assert len(store.get_events("dep-compat")) == 1
    assert len(store.get_all_snapshots("dep-compat")) == 1
    link = store.get_broker_order_link(
        "dep-compat",
        broker_type="qmt",
        client_order_id="dep-compat:2026-04-13:000001.SZ:buy",
    )
    assert link is not None
    assert link["latest_status"] == "filled"
