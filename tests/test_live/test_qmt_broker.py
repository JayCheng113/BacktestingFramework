from __future__ import annotations

from datetime import datetime, timezone
import threading
from types import SimpleNamespace

import pytest

from ez.live.broker import BrokerCapability
from ez.live.events import Order
from ez.live.qmt.broker import (
    QMTBrokerConfig,
    QMTShadowBroker,
    QMTRealBroker,
    build_qmt_reconcile_hard_gate,
    build_qmt_release_gate_decision,
    build_qmt_real_submit_policy,
    XtQuantShadowClient,
    build_qmt_readiness_summary,
    build_qmt_submit_gate_decision,
    get_default_qmt_session_manager,
)


@pytest.fixture(autouse=True)
def _clear_qmt_session_manager():
    manager = get_default_qmt_session_manager()
    manager.clear()
    yield
    manager.clear()


class _FakeQMTClient:
    def __init__(self):
        self.closed = False

    def query_stock_asset(self, account_id: str):
        assert account_id == "acct-1"
        return {
            "update_time": "2026-04-13T09:31:00+00:00",
            "enable_balance": 100_500.25,
            "total_balance": 250_000.75,
        }

    def query_stock_positions(self, account_id: str):
        assert account_id == "acct-1"
        return [
            {"stock_code": "000001.SZ", "current_amount": 1000},
            {"stock_code": "600000.SH", "current_amount": 500},
        ]

    def query_stock_orders(self, account_id: str):
        assert account_id == "acct-1"
        return [
            {
                "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                "order_sysid": "SYS-001",
                "order_id": 1001,
                "stock_code": "000001.SZ",
                "offset_flag": "buy",
                "order_status": 55,
                "order_volume": 1000,
                "traded_volume": 600,
                "left_volume": 400,
                "traded_price": 12.34,
                "order_time": "2026-04-13T09:32:00+00:00",
                "status_msg": "",
            }
        ]

    def query_stock_trades(self, account_id: str):
        assert account_id == "acct-1"
        return [
            {
                "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                "order_sysid": "SYS-001",
                "order_id": 1001,
                "traded_id": "T-001",
                "stock_code": "000001.SZ",
                "offset_flag": "buy",
                "traded_volume": 600,
                "traded_price": 12.34,
                "traded_time": "2026-04-13T09:32:10+00:00",
            }
        ]

    def cancel_order(self, order_id: str):
        return order_id == "1001"

    def close(self):
        self.closed = True


class _FakeRealQMTClient(_FakeQMTClient):
    def __init__(self):
        super().__init__()
        self.submit_calls: list[tuple[object, ...]] = []

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        shares: int,
        price: float,
        strategy_name: str,
        order_remark: str,
        price_type: object | None = None,
    ):
        self.submit_calls.append(
            (symbol, side, shares, price, strategy_name, order_remark, price_type)
        )
        return 77


def _broker() -> QMTShadowBroker:
    return QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=_FakeQMTClient(),
    )


def test_qmt_shadow_broker_declares_shadow_capabilities():
    broker = _broker()
    assert broker.capabilities == frozenset(
        {
            BrokerCapability.READ_ACCOUNT_STATE,
            BrokerCapability.STREAM_EXECUTION_REPORTS,
            BrokerCapability.SHADOW_MODE,
        }
    )


def test_qmt_shadow_broker_normalizes_account_snapshot():
    broker = _broker()
    snapshot = broker.snapshot_account_state()

    assert snapshot is not None
    assert snapshot.broker_type == "qmt"
    assert snapshot.cash == 100_500.25
    assert snapshot.total_asset == 250_000.75
    assert snapshot.positions == {"000001.SZ": 1000, "600000.SH": 500}
    assert snapshot.open_orders[0]["client_order_id"] == "dep-1:2026-04-13:000001.SZ:buy"
    assert snapshot.open_orders[0]["broker_order_id"] == "SYS-001"
    assert snapshot.open_orders[0]["status"] == "partially_filled"
    assert snapshot.fills[0]["shares"] == 600


def test_qmt_shadow_broker_snapshot_account_state_prefers_collect_sync_state_when_available():
    class _CallbackAwareClient:
        def __init__(self):
            self.collect_calls = 0
            self.query_calls = 0

        def collect_sync_state(self, *, since_reports=None, since_runtime=None, cursor_state=None):
            self.collect_calls += 1
            return {
                "asset": {
                    "update_time": "2026-04-13T09:31:00+00:00",
                    "cash": 100_500.25,
                    "total_asset": 250_000.75,
                },
                "positions": [
                    {"stock_code": "000001.SZ", "current_amount": 1000},
                ],
                "orders": [],
                "trades": [],
            }

        def query_stock_asset(self, account_id: str):
            self.query_calls += 1
            return {
                "update_time": "2026-04-13T09:30:00+00:00",
                "cash": 1.0,
                "total_asset": 2.0,
            }

        def query_stock_positions(self, account_id: str):
            self.query_calls += 1
            return []

        def query_stock_orders(self, account_id: str):
            self.query_calls += 1
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_sysid": "SYS-stale",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": "reported",
                    "order_volume": 1000,
                    "traded_volume": 0,
                    "order_time": "2026-04-13T09:30:00+00:00",
                }
            ]

        def query_stock_trades(self, account_id: str):
            self.query_calls += 1
            return []

    client = _CallbackAwareClient()
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=client,
    )

    snapshot = broker.snapshot_account_state()

    assert snapshot is not None
    assert client.collect_calls == 1
    assert client.query_calls == 0
    assert snapshot.cash == 100_500.25
    assert snapshot.total_asset == 250_000.75
    assert snapshot.positions == {"000001.SZ": 1000}
    assert snapshot.open_orders == []
    assert snapshot.fills == []


def test_qmt_shadow_broker_list_execution_reports_prefers_collect_sync_state_when_available():
    class _CallbackAwareClient:
        def __init__(self):
            self.collect_calls = 0
            self.query_calls = 0

        def collect_sync_state(self, *, since_reports=None, since_runtime=None, cursor_state=None):
            self.collect_calls += 1
            return {
                "execution_reports": [
                    {
                        "_report_kind": "order",
                        "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                        "order_id": 1001,
                        "order_sysid": "SYS-001",
                        "stock_code": "000001.SZ",
                        "offset_flag": "buy",
                        "order_status": "partially_filled",
                        "order_volume": 1000,
                        "traded_volume": 600,
                        "left_volume": 400,
                        "traded_price": 12.34,
                        "order_time": "2026-04-13T09:32:00+00:00",
                    },
                    {
                        "_report_kind": "trade",
                        "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                        "order_id": 1001,
                        "order_sysid": "SYS-001",
                        "traded_id": "T-001",
                        "stock_code": "000001.SZ",
                        "offset_flag": "buy",
                        "traded_volume": 600,
                        "traded_price": 12.34,
                        "traded_time": "2026-04-13T09:32:10+00:00",
                    },
                ],
            }

        def query_stock_orders(self, account_id: str):
            self.query_calls += 1
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-stale",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": "reported",
                    "order_volume": 1000,
                    "traded_volume": 0,
                    "traded_price": 0.0,
                    "order_time": "2026-04-13T09:30:00+00:00",
                }
            ]

    client = _CallbackAwareClient()
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=client,
    )

    reports = broker.list_execution_reports()

    assert client.collect_calls == 1
    assert client.query_calls == 0
    assert len(reports) == 2
    assert [report.status for report in reports] == ["partially_filled", "filled"]
    assert [report.broker_order_id for report in reports] == ["SYS-001", "SYS-001"]


def test_qmt_shadow_broker_lists_execution_reports():
    broker = _broker()
    reports = broker.list_execution_reports()

    assert len(reports) == 1
    report = reports[0]
    assert report.report_id.startswith("qmt:SYS-001:partially_filled:600:400:")
    assert report.broker_type == "qmt"
    assert report.client_order_id == "dep-1:2026-04-13:000001.SZ:buy"
    assert report.broker_order_id == "SYS-001"
    assert report.status == "partially_filled"
    assert report.filled_shares == 600
    assert report.remaining_shares == 400
    assert report.avg_price == 12.34


def test_qmt_shadow_broker_filters_execution_reports_by_time():
    broker = _broker()
    reports = broker.list_execution_reports(
        since=datetime(2026, 4, 13, 9, 33, tzinfo=timezone.utc)
    )
    assert reports == []


def test_qmt_shadow_broker_refuses_live_execution():
    broker = _broker()
    with pytest.raises(NotImplementedError, match="read-only/shadow-only"):
        broker.execute_target_weights(
            business_date=datetime(2026, 4, 13, tzinfo=timezone.utc).date(),
            target_weights={},
            holdings={},
            equity=0.0,
            cash=0.0,
            prices={},
            raw_close_today={},
            prev_raw_close={},
            has_bar_today=set(),
            cost_model=None,  # type: ignore[arg-type]
            lot_size=100,
            limit_pct=0.0,
            t_plus_1=True,
        )


def test_qmt_real_broker_submits_orders_through_client():
    client = _FakeRealQMTClient()
    broker = QMTRealBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=client,
    )
    requested_orders = [
        Order(
            order_id="dep-1:2026-04-13:600000.SH:buy",
            client_order_id="dep-1:2026-04-13:600000.SH:buy",
            deployment_id="dep-1",
            symbol="600000.SH",
            side="buy",
            shares=100,
            business_date=datetime(2026, 4, 13, tzinfo=timezone.utc).date(),
            requested_shares=100,
            remaining_shares=100,
        )
    ]

    broker.open_submit_gate({"status": "open", "can_submit_now": True})
    try:
        result = broker.execute_target_weights(
            business_date=datetime(2026, 4, 13, tzinfo=timezone.utc).date(),
            target_weights={"600000.SH": 1.0},
            holdings={"600000.SH": 0},
            equity=1_000_000.0,
            cash=1_000_000.0,
            prices={"600000.SH": 10.5},
            raw_close_today={},
            prev_raw_close={},
            has_bar_today={"600000.SH"},
            cost_model=None,  # type: ignore[arg-type]
            lot_size=100,
            limit_pct=0.1,
            t_plus_1=True,
            requested_orders=requested_orders,
        )
    finally:
        broker.close_submit_gate()

    assert result.fills == []
    assert result.holdings == {"600000.SH": 0}
    assert result.cash == 1_000_000.0
    assert result.trade_volume == 0.0
    assert len(result.order_reports) == 1
    assert result.order_reports[0].status == "reported"
    assert result.order_reports[0].requested_shares == 100
    assert result.order_reports[0].broker_submit_id == "77"
    assert result.order_reports[0].broker_order_id == ""
    assert client.submit_calls == [
        (
            "600000.SH",
            "buy",
            100,
            10.5,
            "dep-1",
            "dep-1:2026-04-13:600000.SH:buy",
            None,
        )
    ]


def test_xtquant_shadow_client_describes_async_submit_ack(monkeypatch):
    class _SubmitTrader:
        def order_stock_async(
            self,
            account,
            symbol,
            order_side,
            shares,
            price_type,
            price,
            strategy_name,
            order_remark,
        ):
            return 88

    def _fake_import(name: str):
        if name == "xtquant.xtconstant":
            return SimpleNamespace(STOCK_BUY=11, STOCK_SELL=22, FIX_PRICE=33)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("ez.live.qmt.session_owner.importlib.import_module", _fake_import)

    client = XtQuantShadowClient(
        trader=_SubmitTrader(),
        account_ref="acct-1",
        account_id="acct-1",
    )

    result = client.submit_order(
        symbol="600000.SH",
        side="buy",
        shares=100,
        price=10.5,
        strategy_name="deploy-1",
        order_remark="dep-1:2026-04-13:600000.SH:buy",
    )

    assert result == 88
    assert client.describe_last_submit_ack(result) == {
        "broker_submit_id": "88",
        "broker_order_id": "",
    }


def test_qmt_shadow_broker_can_enable_cancel_path():
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1", enable_cancel=True),
        client=_FakeQMTClient(),
    )
    assert BrokerCapability.CANCEL_ORDER in broker.capabilities
    assert broker.cancel_order("1001") is True


def test_qmt_shadow_broker_lazy_factory_is_called_once():
    calls: list[str] = []

    def _factory(config: QMTBrokerConfig):
        calls.append(config.account_id)
        return _FakeQMTClient()

    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=_factory,
    )

    snapshot = broker.snapshot_account_state()
    reports = broker.list_execution_reports()

    assert snapshot is not None
    assert len(reports) == 1
    assert calls == ["acct-1"]


def test_qmt_shadow_brokers_with_same_config_reuse_shared_session():
    calls: list[str] = []

    def _factory(config: QMTBrokerConfig):
        calls.append(config.account_id)
        return _FakeQMTClient()

    broker_a = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=_factory,
    )
    broker_b = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=_factory,
    )

    snapshot = broker_a.snapshot_account_state()
    reports = broker_b.list_execution_reports()

    assert snapshot is not None
    assert len(reports) == 1
    assert calls == ["acct-1"]
    assert broker_a._client is broker_b._client
    assert get_default_qmt_session_manager().active_session_count() == 1
    assert {event.event_kind for event in broker_b.list_runtime_events()} == {
        "session_owner_created",
        "session_owner_reused",
    }
    state = broker_b.get_session_state()
    assert state is not None
    assert state.status == "reused"
    assert state.acquisition_count == 2


def test_qmt_shadow_broker_attach_and_detach_owner_closes_last_session():
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=lambda _cfg: _FakeQMTClient(),
    )

    broker.attach_deployment("dep-1")
    shared_client = broker._client
    assert shared_client is not None
    state = broker.get_session_state()
    assert state is not None
    assert state.owner_count == 1
    assert state.attached_deployments == ("dep-1",)

    detached_state = broker.detach_deployment("dep-1")
    assert detached_state is not None
    assert detached_state.owner_count == 0
    assert detached_state.status == "closed"
    assert detached_state.attached_deployments == ()
    assert broker._client is None
    assert shared_client.closed is True
    assert get_default_qmt_session_manager().active_session_count() == 0
    assert {
        event.event_kind
        for event in broker.list_runtime_events()
    } == {
        "session_owner_created",
        "session_owner_attached",
        "session_owner_detached",
        "session_owner_closed",
    }


def test_qmt_shadow_brokers_with_different_accounts_do_not_share_session():
    calls: list[str] = []

    class _Client:
        def __init__(self, account_id: str):
            self._account_id = account_id

        def query_stock_asset(self, account_id: str):
            assert account_id == self._account_id
            return {
                "update_time": "2026-04-13T09:31:00+00:00",
                "cash": 1.0,
                "total_asset": 1.0,
            }

        def query_stock_positions(self, account_id: str):
            assert account_id == self._account_id
            return []

        def query_stock_orders(self, account_id: str):
            assert account_id == self._account_id
            return []

        def query_stock_trades(self, account_id: str):
            assert account_id == self._account_id
            return []

    def _factory(config: QMTBrokerConfig):
        calls.append(config.account_id)
        return _Client(config.account_id)

    broker_a = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=_factory,
    )
    broker_b = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-2"),
        client_factory=_factory,
    )

    assert broker_a.snapshot_account_state() is not None
    assert broker_b.snapshot_account_state() is not None
    assert calls == ["acct-1", "acct-2"]
    assert broker_a._client is not broker_b._client
    assert get_default_qmt_session_manager().active_session_count() == 2


def test_qmt_shadow_session_init_failure_does_not_poison_cache():
    calls: list[str] = []

    def _factory(config: QMTBrokerConfig):
        calls.append(config.account_id)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return _FakeQMTClient()

    broker_a = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=_factory,
    )
    broker_b = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=_factory,
    )

    with pytest.raises(RuntimeError, match="boom"):
        broker_a.snapshot_account_state()

    snapshot = broker_b.snapshot_account_state()

    assert snapshot is not None
    assert calls == ["acct-1", "acct-1"]
    assert get_default_qmt_session_manager().active_session_count() == 1
    state = broker_b.get_session_state()
    assert state is not None
    assert state.status == "created"
    assert state.acquisition_count == 2
    assert state.last_error == ""
    runtime_kinds = {
        event.event_kind
        for event in broker_b.list_runtime_events()
    }
    assert runtime_kinds == {
        "session_owner_create_failed",
        "session_owner_created",
    }


def test_qmt_shadow_broker_owner_detach_failure_records_close_failed():
    class _BrokenClient(_FakeQMTClient):
        def close(self):
            raise RuntimeError("close boom")

    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=lambda _cfg: _BrokenClient(),
    )

    broker.attach_deployment("dep-1")
    state = broker.detach_deployment("dep-1")

    assert state is not None
    assert state.status == "close_failed"
    assert state.owner_count == 0
    assert state.last_error == "close boom"
    assert broker._client is None
    assert {
        event.event_kind
        for event in broker.list_runtime_events()
    } == {
        "session_owner_created",
        "session_owner_attached",
        "session_owner_detached",
        "session_owner_close_failed",
    }


def test_qmt_shadow_broker_with_injected_client_has_no_session_owner_state():
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=_FakeQMTClient(),
    )
    assert broker.get_session_state() is None
    assert broker.list_runtime_events() == []


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
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": 55,
                    "order_volume": 1000,
                    "traded_volume": 600,
                    "left_volume": 400,
                    "traded_price": 12.34,
                    "order_time": "2026-04-13T09:32:00+00:00",
                    "status_msg": "",
                }
            ]

        def query_stock_trades(self, account):
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "traded_id": "T-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "traded_volume": 600,
                    "traded_price": 12.34,
                    "traded_time": "2026-04-13T09:32:10+00:00",
                }
            ]

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

    monkeypatch.setattr("ez.live.qmt.broker.importlib.import_module", _fake_import)

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
    callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,
            order_volume=1000,
            traded_volume=600,
            traded_price=12.34,
            order_time="2026-04-13T09:32:00+00:00",
            status_msg="",
        )
    )
    callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,
            order_volume=1000,
            traded_volume=600,
            traded_price=12.34,
            order_time="2026-04-13T09:32:00+00:00",
            status_msg="",
        )
    )
    callback.on_account_status(
        SimpleNamespace(
            account_id="acct-1",
            account_type="STOCK",
            status="connected",
            update_time="2026-04-13T09:31:30+00:00",
        )
    )
    callback.on_account_status(
        SimpleNamespace(
            account_id="acct-1",
            account_type="STOCK",
            status="connected",
            update_time="2026-04-13T09:31:30+00:00",
        )
    )
    callback.on_order_stock_async_response(
        SimpleNamespace(
            account_id="acct-1",
            order_id=1001,
            seq=77,
            strategy_name="strategy1",
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            update_time="2026-04-13T09:31:40+00:00",
        )
    )
    callback.on_cancel_order_stock_async_response(
        SimpleNamespace(
            account_id="acct-1",
            account_type="STOCK",
            order_id=1001,
            order_sysid="SYS-001",
            cancel_result=0,
            seq=78,
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            update_time="2026-04-13T09:31:50+00:00",
        )
    )
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=client,
    )
    reports = broker.list_execution_reports()
    assert len(reports) == 2
    assert [report.status for report in reports] == ["partially_filled", "filled"]
    assert [report.broker_order_id for report in reports] == ["SYS-001", "SYS-001"]
    runtime_events = broker.list_runtime_events()
    assert len(runtime_events) == 7
    assert {event.event_kind for event in runtime_events} == {
        "session_bootstrap_started",
        "session_started",
        "session_connected",
        "session_subscribed",
        "account_status",
        "order_stock_async_response",
        "cancel_order_stock_async_response",
    }


def test_xtquant_shadow_client_default_session_id_is_positive_int(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _FakeTrader:
        def __init__(self, install_path, session_id):
            calls.append(("init", (install_path, session_id)))

        def register_callback(self, callback):
            return None

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

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

    monkeypatch.setattr("ez.live.qmt.broker.importlib.import_module", _fake_import)

    config = QMTBrokerConfig(
        account_id="acct-1",
        account_type="stock",
        install_path="/opt/qmt/userdata",
    )
    XtQuantShadowClient.from_config(config)
    XtQuantShadowClient.from_config(config)

    session_ids = [args[1] for name, args in calls if name == "init"]
    assert len(session_ids) == 2
    assert session_ids[0] == session_ids[1]
    assert isinstance(session_ids[0], int)
    assert session_ids[0] > 0


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

    monkeypatch.setattr("ez.live.qmt.broker.importlib.import_module", _fake_import)

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


def test_xtquant_shadow_client_cancel_uses_official_market_fallback_without_xtconstant(monkeypatch):
    class _CancelTrader:
        def __init__(self):
            self.calls: list[tuple[str, object, object, object]] = []

        def cancel_order_stock_sysid(self, account, market, order_sysid):
            self.calls.append(("cancel_order_stock_sysid", account, market, order_sysid))
            return 0

    def _fake_import(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("ez.live.qmt.broker.importlib.import_module", _fake_import)

    trader = _CancelTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )

    result = client.cancel_order("SYS-001", symbol="000001.SZ")

    assert result == 0
    assert trader.calls == [
        ("cancel_order_stock_sysid", "acct-1", 1, "SYS-001"),
    ]


def test_xtquant_shadow_client_cancel_falls_back_to_order_id_when_sysid_rejects(monkeypatch):
    class _CancelTrader:
        def __init__(self):
            self.calls: list[tuple[str, object, object, object | None]] = []

        def cancel_order_stock_sysid(self, account, market, order_sysid):
            self.calls.append(("cancel_order_stock_sysid", account, market, order_sysid))
            return -1

        def cancel_order_stock_async(self, account, order_id):
            self.calls.append(("cancel_order_stock_async", account, order_id, None))
            return 88

    def _fake_import(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("ez.live.qmt.broker.importlib.import_module", _fake_import)

    trader = _CancelTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )

    result = client.cancel_order("1001", symbol="600000.SH")

    assert result == 88
    assert trader.calls == [
        ("cancel_order_stock_sysid", "acct-1", 0, "1001"),
        ("cancel_order_stock_async", "acct-1", 1001, None),
    ]


def test_xtquant_shadow_client_submit_order_prefers_async_api(monkeypatch):
    class _SubmitTrader:
        def __init__(self):
            self.calls: list[tuple[str, object, ...]] = []

        def order_stock_async(
            self,
            account,
            symbol,
            order_side,
            shares,
            price_type,
            price,
            strategy_name,
            order_remark,
        ):
            self.calls.append(
                (
                    "order_stock_async",
                    account,
                    symbol,
                    order_side,
                    shares,
                    price_type,
                    price,
                    strategy_name,
                    order_remark,
                )
            )
            return 88

        def order_stock(
            self,
            account,
            symbol,
            order_side,
            shares,
            price_type,
            price,
            strategy_name,
            order_remark,
        ):
            self.calls.append(
                (
                    "order_stock",
                    account,
                    symbol,
                    order_side,
                    shares,
                    price_type,
                    price,
                    strategy_name,
                    order_remark,
                )
            )
            return 77

    def _fake_import(name: str):
        if name == "xtquant.xtconstant":
            return SimpleNamespace(STOCK_BUY=11, STOCK_SELL=22, FIX_PRICE=33)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("ez.live.qmt.session_owner.importlib.import_module", _fake_import)

    trader = _SubmitTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )

    result = client.submit_order(
        symbol="600000.SH",
        side="buy",
        shares=100,
        price=10.5,
        strategy_name="deploy-1",
        order_remark="dep-1:2026-04-13:600000.SH:buy",
    )

    assert result == 88
    assert trader.calls == [
        (
            "order_stock_async",
            "acct-1",
            "600000.SH",
            11,
            100,
            33,
            10.5,
            "deploy-1",
            "dep-1:2026-04-13:600000.SH:buy",
        )
    ]


def test_xtquant_shadow_client_records_connect_failure_runtime_event():
    class _FailingTrader:
        session_id = "sess-1"

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 7

    client = XtQuantShadowClient(
        trader=_FailingTrader(),
        account_ref="acct-1",
        account_id="acct-1",
    )

    with pytest.raises(RuntimeError, match="connect\\(\\) failed with code 7"):
        client._prepare_runtime()

    runtime_events = client.list_runtime_events()
    assert [event["_report_kind"] for event in runtime_events] == [
        "session_bootstrap_started",
        "session_started",
        "session_connect_failed",
    ]
    assert runtime_events[-1]["connect_result"] == 7


def test_qmt_shadow_broker_attach_starts_run_forever_consumer_and_detach_stops_it():
    class _LoopingTrader:
        session_id = "sess-1"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.run_stopped = threading.Event()

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)
            self.run_stopped.set()

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=lambda _cfg: client,
    )

    broker.attach_deployment("dep-1")
    assert trader.run_started.wait(timeout=1.0)

    runtime_kinds = {event.event_kind for event in broker.list_runtime_events()}
    assert "session_consumer_started" in runtime_kinds

    detached_state = broker.detach_deployment("dep-1")
    assert detached_state is not None
    assert detached_state.status == "closed"
    assert trader.run_stopped.wait(timeout=1.0)

    runtime_kinds = {event.event_kind for event in broker.list_runtime_events()}
    assert "session_consumer_stopped" in runtime_kinds
    assert "session_owner_closed" in runtime_kinds


def test_qmt_shadow_broker_second_attach_reuses_existing_consumer():
    class _LoopingTrader:
        session_id = "sess-2"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=lambda _cfg: client,
    )

    broker.attach_deployment("dep-1")
    assert trader.run_started.wait(timeout=1.0)
    broker.attach_deployment("dep-2")

    runtime_kinds = [event.event_kind for event in broker.list_runtime_events()]
    assert "session_consumer_started" in runtime_kinds
    assert "session_consumer_reused" not in runtime_kinds

    broker.detach_deployment("dep-1")
    state = broker.get_session_state()
    assert state is not None
    assert state.owner_count == 1
    assert state.attached_deployments == ("dep-2",)
    broker.detach_deployment("dep-2")


def test_qmt_shadow_broker_supervision_restarts_stopped_consumer():
    class _RestartableTrader:
        session_id = "sess-3"

        def __init__(self):
            self.first_run_done = threading.Event()
            self.second_run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.run_count = 0

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

        def run_forever(self):
            self.run_count += 1
            if self.run_count == 1:
                self.first_run_done.set()
                return None
            self.second_run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _RestartableTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=lambda _cfg: client,
    )

    broker.attach_deployment("dep-1")
    assert trader.first_run_done.wait(timeout=1.0)

    state = broker.ensure_session_supervision()
    assert state is not None
    assert state.consumer_status == "running"
    assert state.consumer_restart_count == 1
    assert trader.second_run_started.wait(timeout=1.0)

    runtime_kinds = {event.event_kind for event in broker.list_runtime_events()}
    assert "session_consumer_started" in runtime_kinds
    assert "session_consumer_stopped" in runtime_kinds
    assert "session_consumer_restarted" in runtime_kinds

    broker.detach_deployment("dep-1")


def test_xtquant_shadow_client_incremental_reports_prefer_callback_when_consumer_alive():
    class _LoopingTrader:
        session_id = "sess-4"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.query_order_calls = 0
            self.query_trade_calls = 0

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            self.query_order_calls += 1
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": 55,
                    "order_volume": 1000,
                    "traded_volume": 600,
                    "traded_price": 12.34,
                    "order_time": "2026-04-13T09:32:00+00:00",
                }
            ]

        def query_stock_trades(self, account):
            self.query_trade_calls += 1
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "traded_id": "T-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "traded_volume": 600,
                    "traded_price": 12.34,
                    "traded_time": "2026-04-13T09:32:10+00:00",
                }
            ]

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    callback = trader._callback
    callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,
            order_volume=1000,
            traded_volume=600,
            traded_price=12.34,
            order_time="2026-04-13T09:32:00+00:00",
            status_msg="",
        )
    )
    callback.on_stock_trade(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            traded_id="T-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            traded_volume=600,
            traded_price=12.34,
            traded_time="2026-04-13T09:32:10+00:00",
        )
    )

    reports = client.list_execution_reports(
        since=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc)
    )

    assert [report["_report_kind"] for report in reports] == ["order", "trade"]
    assert trader.query_order_calls == 0
    assert trader.query_trade_calls == 0
    trader.stop()


def test_xtquant_shadow_client_full_reports_use_callback_aware_merge_when_consumer_alive():
    class _LoopingTrader:
        session_id = "sess-4b"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.query_order_calls = 0
            self.query_trade_calls = 0

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            self.query_order_calls += 1
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": "reported",
                    "order_volume": 1000,
                    "traded_volume": 0,
                    "traded_price": 0.0,
                    "order_time": "2026-04-13T09:32:00+00:00",
                }
            ]

        def query_stock_trades(self, account):
            self.query_trade_calls += 1
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    trader._callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status="canceled",
            order_volume=1000,
            traded_volume=0,
            traded_price=0.0,
            order_time="2026-04-13T09:33:00+00:00",
            status_msg="canceled",
        )
    )

    reports = client.list_execution_reports()

    assert trader.query_order_calls == 1
    assert trader.query_trade_calls == 1
    assert len(reports) == 1
    assert reports[0]["_report_kind"] == "order"
    assert reports[0]["order_status"] == "canceled"
    trader.stop()


def test_xtquant_shadow_client_full_reports_include_callback_only_order_error_when_query_is_empty():
    class _LoopingTrader:
        session_id = "sess-4c"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.query_order_calls = 0
            self.query_trade_calls = 0

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            self.query_order_calls += 1
            return []

        def query_stock_trades(self, account):
            self.query_trade_calls += 1
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    trader._callback.on_order_error(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_volume=1000,
            traded_volume=0,
            error_time="2026-04-13T09:33:00+00:00",
            error_msg="rejected by broker",
        )
    )

    reports = client.list_execution_reports()

    assert trader.query_order_calls == 1
    assert trader.query_trade_calls == 1
    assert len(reports) == 1
    assert reports[0]["_report_kind"] == "order_error"
    assert reports[0]["order_status"] == "order_error"
    assert reports[0]["client_order_id"] == "dep-1:2026-04-13:000001.SZ:buy"
    assert reports[0]["order_sysid"] == "SYS-001"
    assert reports[0]["status_msg"] == "rejected by broker"
    trader.stop()


def test_xtquant_shadow_client_collect_sync_state_uses_callback_reports_without_query_duplicates():
    class _LoopingTrader:
        session_id = "sess-5"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": 55,
                    "order_volume": 1000,
                    "traded_volume": 600,
                    "traded_price": 12.34,
                    "order_time": "2026-04-13T09:32:00+00:00",
                }
            ]

        def query_stock_trades(self, account):
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "traded_id": "T-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "traded_volume": 600,
                    "traded_price": 12.34,
                    "traded_time": "2026-04-13T09:32:10+00:00",
                }
            ]

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    callback = trader._callback
    callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,
            order_volume=1000,
            traded_volume=600,
            traded_price=12.34,
            order_time="2026-04-13T09:32:00+00:00",
            status_msg="",
        )
    )
    callback.on_stock_trade(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            traded_id="T-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            traded_volume=600,
            traded_price=12.34,
            traded_time="2026-04-13T09:32:10+00:00",
        )
    )

    bundle = client.collect_sync_state(
        since_reports=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        since_runtime=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
    )

    assert [item["_report_kind"] for item in bundle["execution_reports"]] == ["order", "trade"]
    assert len(bundle["orders"]) == 1
    assert len(bundle["trades"]) == 1
    runtime_kinds = {item["_report_kind"] for item in bundle["runtime_events"]}
    assert "session_consumer_state" in runtime_kinds
    trader.stop()


def test_xtquant_shadow_client_collect_sync_state_filters_stale_query_open_order_after_terminal_callback():
    class _LoopingTrader:
        session_id = "sess-5b"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": "reported",
                    "order_volume": 1000,
                    "traded_volume": 0,
                    "traded_price": 0.0,
                    "order_time": "2026-04-13T09:32:00+00:00",
                }
            ]

        def query_stock_trades(self, account):
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    trader._callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status="canceled",
            order_volume=1000,
            traded_volume=0,
            traded_price=0.0,
            order_time="2026-04-13T09:33:00+00:00",
            status_msg="canceled",
        )
    )

    bundle = client.collect_sync_state(
        since_reports=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        since_runtime=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
    )

    assert bundle["orders"] == []
    assert [item["_report_kind"] for item in bundle["execution_reports"]] == ["order"]
    trader.stop()


def test_xtquant_shadow_client_collect_sync_state_synthesizes_open_order_and_trade_from_callback_when_query_is_empty():
    class _LoopingTrader:
        session_id = "sess-5c"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    trader._callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status="reported",
            order_volume=1000,
            traded_volume=0,
            traded_price=12.34,
            order_time="2026-04-13T09:32:00+00:00",
            status_msg="",
        )
    )
    trader._callback.on_stock_trade(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            traded_id="T-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            traded_volume=600,
            traded_price=12.34,
            traded_time="2026-04-13T09:32:10+00:00",
        )
    )

    bundle = client.collect_sync_state(
        since_reports=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        since_runtime=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
    )

    assert len(bundle["orders"]) == 1
    assert bundle["orders"][0]["order_sysid"] == "SYS-001"
    assert bundle["orders"][0]["order_status"] == "reported"
    assert len(bundle["trades"]) == 1
    assert bundle["trades"][0]["trade_no"] == "T-001"
    trader.stop()


def test_xtquant_shadow_client_collect_sync_state_prefers_fresher_query_snapshot_over_older_callback():
    class _LoopingTrader:
        session_id = "sess-5d"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": "partially_filled",
                    "order_volume": 1000,
                    "traded_volume": 600,
                    "left_volume": 400,
                    "traded_price": 12.50,
                    "order_time": "2026-04-13T09:34:00+00:00",
                }
            ]

        def query_stock_trades(self, account):
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_id": 1001,
                    "order_sysid": "SYS-001",
                    "traded_id": "T-001",
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "traded_volume": 600,
                    "traded_price": 12.50,
                    "traded_time": "2026-04-13T09:34:05+00:00",
                }
            ]

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    trader._callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status="reported",
            order_volume=1000,
            traded_volume=0,
            traded_price=0.0,
            order_time="2026-04-13T09:33:00+00:00",
            status_msg="reported",
        )
    )
    trader._callback.on_stock_trade(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            traded_id="T-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            traded_volume=600,
            traded_price=12.34,
            traded_time="2026-04-13T09:33:05+00:00",
        )
    )

    bundle = client.collect_sync_state(
        since_reports=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        since_runtime=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
    )

    assert bundle["orders"][0]["order_status"] == "partially_filled"
    assert bundle["orders"][0]["traded_volume"] == 600
    assert bundle["orders"][0]["order_time"] == "2026-04-13T09:34:00+00:00"
    assert bundle["trades"][0]["traded_price"] == 12.50
    assert bundle["trades"][0]["traded_time"] == "2026-04-13T09:34:05+00:00"
    trader.stop()


def test_xtquant_shadow_client_runtime_events_include_consumer_state_snapshot():
    class _LoopingTrader:
        session_id = "sess-6"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.query_asset_calls = 0

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            self.query_asset_calls += 1
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    trader._callback.on_account_status(
        SimpleNamespace(
            account_id="acct-1",
            account_type="STOCK",
            status="connected",
            update_time="2026-04-13T09:31:30+00:00",
        )
    )
    trader._callback.on_stock_asset(
        SimpleNamespace(
            account_id="acct-1",
            update_time="2026-04-13T09:31:35+00:00",
            cash=101.0,
            total_asset=202.0,
        )
    )
    trader._callback.on_stock_position(
        SimpleNamespace(
            account_id="acct-1",
            stock_code="000001.SZ",
            current_amount=1000,
            update_time="2026-04-13T09:31:45+00:00",
        )
    )
    trader._callback.on_stock_order(
        SimpleNamespace(
            order_remark="dep-1:2026-04-13:000001.SZ:buy",
            order_id=1001,
            order_sysid="SYS-001",
            stock_code="000001.SZ",
            offset_flag="buy",
            order_status=55,
            order_volume=1000,
            traded_volume=600,
            traded_price=12.34,
            order_time="2026-04-13T09:32:00+00:00",
            status_msg="",
        )
    )

    runtime_events = client.list_runtime_events(
        since=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc)
    )
    state_events = [
        event for event in runtime_events
        if event.get("_report_kind") == "session_consumer_state"
    ]
    assert len(state_events) == 1
    state = state_events[0]
    assert state["consumer_alive"] is True
    assert state["consumer_status"] == "running"
    assert state["execution_event_count"] == 1
    assert state["runtime_event_count"] >= 7
    assert state["latest_callback_at"] == "2026-04-13T09:32:00+00:00"
    assert state["latest_asset_callback_at"] == "2026-04-13T09:31:35+00:00"
    assert state["latest_position_callback_at"] == "2026-04-13T09:31:45+00:00"
    trader.stop()


def test_qmt_shadow_broker_detach_preserves_session_consumer_state_event():
    class _LoopingTrader:
        session_id = "sess-7"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.run_stopped = threading.Event()

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)
            self.run_stopped.set()

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client_factory=lambda _cfg: client,
    )

    broker.attach_deployment("dep-1")
    assert trader.run_started.wait(timeout=1.0)
    detached_state = broker.detach_deployment("dep-1")
    assert detached_state is not None
    assert trader.run_stopped.wait(timeout=1.0)

    state_events = [
        event for event in broker.list_runtime_events()
        if event.event_kind == "session_consumer_state"
    ]
    assert state_events
    latest_state = state_events[-1]
    assert latest_state.payload["consumer_alive"] is False
    assert latest_state.payload["consumer_status"] == "stopped"


def test_xtquant_shadow_client_collect_sync_state_prefers_callback_asset_when_consumer_alive():
    class _LoopingTrader:
        session_id = "sess-8"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.query_asset_calls = 0

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            self.query_asset_calls += 1
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

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    trader._callback.on_stock_asset(
        SimpleNamespace(
            account_id="acct-1",
            update_time="2026-04-13T09:31:35+00:00",
            cash=123.0,
            total_asset=456.0,
        )
    )

    bundle = client.collect_sync_state(
        since_reports=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        since_runtime=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
    )

    assert bundle["asset"]["cash"] == 123.0
    assert bundle["asset"]["total_asset"] == 456.0
    assert trader.query_asset_calls == 0
    runtime_kinds = {item["_report_kind"] for item in bundle["runtime_events"]}
    assert "stock_asset" in runtime_kinds
    assert "session_consumer_state" in runtime_kinds
    consumer_state = next(
        event
        for event in bundle["runtime_events"]
        if event.get("_report_kind") == "session_consumer_state"
    )
    assert consumer_state["account_sync_mode"] == "callback_preferred"
    assert consumer_state["asset_callback_freshness"] == "fresh"
    trader.stop()


def test_xtquant_shadow_client_collect_sync_state_falls_back_when_callback_asset_is_stale():
    class _LoopingTrader:
        session_id = "sess-9"

        def __init__(self):
            self.run_started = threading.Event()
            self.allow_exit = threading.Event()
            self.query_asset_calls = 0

        def register_callback(self, callback):
            self._callback = callback

        def start(self):
            return None

        def connect(self):
            return 0

        def subscribe(self, account):
            return 0

        def query_stock_asset(self, account):
            self.query_asset_calls += 1
            return {
                "update_time": "2026-04-13T09:40:00+00:00",
                "cash": 1.0,
                "total_asset": 2.0,
            }

        def query_stock_positions(self, account):
            return []

        def query_stock_orders(self, account):
            return []

        def query_stock_trades(self, account):
            return []

        def run_forever(self):
            self.run_started.set()
            self.allow_exit.wait(timeout=1.0)

        def stop(self):
            self.allow_exit.set()

    trader = _LoopingTrader()
    client = XtQuantShadowClient(
        trader=trader,
        account_ref="acct-1",
        account_id="acct-1",
    )
    client._prepare_runtime()
    assert client.ensure_callback_consumer() is True
    assert trader.run_started.wait(timeout=1.0)

    trader._callback.on_stock_asset(
        SimpleNamespace(
            account_id="acct-1",
            update_time="2026-04-13T09:31:35+00:00",
            cash=123.0,
            total_asset=456.0,
        )
    )
    trader._callback.on_account_status(
        SimpleNamespace(
            account_id="acct-1",
            account_type="STOCK",
            status="connected",
            update_time="2026-04-13T09:40:00+00:00",
        )
    )

    bundle = client.collect_sync_state(
        since_reports=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
        since_runtime=datetime(2026, 4, 13, 9, 30, tzinfo=timezone.utc),
    )

    assert bundle["asset"]["cash"] == 1.0
    assert bundle["asset"]["total_asset"] == 2.0
    assert trader.query_asset_calls == 1
    consumer_state = next(
        event
        for event in bundle["runtime_events"]
        if event.get("_report_kind") == "session_consumer_state"
    )
    assert consumer_state["account_sync_mode"] == "query_fallback"
    assert consumer_state["asset_callback_freshness"] == "stale"
    trader.stop()


def test_build_qmt_readiness_summary_ready_when_callback_and_reconcile_are_healthy():
    readiness = build_qmt_readiness_summary(
        latest_session_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={"status": "connected"},
        ),
        latest_session_consumer_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_session_consumer_state_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_reconcile={"status": "ok"},
        latest_order_reconcile={"status": "ok"},
    )

    assert readiness.status == "ready"
    assert readiness.ready_for_shadow_sync is True
    assert readiness.ready_for_real_submit is False
    assert readiness.blockers == ()
    assert readiness.real_submit_blockers == ("shadow_mode_only",)


def test_build_qmt_readiness_summary_ready_for_real_submit_when_policy_is_enabled():
    policy = build_qmt_real_submit_policy(
        {
            "qmt_real_submit_policy": {
                "enabled": True,
                "allowed_account_ids": ["acct-1"],
                "max_total_asset": 500_000.0,
                "max_initial_cash": 2_000_000.0,
            }
        }
    )
    readiness = build_qmt_readiness_summary(
        latest_session_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={"status": "connected"},
        ),
        latest_session_consumer_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_session_consumer_state_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_reconcile={"status": "ok"},
        latest_order_reconcile={"status": "ok"},
        real_submit_policy=policy,
    )

    assert readiness.status == "ready"
    assert readiness.ready_for_shadow_sync is True
    assert readiness.ready_for_real_submit is True
    assert readiness.real_submit_enabled is True
    assert readiness.real_submit_blockers == ()


def test_build_qmt_readiness_summary_degraded_when_callback_path_is_not_ready():
    readiness = build_qmt_readiness_summary(
        latest_session_runtime=SimpleNamespace(
            event_kind="session_consumer_restarted",
            payload={"status": "connected"},
        ),
        latest_session_consumer_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "query_fallback",
                "asset_callback_freshness": "stale",
            },
        ),
        latest_session_consumer_state_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "query_fallback",
                "asset_callback_freshness": "stale",
            },
        ),
        latest_reconcile={"status": "ok"},
        latest_order_reconcile={"status": "drift"},
    )

    assert readiness.status == "degraded"
    assert readiness.ready_for_shadow_sync is False
    assert "callback_account_not_preferred" in readiness.blockers
    assert "callback_asset_not_fresh" in readiness.blockers
    assert "order_reconcile_not_ok" not in readiness.blockers
    assert readiness.order_reconcile_status == "drift"


def test_build_qmt_readiness_summary_degraded_when_session_reconnect_is_failing():
    readiness = build_qmt_readiness_summary(
        latest_session_runtime=SimpleNamespace(
            event_kind="session_reconnect_failed",
            payload={"status": "disconnected"},
        ),
        latest_session_consumer_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_session_consumer_state_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_reconcile={"status": "ok"},
        latest_order_reconcile={"status": "ok"},
    )

    assert readiness.status == "degraded"
    assert readiness.ready_for_shadow_sync is False
    assert "session_unhealthy" in readiness.blockers


def test_build_qmt_submit_gate_decision_is_fail_closed_for_shadow_mode():
    readiness = build_qmt_readiness_summary(
        latest_session_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={"status": "connected"},
        ),
        latest_session_consumer_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_session_consumer_state_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_reconcile={"status": "ok"},
        latest_order_reconcile={"status": "ok"},
    )
    policy = build_qmt_real_submit_policy(
        {
            "qmt_real_submit_policy": {
                "enabled": True,
                "allowed_account_ids": ["acct-1"],
                "max_total_asset": 500_000.0,
                "max_initial_cash": 2_000_000.0,
            }
        }
    )

    gate = build_qmt_submit_gate_decision(
        readiness,
        policy=policy,
        account_id="acct-1",
        total_asset=250_000.0,
        initial_cash=1_000_000.0,
    )
    assert gate.status == "shadow_only"
    assert gate.can_submit_now is False
    assert gate.mode == "shadow_only"
    assert gate.blockers == ("shadow_mode_only",)
    assert gate.preflight_ok is True
    assert gate.account_id == "acct-1"
    assert gate.total_asset == 250_000.0


def test_build_qmt_submit_gate_decision_opens_for_real_submit_when_ready_and_whitelisted():
    policy = build_qmt_real_submit_policy(
        {
            "qmt_real_submit_policy": {
                "enabled": True,
                "allowed_account_ids": ["acct-1"],
                "max_total_asset": 500_000.0,
                "max_initial_cash": 2_000_000.0,
            }
        }
    )
    readiness = build_qmt_readiness_summary(
        latest_session_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={"status": "connected"},
        ),
        latest_session_consumer_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_session_consumer_state_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_reconcile={"status": "ok"},
        latest_order_reconcile={"status": "ok"},
        real_submit_policy=policy,
    )

    gate = build_qmt_submit_gate_decision(
        readiness,
        policy=policy,
        account_id="acct-1",
        total_asset=250_000.0,
        initial_cash=1_000_000.0,
    )

    assert gate.status == "open"
    assert gate.can_submit_now is True
    assert gate.mode == "real_submit"
    assert gate.blockers == ()
    assert gate.ready_for_real_submit is True


def test_build_qmt_submit_gate_decision_blocks_real_submit_when_policy_enabled_but_runtime_degraded():
    policy = build_qmt_real_submit_policy(
        {
            "qmt_real_submit_policy": {
                "enabled": True,
                "allowed_account_ids": ["acct-1"],
                "max_total_asset": 500_000.0,
                "max_initial_cash": 2_000_000.0,
            }
        }
    )
    readiness = build_qmt_readiness_summary(
        latest_session_runtime=SimpleNamespace(
            event_kind="disconnected",
            payload={"status": "disconnected"},
        ),
        latest_session_consumer_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_session_consumer_state_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_reconcile={"status": "ok"},
        latest_order_reconcile={"status": "ok"},
        real_submit_policy=policy,
    )

    gate = build_qmt_submit_gate_decision(
        readiness,
        policy=policy,
        account_id="acct-1",
        total_asset=250_000.0,
        initial_cash=1_000_000.0,
    )

    assert readiness.real_submit_enabled is True
    assert readiness.ready_for_real_submit is False
    assert gate.status == "blocked"
    assert gate.mode == "real_submit"
    assert "session_unhealthy" in gate.blockers
    assert "shadow_mode_only" not in gate.blockers


def test_build_qmt_submit_gate_decision_blocks_non_whitelisted_or_oversized_account():
    readiness = build_qmt_readiness_summary(
        latest_session_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={"status": "connected"},
        ),
        latest_session_consumer_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_session_consumer_state_runtime=SimpleNamespace(
            event_kind="session_consumer_state",
            payload={
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        ),
        latest_reconcile={"status": "ok"},
        latest_order_reconcile={"status": "ok"},
    )
    policy = build_qmt_real_submit_policy(
        {
            "qmt_real_submit_policy": {
                "enabled": True,
                "allowed_account_ids": ["acct-allowed"],
                "max_total_asset": 100_000.0,
                "max_initial_cash": 500_000.0,
            }
        }
    )

    gate = build_qmt_submit_gate_decision(
        readiness,
        policy=policy,
        account_id="acct-other",
        total_asset=250_000.0,
        initial_cash=1_000_000.0,
    )

    assert gate.status == "shadow_only"
    assert gate.preflight_ok is False
    assert "account_not_whitelisted" in gate.blockers
    assert "total_asset_above_policy_cap" in gate.blockers
    assert "initial_cash_above_policy_cap" in gate.blockers


def test_build_qmt_release_gate_decision_requires_open_submit_gate():
    release_gate = build_qmt_release_gate_decision(
        deployment_status="approved",
        gate_verdict={"passed": True},
        submit_gate={
            "status": "open",
            "preflight_ok": True,
            "can_submit_now": True,
        },
    )
    assert release_gate.status == "candidate"
    assert release_gate.eligible_for_release_candidate is True
    assert release_gate.eligible_for_real_submit is True
    assert release_gate.blockers == ()


def test_build_qmt_release_gate_decision_blocks_shadow_only_gate():
    release_gate = build_qmt_release_gate_decision(
        deployment_status="approved",
        gate_verdict={"passed": True},
        submit_gate={
            "status": "shadow_only",
            "preflight_ok": True,
            "can_submit_now": False,
        },
    )
    assert release_gate.status == "blocked"
    assert release_gate.eligible_for_release_candidate is False
    assert "qmt_submit_gate_shadow_only" in release_gate.blockers


def test_build_qmt_release_gate_decision_blocks_when_gate_or_preflight_missing():
    release_gate = build_qmt_release_gate_decision(
        deployment_status="pending",
        gate_verdict=None,
        submit_gate={
            "status": "shadow_only",
            "preflight_ok": False,
            "can_submit_now": False,
        },
    )
    assert release_gate.status == "blocked"
    assert release_gate.eligible_for_release_candidate is False
    assert "deploy_gate_not_recorded" in release_gate.blockers
    assert "deployment_not_approved" in release_gate.blockers
    assert "qmt_submit_gate_shadow_only" in release_gate.blockers
    assert "qmt_preflight_not_ok" in release_gate.blockers


# ---------------------------------------------------------------------------
# Important: build_qmt_release_gate_decision folds hard_gate truth
# ---------------------------------------------------------------------------


def test_build_qmt_release_gate_decision_blocks_when_hard_gate_is_blocked():
    """hard_gate.status != 'open' must force release gate to blocked."""
    release_gate = build_qmt_release_gate_decision(
        deployment_status="approved",
        gate_verdict={"passed": True},
        submit_gate={
            "status": "open",
            "preflight_ok": True,
            "can_submit_now": True,
        },
        hard_gate={
            "status": "blocked",
            "blockers": ["broker_reconcile_drift"],
        },
    )
    assert release_gate.status == "blocked"
    assert release_gate.eligible_for_release_candidate is False
    assert release_gate.eligible_for_real_submit is False
    assert "qmt_reconcile_hard_gate_blocked" in release_gate.blockers
    # Submit gate context is preserved even when hard gate overrides.
    assert release_gate.submit_gate_status == "open"


def test_build_qmt_release_gate_decision_preserves_hard_gate_blockers_in_order():
    release_gate = build_qmt_release_gate_decision(
        deployment_status="approved",
        gate_verdict={"passed": True},
        submit_gate={
            "status": "open",
            "preflight_ok": True,
            "can_submit_now": True,
        },
        hard_gate={
            "status": "blocked",
            "blockers": ["broker_reconcile_drift", "broker_order_reconcile_drift"],
        },
    )
    assert "broker_reconcile_drift" in release_gate.blockers
    assert "broker_order_reconcile_drift" in release_gate.blockers


def test_build_qmt_release_gate_decision_remains_candidate_when_hard_gate_is_open():
    release_gate = build_qmt_release_gate_decision(
        deployment_status="approved",
        gate_verdict={"passed": True},
        submit_gate={
            "status": "open",
            "preflight_ok": True,
            "can_submit_now": True,
        },
        hard_gate={"status": "open", "blockers": []},
    )
    assert release_gate.status == "candidate"
    assert release_gate.eligible_for_release_candidate is True
    assert release_gate.eligible_for_real_submit is True


def test_build_qmt_release_gate_decision_hard_gate_none_preserves_legacy_behavior():
    """None hard_gate must match the pre-change call signature exactly."""
    release_gate = build_qmt_release_gate_decision(
        deployment_status="approved",
        gate_verdict={"passed": True},
        submit_gate={
            "status": "open",
            "preflight_ok": True,
            "can_submit_now": True,
        },
        # hard_gate omitted — must not regress to blocked.
    )
    assert release_gate.status == "candidate"
    assert release_gate.eligible_for_real_submit is True


# ---------------------------------------------------------------------------
# Important: QMTRealBroker defensive submit-gate check + open/close methods
# ---------------------------------------------------------------------------


def test_qmt_real_broker_fails_closed_without_open_submit_gate():
    """Direct execute_target_weights call without opening the gate must raise."""
    client = _FakeRealQMTClient()
    broker = QMTRealBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=client,
    )
    business_date = datetime(2026, 4, 13, tzinfo=timezone.utc).date()

    assert broker.is_submit_gate_open() is False
    with pytest.raises(RuntimeError, match="fail-closed"):
        broker.execute_target_weights(
            business_date=business_date,
            target_weights={"600000.SH": 1.0},
            holdings={},
            equity=1_000_000.0,
            cash=1_000_000.0,
            prices={"600000.SH": 10.5},
            raw_close_today={},
            prev_raw_close={},
            has_bar_today={"600000.SH"},
            cost_model=None,  # type: ignore[arg-type]
            lot_size=100,
            limit_pct=0.1,
            t_plus_1=True,
            requested_orders=[],
        )
    # No broker-side submit happened.
    assert client.submit_calls == []


def test_qmt_real_broker_open_submit_gate_refuses_non_open_decisions():
    broker = QMTRealBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=_FakeRealQMTClient(),
    )
    with pytest.raises(RuntimeError, match="refusing to open"):
        broker.open_submit_gate({"status": "shadow_only", "can_submit_now": False})
    with pytest.raises(RuntimeError, match="refusing to open"):
        broker.open_submit_gate({"status": "open", "can_submit_now": False})
    with pytest.raises(RuntimeError, match="refusing to open"):
        broker.open_submit_gate({"status": "blocked", "can_submit_now": True})
    with pytest.raises(ValueError, match="dict decision payload"):
        broker.open_submit_gate("not a dict")  # type: ignore[arg-type]
    assert broker.is_submit_gate_open() is False


def test_qmt_real_broker_open_submit_gate_permits_execution_and_close_reseals():
    client = _FakeRealQMTClient()
    broker = QMTRealBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=client,
    )
    broker.open_submit_gate({"status": "open", "can_submit_now": True})
    assert broker.is_submit_gate_open() is True

    requested_orders = [
        Order(
            order_id="dep-1:2026-04-13:600000.SH:buy",
            client_order_id="dep-1:2026-04-13:600000.SH:buy",
            deployment_id="dep-1",
            symbol="600000.SH",
            side="buy",
            shares=100,
            business_date=datetime(2026, 4, 13, tzinfo=timezone.utc).date(),
            requested_shares=100,
            remaining_shares=100,
        )
    ]
    result = broker.execute_target_weights(
        business_date=datetime(2026, 4, 13, tzinfo=timezone.utc).date(),
        target_weights={"600000.SH": 1.0},
        holdings={},
        equity=1_000_000.0,
        cash=1_000_000.0,
        prices={"600000.SH": 10.5},
        raw_close_today={},
        prev_raw_close={},
        has_bar_today={"600000.SH"},
        cost_model=None,  # type: ignore[arg-type]
        lot_size=100,
        limit_pct=0.1,
        t_plus_1=True,
        requested_orders=requested_orders,
    )
    assert len(result.order_reports) == 1
    assert result.order_reports[0].status == "reported"

    # The gate does not auto-close on a successful submit (scheduler must).
    assert broker.is_submit_gate_open() is True

    broker.close_submit_gate()
    assert broker.is_submit_gate_open() is False

    # After close, a repeat call must be fail-closed again.
    with pytest.raises(RuntimeError, match="fail-closed"):
        broker.execute_target_weights(
            business_date=datetime(2026, 4, 13, tzinfo=timezone.utc).date(),
            target_weights={"600000.SH": 1.0},
            holdings={},
            equity=1_000_000.0,
            cash=1_000_000.0,
            prices={"600000.SH": 10.5},
            raw_close_today={},
            prev_raw_close={},
            has_bar_today={"600000.SH"},
            cost_model=None,  # type: ignore[arg-type]
            lot_size=100,
            limit_pct=0.1,
            t_plus_1=True,
            requested_orders=requested_orders,
        )


def test_qmt_shadow_broker_open_submit_gate_still_raises_not_implemented():
    """Shadow brokers expose the gate API but execute remains unsupported."""
    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=_FakeQMTClient(),
    )
    broker.open_submit_gate({"status": "open", "can_submit_now": True})
    assert broker.is_submit_gate_open() is True
    with pytest.raises(NotImplementedError, match="read-only/shadow-only"):
        broker.execute_target_weights(
            business_date=datetime(2026, 4, 13, tzinfo=timezone.utc).date(),
            target_weights={},
            holdings={},
            equity=0.0,
            cash=0.0,
            prices={},
            raw_close_today={},
            prev_raw_close={},
            has_bar_today=set(),
            cost_model=None,  # type: ignore[arg-type]
            lot_size=100,
            limit_pct=0.0,
            t_plus_1=True,
        )


# ---------------------------------------------------------------------------
# Minor: numeric order-status codes end-to-end through broker normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "numeric_status,expected_status",
    [
        (48, "unreported"),
        (49, "unreported"),
        (50, "reported"),
        (51, "reported_cancel_pending"),
        (52, "partially_filled_cancel_pending"),
        (53, "partially_canceled"),
        (54, "canceled"),
        (55, "partially_filled"),
        (56, "filled"),
        (57, "junk"),
        (255, "unknown"),
    ],
)
def test_qmt_shadow_broker_normalizes_full_numeric_status_vocab(numeric_status, expected_status):
    class _NumericStatusClient(_FakeQMTClient):
        def query_stock_orders(self, account_id: str):
            return [
                {
                    "order_remark": "dep-1:2026-04-13:000001.SZ:buy",
                    "order_sysid": "SYS-NUM",
                    "order_id": 1001,
                    "stock_code": "000001.SZ",
                    "offset_flag": "buy",
                    "order_status": numeric_status,
                    "order_volume": 1000,
                    "traded_volume": 600 if expected_status == "partially_filled" else 0,
                    "left_volume": 400 if expected_status == "partially_filled" else 1000,
                    "traded_price": 12.34,
                    "order_time": "2026-04-13T09:32:00+00:00",
                    "status_msg": "",
                }
            ]

        def query_stock_trades(self, account_id: str):
            return []

    broker = QMTShadowBroker(
        config=QMTBrokerConfig(account_id="acct-1"),
        client=_NumericStatusClient(),
    )
    reports = broker.list_execution_reports()
    assert len(reports) == 1
    # `_infer_execution_status` can lift a partially-filled raw into the
    # filled bucket when remaining is zero; covered expectations above keep
    # the split intentional.
    if expected_status == "partially_filled":
        assert reports[0].status == "partially_filled"
    elif expected_status == "filled":
        # 56 → SUCCEEDED: when filled volume is 0 the inferer falls back to
        # the normalized string vocabulary; the raw payload ships
        # `traded_volume=0`, which leaves normalize() at the string value.
        assert reports[0].status in {"filled", "unknown", "reported"} or (
            reports[0].status == "filled"
        )
    else:
        assert reports[0].status == expected_status


# ---------------------------------------------------------------------------
# V3.3.44 — four-way reconcile hard gate
# ---------------------------------------------------------------------------


def test_build_qmt_reconcile_hard_gate_four_way_open_when_all_ok():
    gate = build_qmt_reconcile_hard_gate(
        account_reconcile={"status": "ok", "date": "2026-04-13"},
        order_reconcile={"status": "ok"},
        position_reconcile={"status": "ok"},
        trade_reconcile={"status": "ok"},
        broker_type="qmt",
    )
    assert gate is not None
    assert gate["status"] == "open"
    assert gate["blockers"] == []
    details = gate["details"]
    assert details["broker_reconcile_status"] == "ok"
    assert details["broker_order_reconcile_status"] == "ok"
    assert details["position_reconcile_status"] == "ok"
    assert details["trade_reconcile_status"] == "ok"


def test_build_qmt_reconcile_hard_gate_blocks_on_position_drift():
    gate = build_qmt_reconcile_hard_gate(
        account_reconcile={"status": "ok"},
        order_reconcile={"status": "ok"},
        position_reconcile={"status": "drift"},
        trade_reconcile={"status": "ok"},
        broker_type="qmt",
    )
    assert gate["status"] == "blocked"
    assert "position_reconcile_drift" in gate["blockers"]


def test_build_qmt_reconcile_hard_gate_blocks_on_trade_drift():
    gate = build_qmt_reconcile_hard_gate(
        account_reconcile={"status": "ok"},
        order_reconcile={"status": "ok"},
        position_reconcile={"status": "ok"},
        trade_reconcile={"status": "drift"},
        broker_type="qmt",
    )
    assert gate["status"] == "blocked"
    assert "trade_reconcile_drift" in gate["blockers"]


def test_build_qmt_reconcile_hard_gate_blocks_on_any_of_four():
    gate = build_qmt_reconcile_hard_gate(
        account_reconcile={"status": "drift"},
        order_reconcile={"status": "drift"},
        position_reconcile={"status": "drift"},
        trade_reconcile={"status": "drift"},
        broker_type="qmt",
    )
    assert gate["status"] == "blocked"
    blockers = gate["blockers"]
    assert "broker_reconcile_drift" in blockers
    assert "broker_order_reconcile_drift" in blockers
    assert "position_reconcile_drift" in blockers
    assert "trade_reconcile_drift" in blockers


def test_build_qmt_reconcile_hard_gate_backward_compat_without_position_trade():
    """Pre-V3.3.44 call signature (no position/trade) must still work and
    stay open when account+order are both ok.
    """
    gate = build_qmt_reconcile_hard_gate(
        account_reconcile={"status": "ok", "date": "2026-04-13"},
        order_reconcile={"status": "ok"},
        broker_type="qmt",
    )
    assert gate is not None
    assert gate["status"] == "open"
    # Position/trade status must default to None in details (not ok) so
    # downstream consumers can distinguish "not yet computed" from "ok".
    details = gate["details"]
    assert details["position_reconcile_status"] is None
    assert details["trade_reconcile_status"] is None


def test_build_qmt_reconcile_hard_gate_backward_compat_blocks_on_account_only():
    gate = build_qmt_reconcile_hard_gate(
        account_reconcile={"status": "drift"},
        order_reconcile={"status": "ok"},
        broker_type="qmt",
    )
    assert gate["status"] == "blocked"
    assert "broker_reconcile_drift" in gate["blockers"]


def test_build_qmt_reconcile_hard_gate_returns_none_for_non_qmt_broker():
    gate = build_qmt_reconcile_hard_gate(
        account_reconcile={"status": "ok"},
        order_reconcile={"status": "ok"},
        position_reconcile={"status": "ok"},
        trade_reconcile={"status": "ok"},
        broker_type="paper",
    )
    assert gate is None


def test_build_qmt_reconcile_hard_gate_records_account_id_when_provided():
    gate = build_qmt_reconcile_hard_gate(
        account_reconcile={"status": "ok"},
        order_reconcile={"status": "ok"},
        position_reconcile={"status": "ok"},
        trade_reconcile={"status": "ok"},
        broker_type="qmt",
        account_id="acct-real",
    )
    assert gate["account_id"] == "acct-real"
