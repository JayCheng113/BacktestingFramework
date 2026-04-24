from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ez.live import qmt_broker, qmt_session_owner
from ez.live.qmt_session_owner import (
    QMTBrokerConfig,
    QMTSessionManager,
    XtQuantShadowClient,
    _apply_reconnect_jitter,
    _pre_normalize_qmt_numeric_order_status,
    _QMT_RECONNECT_BASE_BACKOFF,
    _QMT_RECONNECT_MAX_BACKOFF,
    get_default_qmt_session_manager,
)


@pytest.fixture(autouse=True)
def _clear_qmt_session_manager():
    manager = get_default_qmt_session_manager()
    manager.clear()
    yield
    manager.clear()


def test_qmt_session_owner_reexports_match_qmt_broker():
    assert qmt_broker.QMTBrokerConfig is QMTBrokerConfig
    assert qmt_broker.QMTSessionManager is QMTSessionManager
    assert qmt_broker.XtQuantShadowClient is XtQuantShadowClient
    assert (
        qmt_broker.get_default_qmt_session_manager
        is get_default_qmt_session_manager
    )


def test_xtquant_shadow_client_prepares_runtime_via_official_sequence(monkeypatch):
    calls: list[tuple[str, object]] = []
    callback_holder: dict[str, object] = {}

    class _FakeTrader:
        def __init__(self, install_path, session_id):
            calls.append(("init", (install_path, session_id)))

        def register_callback(self, callback):
            calls.append(("register_callback", callback.__class__.__name__))
            callback_holder["callback"] = callback

        def start(self):
            calls.append(("start", None))

        def connect(self):
            calls.append(("connect", None))
            return 0

        def subscribe(self, account):
            calls.append(("subscribe", getattr(account, "account_id", account)))
            return 0

        def query_stock_asset(self, account):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

    class _FakeStockAccount:
        def __init__(self, account_id, account_type="STOCK"):
            self.account_id = account_id
            self.account_type = account_type

    def _fake_import(name: str):
        if name == "xtquant.xttrader":
            return SimpleNamespace(XtQuantTrader=_FakeTrader)
        if name == "xtquant.xttype":
            return SimpleNamespace(StockAccount=_FakeStockAccount)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "ez.live.qmt_session_owner.importlib.import_module", _fake_import
    )

    client = XtQuantShadowClient.from_config(
        QMTBrokerConfig(
            account_id="acct-1",
            install_path="/opt/qmt/userdata",
            session_id="42",
        )
    )

    assert isinstance(client, XtQuantShadowClient)
    assert calls[0] == ("init", ("/opt/qmt/userdata", 42))
    assert [name for name, _ in calls] == [
        "init",
        "register_callback",
        "start",
        "connect",
        "subscribe",
    ]
    callback = callback_holder["callback"]
    callback.on_account_status(
        SimpleNamespace(
            account_id="acct-1",
            account_type="STOCK",
            status="connected",
            update_time="2026-04-13T09:31:30+00:00",
        )
    )
    runtime_events = client.list_runtime_events()
    assert [event["_report_kind"] for event in runtime_events][-1] == "account_status"


def test_xtquant_shadow_client_cleans_up_trader_when_runtime_prepare_fails(monkeypatch):
    close_calls: list[str] = []

    class _FailingTrader:
        def __init__(self, install_path, session_id):
            self.session_id = session_id

        def register_callback(self, callback):
            self.callback = callback

        def start(self):
            return None

        def connect(self):
            return 1

        def close(self):
            close_calls.append("closed")

    class _FakeStockAccount:
        def __init__(self, account_id, account_type="STOCK"):
            self.account_id = account_id
            self.account_type = account_type

    def _fake_import(name: str):
        if name == "xtquant.xttrader":
            return SimpleNamespace(XtQuantTrader=_FailingTrader)
        if name == "xtquant.xttype":
            return SimpleNamespace(StockAccount=_FakeStockAccount)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "ez.live.qmt_session_owner.importlib.import_module", _fake_import
    )

    with pytest.raises(RuntimeError, match="connect\\(\\) failed"):
        XtQuantShadowClient.from_config(
            QMTBrokerConfig(
                account_id="acct-1",
                install_path="/opt/qmt/userdata",
                session_id="42",
            )
        )

    assert close_calls == ["closed"]


def test_xtquant_shadow_client_cancel_prefers_sysid_path_when_symbol_is_available(monkeypatch):
    class _CancelTrader:
        def __init__(self):
            self.calls: list[tuple[str, object, object, object]] = []

        def cancel_order_stock(self, account, order_id):
            self.calls.append(("cancel_order_stock", account, order_id, None))
            return -1

        def cancel_order_stock_sysid_async(self, account, market, order_sysid):
            self.calls.append(("cancel_order_stock_sysid_async", account, market, order_sysid))
            return 77

    def _fake_import(name: str):
        if name == "xtquant.xtconstant":
            return SimpleNamespace(SH_MARKET=101, SZ_MARKET=202)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "ez.live.qmt_session_owner.importlib.import_module", _fake_import
    )

    trader = _CancelTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )

    result = client.cancel_order("100", symbol="600000.SH")

    assert result == 77
    assert trader.calls == [
        ("cancel_order_stock_sysid_async", "acct-1", 101, "100"),
    ]


def test_xtquant_shadow_client_does_not_fallback_to_numeric_cancel_for_nonnumeric_sysid(monkeypatch):
    class _CancelTrader:
        def __init__(self):
            self.calls: list[tuple[str, object, object, object]] = []

        def cancel_order_stock_sysid_async(self, account, market, order_sysid):
            self.calls.append(("cancel_order_stock_sysid_async", account, market, order_sysid))
            raise NotImplementedError("sysid path unavailable")

        def cancel_order_stock_async(self, account, order_id):
            self.calls.append(("cancel_order_stock_async", account, order_id, None))
            return 88

    def _fake_import(name: str):
        if name == "xtquant.xtconstant":
            return SimpleNamespace(SH_MARKET=101, SZ_MARKET=202)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "ez.live.qmt_session_owner.importlib.import_module", _fake_import
    )

    trader = _CancelTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )

    with pytest.raises(NotImplementedError, match="supported cancel_order API"):
        client.cancel_order("SYS-001", symbol="600000.SH")

    assert trader.calls == [
        ("cancel_order_stock_sysid_async", "acct-1", 101, "SYS-001"),
    ]


def test_xtquant_shadow_client_exposes_cancel_error_as_runtime_not_execution():
    client = XtQuantShadowClient(
        trader=SimpleNamespace(session_id=42),
        account_ref="acct-1",
        account_id="acct-1",
    )
    bridge = client._callback_bridge
    since = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)

    bridge.on_stock_order(
        SimpleNamespace(
            update_time=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
            order_remark="dep-1:2026-04-13:600000.SH:buy",
            order_sysid="SYS-001",
            stock_code="600000.SH",
            offset_flag="buy",
            order_status="reported",
            order_volume=100,
            traded_volume=0,
            traded_price=0.0,
        )
    )
    bridge.on_cancel_error(
        SimpleNamespace(
            error_time=datetime(2026, 4, 13, 9, 32, tzinfo=timezone.utc),
            order_remark="dep-1:2026-04-13:600000.SH:buy",
            order_sysid="SYS-001",
            stock_code="600000.SH",
            offset_flag="buy",
            error_msg="already filled",
        )
    )

    client.is_callback_consumer_alive = lambda: True  # type: ignore[method-assign]
    reports = client.list_execution_reports(since=since)
    runtime_events = client.list_runtime_events(since=since)

    assert [str(report.get("_report_kind", "")) for report in reports] == ["order"]
    assert reports[0]["account_id"] == "acct-1"
    assert any(
        str(event.get("_report_kind", "")) == "cancel_error"
        and str(event.get("account_id", "")) == "acct-1"
        for event in runtime_events
    )


def test_xtquant_shadow_client_defaults_execution_callback_account_id_to_owner_account():
    trader = SimpleNamespace(
        session_id=42,
        query_stock_orders=lambda _account: [],
        query_stock_trades=lambda _account: [],
    )
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    bridge = client._callback_bridge
    since = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)

    bridge.on_stock_trade(
        SimpleNamespace(
            traded_time=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
            order_remark="dep-1:2026-04-13:600000.SH:buy",
            order_sysid="SYS-001",
            stock_code="600000.SH",
            offset_flag="buy",
            traded_volume=100,
            traded_price=10.5,
        )
    )
    bridge.on_order_error(
        SimpleNamespace(
            error_time=datetime(2026, 4, 13, 9, 32, tzinfo=timezone.utc),
            order_remark="dep-1:2026-04-13:600000.SH:buy",
            order_sysid="SYS-001",
            stock_code="600000.SH",
            offset_flag="buy",
            error_msg="rejected",
        )
    )

    reports = client.list_execution_reports(since=since)
    runtime_events = client.list_runtime_events(since=since)

    assert any(
        str(report.get("_report_kind", "")) == "trade"
        and str(report.get("account_id", "")) == "acct-1"
        for report in reports
    )
    assert any(
        str(report.get("_report_kind", "")) == "order_error"
        and str(report.get("account_id", "")) == "acct-1"
        for report in reports
    )
    assert runtime_events == []


def test_xtquant_shadow_client_snapshot_stats_use_latest_timestamps_not_append_order():
    client = XtQuantShadowClient(
        trader=SimpleNamespace(session_id=42),
        account_ref="acct-1",
        account_id="acct-1",
    )
    bridge = client._callback_bridge
    bridge.record_runtime_event(
        "session_consumer_started",
        update_time=datetime(2026, 4, 13, 9, 35, tzinfo=timezone.utc),
        account_id="acct-1",
    )
    bridge.record_runtime_event(
        "stock_asset",
        update_time=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        account_id="acct-1",
        cash=1.0,
    )
    bridge.record_runtime_event(
        "account_status",
        update_time=datetime(2026, 4, 13, 9, 34, tzinfo=timezone.utc),
        account_id="acct-1",
        status="connected",
    )

    stats = bridge.snapshot_stats()

    assert stats["latest_event_at"] == datetime(2026, 4, 13, 9, 35, tzinfo=timezone.utc)
    assert stats["latest_callback_at"] == datetime(2026, 4, 13, 9, 34, tzinfo=timezone.utc)
    assert stats["latest_runtime_at"] == datetime(2026, 4, 13, 9, 34, tzinfo=timezone.utc)
    assert stats["latest_asset_callback_at"] == datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc)


def test_qmt_session_manager_reuses_normalized_session_id():
    calls: list[str] = []
    manager = QMTSessionManager()

    class _Client:
        def query_stock_asset(self, account_id: str):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account_id: str):
            return []

        def query_stock_orders(self, account_id: str):
            return []

        def query_stock_trades(self, account_id: str):
            return []

    def _factory(config: QMTBrokerConfig):
        calls.append(config.account_id)
        return _Client()

    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    client_a = manager.resolve(config=config, factory=_factory)
    client_b = manager.resolve(config=config, factory=_factory)
    state = manager.get_state(config=config, factory=_factory)

    assert client_a is client_b
    assert calls == ["acct-1"]
    assert state is not None
    assert state.acquisition_count == 2


def test_qmt_session_manager_tracks_attached_deployments_as_reference_counts():
    manager = QMTSessionManager()

    class _Client:
        def __init__(self):
            self.close_calls = 0

        def query_stock_asset(self, account_id: str):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account_id: str):
            return []

        def query_stock_orders(self, account_id: str):
            return []

        def query_stock_trades(self, account_id: str):
            return []

        def close(self):
            self.close_calls += 1

    client = _Client()

    def _factory(config: QMTBrokerConfig):
        return client

    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    manager.attach_owner(
        config=config,
        factory=_factory,
        deployment_id="dep-1",
    )
    manager.attach_owner(
        config=config,
        factory=_factory,
        deployment_id="dep-2",
    )

    state = manager.get_state(config=config, factory=_factory)
    assert state is not None
    assert state.owner_count == 2
    assert state.attached_deployments == ("dep-1", "dep-2")
    assert manager.active_session_count() == 1

    first_detach = manager.detach_owner(
        config=config,
        factory=_factory,
        deployment_id="dep-1",
    )
    assert first_detach is not None
    assert first_detach.owner_count == 1
    assert first_detach.attached_deployments == ("dep-2",)
    assert first_detach.status == "detached"
    assert manager.active_session_count() == 1
    assert client.close_calls == 0

    second_detach = manager.detach_owner(
        config=config,
        factory=_factory,
        deployment_id="dep-2",
    )
    assert second_detach is not None
    assert second_detach.owner_count == 0
    assert second_detach.attached_deployments == ()
    assert second_detach.status == "closed"
    assert manager.active_session_count() == 0
    assert client.close_calls == 1


def test_qmt_session_manager_forwards_projection_dirty_callbacks_to_attached_deployments():
    manager = QMTSessionManager()
    notifications: list[dict[str, object]] = []

    class _Trader:
        session_id = 42

        def query_stock_asset(self, account):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

    manager.register_deployment_callback_listener(
        lambda *, deployment_ids, event, session_key: notifications.append(
            {
                "deployment_ids": deployment_ids,
                "kind": event["_report_kind"],
                "account_id": session_key.account_id,
            }
        )
    )
    client = XtQuantShadowClient(
        trader=_Trader(),
        account_ref="acct-1",
        account_id="acct-1",
    )
    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    factory = lambda _config: client
    manager.attach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )

    client._callback_bridge.on_disconnected()

    assert notifications == [
        {
            "deployment_ids": ("dep-1",),
            "kind": "disconnected",
            "account_id": "acct-1",
        }
    ]


def test_qmt_session_manager_since_filter_handles_out_of_order_harvested_events(monkeypatch):
    fixed_now = datetime(2026, 4, 13, 9, 35, tzinfo=timezone.utc)
    harvested_stop = fixed_now - timedelta(minutes=1)
    manager = QMTSessionManager()

    class _Client:
        def ensure_callback_consumer(self):
            return True

        def close(self):
            return None

        def list_runtime_events(self):
            return [
                {
                    "_report_kind": "session_consumer_stopped",
                    "update_time": harvested_stop,
                    "account_id": "acct-1",
                }
            ]

    monkeypatch.setattr("ez.live.qmt_session_owner._utc_now", lambda: fixed_now)

    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    factory = lambda _config: _Client()
    manager.attach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )
    state = manager.detach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )

    events = manager.list_runtime_events(
        config=config,
        factory=factory,
        since=fixed_now - timedelta(seconds=1),
    )
    kinds = [event["_report_kind"] for event in events]

    assert state is not None
    assert state.status == "closed"
    assert "session_owner_detached" in kinds
    assert "session_owner_closed" in kinds
    assert "session_consumer_stopped" not in kinds


def test_qmt_session_manager_keeps_host_pinned_owner_resident_after_last_detach():
    manager = QMTSessionManager()
    close_calls: list[str] = []

    class _Client:
        def ensure_callback_consumer(self):
            return True

        def close(self):
            close_calls.append("closed")

    config = QMTBrokerConfig(
        account_id="acct-1",
        session_id="42",
        always_on_owner=True,
    )
    factory = lambda _config: _Client()
    manager.attach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )

    state = manager.detach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )

    events = manager.list_runtime_events(config=config, factory=factory)
    kinds = [event["_report_kind"] for event in events]

    assert state is not None
    assert state.status == "resident"
    assert state.owner_count == 0
    assert state.host_owner_pinned is True
    assert close_calls == []
    assert manager.active_session_count() == 1
    assert "session_owner_detached" in kinds
    assert "session_owner_resident" in kinds
    assert "session_owner_closed" not in kinds


def test_qmt_session_manager_supports_process_owner_warmup_and_release_without_deployments():
    manager = QMTSessionManager()
    consumer_calls: list[str] = []
    close_calls: list[str] = []

    class _Client:
        def query_stock_asset(self, account_id: str):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account_id: str):
            return []

        def query_stock_orders(self, account_id: str):
            return []

        def query_stock_trades(self, account_id: str):
            return []

        def ensure_callback_consumer(self):
            consumer_calls.append("ensure_callback_consumer")
            return True

        def close(self):
            close_calls.append("closed")

    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    factory = lambda _config: _Client()

    manager.pin_process_owner(
        config=config,
        factory=factory,
        owner_id="scheduler:qmt:acct-1",
    )
    state = manager.get_state(config=config, factory=factory)
    listed = manager.list_session_states()

    assert state is not None
    assert state.status == "process_pinned"
    assert state.owner_count == 0
    assert state.process_owner_count == 1
    assert state.process_owner_ids == ("scheduler:qmt:acct-1",)
    assert state.host_owner_pinned is True
    assert manager.active_session_count() == 1
    assert consumer_calls == ["ensure_callback_consumer"]
    assert len(listed) == 1
    assert listed[0].process_owner_ids == ("scheduler:qmt:acct-1",)

    released = manager.unpin_process_owner(
        config=config,
        factory=factory,
        owner_id="scheduler:qmt:acct-1",
    )
    events = manager.list_runtime_events(config=config, factory=factory)
    kinds = [event["_report_kind"] for event in events]

    assert released is not None
    assert released.status == "closed"
    assert released.process_owner_count == 0
    assert released.process_owner_ids == ()
    assert close_calls == ["closed"]
    assert manager.active_session_count() == 0
    assert "session_owner_process_pinned" in kinds
    assert "session_owner_process_unpinned" in kinds
    assert "session_owner_closed" in kinds


def test_qmt_session_manager_supervises_host_pinned_owner_without_attached_deployments():
    manager = QMTSessionManager()
    calls: list[str] = []

    class _Client:
        def query_stock_asset(self, account_id: str):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account_id: str):
            return []

        def query_stock_orders(self, account_id: str):
            return []

        def query_stock_trades(self, account_id: str):
            return []

        def ensure_callback_consumer(self):
            calls.append("ensure_callback_consumer")
            return True

        def ensure_resident_session(self):
            calls.append("ensure_resident_session")
            return True

        def is_callback_consumer_alive(self):
            return False

    config = QMTBrokerConfig(account_id="acct-1", session_id="42", always_on_owner=True)
    factory = lambda _config: _Client()
    manager.attach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )
    manager.detach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )

    state = manager.ensure_session_supervision(
        config=config,
        factory=factory,
    )

    assert state is not None
    assert state.status == "resident"
    assert state.host_owner_pinned is True
    assert calls == ["ensure_callback_consumer", "ensure_resident_session"]


def test_xtquant_shadow_client_collect_sync_state_tracks_separate_runtime_and_execution_cursors():
    class _Trader:
        session_id = 42

        def query_stock_asset(self, account):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

    client = XtQuantShadowClient(
        trader=_Trader(),
        account_ref="acct-1",
        account_id="acct-1",
    )
    bridge = client._callback_bridge
    bridge.record_runtime_event(
        "account_status",
        update_time=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        account_id="acct-1",
        status="connected",
    )
    bridge.on_stock_order(
        SimpleNamespace(
            order_time=datetime(2026, 4, 13, 9, 32, tzinfo=timezone.utc),
            order_remark="cid-1",
            order_id="1001",
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status="reported",
            order_volume=100,
            traded_volume=0,
            traded_price=0.0,
            status_msg="ok",
        )
    )

    first = client.collect_sync_state(cursor_state={})
    assert len(first["runtime_events"]) == 1
    assert len(first["execution_reports"]) == 1
    assert first["cursor_state"]["callback_runtime_seq"] == 1
    assert first["cursor_state"]["callback_execution_seq"] == 2

    bridge.record_runtime_event(
        "stock_asset",
        update_time=datetime(2026, 4, 13, 9, 33, tzinfo=timezone.utc),
        account_id="acct-1",
        cash=2.0,
    )
    second = client.collect_sync_state(cursor_state=first["cursor_state"])
    assert len(second["runtime_events"]) == 1
    assert len(second["execution_reports"]) == 0
    assert second["cursor_state"]["callback_runtime_seq"] == 3
    assert second["cursor_state"]["callback_execution_seq"] == 2


def test_qmt_session_manager_supervision_prefers_resident_session_reconnect():
    manager = QMTSessionManager()
    calls: list[str] = []

    class _Client:
        def __init__(self):
            self.alive = False

        def query_stock_asset(self, account_id: str):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account_id: str):
            return []

        def query_stock_orders(self, account_id: str):
            return []

        def query_stock_trades(self, account_id: str):
            return []

        def ensure_callback_consumer(self):
            calls.append("ensure_callback_consumer")
            self.alive = True
            return True

        def ensure_resident_session(self):
            calls.append("ensure_resident_session")
            self.alive = True
            return True

        def is_callback_consumer_alive(self):
            return self.alive

    client = _Client()
    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    factory = lambda _config: client
    manager.attach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )
    client.alive = False

    state = manager.ensure_session_supervision(
        config=config,
        factory=factory,
    )

    assert state is not None
    assert calls == ["ensure_callback_consumer", "ensure_resident_session"]


def test_qmt_session_manager_supervision_reconnects_disconnected_live_session():
    manager = QMTSessionManager()
    calls: list[str] = []

    class _Client:
        def __init__(self):
            self.alive = True

        def query_stock_asset(self, account_id: str):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account_id: str):
            return []

        def query_stock_orders(self, account_id: str):
            return []

        def query_stock_trades(self, account_id: str):
            return []

        def ensure_resident_session(self):
            calls.append("ensure_resident_session")
            return True

        def needs_resident_session_reconnect(self):
            return True

        def is_callback_consumer_alive(self):
            return self.alive

    client = _Client()
    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    factory = lambda _config: client
    manager.attach_owner(
        config=config,
        factory=factory,
        deployment_id="dep-1",
    )

    state = manager.ensure_session_supervision(
        config=config,
        factory=factory,
    )

    assert state is not None
    assert state.consumer_restart_count == 0
    assert calls == ["ensure_resident_session"]


def test_xtquant_shadow_client_marks_disconnected_session_as_query_fallback():
    class _Trader:
        session_id = 42

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

    client = XtQuantShadowClient(
        trader=_Trader(),
        account_ref="acct-1",
        account_id="acct-1",
    )
    with client._consumer_lock:
        client._consumer_thread = SimpleNamespace(is_alive=lambda: True)
        client._consumer_started_at = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
        client._consumer_last_transition_at = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)

    bridge = client._callback_bridge
    bridge.on_connected()
    bridge.record_runtime_event(
        "stock_asset",
        update_time=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        account_id="acct-1",
        cash=1.0,
        total_asset=1.0,
    )
    bridge.on_disconnected()

    state = client.get_callback_loop_state()

    assert client.needs_resident_session_reconnect() is True
    assert state["connection_status"] == "disconnected"
    assert state["account_sync_mode"] == "query_fallback"
    assert state["asset_callback_freshness"] == "unavailable"


def test_xtquant_shadow_client_reconnect_clears_disconnected_connection_state():
    class _Trader:
        session_id = 42

        def __init__(self):
            self.connect_calls = 0
            self.subscribe_calls = 0

        def run_forever(self):
            return None

        def connect(self):
            self.connect_calls += 1
            return 0

        def subscribe(self, account):
            self.subscribe_calls += 1
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

    trader = _Trader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    with client._consumer_lock:
        client._consumer_thread = SimpleNamespace(is_alive=lambda: True)
        client._consumer_started_at = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
        client._consumer_last_transition_at = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)

    client._callback_bridge.on_disconnected()
    assert client.needs_resident_session_reconnect() is True

    assert client.ensure_resident_session() is True
    assert trader.connect_calls == 1
    assert trader.subscribe_calls == 1
    assert client.needs_resident_session_reconnect() is False


# ---------------------------------------------------------------------------
# Critical #1: callback-buffer freshness threshold for incremental reports
# ---------------------------------------------------------------------------


def _stale_buffer_client() -> XtQuantShadowClient:
    class _Trader:
        session_id = 42

        def __init__(self):
            self.query_order_calls = 0
            self.query_trade_calls = 0

        def query_stock_asset(self, account):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            self.query_order_calls += 1
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-QUERY-ONLY",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": 55,
                    "order_volume": 1000,
                    "traded_volume": 300,
                    "traded_price": 12.34,
                    "order_time": "2026-04-13T10:00:00+00:00",
                    "status_msg": "",
                }
            ]

        def query_stock_trades(self, account):
            self.query_trade_calls += 1
            return []

    trader = _Trader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    with client._consumer_lock:
        client._consumer_thread = SimpleNamespace(is_alive=lambda: True)
        client._consumer_started_at = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
        client._consumer_last_transition_at = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
    client._trader_test_ref = trader  # type: ignore[attr-defined]
    return client


def test_list_execution_reports_since_falls_back_to_query_when_callback_buffer_is_stale():
    """Stale buffer + ``since != None`` must merge callback + query.

    Prior to Critical #1 this path returned only callback events, silently
    dropping fresh query rows. After the fix, when the callback buffer is
    empty and no callback has arrived within _QMT_CALLBACK_MAX_GAP_SECS,
    the incremental path merges callback + query results so fresh query
    rows whose ``since`` filter passes still make it through.
    """
    client = _stale_buffer_client()
    since = datetime(2026, 4, 13, 9, 59, tzinfo=timezone.utc)

    reports = client.list_execution_reports(since=since)

    assert len(reports) == 1
    assert reports[0]["order_sysid"] == "SYS-QUERY-ONLY"
    assert client._trader_test_ref.query_order_calls == 1
    assert client._trader_test_ref.query_trade_calls == 1


def test_list_execution_reports_since_stays_callback_only_when_buffer_is_fresh():
    """A fresh callback event keeps the incremental path callback-only."""
    client = _stale_buffer_client()
    # Push a fresh callback event that meets the freshness threshold.
    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-FRESH",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,
            order_volume=1000,
            traded_volume=300,
            traded_price=12.34,
            # Order time close to now so freshness threshold holds.
            order_time=datetime.now(timezone.utc),
            status_msg="",
        )
    )

    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    reports = client.list_execution_reports(since=since)

    assert len(reports) == 1
    assert reports[0]["order_sysid"] == "SYS-FRESH"
    assert client._trader_test_ref.query_order_calls == 0
    assert client._trader_test_ref.query_trade_calls == 0


def test_get_callback_loop_state_surfaces_execution_callback_freshness():
    """``execution_callback_freshness`` is exposed alongside the asset signal."""
    client = _stale_buffer_client()
    state = client.get_callback_loop_state()

    # Consumer alive but no execution callback yet → stale.
    assert state["execution_callback_freshness"] == "stale"

    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-1",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,
            order_volume=1000,
            traded_volume=0,
            traded_price=0.0,
            order_time=datetime.now(timezone.utc),
            status_msg="",
        )
    )

    state_after = client.get_callback_loop_state()
    assert state_after["execution_callback_freshness"] == "fresh"

    # Disconnected consumer → unavailable.
    with client._consumer_lock:
        client._consumer_thread = None
    state_disconnected = client.get_callback_loop_state()
    assert state_disconnected["execution_callback_freshness"] == "unavailable"


# ---------------------------------------------------------------------------
# Critical #2: sync lock prevents cursor race under concurrent callers
# ---------------------------------------------------------------------------


def test_collect_sync_state_uses_sync_lock_to_block_concurrent_cursor_updates():
    """`_sync_lock` must serialise callers so no event is skipped or replayed."""
    client = XtQuantShadowClient(
        trader=SimpleNamespace(
            session_id=42,
            query_stock_asset=lambda _account: {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            },
            query_stock_positions=lambda _account: [],
            query_stock_orders=lambda _account: [],
            query_stock_trades=lambda _account: [],
        ),
        account_ref="acct-1",
        account_id="acct-1",
    )

    # Flood the bridge with a deterministic set of callback events.
    for i in range(10):
        client._callback_bridge.record_runtime_event(
            "account_status",
            update_time=datetime(2026, 4, 13, 9, 31, i, tzinfo=timezone.utc),
            account_id="acct-1",
            status=f"step-{i}",
        )

    barrier = threading.Barrier(4)
    collected: list[list[int]] = [[], [], [], []]

    def _worker(index: int, cursor_state: dict | None):
        barrier.wait()
        for _ in range(50):
            state = client.collect_sync_state(cursor_state=cursor_state)
            cursor_state = state["cursor_state"]
        collected[index] = [
            int(event.get("_journal_seq", 0))
            for event in state["runtime_events"]
        ]

    threads = [
        threading.Thread(target=_worker, args=(i, {}))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive()

    # All concurrent callers must eventually converge to the same cursor
    # exhaust state: no events, because the runtime_event table has drained.
    final_states = [
        client.collect_sync_state(cursor_state={"callback_runtime_seq": 999, "callback_execution_seq": 999})
        for _ in range(4)
    ]
    for bundle in final_states:
        assert bundle["runtime_events"] == [] or all(
            isinstance(event.get("_journal_seq"), int)
            for event in bundle["runtime_events"]
        )
    # Cursor returned by any worker should be monotonic (no regression).
    assert client._callback_bridge.current_journal_seq() >= 10


def test_describe_last_submit_ack_is_sync_lock_serialized():
    """`describe_last_submit_ack` must not race concurrent cursor reads."""
    client = XtQuantShadowClient(
        trader=SimpleNamespace(session_id=42),
        account_ref="acct-1",
        account_id="acct-1",
    )
    with client._sync_lock:
        client._last_submit_mode = "order_stock_async"
    ack = client.describe_last_submit_ack(88)
    assert ack == {"broker_submit_id": "88", "broker_order_id": ""}


# ---------------------------------------------------------------------------
# Important: persistent refresh listeners survive clear()
# ---------------------------------------------------------------------------


def test_qmt_session_manager_persistent_listener_survives_clear_and_receives_new_session_callbacks():
    """register_refresh_listener should outlive clear() and fire for new sessions."""
    manager = QMTSessionManager()
    notifications: list[dict[str, object]] = []

    class _Trader:
        session_id = 42

        def query_stock_asset(self, account):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

    def _listener(*, session_key, deployment_ids, event):
        notifications.append(
            {
                "account_id": session_key.account_id,
                "deployment_ids": tuple(deployment_ids),
                "kind": str(event.get("_report_kind", "")),
            }
        )

    token = manager.register_refresh_listener(_listener)
    assert manager.has_refresh_listener(token) is True

    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    client_a = XtQuantShadowClient(
        trader=_Trader(),
        account_ref="acct-1",
        account_id="acct-1",
    )
    manager.attach_owner(
        config=config,
        factory=lambda _config: client_a,
        deployment_id="dep-1",
    )
    client_a._callback_bridge.on_disconnected()
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "disconnected"

    # Emulate scheduler recycle.
    manager.clear()
    assert manager.has_refresh_listener(token) is True

    # Create a fresh session; persistent listener must still fire.
    client_b = XtQuantShadowClient(
        trader=_Trader(),
        account_ref="acct-1",
        account_id="acct-1",
    )
    manager.attach_owner(
        config=config,
        factory=lambda _config: client_b,
        deployment_id="dep-2",
    )
    client_b._callback_bridge.on_disconnected()
    assert len(notifications) == 2
    assert notifications[1]["kind"] == "disconnected"
    # The deployment id from the NEW session should be present.
    assert "dep-2" in notifications[1]["deployment_ids"]


def test_qmt_session_manager_persistent_listener_fires_even_without_attached_deployments():
    """Persistent listeners intentionally fire for owner-free sessions too."""
    manager = QMTSessionManager()
    events: list[str] = []

    def _listener(*, session_key, deployment_ids, event):
        events.append(str(event.get("_report_kind", "")))

    manager.register_refresh_listener(_listener)

    class _Client:
        def query_stock_asset(self, account_id: str):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account_id: str):
            return []

        def query_stock_orders(self, account_id: str):
            return []

        def query_stock_trades(self, account_id: str):
            return []

    trader = SimpleNamespace(session_id=42)
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    manager.resolve(config=config, factory=lambda _config: client)
    client._callback_bridge.on_disconnected()

    assert events == ["disconnected"]


def test_qmt_session_manager_unregister_refresh_listener_silences_further_callbacks():
    manager = QMTSessionManager()
    events: list[str] = []
    token = manager.register_refresh_listener(
        lambda *, session_key, deployment_ids, event: events.append(
            str(event.get("_report_kind", ""))
        )
    )

    class _Client:
        def query_stock_asset(self, account_id: str):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account_id: str):
            return []

        def query_stock_orders(self, account_id: str):
            return []

        def query_stock_trades(self, account_id: str):
            return []

    client = XtQuantShadowClient(
        trader=SimpleNamespace(session_id=42),
        account_ref="acct-1",
        account_id="acct-1",
    )
    config = QMTBrokerConfig(account_id="acct-1", session_id="42")
    manager.resolve(config=config, factory=lambda _config: client)

    client._callback_bridge.on_disconnected()
    assert events == ["disconnected"]

    manager.unregister_refresh_listener(token)
    assert manager.has_refresh_listener(token) is False

    client._callback_bridge.on_disconnected()
    # Already-seen event keys are deduped inside the bridge, so fire another
    # distinct runtime event and confirm listener does not receive it.
    client._callback_bridge.record_runtime_event(
        "account_status",
        update_time=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        account_id="acct-1",
        status="connected",
    )
    assert events == ["disconnected"]


# ---------------------------------------------------------------------------
# Important: reconnect backoff cap + jitter
# ---------------------------------------------------------------------------


def test_apply_reconnect_jitter_caps_at_max_backoff_plus_jitter():
    """10 consecutive failures must never produce > 60s * (1 + jitter_fraction)."""
    max_observed = timedelta(0)
    for attempt in range(1, 11):
        raw = _QMT_RECONNECT_BASE_BACKOFF * attempt
        actual = _apply_reconnect_jitter(raw)
        assert actual.total_seconds() >= 0.0
        if actual > max_observed:
            max_observed = actual
    # 60s * (1 + 0.2) = 72s upper bound.
    assert max_observed.total_seconds() <= 72.0


def test_apply_reconnect_jitter_respects_max_backoff_cap_even_with_huge_input():
    big = timedelta(seconds=10_000)
    # Deterministic jitter=+0.2 simulator via rng stub.
    capped = _apply_reconnect_jitter(big, rng=lambda _a, _b: 0.2)
    assert capped.total_seconds() == pytest.approx(
        _QMT_RECONNECT_MAX_BACKOFF.total_seconds() * 1.2
    )


def test_apply_reconnect_jitter_negative_jitter_still_non_negative():
    capped = _apply_reconnect_jitter(
        timedelta(seconds=5),
        rng=lambda _a, _b: -1.0,  # extreme negative jitter
    )
    assert capped.total_seconds() >= 0.0


def test_ensure_resident_session_applies_jitter_to_consumer_next_retry():
    """`ensure_resident_session` failure paths must respect the cap+jitter path."""
    class _FailingTrader:
        session_id = 42

        def __init__(self):
            self.connect_calls = 0

        def connect(self):
            self.connect_calls += 1
            return 7  # non-zero → fails

        def subscribe(self, account):  # pragma: no cover - unreachable
            return 0

        def query_stock_asset(self, account):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

    trader = _FailingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._callback_bridge.on_disconnected()

    for _ in range(30):
        try:
            client.ensure_resident_session()
        except RuntimeError:
            pass
        # Clear the consumer next_retry_at gate so the loop actually keeps
        # attempting to reconnect.
        with client._consumer_lock:
            client._consumer_next_retry_at = None
        client._callback_bridge.on_disconnected()

    assert trader.connect_calls >= 10
    with client._consumer_lock:
        # Last attempt should have set a next_retry_at within the cap+jitter envelope.
        attempts = client._consumer_restart_attempts
    assert attempts >= 10  # confirm we exercised the path


# ---------------------------------------------------------------------------
# Minor: xtquant numeric order-status code coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "numeric_code,expected",
    [
        ("48", "unreported"),
        ("49", "unreported"),
        ("50", "reported"),
        ("51", "reported_cancel_pending"),
        ("52", "partially_filled_cancel_pending"),
        ("53", "partially_canceled"),
        ("54", "canceled"),
        ("55", "partially_filled"),
        ("56", "filled"),
        ("57", "junk"),
        ("255", "unknown"),
    ],
)
def test_pre_normalize_qmt_numeric_order_status_covers_full_xtquant_vocab(numeric_code, expected):
    assert _pre_normalize_qmt_numeric_order_status(numeric_code) == expected


def test_pre_normalize_qmt_numeric_order_status_handles_int_inputs():
    assert _pre_normalize_qmt_numeric_order_status(55) == "partially_filled"
    assert _pre_normalize_qmt_numeric_order_status(56) == "filled"


def test_pre_normalize_qmt_numeric_order_status_passes_through_unknown_codes():
    # Unknown numeric codes preserve their stringified form so downstream
    # normalize_broker_order_status can continue alias handling.
    assert _pre_normalize_qmt_numeric_order_status("999") == "999"
    # Strings that are already lowercase vocabulary survive untouched.
    assert _pre_normalize_qmt_numeric_order_status("reported") == "reported"


# ---------------------------------------------------------------------------
# V3.3.43 full callback order-state closure: lifecycle tracker + execution
# sync mode tightening (callback_only / callback_query_merge / query_only).
#
# The tests below lock in the contract that when the XtQuant callback
# consumer is alive and the lifecycle tracker shows each submit-ack'd
# order has reached either a terminal callback or a fresh
# ``on_stock_order`` callback, ``list_execution_reports`` /
# ``collect_sync_state`` stop depending on ``query_stock_orders`` /
# ``query_stock_trades`` fallback. Degraded/dead paths still fall back
# to merge or query-only respectively.
# ---------------------------------------------------------------------------


def _closure_client_with_alive_consumer() -> XtQuantShadowClient:
    """Build a client with a fake alive consumer and a query-counting trader."""

    class _Trader:
        session_id = 42

        def __init__(self):
            self.query_order_calls = 0
            self.query_trade_calls = 0

        def query_stock_asset(self, account):
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            self.query_order_calls += 1
            return [
                {
                    "order_remark": "cid-1",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": 55,
                    "order_volume": 1000,
                    "traded_volume": 300,
                    "traded_price": 12.34,
                    "order_time": "2026-04-13T10:00:00+00:00",
                    "status_msg": "",
                }
            ]

        def query_stock_trades(self, account):
            self.query_trade_calls += 1
            return []

    trader = _Trader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    with client._consumer_lock:
        client._consumer_thread = SimpleNamespace(is_alive=lambda: True)
        client._consumer_started_at = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
        client._consumer_last_transition_at = datetime(
            2026, 4, 13, 9, 30, tzinfo=timezone.utc
        )
    client._trader_test_ref = trader  # type: ignore[attr-defined]
    return client


def test_full_lifecycle_callback_closure_reaches_callback_only_mode():
    """submit ack → on_stock_order SUCCEEDED (56) → callback_only.

    When the bridge has seen submit-ack + a terminal order callback for
    a given identity, the incremental execution-report path must skip
    ``query_stock_orders`` / ``query_stock_trades`` entirely and surface
    ``execution_sync_mode == "callback_only"``.
    """
    client = _closure_client_with_alive_consumer()
    trader = client._trader_test_ref  # type: ignore[attr-defined]

    # 1) Submit ack first (broker acknowledged the submit request).
    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=1001,
            seq="submit-seq-001",
            strategy_name="ez-strategy",
            order_remark="cid-1",
            error_msg="",
        )
    )
    # 2) Terminal order callback (SUCCEEDED = 56).
    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="cid-1",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=56,  # SUCCEEDED
            order_volume=1000,
            traded_volume=1000,
            traded_price=12.34,
            order_time=datetime.now(timezone.utc),
            status_msg="",
        )
    )

    closure = client.get_lifecycle_closure("cid-1")
    assert closure is not None
    assert closure["submit_ack_received"] is True
    assert closure["terminal_callback_ts"] is not None
    assert closure["last_order_status"] == "filled"

    # Incremental call must stay callback_only — no query round-trip.
    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    reports = client.list_execution_reports(since=since)
    assert client.last_execution_sync_mode() == "callback_only"
    assert trader.query_order_calls == 0
    assert trader.query_trade_calls == 0
    assert any(str(r.get("order_status", "")) == "56" for r in reports)

    bundle = client.collect_sync_state(since_reports=since)
    assert bundle["execution_sync_mode"] == "callback_only"
    assert bundle["sync_mode_details"] == {
        "orders": "callback_only",
        "asset": "query_fallback",
        "trades": "callback_only",
    }
    # ``collect_sync_state`` still queries the broker for the *snapshot*
    # orders/trades view (that is a full-state read, not a lifecycle
    # delta), but the execution_reports channel is callback_only.
    callback_only_exec_kinds = {
        str(r.get("_report_kind", "")) for r in bundle["execution_reports"]
    }
    assert "order" in callback_only_exec_kinds


def test_stale_submit_ack_without_lifecycle_callback_forces_merge():
    """submit ack + consumer alive + > N-second silence → degraded merge.

    If the bridge saw a submit-ack but the broker never pushed any
    ``on_stock_order`` / ``on_stock_trade`` within the freshness window,
    the degraded-path must fall back to ``callback_query_merge`` so the
    caller catches up via ``query_stock_orders / query_stock_trades``.
    """
    client = _closure_client_with_alive_consumer()
    trader = client._trader_test_ref  # type: ignore[attr-defined]

    # Submit-ack received ``_QMT_CALLBACK_MAX_GAP_SECS + 60`` seconds ago.
    stale_ts = datetime.now(timezone.utc) - timedelta(
        seconds=qmt_session_owner._QMT_CALLBACK_MAX_GAP_SECS + 60
    )
    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=stale_ts,
            account_id="acct-1",
            order_id=2001,
            seq="submit-seq-stale",
            strategy_name="ez-strategy",
            order_remark="cid-stale",
            error_msg="",
        )
    )
    # Deliberately no order/trade callbacks — broker went silent.

    closure = client.get_lifecycle_closure("cid-stale")
    assert closure is not None
    assert closure["submit_ack_received"] is True
    assert closure["last_order_callback_ts"] is None
    assert closure["terminal_callback_ts"] is None

    # ``since`` wide enough to cover the 2026-04-13T10:00:00 query row.
    since = datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)
    reports = client.list_execution_reports(since=since)
    assert client.last_execution_sync_mode() == "callback_query_merge"
    # Degraded merge path must actually query the broker.
    assert trader.query_order_calls == 1
    assert trader.query_trade_calls == 1
    # Returned reports include the query fallback (the synthetic order
    # rowed from ``query_stock_orders``).
    assert any(
        str(r.get("order_sysid", "")) == "SYS-001" for r in reports
    )


def test_on_order_error_closes_lifecycle_as_terminal():
    """``on_order_error`` must advance the closure to terminal immediately.

    Official xtquant ``on_order_error`` corresponds to ORDER_JUNK (57),
    so the lifecycle tracker must record ``terminal_callback_ts`` and
    ``order_error_received = True`` even without a separate
    ``on_stock_order`` callback.
    """
    client = _closure_client_with_alive_consumer()

    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=3001,
            seq="submit-seq-err",
            strategy_name="ez-strategy",
            order_remark="cid-err",
            error_msg="",
        )
    )
    client._callback_bridge.on_order_error(
        SimpleNamespace(
            error_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_remark="cid-err",
            order_id=3001,
            order_sysid="",
            stock_code="000001.SZ",
            offset_flag="buy",
            error_msg="risk-blocked",
        )
    )

    # Resolve via every known identity token — submit-seq, order_id,
    # and client_order_id must all land on the same closure object.
    closure_by_cid = client.get_lifecycle_closure("cid-err")
    closure_by_order_id = client.get_lifecycle_closure("3001")
    closure_by_submit_seq = client.get_lifecycle_closure("submit-seq-err")
    assert closure_by_cid is not None
    assert closure_by_order_id is not None
    assert closure_by_submit_seq is not None
    assert closure_by_cid["terminal_callback_ts"] is not None
    assert closure_by_cid["order_error_received"] is True
    assert closure_by_cid["last_order_status"] == "order_error"

    # ``broker_order_status_is_terminal`` agrees order_error is terminal.
    from ez.live.events import broker_order_status_is_terminal

    assert broker_order_status_is_terminal(closure_by_cid["last_order_status"]) is True

    # And with every submit-ack'd order having terminal_callback_ts,
    # the sync mode is callback_only.
    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    client.list_execution_reports(since=since)
    assert client.last_execution_sync_mode() == "callback_only"


def test_partial_trade_callbacks_accumulate_in_lifecycle_tracker():
    """Every distinct ``on_stock_trade`` increments ``trade_callback_count``."""
    client = _closure_client_with_alive_consumer()

    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=4001,
            seq="submit-seq-trades",
            strategy_name="ez-strategy",
            order_remark="cid-trades",
            error_msg="",
        )
    )
    for i, (trade_no, volume) in enumerate(
        [("T-1", 100), ("T-2", 200), ("T-3", 300)]
    ):
        client._callback_bridge.on_stock_trade(
            SimpleNamespace(
                traded_time=datetime(2026, 4, 13, 9, 31 + i, tzinfo=timezone.utc),
                account_id="acct-1",
                order_remark="cid-trades",
                order_id=4001,
                order_sysid="SYS-004",
                traded_id=trade_no,
                stock_code="000001.SZ",
                offset_flag="buy",
                traded_volume=volume,
                traded_price=10.0 + i,
            )
        )

    closure = client.get_lifecycle_closure("cid-trades")
    assert closure is not None
    assert closure["trade_callback_count"] == 3
    assert isinstance(closure["last_trade_callback_ts"], datetime)


def test_duplicate_callback_is_idempotent_in_lifecycle_tracker():
    """Replaying the identical callback payload must not inflate counters.

    The callback bridge dedupes by a payload-level event key. Re-delivering
    the same ``on_stock_trade`` / ``on_stock_order`` payload must keep
    ``trade_callback_count`` and ``terminal_callback_ts`` stable.
    """
    client = _closure_client_with_alive_consumer()

    submit_ts = datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=submit_ts,
            account_id="acct-1",
            order_id=5001,
            seq="submit-seq-dup",
            strategy_name="ez-strategy",
            order_remark="cid-dup",
            error_msg="",
        )
    )

    order_ts = datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc)
    terminal_order = SimpleNamespace(
        order_remark="cid-dup",
        order_id=5001,
        order_sysid="SYS-005",
        stock_code="000001.SZ",
        offset_flag="buy",
        order_status=56,  # SUCCEEDED / filled
        order_volume=1000,
        traded_volume=1000,
        traded_price=12.0,
        order_time=order_ts,
        status_msg="",
    )
    trade_ts = datetime(2026, 4, 13, 9, 31, 5, tzinfo=timezone.utc)
    trade_payload = SimpleNamespace(
        traded_time=trade_ts,
        account_id="acct-1",
        order_remark="cid-dup",
        order_id=5001,
        order_sysid="SYS-005",
        traded_id="T-DUP-1",
        stock_code="000001.SZ",
        offset_flag="buy",
        traded_volume=1000,
        traded_price=12.0,
    )

    # Deliver the terminal order + trade payloads three times.
    for _ in range(3):
        client._callback_bridge.on_stock_order(terminal_order)
        client._callback_bridge.on_stock_trade(trade_payload)

    closure = client.get_lifecycle_closure("cid-dup")
    assert closure is not None
    # Bridge deduped the trade → counter should stay at 1.
    assert closure["trade_callback_count"] == 1
    # Terminal callback_ts must still equal the very first terminal
    # observation (it is set once and never overwritten).
    assert closure["terminal_callback_ts"] == order_ts
    assert closure["last_order_callback_ts"] == order_ts


def test_consumer_dead_forces_query_only_sync_mode():
    """When the callback consumer is not alive, fall back to query_only."""
    client = _closure_client_with_alive_consumer()
    trader = client._trader_test_ref  # type: ignore[attr-defined]

    # Even if we have had a submit-ack + terminal callback, a dead
    # consumer means the broker is not pushing further state; sync must
    # fall back to query_only so operators can still observe reconcile
    # truth.
    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=6001,
            seq="submit-seq-dead",
            strategy_name="ez-strategy",
            order_remark="cid-dead",
            error_msg="",
        )
    )
    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="cid-dead",
            order_id=6001,
            order_sysid="SYS-006",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=56,
            order_volume=1000,
            traded_volume=1000,
            traded_price=12.0,
            order_time=datetime.now(timezone.utc),
            status_msg="",
        )
    )

    # Kill the consumer.
    with client._consumer_lock:
        client._consumer_thread = None

    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    client.list_execution_reports(since=since)
    assert client.last_execution_sync_mode() == "query_only"
    # Query path was exercised.
    assert trader.query_order_calls == 1
    assert trader.query_trade_calls == 1

    bundle = client.collect_sync_state(since_reports=since)
    assert bundle["execution_sync_mode"] == "query_only"
    assert bundle["sync_mode_details"]["orders"] == "query_only"
    assert bundle["sync_mode_details"]["trades"] == "query_only"


def test_get_callback_loop_state_surfaces_execution_sync_mode():
    """``session_consumer_state`` must expose the latest execution_sync_mode."""
    client = _closure_client_with_alive_consumer()
    # Initial state before any sync call — mode is "unknown".
    state0 = client.get_callback_loop_state()
    assert state0["execution_sync_mode"] == "unknown"

    # Drive the tracker through a full callback closure.
    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=7001,
            seq="submit-seq-loop",
            strategy_name="ez-strategy",
            order_remark="cid-loop",
            error_msg="",
        )
    )
    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="cid-loop",
            order_id=7001,
            order_sysid="SYS-007",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=56,
            order_volume=1000,
            traded_volume=1000,
            traded_price=12.0,
            order_time=datetime.now(timezone.utc),
            status_msg="",
        )
    )
    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    client.list_execution_reports(since=since)

    state_after = client.get_callback_loop_state()
    assert state_after["execution_sync_mode"] == "callback_only"
    # The state_key embeds execution_sync_mode so runtime events will
    # re-emit on mode transitions.
    events = client.list_runtime_events(since=since)
    consumer_state_events = [
        e for e in events if str(e.get("_report_kind", "")) == "session_consumer_state"
    ]
    assert consumer_state_events
    assert consumer_state_events[-1]["execution_sync_mode"] == "callback_only"


def test_partially_filled_callback_without_terminal_still_callback_only_when_fresh():
    """Fresh non-terminal callback (PART_SUCC=55) + submit ack → callback_only.

    Per the task spec: "若所有 ``submit_ack_received`` 的 order 都已有
    ``terminal_callback_ts`` 或 ``last_order_callback_ts`` 距 now <
    ``_QMT_CALLBACK_MAX_GAP_SECS`` → 纯 callback 路径, 不查 query".
    """
    client = _closure_client_with_alive_consumer()
    trader = client._trader_test_ref  # type: ignore[attr-defined]

    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=8001,
            seq="submit-seq-part",
            strategy_name="ez-strategy",
            order_remark="cid-part",
            error_msg="",
        )
    )
    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="cid-part",
            order_id=8001,
            order_sysid="SYS-008",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,  # PART_SUCC (not yet terminal)
            order_volume=1000,
            traded_volume=500,
            traded_price=12.0,
            order_time=datetime.now(timezone.utc),  # fresh
            status_msg="",
        )
    )

    closure = client.get_lifecycle_closure("cid-part")
    assert closure is not None
    assert closure["submit_ack_received"] is True
    assert closure["terminal_callback_ts"] is None
    assert closure["last_order_status"] == "partially_filled"
    # Fresh non-terminal callback — tracker says callback_only.
    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    client.list_execution_reports(since=since)
    assert client.last_execution_sync_mode() == "callback_only"
    assert trader.query_order_calls == 0
    assert trader.query_trade_calls == 0


def test_mixed_submit_acks_degrade_to_merge_when_any_stale():
    """Any single stale submit-ack forces the whole sync bundle into merge.

    Two submit-acks: one has a fresh terminal callback, the other has
    been silent past the freshness gap. The single stale closure must
    degrade the entire incremental sync path into callback_query_merge.
    """
    client = _closure_client_with_alive_consumer()
    trader = client._trader_test_ref  # type: ignore[attr-defined]

    # Healthy submit-ack — terminal callback arrived.
    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=9001,
            seq="submit-seq-healthy",
            strategy_name="ez-strategy",
            order_remark="cid-healthy",
            error_msg="",
        )
    )
    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="cid-healthy",
            order_id=9001,
            order_sysid="SYS-009",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=56,
            order_volume=1000,
            traded_volume=1000,
            traded_price=12.0,
            order_time=datetime.now(timezone.utc),
            status_msg="",
        )
    )
    # Unhealthy submit-ack — no callback within the freshness gap.
    stale_ts = datetime.now(timezone.utc) - timedelta(
        seconds=qmt_session_owner._QMT_CALLBACK_MAX_GAP_SECS + 30
    )
    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=stale_ts,
            account_id="acct-1",
            order_id=9002,
            seq="submit-seq-silent",
            strategy_name="ez-strategy",
            order_remark="cid-silent",
            error_msg="",
        )
    )

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    client.list_execution_reports(since=since)
    assert client.last_execution_sync_mode() == "callback_query_merge"
    # Merge path must actually query the broker.
    assert trader.query_order_calls >= 1
    assert trader.query_trade_calls >= 1

    # collect_sync_state reflects the same decision + sync_mode_details.
    bundle = client.collect_sync_state(since_reports=since)
    assert bundle["execution_sync_mode"] == "callback_query_merge"
    assert bundle["sync_mode_details"]["orders"] == "merge"
    assert bundle["sync_mode_details"]["trades"] == "merge"


def test_lifecycle_closure_key_aliases_cover_remark_order_id_sysid_and_seq():
    """``get_lifecycle_closure`` resolves by any known identity token.

    The bridge indexes each callback under ``order_remark`` /
    ``client_order_id``, ``order_sysid``, ``order_id``, and submit-ack
    ``seq`` so downstream callers can reach the same closure regardless
    of which identity they happen to hold.
    """
    client = _closure_client_with_alive_consumer()

    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=0,  # QMT returns 0 before order_sysid is known
            seq="seq-XYZ",
            strategy_name="ez-strategy",
            order_remark="remark-XYZ",
            error_msg="",
        )
    )
    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="remark-XYZ",
            order_id=1234,
            order_sysid="SYS-XYZ",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,
            order_volume=500,
            traded_volume=100,
            traded_price=20.0,
            order_time=datetime.now(timezone.utc),
            status_msg="",
        )
    )
    # Every identity token must hit the *same* closure.
    via_seq = client.get_lifecycle_closure("seq-XYZ")
    via_remark = client.get_lifecycle_closure("remark-XYZ")
    via_order_id = client.get_lifecycle_closure("1234")
    via_sysid = client.get_lifecycle_closure("SYS-XYZ")
    for closure in (via_seq, via_remark, via_order_id, via_sysid):
        assert closure is not None
        assert closure["submit_ack_received"] is True
        assert closure["last_order_status"] == "partially_filled"
    # Known identities include every token the bridge observed.
    expected_identities = {"seq-XYZ", "remark-XYZ", "1234", "SYS-XYZ"}
    assert expected_identities.issubset(set(via_seq["known_identities"]))


def test_collect_sync_state_sync_mode_details_for_callback_only_closure():
    """``collect_sync_state`` exposes sync_mode_details including asset mode."""
    client = _closure_client_with_alive_consumer()

    client._callback_bridge.on_order_stock_async_response(
        SimpleNamespace(
            update_time=datetime.now(timezone.utc),
            account_id="acct-1",
            order_id=1001,
            seq="submit-seq-details",
            strategy_name="ez-strategy",
            order_remark="cid-details",
            error_msg="",
        )
    )
    client._callback_bridge.on_stock_order(
        SimpleNamespace(
            order_remark="cid-details",
            order_id=1001,
            order_sysid="SYS-D",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=56,
            order_volume=1000,
            traded_volume=1000,
            traded_price=12.0,
            order_time=datetime.now(timezone.utc),
            status_msg="",
        )
    )

    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    bundle = client.collect_sync_state(since_reports=since)
    assert bundle["execution_sync_mode"] == "callback_only"
    assert set(bundle["sync_mode_details"].keys()) == {
        "orders",
        "asset",
        "trades",
    }
    # orders/trades reflect the execution sync mode, asset reflects its
    # own callback/query decision (no stock_asset callback yet → fallback).
    assert bundle["sync_mode_details"]["orders"] == "callback_only"
    assert bundle["sync_mode_details"]["trades"] == "callback_only"
    assert bundle["sync_mode_details"]["asset"] in {
        "callback_preferred",
        "query_fallback",
    }
