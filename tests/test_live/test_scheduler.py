"""Tests for ez/live/scheduler.py — Scheduler idempotent tick, pause/resume, auto-recovery."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from ez.live.broker import (
    BrokerAccountSnapshot,
    BrokerCapability,
    BrokerExecutionReport,
    BrokerRuntimeEvent,
    BrokerSyncBundle,
)
from ez.live.events import (
    DeploymentEvent,
    EventType,
    make_broker_runtime_event,
    make_shadow_broker_client_order_id,
    utcnow,
)
from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.qmt_broker import QMTRealBroker, QMTShadowBroker
from ez.live.qmt_session_owner import QMTBrokerConfig, QMTSessionManager, XtQuantShadowClient
from ez.live.paper_broker import PaperBroker
from ez.live.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(**overrides) -> DeploymentSpec:
    """Create a minimal DeploymentSpec for testing."""
    defaults = dict(
        strategy_name="TopNRotation",
        strategy_params={"factor": "momentum_rank_20", "top_n": 5},
        symbols=("000001.SZ", "000002.SZ"),
        market="cn_stock",
        freq="weekly",
        initial_cash=1_000_000.0,
    )
    defaults.update(overrides)
    return DeploymentSpec(**defaults)


def _make_record(spec: DeploymentSpec, status: str = "approved", **overrides) -> DeploymentRecord:
    """Create a DeploymentRecord with defaults."""
    return DeploymentRecord(
        spec_id=spec.spec_id,
        name="test-deployment",
        status=status,
        **overrides,
    )


def _make_store() -> DeploymentStore:
    """Create in-memory DuckDB store."""
    conn = duckdb.connect(":memory:")
    return DeploymentStore(conn)


def _make_mock_engine(spec: DeploymentSpec, execute_result: dict | None = None):
    """Create a mock PaperTradingEngine."""
    engine = MagicMock()
    engine.spec = spec
    engine.cash = spec.initial_cash
    engine.holdings = {}
    engine.equity_curve = []
    engine.dates = []
    engine.trades = []
    engine.prev_weights = {}
    engine.prev_returns = {}
    engine.risk_events = []
    engine.risk_manager = None
    engine.optimizer = None
    engine._last_prices = {"000001.SZ": 10.0}
    engine._mark_to_market.return_value = spec.initial_cash

    if execute_result is None:
        execute_result = {
            "date": "2024-01-15",
            "equity": 1_000_000.0,
            "cash": 500_000.0,
            "holdings": {"000001.SZ": 1000},
            "weights": {"000001.SZ": 0.5},
            "prev_returns": {},
            "trades": [],
            "risk_events": [],
            "rebalanced": True,
            "_market_snapshot": {
                "prices": {"000001.SZ": 10.0},
                "has_bar_symbols": ["000001.SZ"],
                "source": "live",
            },
            "_market_bars": [
                {
                    "symbol": "000001.SZ",
                    "open": 9.8,
                    "high": 10.2,
                    "low": 9.7,
                    "close": 10.0,
                    "adj_close": 10.0,
                    "volume": 1234.0,
                    "source": "live",
                }
            ],
        }
    engine.execute_day.return_value = execute_result
    return engine


def _weekday_calendar():
    """Create a weekday-only TradingCalendar for 2024."""
    from ez.portfolio.calendar import TradingCalendar
    from datetime import timedelta
    start = date(2023, 1, 1)
    end = date(2025, 12, 31)
    return TradingCalendar.weekday_fallback(start, end)


class _FakeShadowBroker:
    broker_type = "qmt"

    def __init__(self):
        self.cancel_calls: list[tuple[str, str]] = []

    @property
    def capabilities(self):
        return frozenset(
            {
                BrokerCapability.READ_ACCOUNT_STATE,
                BrokerCapability.SHADOW_MODE,
                BrokerCapability.STREAM_EXECUTION_REPORTS,
                BrokerCapability.CANCEL_ORDER,
            }
        )

    def snapshot_account_state(self):
        return BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
            cash=90_000.0,
            total_asset=140_000.0,
            positions={"000001.SZ": 900},
            open_orders=[
                {
                    "client_order_id": "dep-shadow:2024-01-15:000001.SZ:buy",
                    "broker_order_id": "SYS-001",
                    "symbol": "000001.SZ",
                    "status": "partially_filled",
                }
            ],
            fills=[],
        )

    def list_execution_reports(self, *, since=None):
        report_time = datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc)
        if since is not None and report_time < since:
            return []
        from ez.live.broker import BrokerExecutionReport

        return [
            BrokerExecutionReport(
                report_id="qmt:SYS-001:partially_filled:600:400:2024-01-15T15:00:00+00:00",
                broker_type="qmt",
                as_of=report_time,
                client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
                broker_order_id="SYS-001",
                symbol="000001.SZ",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=12.34,
                message="partial",
                raw_payload={"entrust_no": "SYS-001"},
            )
        ]

    def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
        self.cancel_calls.append((order_id, symbol))
        return True


class _FakeRealBroker:
    broker_type = "qmt"

    @property
    def capabilities(self):
        return frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})

    def execute_target_weights(self, **kwargs):
        return kwargs


class _BrokenShadowBroker(_FakeShadowBroker):
    def list_execution_reports(self, *, since=None):
        raise RuntimeError("shadow report sync failed")


class _NoClientIdShadowBroker(_FakeShadowBroker):
    def list_execution_reports(self, *, since=None):
        reports = super().list_execution_reports(since=since)
        reports[0].client_order_id = ""
        reports[0].broker_order_id = "SYS-001"
        reports[0].report_id = "qmt:SYS-001:partially_filled:600:400:2024-01-15T15:00:00+00:00"
        reports[0].raw_payload = {"order_id": 1001, "order_sysid": "SYS-001"}
        reports[0].account_id = "acct-1"
        return reports


class _LegacyLinkTransitionShadowBroker(_FakeShadowBroker):
    def list_execution_reports(self, *, since=None):
        report_time = datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc)
        if since is not None and report_time < since:
            return []
        return [
            BrokerExecutionReport(
                report_id="qmt:SYS-001:partially_filled:600:400:2024-01-15T15:00:00+00:00",
                broker_type="qmt",
                as_of=report_time,
                client_order_id="",
                broker_order_id="SYS-001",
                symbol="000001.SZ",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=12.34,
                message="partial",
                raw_payload={"order_id": 1001, "order_sysid": "SYS-001"},
                account_id="acct-1",
            )
        ]


class _CancelableShadowBroker(_FakeShadowBroker):
    def __init__(self):
        super().__init__()
        self._cancel_requested = False

    def list_execution_reports(self, *, since=None):
        from ez.live.broker import BrokerExecutionReport

        reports = [
            BrokerExecutionReport(
                report_id="qmt:SYS-001:partially_filled:600:400:2024-01-15T15:00:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
                broker_order_id="SYS-001",
                symbol="000001.SZ",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=12.34,
                message="partial",
                raw_payload={"entrust_no": "SYS-001"},
            )
        ]
        if self._cancel_requested:
            reports.append(
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:600:0:2024-01-15T15:01:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=600,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="canceled",
                    raw_payload={"entrust_no": "SYS-001", "status": "canceled"},
                )
            )
        if since is not None:
            reports = [report for report in reports if report.as_of >= since]
        return reports

    def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
        self._cancel_requested = True
        return super().cancel_order(order_id, symbol=symbol)


class _OwningShadowBroker(_FakeShadowBroker):
    def __init__(self):
        super().__init__()
        self.attach_calls: list[str] = []
        self.detach_calls: list[str] = []

    def attach_deployment(self, deployment_id: str) -> None:
        self.attach_calls.append(deployment_id)

    def detach_deployment(self, deployment_id: str):
        self.detach_calls.append(deployment_id)
        return None

    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="account_status:acct-1:connected:2024-01-15T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-1",
                    "account_type": "STOCK",
                    "status": "connected",
                },
            )
        ]
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events


class _AccountScopedOwningQmtBroker(_OwningShadowBroker):
    def __init__(
        self,
        *,
        account_id: str,
        total_asset: float = 140_000.0,
        runtime_events: list[BrokerRuntimeEvent] | None = None,
        capabilities: frozenset[BrokerCapability] | None = None,
    ):
        super().__init__()
        self.account_id = account_id
        self.total_asset = total_asset
        self.runtime_events = runtime_events or [
            BrokerRuntimeEvent(
                event_id=f"account_status:{account_id}:connected:2024-01-15T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": account_id,
                    "account_type": "STOCK",
                    "status": "connected",
                },
            )
        ]
        self._capabilities = capabilities
        self.supervision_calls = 0

    @property
    def capabilities(self):
        if self._capabilities is not None:
            return self._capabilities
        return super().capabilities

    def ensure_session_supervision(self):
        self.supervision_calls += 1

    def execute_target_weights(self, **kwargs):
        return kwargs

    def snapshot_account_state(self):
        snapshot = super().snapshot_account_state()
        snapshot.account_id = self.account_id
        snapshot.total_asset = self.total_asset
        snapshot.cash = min(snapshot.cash, self.total_asset)
        snapshot.positions = {}
        snapshot.open_orders = []
        snapshot.fills = []
        return snapshot

    def list_runtime_events(self, *, since=None):
        events = list(self.runtime_events)
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events


class _RuntimeShadowBroker(_FakeShadowBroker):
    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="account_status:acct-1:connected:2024-01-15T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-1",
                    "account_type": "STOCK",
                    "status": "connected",
                },
            )
        ]
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events


class _CursorShadowBroker(_FakeShadowBroker):
    def collect_sync_state(self, *, since_reports=None, since_runtime=None, cursor_state=None):
        return BrokerSyncBundle(
            snapshot=None,
            execution_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-009:reported:0:100:2024-01-15T14:58:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 14, 58, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
                    broker_order_id="SYS-009",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=100,
                    avg_price=10.0,
                )
            ],
            runtime_events=[
                BrokerRuntimeEvent(
                    event_id="account_status:acct-1:connected:2024-01-15T14:59:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                    event_kind="account_status",
                    payload={
                        "_report_kind": "account_status",
                        "account_id": "acct-1",
                        "account_type": "STOCK",
                        "status": "connected",
                    },
                )
            ],
            cursor_state={
                "owner_runtime_seq": 3,
                "callback_runtime_seq": 5,
                "callback_execution_seq": 9,
            },
        )


class _SessionAwareShadowBroker(_FakeShadowBroker):
    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="session_connected:acct-1:2024-01-15T14:58:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 58, tzinfo=timezone.utc),
                event_kind="session_connected",
                payload={
                    "_report_kind": "session_connected",
                    "account_id": "acct-1",
                    "status": "connected",
                },
            ),
            BrokerRuntimeEvent(
                event_id="session_consumer_state:acct-1:2024-01-15T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                event_kind="session_consumer_state",
                payload={
                    "_report_kind": "session_consumer_state",
                    "account_id": "acct-1",
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
            ),
        ]
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events


class _ReadyRealQmtShadowBroker(_SessionAwareShadowBroker):
    def snapshot_account_state(self):
        return BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
            cash=100_000.0,
            total_asset=100_000.0,
            positions={},
            open_orders=[],
            fills=[],
        )

    def list_execution_reports(self, *, since=None):
        return []


class _LifecycleShadowBroker(_OwningShadowBroker):
    def __init__(self):
        super().__init__()
        self._attached = False

    def attach_deployment(self, deployment_id: str) -> None:
        super().attach_deployment(deployment_id)
        self._attached = True

    def detach_deployment(self, deployment_id: str):
        result = super().detach_deployment(deployment_id)
        self._attached = False
        return result

    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="session_consumer_started:acct-1:2024-01-15T14:58:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 58, tzinfo=timezone.utc),
                event_kind="session_consumer_started",
                payload={
                    "_report_kind": "session_consumer_started",
                    "account_id": "acct-1",
                    "session_id": "sess-1",
                },
            ),
            BrokerRuntimeEvent(
                event_id="account_status:acct-1:connected:2024-01-15T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-1",
                    "account_type": "STOCK",
                    "status": "connected",
                },
            ),
        ]
        if not self._attached:
            events.extend(
                [
                    BrokerRuntimeEvent(
                        event_id="session_consumer_stopped:acct-1:2024-01-15T15:05:00+00:00",
                        broker_type="qmt",
                        as_of=datetime(2024, 1, 15, 15, 5, tzinfo=timezone.utc),
                        event_kind="session_consumer_stopped",
                        payload={
                            "_report_kind": "session_consumer_stopped",
                            "account_id": "acct-1",
                            "session_id": "sess-1",
                        },
                    ),
                    BrokerRuntimeEvent(
                        event_id="session_owner_closed:acct-1:2024-01-15T15:05:01+00:00",
                        broker_type="qmt",
                        as_of=datetime(2024, 1, 15, 15, 5, 1, tzinfo=timezone.utc),
                        event_kind="session_owner_closed",
                        payload={
                            "_report_kind": "session_owner_closed",
                            "account_id": "acct-1",
                            "session_id": "sess-1",
                        },
                    ),
                ]
            )
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events


class _SupervisedShadowBroker(_OwningShadowBroker):
    def __init__(self):
        super().__init__()
        self.supervision_calls = 0

    def ensure_session_supervision(self):
        self.supervision_calls += 1

    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="session_consumer_restarted:acct-1:2024-01-15T14:57:30+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 57, 30, tzinfo=timezone.utc),
                event_kind="session_consumer_restarted",
                payload={
                    "_report_kind": "session_consumer_restarted",
                    "account_id": "acct-1",
                    "session_id": "sess-1",
                    "consumer_restart_count": 1,
                },
            ),
            BrokerRuntimeEvent(
                event_id="account_status:acct-1:connected:2024-01-15T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-1",
                    "account_type": "STOCK",
                    "status": "connected",
                },
            ),
        ]
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStartDeploymentRejectsNonApproved:
    """test_start_deployment_rejects_non_approved: status != 'approved' -> ValueError."""

    @pytest.mark.asyncio
    async def test_pending_rejected(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="pending")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with pytest.raises(ValueError, match="must be 'approved'"):
            await scheduler.start_deployment(record.deployment_id)

    @pytest.mark.asyncio
    async def test_running_rejected(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with pytest.raises(ValueError, match="must be 'approved'"):
            await scheduler.start_deployment(record.deployment_id)

    @pytest.mark.asyncio
    async def test_stopped_rejected(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="stopped")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with pytest.raises(ValueError, match="must be 'approved'"):
            await scheduler.start_deployment(record.deployment_id)


class TestBrokerResolution:
    @pytest.mark.asyncio
    async def test_start_deployment_fail_closes_historical_non_cn_spec_with_cn_rules(self):
        store = _make_store()
        spec = _make_spec(
            market="us_stock",
            t_plus_1=True,
            stamp_tax_rate=0.0005,
            price_limit_pct=0.10,
            lot_size=100,
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())

        with pytest.raises(ValueError, match="A-share market rules"):
            await scheduler.start_deployment(record.deployment_id)

        assert record.deployment_id not in scheduler._engines
        persisted = store.get_record(record.deployment_id)
        assert persisted is not None
        assert persisted.status == "approved"

    @pytest.mark.asyncio
    async def test_start_deployment_rejects_unsupported_broker_type(self):
        store = _make_store()
        spec = _make_spec(broker_type="qmt")
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with pytest.raises(NotImplementedError, match="QMT real execution is disabled by policy"):
            await scheduler.start_deployment(record.deployment_id)

    @pytest.mark.asyncio
    async def test_start_deployment_uses_qmt_real_broker_when_policy_is_enabled(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: _OwningShadowBroker(),
            },
        )
        real_broker = _FakeRealBroker()
        with patch.object(scheduler, "_build_qmt_real_broker", return_value=real_broker):
            with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
                with patch.object(scheduler, "_restore_full_state"):
                    await scheduler.start_deployment(record.deployment_id)

        assert scheduler._engines[record.deployment_id].broker is real_broker

    def test_build_qmt_real_broker_defaults_to_host_pinned_owner(self):
        scheduler = Scheduler(store=_make_store(), data_chain=MagicMock())
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )

        broker = scheduler._build_qmt_real_broker(spec)

        assert broker.config.account_id == "acct-real"
        assert broker.config.always_on_owner is True

    def test_warmup_qmt_process_owner_primes_real_qmt_without_attaching_deployment(self):
        store = _make_store()
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        manager = QMTSessionManager()
        consumer_calls: list[str] = []

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
                consumer_calls.append("ensure_callback_consumer")
                self.alive = True
                return True

            def ensure_resident_session(self):
                consumer_calls.append("ensure_resident_session")
                self.alive = True
                return True

            def is_callback_consumer_alive(self):
                return self.alive

        broker = QMTRealBroker(
            QMTBrokerConfig(account_id="acct-real", session_id="42", always_on_owner=True),
            client_factory=lambda _config: _Client(),
            session_manager=manager,
        )

        with patch.object(scheduler, "_build_qmt_real_broker", return_value=broker):
            state = scheduler.warmup_qmt_process_owner(
                spec,
                owner_id="scheduler:qmt:acct-real",
            )

        assert state.status == "process_pinned"
        assert state.owner_count == 0
        assert state.process_owner_count == 1
        assert state.process_owner_ids == ("scheduler:qmt:acct-real",)
        assert state.host_owner_pinned is True
        assert manager.active_session_count() == 1
        assert consumer_calls == ["ensure_callback_consumer"]
        assert scheduler._engines == {}

    @pytest.mark.asyncio
    async def test_start_deployment_accepts_shadow_broker_type_when_factory_is_present(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: _FakeShadowBroker(),
            },
        )
        with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
            with patch.object(scheduler, "_restore_full_state"):
                await scheduler.start_deployment(record.deployment_id)

        assert record.deployment_id in scheduler._engines

    @pytest.mark.asyncio
    async def test_start_deployment_persists_shadow_runtime_events_before_tick(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _RuntimeShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
            with patch.object(scheduler, "_restore_full_state"):
                await scheduler.start_deployment(record.deployment_id)

        runtime_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_RUNTIME_RECORDED
        ]
        assert len(runtime_events) == 1
        assert runtime_events[0].payload["runtime_kind"] == "account_status"

    @pytest.mark.asyncio
    async def test_start_deployment_persists_runtime_cursor_without_advancing_execution_cursor(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _CursorShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
            with patch.object(scheduler, "_restore_full_state"):
                await scheduler.start_deployment(record.deployment_id)

        cursor = store.get_broker_sync_cursor(
            record.deployment_id,
            broker_type="qmt",
        )
        assert cursor == {
            "owner_runtime_seq": 3,
            "callback_runtime_seq": 5,
        }

    @pytest.mark.asyncio
    async def test_start_deployment_rolls_back_engine_when_shadow_runtime_sync_fails(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _OwningShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
            with patch.object(scheduler, "_restore_full_state"):
                with patch.object(
                    scheduler,
                    "_sync_shadow_runtime_state",
                    side_effect=RuntimeError("runtime sync failed"),
                ):
                    with pytest.raises(RuntimeError, match="runtime sync failed"):
                        await scheduler.start_deployment(record.deployment_id)

        assert record.deployment_id not in scheduler._engines
        assert shadow_broker.attach_calls == [record.deployment_id]
        assert shadow_broker.detach_calls == [record.deployment_id]
        assert store.get_record(record.deployment_id).status == "approved"

    @pytest.mark.asyncio
    async def test_start_deployment_rolls_back_engine_when_status_persist_fails(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _OwningShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
            with patch.object(scheduler, "_restore_full_state"):
                with patch.object(
                    store,
                    "update_status",
                    side_effect=RuntimeError("db write failed"),
                ):
                    with pytest.raises(RuntimeError, match="db write failed"):
                        await scheduler.start_deployment(record.deployment_id)

        assert record.deployment_id not in scheduler._engines
        assert shadow_broker.attach_calls == [record.deployment_id]
        assert shadow_broker.detach_calls == [record.deployment_id]
        assert store.get_record(record.deployment_id).status == "approved"

    @pytest.mark.asyncio
    async def test_start_and_stop_deployment_attach_and_detach_shadow_owner(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _OwningShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
            with patch.object(scheduler, "_restore_full_state"):
                await scheduler.start_deployment(record.deployment_id)

        await scheduler.stop_deployment(record.deployment_id, reason="unit test")

        assert shadow_broker.attach_calls == [record.deployment_id]
        assert shadow_broker.detach_calls == [record.deployment_id]

    @pytest.mark.asyncio
    async def test_start_and_stop_deployment_attach_and_detach_real_and_shadow_qmt_owners(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _AccountScopedOwningQmtBroker(account_id="acct-shadow")
        real_broker = _AccountScopedOwningQmtBroker(
            account_id="acct-real",
            capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION}),
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        with patch.object(scheduler, "_build_qmt_real_broker", return_value=real_broker):
            with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
                with patch.object(scheduler, "_restore_full_state"):
                    await scheduler.start_deployment(record.deployment_id)

        await scheduler.stop_deployment(record.deployment_id, reason="unit test")

        assert shadow_broker.attach_calls == [record.deployment_id]
        assert shadow_broker.detach_calls == [record.deployment_id]
        assert real_broker.attach_calls == [record.deployment_id]
        assert real_broker.detach_calls == [record.deployment_id]

    @pytest.mark.asyncio
    async def test_stop_deployment_keeps_shared_qmt_owner_alive_until_last_reference(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
            },
        )
        store.save_spec(spec)
        record_a = _make_record(spec, status="running")
        record_b = _make_record(spec, status="running")
        store.save_record(record_a)
        store.save_record(record_b)

        class _Client:
            def __init__(self):
                self.close_calls = 0

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

            def close(self):
                self.close_calls += 1

        shared_client = _Client()
        session_manager = QMTSessionManager()
        shared_broker = QMTShadowBroker(
            QMTBrokerConfig(account_id="acct-1"),
            client_factory=lambda _config: shared_client,
            session_manager=session_manager,
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shared_broker,
            },
        )

        engine_a = _make_mock_engine(spec)
        engine_a.shadow_broker = shared_broker
        engine_b = _make_mock_engine(spec)
        engine_b.shadow_broker = shared_broker
        scheduler._engines[record_a.deployment_id] = engine_a
        scheduler._engines[record_b.deployment_id] = engine_b
        scheduler._attach_qmt_broker_owners(
            deployment_id=record_a.deployment_id,
            engine=engine_a,
        )
        scheduler._attach_qmt_broker_owners(
            deployment_id=record_b.deployment_id,
            engine=engine_b,
        )

        state = shared_broker.get_session_state()
        assert state is not None
        assert state.owner_count == 2
        assert state.attached_deployments == tuple(
            sorted((record_a.deployment_id, record_b.deployment_id))
        )
        assert session_manager.active_session_count() == 1

        await scheduler.stop_deployment(record_a.deployment_id, reason="unit test")

        state = shared_broker.get_session_state()
        assert state is not None
        assert state.owner_count == 1
        assert state.attached_deployments == (record_b.deployment_id,)
        assert state.status == "detached"
        assert record_a.deployment_id not in scheduler._engines
        assert record_b.deployment_id in scheduler._engines
        assert shared_client.close_calls == 0
        assert session_manager.active_session_count() == 1

        await scheduler.stop_deployment(record_b.deployment_id, reason="unit test")

        state = shared_broker.get_session_state()
        assert state is not None
        assert state.owner_count == 0
        assert state.attached_deployments == ()
        assert shared_client.close_calls == 1

    @pytest.mark.asyncio
    async def test_stop_deployment_persists_shadow_runtime_after_detach(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _LifecycleShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        with patch.object(scheduler, "_instantiate", return_value=(MagicMock(), None, None)):
            with patch.object(scheduler, "_restore_full_state"):
                await scheduler.start_deployment(record.deployment_id)

        await scheduler.stop_deployment(record.deployment_id, reason="unit test")

        runtime_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_RUNTIME_RECORDED
        ]
        runtime_kinds = [event.payload["runtime_kind"] for event in runtime_events]
        assert "session_consumer_started" in runtime_kinds
        assert "session_consumer_stopped" in runtime_kinds
        assert "session_owner_closed" in runtime_kinds

    @pytest.mark.asyncio
    async def test_stop_deployment_rolls_back_engine_when_status_persist_fails(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _OwningShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._attach_qmt_broker_owners(
            deployment_id=record.deployment_id,
            engine=engine,
        )

        with patch.object(
            store,
            "update_status",
            side_effect=RuntimeError("db write failed"),
        ):
            with pytest.raises(RuntimeError, match="db write failed"):
                await scheduler.stop_deployment(record.deployment_id, reason="unit test")

        assert record.deployment_id in scheduler._engines
        assert shadow_broker.detach_calls == [record.deployment_id]
        assert shadow_broker.attach_calls == [
            record.deployment_id,
            record.deployment_id,
        ]

    @pytest.mark.asyncio
    async def test_pump_broker_state_persists_shadow_account_runtime_execution_and_reconcile(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: _RuntimeShadowBroker(),
            },
        )
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine

        result = await scheduler.pump_broker_state(record.deployment_id)

        assert result["status"] == "broker_synced"
        assert result["account_event_count"] == 1
        assert result["runtime_event_count"] == 1
        assert result["execution_report_count"] == 1
        assert result["reconcile_status"] == "drift"
        assert result["order_reconcile_status"] == "ok"
        assert result["qmt_readiness"]["status"] == "degraded"
        assert result["qmt_readiness"]["ready_for_real_submit"] is False
        assert result["qmt_submit_gate"]["status"] == "blocked"
        assert result["qmt_submit_gate"]["can_submit_now"] is False
        assert result["qmt_submit_gate"]["preflight_ok"] is True
        assert result["qmt_submit_gate"]["account_id"] == "acct-1"
        assert result["qmt_submit_gate"]["total_asset"] == 140_000.0
        assert "broker_reconcile_drift" in result["qmt_submit_gate"]["blockers"]
        assert result["qmt_release_gate"]["status"] == "blocked"
        assert "deploy_gate_not_recorded" in result["qmt_release_gate"]["blockers"]

        events = store.get_events(record.deployment_id)
        event_types = [event.event_type for event in events]
        assert EventType.BROKER_ACCOUNT_RECORDED in event_types
        assert EventType.BROKER_RUNTIME_RECORDED in event_types
        assert EventType.BROKER_EXECUTION_RECORDED in event_types
        assert EventType.RISK_RECORDED in event_types

        link = store.find_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            broker_order_id="SYS-001",
        )
        assert link is not None
        assert link["latest_status"] == "partially_filled"

    @pytest.mark.asyncio
    async def test_pump_broker_state_uses_persisted_gate_verdict_to_block_release(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)
        store.update_gate_verdict(
            record.deployment_id,
            '{"passed": false, "reason": "risk_blocked"}',
        )

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: _RuntimeShadowBroker(),
            },
        )
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine

        result = await scheduler.pump_broker_state(record.deployment_id)

        assert result["qmt_release_gate"]["status"] == "blocked"
        assert "deploy_gate_failed" in result["qmt_release_gate"]["blockers"]
        assert "deploy_gate_not_recorded" not in result["qmt_release_gate"]["blockers"]

    @pytest.mark.asyncio
    async def test_pump_broker_state_consumes_real_qmt_execution_reports_from_execution_broker(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        class _NoExecutionShadowBroker(_OwningShadowBroker):
            def snapshot_account_state(self):
                return BrokerAccountSnapshot(
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                    cash=100_000.0,
                    total_asset=100_000.0,
                    positions={},
                    open_orders=[],
                    fills=[],
                    account_id="acct-shadow",
                )

            def list_execution_reports(self, *, since=None):
                return []

        class _RealExecutionBroker(_AccountScopedOwningQmtBroker):
            def list_execution_reports(self, *, since=None):
                report_time = datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc)
                if since is not None and report_time < since:
                    return []
                return [
                    BrokerExecutionReport(
                        report_id="qmt:SYS-REAL-001:reported:0:1000:2024-01-15T15:00:00+00:00",
                        broker_type="qmt",
                        as_of=report_time,
                        client_order_id="dep-real:2024-01-15:000001.SZ:buy",
                        broker_order_id="SYS-REAL-001",
                        symbol="000001.SZ",
                        side="buy",
                        status="reported",
                        filled_shares=0,
                        remaining_shares=1000,
                        avg_price=12.34,
                        message="submitted",
                        raw_payload={
                            "order_sysid": "SYS-REAL-001",
                            "order_remark": "dep-real:2024-01-15:000001.SZ:buy",
                        },
                    )
                ]

        shadow_broker = _NoExecutionShadowBroker()
        real_broker = _RealExecutionBroker(
            account_id="acct-real",
            capabilities=frozenset(
                {
                    BrokerCapability.TARGET_WEIGHT_EXECUTION,
                    BrokerCapability.READ_ACCOUNT_STATE,
                    BrokerCapability.STREAM_EXECUTION_REPORTS,
                }
            ),
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._build_execution_broker = MagicMock(return_value=real_broker)

        result = await scheduler.pump_broker_state(record.deployment_id)

        assert result["status"] == "broker_synced"
        execution_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
        ]
        assert len(execution_events) == 1
        assert execution_events[0].client_order_id == "dep-real:2024-01-15:000001.SZ:buy"
        assert execution_events[0].payload["broker_order_id"] == "SYS-REAL-001"

        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
        )
        assert link is not None
        assert link["broker_order_id"] == "SYS-REAL-001"
        assert link["latest_status"] == "reported"

    @pytest.mark.asyncio
    async def test_pump_broker_state_for_real_qmt_uses_real_reconcile_not_shadow_reconcile(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 2_000_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        class _HealthyShadowBroker(_OwningShadowBroker):
            def snapshot_account_state(self):
                return BrokerAccountSnapshot(
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                    cash=1_000_000.0,
                    total_asset=1_000_000.0,
                    positions={},
                    open_orders=[],
                    fills=[],
                    account_id="acct-shadow",
                )

            def list_execution_reports(self, *, since=None):
                return []

        real_runtime_events = [
            BrokerRuntimeEvent(
                event_id="session_connected:acct-real:2024-01-15T14:57:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 57, tzinfo=timezone.utc),
                event_kind="session_connected",
                payload={"_report_kind": "session_connected", "account_id": "acct-real", "status": "connected"},
            ),
            BrokerRuntimeEvent(
                event_id="session_consumer_state:acct-real:2024-01-15T14:59:30+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 1, 15, 14, 59, 30, tzinfo=timezone.utc),
                event_kind="session_consumer_state",
                payload={
                    "_report_kind": "session_consumer_state",
                    "account_id": "acct-real",
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
            ),
        ]
        shadow_broker = _HealthyShadowBroker()
        real_broker = _AccountScopedOwningQmtBroker(
            account_id="acct-real",
            total_asset=140_000.0,
            runtime_events=real_runtime_events,
            capabilities=frozenset(
                {
                    BrokerCapability.TARGET_WEIGHT_EXECUTION,
                    BrokerCapability.READ_ACCOUNT_STATE,
                    BrokerCapability.STREAM_EXECUTION_REPORTS,
                }
            ),
        )
        real_broker.list_execution_reports = lambda *, since=None: []

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        engine.broker = real_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._build_execution_broker = MagicMock(return_value=real_broker)

        result = await scheduler.pump_broker_state(record.deployment_id)

        assert result["qmt_submit_gate"]["status"] == "blocked"
        assert "broker_reconcile_drift" in result["qmt_submit_gate"]["blockers"]

        projection = store.get_broker_state_projection(record.deployment_id, broker_type="qmt")
        assert projection is not None
        assert projection["latest_reconcile"]["event"] == "real_broker_reconcile"
        assert projection["latest_reconcile"]["status"] == "drift"
        assert projection["latest_reconcile"]["account_id"] == "acct-real"
        assert projection["latest_order_reconcile"]["event"] == "real_broker_order_reconcile"
        assert projection["latest_order_reconcile"]["status"] == "ok"
        assert projection["latest_qmt_hard_gate"]["event"] == "real_qmt_reconcile_hard_gate"
        assert projection["latest_qmt_hard_gate"]["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_pump_broker_state_runs_shadow_session_supervision_before_sync(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _SupervisedShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine

        result = await scheduler.pump_broker_state(record.deployment_id)

        assert result["runtime_event_count"] == 2
        assert shadow_broker.supervision_calls == 1
        runtime_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_RUNTIME_RECORDED
        ]
        runtime_kinds = [event.payload["runtime_kind"] for event in runtime_events]
        assert "session_consumer_restarted" in runtime_kinds

    @pytest.mark.asyncio
    async def test_pump_broker_state_reuses_persisted_runtime_for_qmt_gates(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _SessionAwareShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine

        first = await scheduler.pump_broker_state(record.deployment_id)
        second = await scheduler.pump_broker_state(record.deployment_id)

        assert first["qmt_readiness"]["session_runtime_kind"] == "session_connected"
        assert first["qmt_readiness"]["account_sync_mode"] == "callback_preferred"
        assert second["runtime_event_count"] >= 1
        assert second["qmt_readiness"]["session_runtime_kind"] == "session_connected"
        assert second["qmt_readiness"]["account_sync_mode"] == "callback_preferred"

    @pytest.mark.asyncio
    async def test_qmt_callback_push_refreshes_runtime_projection_without_manual_sync(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        class _Trader:
            session_id = 42

            def query_stock_asset(self, account):
                return {
                    "update_time": "2026-04-13T09:31:00+00:00",
                    "cash": 90_000.0,
                    "total_asset": 140_000.0,
                }

            def query_stock_positions(self, account):
                return []

            def query_stock_orders(self, account):
                return []

            def query_stock_trades(self, account):
                return []

        manager = QMTSessionManager()
        client = XtQuantShadowClient(
            trader=_Trader(),
            account_ref="acct-1",
            account_id="acct-1",
        )
        shadow_broker = QMTShadowBroker(
            QMTBrokerConfig(account_id="acct-1"),
            client_factory=lambda _config: client,
            session_manager=manager,
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._attach_qmt_broker_owners(
            deployment_id=record.deployment_id,
            engine=engine,
        )

        assert store.get_broker_state_projection(record.deployment_id, broker_type="qmt") is None

        client._callback_bridge.on_disconnected()

        projection = None
        for _ in range(20):
            await asyncio.sleep(0)
            projection = store.get_broker_state_projection(
                record.deployment_id,
                broker_type="qmt",
            )
            if projection is not None:
                break

        assert projection is not None
        assert projection["projection_source"] == "runtime"
        assert projection["qmt_readiness"]["ready_for_real_submit"] is False
        assert projection["qmt_submit_gate"]["status"] == "blocked"
        assert "session_unhealthy" in projection["qmt_submit_gate"]["blockers"]
        runtime_kinds = [
            event.payload["runtime_kind"]
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_RUNTIME_RECORDED
        ]
        assert "disconnected" in runtime_kinds

    @pytest.mark.asyncio
    async def test_real_qmt_callback_only_order_error_persists_even_when_callback_refresh_pump_fails(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            risk_params={
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 2_000_000.0,
                    "max_initial_cash": 1_500_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        class _Trader:
            session_id = 84

            def query_stock_asset(self, account):
                return {
                    "update_time": "2026-04-13T09:31:00+00:00",
                    "cash": 1_000_000.0,
                    "total_asset": 1_000_000.0,
                }

            def query_stock_positions(self, account):
                return []

            def query_stock_orders(self, account):
                return []

            def query_stock_trades(self, account):
                return []

        manager = QMTSessionManager()
        client = XtQuantShadowClient(
            trader=_Trader(),
            account_ref="acct-real",
            account_id="acct-real",
        )
        real_broker = QMTRealBroker(
            QMTBrokerConfig(account_id="acct-real"),
            client_factory=lambda _config: client,
            session_manager=manager,
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={"paper": lambda _spec: PaperBroker()},
        )
        engine = _make_mock_engine(spec)
        engine.broker = real_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._remember_callback_refresh_loop()
        scheduler._attach_qmt_broker_owners(
            deployment_id=record.deployment_id,
            engine=engine,
        )

        async def _failing_pump(_deployment_id: str):
            raise RuntimeError("boom")

        scheduler.pump_broker_state = _failing_pump  # type: ignore[method-assign]

        client._callback_bridge.on_order_error(
            {
                "update_time": "2026-04-13T09:32:00+00:00",
                "account_id": "acct-real",
                "order_remark": "",
                "order_id": 700,
                "order_sysid": "SYS-REAL-001",
                "stock_code": "000001.SZ",
                "offset_flag": "buy",
                "order_status": "order_error",
                "order_volume": 1000,
                "traded_volume": 0,
                "status_msg": "callback-only-order-error",
            }
        )

        for _ in range(20):
            await asyncio.sleep(0)

        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
        ]
        assert len(broker_events) == 1
        assert broker_events[0].payload["status"] == "order_error"
        assert broker_events[0].payload["account_id"] == "acct-real"

        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["latest_status"] == "order_error"
        assert links[0]["account_id"] == "acct-real"
        assert str(links[0]["client_order_id"]).endswith(":broker_order:qmt:SYS-REAL-001")

    def test_register_qmt_callback_refresh_listener_reregisters_after_manager_clear(self):
        manager = QMTSessionManager()
        scheduler = Scheduler(store=_make_store(), data_chain=MagicMock())

        class _Client:
            def query_stock_asset(self, account_id: str):
                return {"update_time": "2026-04-13T09:31:00+00:00", "cash": 1.0, "total_asset": 1.0}

            def query_stock_positions(self, account_id: str):
                return []

            def query_stock_orders(self, account_id: str):
                return []

            def query_stock_trades(self, account_id: str):
                return []

            def register_projection_dirty_listener(self, listener):
                self.listener = listener

        broker = QMTShadowBroker(
            QMTBrokerConfig(account_id="acct-1"),
            client_factory=lambda _config: _Client(),
            session_manager=manager,
        )

        scheduler._register_qmt_callback_refresh_listener(broker)
        first_token = scheduler._qmt_callback_listener_tokens[id(manager)][1]
        assert manager.has_deployment_callback_listener(first_token) is True

        manager.clear()
        assert manager.has_deployment_callback_listener(first_token) is False

        scheduler._register_qmt_callback_refresh_listener(broker)
        second_token = scheduler._qmt_callback_listener_tokens[id(manager)][1]
        assert manager.has_deployment_callback_listener(second_token) is True

    @pytest.mark.asyncio
    async def test_sync_shadow_runtime_state_prefers_real_qmt_account_truth(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _AccountScopedOwningQmtBroker(
            account_id="acct-shadow",
            total_asset=140_000.0,
            runtime_events=[
                BrokerRuntimeEvent(
                    event_id="account_status:acct-shadow:connected:2024-01-15T15:01:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
                    event_kind="account_status",
                    payload={
                        "_report_kind": "account_status",
                        "account_id": "acct-shadow",
                        "account_type": "STOCK",
                        "status": "connected",
                    },
                )
            ],
        )
        real_broker = _AccountScopedOwningQmtBroker(
            account_id="acct-real",
            total_asset=1_000_000.0,
            runtime_events=[
                BrokerRuntimeEvent(
                    event_id="session_connected:acct-real:2024-01-15T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    event_kind="session_connected",
                    payload={
                        "_report_kind": "session_connected",
                        "account_id": "acct-real",
                        "status": "connected",
                    },
                ),
                BrokerRuntimeEvent(
                    event_id="session_consumer_state:acct-real:2024-01-15T15:00:30+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, 30, tzinfo=timezone.utc),
                    event_kind="session_consumer_state",
                    payload={
                        "_report_kind": "session_consumer_state",
                        "account_id": "acct-real",
                        "status": "connected",
                        "consumer_status": "running",
                        "account_sync_mode": "callback_preferred",
                        "asset_callback_freshness": "fresh",
                    },
                ),
            ],
            capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION}),
        )
        real_broker.list_execution_reports = lambda *, since=None: []
        real_broker.snapshot_account_state = lambda: BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
            cash=1_000_000.0,
            total_asset=1_000_000.0,
            positions={},
            open_orders=[],
            fills=[],
            account_id="acct-real",
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        engine.broker = real_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._build_execution_broker = MagicMock(return_value=real_broker)

        with patch.object(scheduler, "_build_qmt_real_broker", return_value=real_broker):
            scheduler._sync_shadow_runtime_state(
                deployment_id=record.deployment_id,
                engine=engine,
            )

        projection = store.get_broker_state_projection(
            record.deployment_id,
            broker_type="qmt",
        )
        assert projection is not None
        assert projection["latest_broker_account"]["payload"]["account_id"] == "acct-real"
        assert projection["latest_broker_account"]["payload"]["total_asset"] == 1_000_000.0
        assert projection["latest_runtime_event"]["payload"]["payload"]["account_id"] == "acct-real"
        assert projection["qmt_submit_gate"]["account_id"] == "acct-real"
        assert projection["qmt_submit_gate"]["status"] == "open"
        assert projection["qmt_release_gate"]["status"] == "blocked"
        assert "deploy_gate_not_recorded" in projection["qmt_release_gate"]["blockers"]

    @pytest.mark.asyncio
    async def test_not_found_rejected(self):
        store = _make_store()
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with pytest.raises(ValueError, match="not found"):
            await scheduler.start_deployment("nonexistent-id")

    @pytest.mark.asyncio
    async def test_pump_broker_state_requires_loaded_engine(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())

        with pytest.raises(ValueError, match="is not loaded"):
            await scheduler.pump_broker_state(record.deployment_id)

    @pytest.mark.asyncio
    async def test_approved_accepted(self):
        """Approved deployment should start (mock engine creation)."""
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        mock_engine = _make_mock_engine(spec)

        with patch.object(scheduler, "_start_engine") as mock_start:
            await scheduler.start_deployment(record.deployment_id)
            mock_start.assert_called_once_with(record.deployment_id)

        # Status should be updated to running
        updated = store.get_record(record.deployment_id)
        assert updated.status == "running"


class TestTickIdempotency:
    """test_tick_idempotency: same date twice -> second skipped."""

    @pytest.mark.asyncio
    async def test_second_tick_skipped(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine

        # Inject a weekday calendar
        cal = _weekday_calendar()
        scheduler._calendars["cn_stock"] = cal

        # First tick on a Monday
        biz_date = date(2024, 1, 15)  # Monday
        results1 = await scheduler.tick(biz_date)
        assert len(results1) == 1
        assert engine.execute_day.call_count == 1

        # Second tick same date -> skipped
        results2 = await scheduler.tick(biz_date)
        assert len(results2) == 0
        assert engine.execute_day.call_count == 1  # NOT called again

    @pytest.mark.asyncio
    async def test_tick_persists_oms_events_and_snapshot_event(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        biz_date = date(2024, 1, 15)
        engine.execute_day.return_value["risk_events"] = [
            {"event": "runtime_allocator", "rule": "max_names", "details": {"dropped_symbols": ["BBB"]}}
        ]
        engine.execute_day.return_value["_oms_events"] = [
            DeploymentEvent(
                event_id=f"{record.deployment_id}:{biz_date}:AAA:buy:{EventType.ORDER_SUBMITTED.value}",
                deployment_id=record.deployment_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=utcnow(),
                client_order_id=f"{record.deployment_id}:{biz_date}:AAA:buy",
                payload={"symbol": "AAA", "side": "buy", "shares": 100},
            )
        ]
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        results = await scheduler.tick(biz_date)
        assert len(results) == 1

        events = store.get_events(record.deployment_id)
        assert len(events) == 6
        event_types = [event.event_type.value for event in events]
        assert event_types[:2] == ["market_bar_recorded", "market_snapshot"]
        assert "market_snapshot" in event_types
        assert "market_bar_recorded" in event_types
        assert "order_submitted" in event_types
        assert "risk_recorded" in event_types
        assert event_types[-2:] == ["snapshot_saved", "tick_completed"]
        bar_event = next(event for event in events if event.event_type.value == "market_bar_recorded")
        market_event = next(event for event in events if event.event_type.value == "market_snapshot")
        risk_event = next(event for event in events if event.event_type.value == "risk_recorded")
        assert bar_event.payload["symbol"] == "000001.SZ"
        assert bar_event.payload["adj_close"] == 10.0
        assert market_event.payload["prices"]["000001.SZ"] == 10.0
        assert risk_event.payload["risk_event"]["event"] == "runtime_allocator"
        assert events[-1].payload["trade_count"] == 0

    @pytest.mark.asyncio
    async def test_tick_appends_shadow_reconcile_risk_event(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: _FakeShadowBroker(),
            },
        )
        engine = _make_mock_engine(spec)
        engine._last_prices = {"000001.SZ": 10.0}
        engine.shadow_broker = _FakeShadowBroker()
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        results = await scheduler.tick(date(2024, 1, 15))
        assert len(results) == 1
        assert any(
            event["event"] == "broker_reconcile" and event["status"] == "drift"
            for event in results[0]["risk_events"]
        )
        assert any(
            event["event"] == "broker_order_reconcile" and event["status"] == "ok"
            for event in results[0]["risk_events"]
        )

        latest = store.get_latest_snapshot(record.deployment_id)
        assert any(
            event["event"] == "broker_reconcile" and event["status"] == "drift"
            for event in latest["risk_events"]
        )
        assert any(
            event["event"] == "broker_order_reconcile" and event["status"] == "ok"
            for event in latest["risk_events"]
        )
        account_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_ACCOUNT_RECORDED
        ]
        assert len(account_events) == 1
        assert account_events[0].payload["broker_type"] == "qmt"
        assert account_events[0].payload["positions"]["000001.SZ"] == 900

    @pytest.mark.asyncio
    async def test_tick_appends_shadow_execution_report_events_idempotently(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _FakeShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        first_events = store.get_events(record.deployment_id)
        broker_events = [
            event for event in first_events if event.event_type == EventType.BROKER_EXECUTION_RECORDED
        ]
        assert len(broker_events) == 1
        assert broker_events[0].payload["status"] == "partially_filled"
        event_types = [event.event_type for event in first_events]
        assert event_types.index(EventType.BROKER_EXECUTION_RECORDED) < event_types.index(
            EventType.RISK_RECORDED
        )

        await scheduler.tick(date(2024, 1, 16))
        second_events = store.get_events(record.deployment_id)
        broker_events = [
            event for event in second_events if event.event_type == EventType.BROKER_EXECUTION_RECORDED
        ]
        assert len(broker_events) == 1
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == "dep-shadow:2024-01-15:000001.SZ:buy"
        assert links[0]["broker_order_id"] == "SYS-001"

    @pytest.mark.asyncio
    async def test_tick_ignores_shadow_execution_report_sync_failures(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _BrokenShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        results = await scheduler.tick(date(2024, 1, 15))
        assert len(results) == 1
        assert all(
            event.event_type != EventType.BROKER_EXECUTION_RECORDED
            for event in store.get_events(record.deployment_id)
        )

    @pytest.mark.asyncio
    async def test_tick_falls_back_to_synthetic_shadow_client_order_id(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _NoClientIdShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id.endswith(":broker_order:qmt:SYS-001")
        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id=broker_events[0].client_order_id,
        )
        assert link is not None
        assert link["broker_order_id"] == "SYS-001"

    @pytest.mark.asyncio
    async def test_tick_reuses_existing_shadow_client_order_id_by_broker_order_id(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        canonical_client_order_id = "dep-shadow:2024-01-15:000001.SZ:buy"
        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:reported:0:1000:2024-01-14T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 14, 15, 0, tzinfo=timezone.utc),
                    client_order_id=canonical_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=12.34,
                    message="existing-link",
                    raw_payload={"order_sysid": "SYS-001"},
                )
            ],
        )

        shadow_broker = _NoClientIdShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"].startswith("qmt:SYS-001:")
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id == canonical_client_order_id
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == canonical_client_order_id
        assert links[0]["broker_order_id"] == "SYS-001"
        assert not any(
            ":broker_order:qmt:SYS-001" in str(link["client_order_id"]) for link in links
        )

    @pytest.mark.asyncio
    async def test_tick_reuses_shadow_client_order_id_when_broker_order_id_is_disambiguated_by_account_id(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:reported:0:1000:2024-01-14T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 14, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2024-01-14:000001.SZ:buy:a",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=12.34,
                    message="existing-link-a",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-1",
                ),
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:reported:0:1000:2024-01-14T15:01:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 14, 15, 1, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2024-01-14:000001.SZ:buy:b",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=12.34,
                    message="existing-link-b",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-2",
                ),
            ],
        )

        shadow_broker = _NoClientIdShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"].startswith("qmt:SYS-001:")
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id == "dep-shadow:2024-01-14:000001.SZ:buy:a"
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 2
        assert {link["client_order_id"] for link in links} == {
            "dep-shadow:2024-01-14:000001.SZ:buy:a",
            "dep-shadow:2024-01-14:000001.SZ:buy:b",
        }
        assert not any(
            str(link["client_order_id"]).endswith(":broker_order:qmt:SYS-001")
            for link in links
        )

    @pytest.mark.asyncio
    async def test_tick_reuses_terminal_shadow_client_order_id_for_same_timestamp_stale_report(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        canonical_client_order_id = "dep-shadow:2024-01-15:000001.SZ:buy"
        terminal_report_id = "qmt:SYS-001:canceled:0:0:2024-01-15T15:00:00+00:00"
        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id=terminal_report_id,
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id=canonical_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="terminal-callback-first",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-1",
                )
            ],
        )

        shadow_broker = _NoClientIdShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"]
            == "qmt:SYS-001:partially_filled:600:400:2024-01-15T15:00:00+00:00"
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id == canonical_client_order_id
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == canonical_client_order_id
        assert links[0]["latest_status"] == "canceled"
        assert not any(
            str(link["client_order_id"]).endswith(":broker_order:qmt:SYS-001")
            for link in links
        )

    @pytest.mark.asyncio
    async def test_tick_does_not_reuse_terminal_shadow_client_order_id_for_later_non_terminal_report(self):
        class _LaterReportedNoClientIdShadowBroker(_NoClientIdShadowBroker):
            def list_execution_reports(self, *, since=None):
                report_time = datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc)
                if since is not None and report_time < since:
                    return []
                return [
                    BrokerExecutionReport(
                        report_id="qmt:SYS-001:reported:0:1000:2024-01-15T15:01:00+00:00",
                        broker_type="qmt",
                        as_of=report_time,
                        client_order_id="",
                        broker_order_id="SYS-001",
                        symbol="000001.SZ",
                        side="buy",
                        status="reported",
                        filled_shares=0,
                        remaining_shares=1000,
                        avg_price=12.34,
                        message="later-open-lifecycle",
                        raw_payload={"order_id": 1001, "order_sysid": "SYS-001"},
                        account_id="acct-1",
                    )
                ]

        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        canonical_client_order_id = "dep-shadow:2024-01-15:000001.SZ:buy"
        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id=canonical_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="terminal-callback-first",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-1",
                )
            ],
        )

        shadow_broker = _LaterReportedNoClientIdShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"]
            == "qmt:SYS-001:reported:0:1000:2024-01-15T15:01:00+00:00"
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id != canonical_client_order_id
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 2
        assert any(
            str(link["client_order_id"]).endswith(":broker_order:qmt:SYS-001")
            for link in links
        )
        canonical_link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id=canonical_client_order_id,
        )
        assert canonical_link is not None
        assert canonical_link["latest_status"] == "canceled"

    @pytest.mark.asyncio
    async def test_tick_reuses_terminal_shadow_client_order_id_for_later_terminal_report(self):
        class _LaterCanceledNoClientIdShadowBroker(_NoClientIdShadowBroker):
            def list_execution_reports(self, *, since=None):
                report_time = datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc)
                if since is not None and report_time < since:
                    return []
                return [
                    BrokerExecutionReport(
                        report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:01:00+00:00",
                        broker_type="qmt",
                        as_of=report_time,
                        client_order_id="",
                        broker_order_id="SYS-001",
                        symbol="000001.SZ",
                        side="buy",
                        status="canceled",
                        filled_shares=0,
                        remaining_shares=0,
                        avg_price=12.34,
                        message="terminal-confirm",
                        raw_payload={"order_id": 1001, "order_sysid": "SYS-001"},
                        account_id="acct-1",
                    )
                ]

        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        canonical_client_order_id = "dep-shadow:2024-01-15:000001.SZ:buy"
        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id=canonical_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="terminal-callback-first",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-1",
                )
            ],
        )

        shadow_broker = _LaterCanceledNoClientIdShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"]
            == "qmt:SYS-001:canceled:0:0:2024-01-15T15:01:00+00:00"
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id == canonical_client_order_id
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == canonical_client_order_id
        assert links[0]["latest_status"] == "canceled"

    @pytest.mark.asyncio
    async def test_tick_reuses_existing_synthetic_shadow_client_order_id_when_later_report_has_real_client_order_id(self):
        class _CanonicalizingShadowBroker(_FakeShadowBroker):
            def snapshot_account_state(self):
                return BrokerAccountSnapshot(
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
                    cash=90_000.0,
                    total_asset=140_000.0,
                    positions={},
                    open_orders=[],
                    fills=[],
                )

            def list_execution_reports(self, *, since=None):
                report_time = datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc)
                if since is not None and report_time < since:
                    return []
                return [
                    BrokerExecutionReport(
                        report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:01:00+00:00",
                        broker_type="qmt",
                        as_of=report_time,
                        client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
                        broker_order_id="SYS-001",
                        symbol="000001.SZ",
                        side="buy",
                        status="canceled",
                        filled_shares=0,
                        remaining_shares=0,
                        avg_price=12.34,
                        message="terminal-confirm",
                        raw_payload={
                            "order_remark": "dep-shadow:2024-01-15:000001.SZ:buy",
                            "order_sysid": "SYS-001",
                        },
                        account_id="acct-1",
                    )
                ]

        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        synthetic_client_order_id = make_shadow_broker_client_order_id(
            record.deployment_id,
            broker_type="qmt",
            broker_order_id="SYS-001",
            report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:00:00+00:00",
        )
        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id=synthetic_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="callback-only-terminal",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-1",
                )
            ],
        )

        shadow_broker = _CanonicalizingShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"]
            == "qmt:SYS-001:canceled:0:0:2024-01-15T15:01:00+00:00"
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id == synthetic_client_order_id
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == synthetic_client_order_id
        assert links[0]["latest_status"] == "canceled"

    @pytest.mark.asyncio
    async def test_tick_reuses_existing_synthetic_shadow_client_order_id_for_order_error_with_later_real_client_order_id(self):
        class _CanonicalizingOrderErrorShadowBroker(_FakeShadowBroker):
            def snapshot_account_state(self):
                return BrokerAccountSnapshot(
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
                    cash=90_000.0,
                    total_asset=140_000.0,
                    positions={},
                    open_orders=[],
                    fills=[],
                )

            def list_execution_reports(self, *, since=None):
                report_time = datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc)
                if since is not None and report_time < since:
                    return []
                return [
                    BrokerExecutionReport(
                        report_id="qmt:SYS-001:order_error:0:0:2024-01-15T15:01:00+00:00",
                        broker_type="qmt",
                        as_of=report_time,
                        client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
                        broker_order_id="SYS-001",
                        symbol="000001.SZ",
                        side="buy",
                        status="order_error",
                        filled_shares=0,
                        remaining_shares=0,
                        avg_price=0.0,
                        message="order rejected",
                        raw_payload={
                            "order_remark": "dep-shadow:2024-01-15:000001.SZ:buy",
                            "order_sysid": "SYS-001",
                            "status_msg": "order rejected",
                        },
                        account_id="acct-1",
                    )
                ]

        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        synthetic_client_order_id = make_shadow_broker_client_order_id(
            record.deployment_id,
            broker_type="qmt",
            broker_order_id="SYS-001",
            report_id="qmt:SYS-001:order_error:0:0:2024-01-15T15:00:00+00:00",
        )
        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:order_error:0:0:2024-01-15T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id=synthetic_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="order_error",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=0.0,
                    message="callback-only-order-error",
                    raw_payload={"order_sysid": "SYS-001", "status_msg": "callback-only-order-error"},
                    account_id="acct-1",
                )
            ],
        )

        shadow_broker = _CanonicalizingOrderErrorShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"]
            == "qmt:SYS-001:order_error:0:0:2024-01-15T15:01:00+00:00"
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id == synthetic_client_order_id
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == synthetic_client_order_id
        assert links[0]["latest_status"] == "order_error"

    @pytest.mark.asyncio
    async def test_tick_keeps_later_real_client_order_id_when_multiple_existing_links_match_same_broker_order_id(self):
        class _AmbiguousCanonicalizingShadowBroker(_FakeShadowBroker):
            def snapshot_account_state(self):
                return BrokerAccountSnapshot(
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
                    cash=90_000.0,
                    total_asset=140_000.0,
                    positions={},
                    open_orders=[],
                    fills=[],
                )

            def list_execution_reports(self, *, since=None):
                report_time = datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc)
                if since is not None and report_time < since:
                    return []
                return [
                    BrokerExecutionReport(
                        report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:01:00+00:00",
                        broker_type="qmt",
                        as_of=report_time,
                        client_order_id="dep-shadow:2024-01-15:000001.SZ:buy:real",
                        broker_order_id="SYS-001",
                        symbol="000001.SZ",
                        side="buy",
                        status="canceled",
                        filled_shares=0,
                        remaining_shares=0,
                        avg_price=12.34,
                        message="terminal-confirm",
                        raw_payload={
                            "order_remark": "dep-shadow:2024-01-15:000001.SZ:buy:real",
                            "order_sysid": "SYS-001",
                        },
                        account_id="acct-1",
                    )
                ]

        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2024-01-15:000001.SZ:buy:a",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="callback-only-terminal-a",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-1",
                ),
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:0:0:2024-01-15T15:00:00+00:00:b",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2024-01-15:000001.SZ:buy:b",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="callback-only-terminal-b",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-1",
                ),
            ],
        )

        shadow_broker = _AmbiguousCanonicalizingShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"]
            == "qmt:SYS-001:canceled:0:0:2024-01-15T15:01:00+00:00"
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id == "dep-shadow:2024-01-15:000001.SZ:buy:real"
        links = store.list_broker_order_links(record.deployment_id, broker_type="qmt")
        assert len(links) == 3
        assert any(
            link["client_order_id"] == "dep-shadow:2024-01-15:000001.SZ:buy:real"
            for link in links
        )

    @pytest.mark.asyncio
    async def test_tick_reuses_legacy_shadow_client_order_id_when_link_already_exists(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        legacy_client_order_id = make_shadow_broker_client_order_id(
            record.deployment_id,
            broker_type="qmt",
            broker_order_id="1001",
        )
        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:1001:partially_filled:600:400:2024-01-14T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 14, 15, 0, tzinfo=timezone.utc),
                    client_order_id=legacy_client_order_id,
                    broker_order_id="1001",
                    symbol="000001.SZ",
                    side="buy",
                    status="partially_filled",
                    filled_shares=600,
                    remaining_shares=400,
                    avg_price=12.34,
                    message="legacy",
                    raw_payload={"order_id": 1001},
                )
            ],
        )

        shadow_broker = _LegacyLinkTransitionShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
            and event.payload["report_id"].startswith("qmt:SYS-001:")
        ]
        assert len(broker_events) == 1
        assert broker_events[0].client_order_id == legacy_client_order_id
        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id=legacy_client_order_id,
        )
        assert link is not None
        assert link["broker_order_id"] == "SYS-001"

    @pytest.mark.asyncio
    async def test_tick_persists_shadow_runtime_events(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _RuntimeShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        runtime_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_RUNTIME_RECORDED
        ]
        assert len(runtime_events) == 1
        assert runtime_events[0].payload["runtime_kind"] == "account_status"
        assert runtime_events[0].payload["payload"]["status"] == "connected"

    @pytest.mark.asyncio
    async def test_tick_allows_qmt_real_submit_only_when_runtime_gate_is_open(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            initial_cash=100_000.0,
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        record.gate_verdict = '{"passed": true}'
        store.save_record(record)
        store.update_status(record.deployment_id, "running")
        store.upsert_broker_state_projection(
            record.deployment_id,
            broker_type="qmt",
            projection={
                "qmt_submit_gate": {
                    "status": "open",
                    "can_submit_now": True,
                    "preflight_ok": True,
                },
                "qmt_release_gate": {
                    "status": "candidate",
                    "eligible_for_real_submit": True,
                },
            },
        )

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: _ReadyRealQmtShadowBroker(),
            },
        )
        engine = _make_mock_engine(
            spec,
            execute_result={
                "date": "2024-01-15",
                "equity": 100_000.0,
                "cash": 100_000.0,
                "holdings": {},
                "weights": {},
                "prev_returns": {},
                "trades": [],
                "risk_events": [],
                "rebalanced": False,
                "_market_snapshot": {
                    "prices": {"000001.SZ": 10.0},
                    "has_bar_symbols": ["000001.SZ"],
                    "source": "live",
                },
                "_market_bars": [],
            },
        )
        engine.shadow_broker = _ReadyRealQmtShadowBroker()
        engine.broker = _AccountScopedOwningQmtBroker(
            account_id="acct-1",
            total_asset=100_000.0,
            runtime_events=[
                BrokerRuntimeEvent(
                    event_id="session_connected:acct-1:2024-01-15T14:57:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 14, 57, tzinfo=timezone.utc),
                    event_kind="session_connected",
                    payload={"_report_kind": "session_connected", "account_id": "acct-1", "status": "connected"},
                ),
                BrokerRuntimeEvent(
                    event_id="session_consumer_state:acct-1:2024-01-15T14:59:30+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 14, 59, 30, tzinfo=timezone.utc),
                    event_kind="session_consumer_state",
                    payload={
                        "_report_kind": "session_consumer_state",
                        "account_id": "acct-1",
                        "status": "connected",
                        "consumer_status": "running",
                        "account_sync_mode": "callback_preferred",
                        "asset_callback_freshness": "fresh",
                    },
                ),
            ],
            capabilities=frozenset(
                {
                    BrokerCapability.TARGET_WEIGHT_EXECUTION,
                    BrokerCapability.READ_ACCOUNT_STATE,
                    BrokerCapability.STREAM_EXECUTION_REPORTS,
                }
            ),
        )
        engine.broker.list_execution_reports = lambda *, since=None: []
        engine.broker.snapshot_account_state = lambda: BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
            cash=100_000.0,
            total_asset=100_000.0,
            positions={},
            open_orders=[],
            fills=[],
            account_id="acct-1",
        )
        engine._mark_to_market.return_value = 100_000.0
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()
        scheduler._build_execution_broker = MagicMock(return_value=engine.broker)

        results = await scheduler.tick(date(2024, 1, 15))

        assert len(results) == 1
        engine.execute_day.assert_called_once_with(date(2024, 1, 15))

    @pytest.mark.asyncio
    async def test_tick_preflights_qmt_real_submit_projection_when_missing(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            initial_cash=100_000.0,
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 200_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        record.gate_verdict = '{"passed": true}'
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: _ReadyRealQmtShadowBroker(),
            },
        )
        engine = _make_mock_engine(
            spec,
            execute_result={
                "date": "2024-01-15",
                "equity": 100_000.0,
                "cash": 100_000.0,
                "holdings": {},
                "weights": {},
                "prev_returns": {},
                "trades": [],
                "risk_events": [],
                "rebalanced": False,
                "_market_snapshot": {
                    "prices": {"000001.SZ": 10.0},
                    "has_bar_symbols": ["000001.SZ"],
                    "source": "live",
                },
                "_market_bars": [],
            },
        )
        engine.shadow_broker = _ReadyRealQmtShadowBroker()
        engine.broker = _AccountScopedOwningQmtBroker(
            account_id="acct-1",
            total_asset=100_000.0,
            runtime_events=[
                BrokerRuntimeEvent(
                    event_id="session_connected:acct-1:2024-01-15T14:57:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 14, 57, tzinfo=timezone.utc),
                    event_kind="session_connected",
                    payload={"_report_kind": "session_connected", "account_id": "acct-1", "status": "connected"},
                ),
                BrokerRuntimeEvent(
                    event_id="session_consumer_state:acct-1:2024-01-15T14:59:30+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 14, 59, 30, tzinfo=timezone.utc),
                    event_kind="session_consumer_state",
                    payload={
                        "_report_kind": "session_consumer_state",
                        "account_id": "acct-1",
                        "status": "connected",
                        "consumer_status": "running",
                        "account_sync_mode": "callback_preferred",
                        "asset_callback_freshness": "fresh",
                    },
                ),
            ],
            capabilities=frozenset(
                {
                    BrokerCapability.TARGET_WEIGHT_EXECUTION,
                    BrokerCapability.READ_ACCOUNT_STATE,
                    BrokerCapability.STREAM_EXECUTION_REPORTS,
                }
            ),
        )
        engine.broker.list_execution_reports = lambda *, since=None: []
        engine.broker.snapshot_account_state = lambda: BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
            cash=100_000.0,
            total_asset=100_000.0,
            positions={},
            open_orders=[],
            fills=[],
            account_id="acct-1",
        )
        engine._mark_to_market.return_value = 100_000.0
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()
        scheduler._build_execution_broker = MagicMock(return_value=engine.broker)

        results = await scheduler.tick(date(2024, 1, 15))

        assert len(results) == 1
        engine.execute_day.assert_called_once_with(date(2024, 1, 15))
        projection = store.get_broker_state_projection(
            record.deployment_id,
            broker_type="qmt",
        )
        assert projection is not None
        assert projection["qmt_submit_gate"]["status"] == "open"
        assert projection["qmt_submit_gate"]["can_submit_now"] is True
        assert projection["qmt_release_gate"]["status"] == "candidate"
        assert projection["qmt_release_gate"]["eligible_for_real_submit"] is True

    @pytest.mark.asyncio
    async def test_tick_blocks_qmt_real_submit_when_runtime_gate_is_closed(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")
        store.upsert_broker_state_projection(
            record.deployment_id,
            broker_type="qmt",
            projection={
                "qmt_submit_gate": {
                    "status": "blocked",
                    "can_submit_now": False,
                    "preflight_ok": True,
                    "blockers": ["broker_reconcile_drift"],
                },
                "qmt_release_gate": {
                    "status": "blocked",
                    "eligible_for_real_submit": False,
                },
            },
        )

        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: _FakeShadowBroker(),
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = _FakeShadowBroker()
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        results = await scheduler.tick(date(2024, 1, 15))

        assert results == []
        engine.execute_day.assert_not_called()


class TestTickSkipsPaused:
    """test_tick_skips_paused: paused deployment not executed."""

    @pytest.mark.asyncio
    async def test_paused_not_executed(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine
        scheduler._paused.add(record.deployment_id)

        cal = _weekday_calendar()
        scheduler._calendars["cn_stock"] = cal

        biz_date = date(2024, 1, 15)  # Monday
        results = await scheduler.tick(biz_date)
        assert len(results) == 0
        engine.execute_day.assert_not_called()


class TestTickSkipsNonTradingDay:
    """test_tick_skips_non_trading_day: weekend/holiday skipped."""

    @pytest.mark.asyncio
    async def test_weekend_skipped(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine

        cal = _weekday_calendar()
        scheduler._calendars["cn_stock"] = cal

        # Saturday
        biz_date = date(2024, 1, 13)
        results = await scheduler.tick(biz_date)
        assert len(results) == 0
        engine.execute_day.assert_not_called()

    @pytest.mark.asyncio
    async def test_sunday_skipped(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine

        cal = _weekday_calendar()
        scheduler._calendars["cn_stock"] = cal

        # Sunday
        biz_date = date(2024, 1, 14)
        results = await scheduler.tick(biz_date)
        assert len(results) == 0
        engine.execute_day.assert_not_called()


class TestTickErrorEscalation:
    """test_tick_error_escalation: 3 consecutive errors -> status 'error'."""

    @pytest.mark.asyncio
    async def test_three_errors_triggers_error_status(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        engine.execute_day.side_effect = RuntimeError("data fetch failed")
        scheduler._engines[record.deployment_id] = engine

        cal = _weekday_calendar()
        scheduler._calendars["cn_stock"] = cal

        dep_id = record.deployment_id

        # Tick 1: error 1
        results = await scheduler.tick(date(2024, 1, 15))
        assert len(results) == 0
        rec = store.get_record(dep_id)
        assert rec.status == "running"  # Not yet error
        assert dep_id in scheduler._engines

        # Tick 2: error 2
        results = await scheduler.tick(date(2024, 1, 16))
        assert len(results) == 0
        rec = store.get_record(dep_id)
        assert rec.status == "running"
        assert dep_id in scheduler._engines

        # Tick 3: error 3 -> escalation
        results = await scheduler.tick(date(2024, 1, 17))
        assert len(results) == 0
        rec = store.get_record(dep_id)
        assert rec.status == "error"
        assert dep_id not in scheduler._engines  # Engine removed

    @pytest.mark.asyncio
    async def test_success_resets_error_count(self):
        """A successful tick after errors should reset the error counter."""
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())

        call_count = 0
        def _execute_side_effect(d):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("temporary failure")
            return {
                "date": str(d),
                "equity": 1_000_000.0,
                "cash": 500_000.0,
                "holdings": {},
                "weights": {},
                "prev_returns": {},
                "trades": [],
                "risk_events": [],
                "rebalanced": False,
            }

        engine = _make_mock_engine(spec)
        engine.execute_day.side_effect = _execute_side_effect
        scheduler._engines[record.deployment_id] = engine

        cal = _weekday_calendar()
        scheduler._calendars["cn_stock"] = cal

        dep_id = record.deployment_id

        # Error 1
        await scheduler.tick(date(2024, 1, 15))
        # Error 2
        await scheduler.tick(date(2024, 1, 16))
        # Success on tick 3 — resets error count
        results = await scheduler.tick(date(2024, 1, 17))
        assert len(results) == 1

        # Verify error count was reset
        row = store._conn.execute(
            "SELECT consecutive_errors FROM deployment_records WHERE deployment_id = ?",
            [dep_id],
        ).fetchone()
        assert row[0] == 0

        # Now 2 more errors should NOT trigger escalation (counter was reset)
        call_count = 0  # Reset for 2 more failures
        engine.execute_day.side_effect = RuntimeError("another failure")
        await scheduler.tick(date(2024, 1, 18))
        await scheduler.tick(date(2024, 1, 19))

        rec = store.get_record(dep_id)
        assert rec.status == "running"  # Still running, not error


class TestResumeAll:
    """test_resume_all: restores running deployments from DB."""

    @pytest.mark.asyncio
    async def test_restores_running_deployments(self):
        store = _make_store()

        # Create 3 deployments: 1 running, 1 stopped, 1 approved
        spec = _make_spec()
        store.save_spec(spec)

        rec_running = _make_record(spec, status="running", deployment_id="dep-running")
        store.save_record(rec_running)

        rec_stopped = _make_record(spec, status="stopped", deployment_id="dep-stopped")
        store.save_record(rec_stopped)

        rec_approved = _make_record(spec, status="approved", deployment_id="dep-approved")
        store.save_record(rec_approved)

        scheduler = Scheduler(store=store, data_chain=MagicMock())

        # Mock _start_engine to avoid real strategy instantiation
        started_ids = []

        async def mock_start_engine(dep_id):
            started_ids.append(dep_id)
            scheduler._engines[dep_id] = _make_mock_engine(spec)

        with patch.object(scheduler, "_start_engine", side_effect=mock_start_engine):
            restored = await scheduler.resume_all()

        # Only the running deployment should be restored
        assert restored == 1
        assert "dep-running" in started_ids
        assert "dep-stopped" not in started_ids
        assert "dep-approved" not in started_ids

    @pytest.mark.asyncio
    async def test_resume_all_handles_failures_gracefully(self):
        """If one deployment fails to restore, others still proceed."""
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)

        rec1 = _make_record(spec, status="running", deployment_id="dep-1")
        store.save_record(rec1)

        rec2 = _make_record(spec, status="running", deployment_id="dep-2")
        store.save_record(rec2)

        scheduler = Scheduler(store=store, data_chain=MagicMock())

        call_order = []

        async def mock_start_engine(dep_id):
            call_order.append(dep_id)
            if dep_id == "dep-1":
                raise RuntimeError("failed to restore dep-1")
            scheduler._engines[dep_id] = _make_mock_engine(spec)

        with patch.object(scheduler, "_start_engine", side_effect=mock_start_engine):
            restored = await scheduler.resume_all()

        # Only dep-2 should succeed
        assert restored == 1
        assert len(call_order) == 2

    @pytest.mark.asyncio
    async def test_resume_all_fail_closes_historical_non_cn_spec_with_cn_rules(self):
        store = _make_store()
        spec = _make_spec(
            market="us_stock",
            t_plus_1=True,
            stamp_tax_rate=0.0005,
            price_limit_pct=0.10,
            lot_size=100,
        )
        store.save_spec(spec)

        record = _make_record(spec, status="running", deployment_id="dep-bad-spec")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())

        restored = await scheduler.resume_all()

        assert restored == 0
        assert record.deployment_id not in scheduler._engines
        persisted = store.get_record(record.deployment_id)
        assert persisted is not None
        assert persisted.status == "error"
        assert persisted.stop_reason == "恢复引擎失败"


class TestPauseResume:
    """Test pause and resume lifecycle."""

    @pytest.mark.asyncio
    async def test_pause_adds_to_paused_set(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine

        await scheduler.pause_deployment(record.deployment_id)
        assert record.deployment_id in scheduler._paused

        rec = store.get_record(record.deployment_id)
        assert rec.status == "paused"

    @pytest.mark.asyncio
    async def test_resume_removes_from_paused(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="paused")  # must be paused to resume
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine
        scheduler._paused.add(record.deployment_id)

        await scheduler.resume_deployment(record.deployment_id)
        assert record.deployment_id not in scheduler._paused

        rec = store.get_record(record.deployment_id)
        assert rec.status == "running"

    @pytest.mark.asyncio
    async def test_pause_nonexistent_raises(self):
        store = _make_store()
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with pytest.raises(ValueError, match="not running"):
            await scheduler.pause_deployment("no-such-id")


class TestStopDeployment:
    """Test stop lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_removes_engine(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine

        await scheduler.stop_deployment(record.deployment_id, "manual stop")

        assert record.deployment_id not in scheduler._engines
        rec = store.get_record(record.deployment_id)
        assert rec.status == "stopped"
        assert rec.stop_reason == "manual stop"

    @pytest.mark.asyncio
    async def test_stop_with_liquidation_persists_liquidation_snapshot(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        engine.cash = 100_000.0
        engine.holdings = {"000001.SZ": 1000}
        engine._last_prices = {"000001.SZ": 10.0}
        scheduler._engines[record.deployment_id] = engine

        await scheduler.stop_deployment(record.deployment_id, "manual stop", liquidate=True)

        latest = store.get_latest_snapshot(record.deployment_id)
        assert latest is not None
        assert latest["liquidation"] is True
        assert latest["holdings"] == {}
        assert latest["trades"][0]["symbol"] == "000001.SZ"


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_order_resolves_client_order_link_and_logs_event(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1", "enable_cancel": True}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _FakeShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        result = await scheduler.cancel_order(
            record.deployment_id,
            client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
        )

        assert result["status"] == "cancel_requested"
        assert shadow_broker.cancel_calls == [("SYS-001", "000001.SZ")]
        cancel_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_CANCEL_REQUESTED
        ]
        assert len(cancel_events) == 1
        assert cancel_events[0].payload["broker_order_id"] == "SYS-001"

    @pytest.mark.asyncio
    async def test_cancel_order_can_resolve_by_broker_order_id(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1", "enable_cancel": True}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _FakeShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        result = await scheduler.cancel_order(
            record.deployment_id,
            broker_order_id="SYS-001",
        )

        assert result["client_order_id"] == "dep-shadow:2024-01-15:000001.SZ:buy"
        assert shadow_broker.cancel_calls == [("SYS-001", "000001.SZ")]

    @pytest.mark.asyncio
    async def test_cancel_order_prefers_execution_broker_when_shadow_is_not_cancel_capable(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        class _NoCancelShadowBroker(_OwningShadowBroker):
            @property
            def capabilities(self):
                return frozenset(
                    {
                        BrokerCapability.READ_ACCOUNT_STATE,
                        BrokerCapability.SHADOW_MODE,
                        BrokerCapability.STREAM_EXECUTION_REPORTS,
                    }
                )

        shadow_broker = _NoCancelShadowBroker()
        real_broker = _AccountScopedOwningQmtBroker(
            account_id="acct-real",
            capabilities=frozenset(
                {
                    BrokerCapability.TARGET_WEIGHT_EXECUTION,
                    BrokerCapability.CANCEL_ORDER,
                }
            ),
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._build_execution_broker = MagicMock(return_value=real_broker)

        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-REAL-001:reported:0:1000:2024-01-15T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-real:2024-01-15:000001.SZ:buy",
                    broker_order_id="SYS-REAL-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=12.34,
                )
            ],
        )

        result = await scheduler.cancel_order(
            record.deployment_id,
            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
        )

        assert result["status"] == "cancel_requested"
        assert shadow_broker.cancel_calls == []
        assert real_broker.cancel_calls == [("SYS-REAL-001", "000001.SZ")]

    @pytest.mark.asyncio
    async def test_cancel_order_prefers_execution_broker_for_real_qmt_when_both_support_cancel(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow", "enable_cancel": True},
                "qmt_real_broker_config": {"account_id": "acct-real", "enable_cancel": True},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _OwningShadowBroker()
        real_broker = _AccountScopedOwningQmtBroker(
            account_id="acct-real",
            capabilities=frozenset(
                {
                    BrokerCapability.TARGET_WEIGHT_EXECUTION,
                    BrokerCapability.CANCEL_ORDER,
                }
            ),
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._build_execution_broker = MagicMock(return_value=real_broker)

        store.save_broker_sync_result(
            deployment_id=record.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-REAL-001:reported:0:1000:2024-01-15T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-real:2024-01-15:000001.SZ:buy",
                    broker_order_id="SYS-REAL-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=12.34,
                )
            ],
        )

        result = await scheduler.cancel_order(
            record.deployment_id,
            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
        )

        assert result["status"] == "cancel_requested"
        assert shadow_broker.cancel_calls == []
        assert real_broker.cancel_calls == [("SYS-REAL-001", "000001.SZ")]

    @pytest.mark.asyncio
    async def test_cancel_order_persists_cancel_pending_and_blocks_duplicates(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1", "enable_cancel": True}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _FakeShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        first = await scheduler.cancel_order(
            record.deployment_id,
            broker_order_id="SYS-001",
        )

        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
        )
        assert first["status"] == "cancel_requested"
        assert link is not None
        assert link["latest_status"] == "partially_filled_cancel_pending"

        with pytest.raises(ValueError, match="already in cancel-inflight"):
            await scheduler.cancel_order(
                record.deployment_id,
                broker_order_id="SYS-001",
            )

        assert shadow_broker.cancel_calls == [("SYS-001", "000001.SZ")]

    @pytest.mark.asyncio
    async def test_cancel_order_reopens_link_when_broker_rejects_request(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1", "enable_cancel": True}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        class _RejectingShadowBroker(_FakeShadowBroker):
            def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
                self.cancel_calls.append((order_id, symbol))
                return False

        shadow_broker = _RejectingShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        with pytest.raises(RuntimeError, match="rejected cancel request"):
            await scheduler.cancel_order(
                record.deployment_id,
                broker_order_id="SYS-001",
            )

        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
        )
        runtime_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_RUNTIME_RECORDED
        ]
        assert link is not None
        assert link["latest_status"] == "partially_filled"
        assert any(
            event.payload["runtime_kind"] == "cancel_error"
            for event in runtime_events
        )

    @pytest.mark.asyncio
    async def test_cancel_order_allows_retry_after_cancel_error_runtime(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1", "enable_cancel": True}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _FakeShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        await scheduler.cancel_order(
            record.deployment_id,
            broker_order_id="SYS-001",
        )
        store.append_event(
            make_broker_runtime_event(
                record.deployment_id,
                runtime_event_id="cancel_error:SYS-001",
                broker_type="qmt",
                runtime_kind="cancel_error",
                event_ts=datetime(2024, 1, 15, 15, 1, tzinfo=timezone.utc),
                payload={
                    "client_order_id": "dep-shadow:2024-01-15:000001.SZ:buy",
                    "order_sysid": "SYS-001",
                    "status_msg": "cancel rejected",
                },
            )
        )

        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
        )
        assert link is not None
        assert link["latest_status"] == "partially_filled"

        second = await scheduler.cancel_order(
            record.deployment_id,
            broker_order_id="SYS-001",
        )

        assert second["status"] == "cancel_requested"
        assert shadow_broker.cancel_calls == [
            ("SYS-001", "000001.SZ"),
            ("SYS-001", "000001.SZ"),
        ]

    @pytest.mark.asyncio
    async def test_real_qmt_cancel_ack_advances_pending_before_terminal_report_without_manual_sync(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-shadow", "enable_cancel": True},
                "qmt_real_broker_config": {"account_id": "acct-real", "enable_cancel": True},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        class _CancelState:
            def __init__(self):
                self.cancel_requested = False
                self.cancel_finalized = False

            def request_cancel(self) -> None:
                self.cancel_requested = True

            def finalize_cancel(self) -> None:
                self.cancel_finalized = True

        class _RealCancelBroker(_AccountScopedOwningQmtBroker):
            def __init__(self, *, state: _CancelState):
                super().__init__(
                    account_id="acct-real",
                    capabilities=frozenset(
                        {
                            BrokerCapability.READ_ACCOUNT_STATE,
                            BrokerCapability.STREAM_EXECUTION_REPORTS,
                            BrokerCapability.TARGET_WEIGHT_EXECUTION,
                            BrokerCapability.CANCEL_ORDER,
                        }
                    ),
                )
                self._state = state

            def snapshot_account_state(self):
                open_orders: list[dict[str, object]] = []
                if not self._state.cancel_finalized:
                    open_orders.append(
                        {
                            "client_order_id": "dep-real:2024-01-15:000001.SZ:buy",
                            "broker_order_id": "SYS-REAL-001",
                            "symbol": "000001.SZ",
                            "status": (
                                "reported_cancel_pending"
                                if self._state.cancel_requested
                                else "reported"
                            ),
                            "requested_shares": 1000,
                            "filled_shares": 0,
                            "remaining_shares": 1000,
                            "avg_price": 12.34,
                            "updated_at": "2024-01-15T15:00:00+00:00",
                        }
                    )
                return BrokerAccountSnapshot(
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 2, tzinfo=timezone.utc),
                    cash=1_000_000.0,
                    total_asset=1_000_000.0,
                    positions={},
                    open_orders=open_orders,
                    fills=[],
                    account_id="acct-real",
                )

            def list_runtime_events(self, *, since=None):
                events = [
                    BrokerRuntimeEvent(
                        event_id="session_connected:acct-real:2024-01-15T14:57:00+00:00",
                        broker_type="qmt",
                        as_of=datetime(2024, 1, 15, 14, 57, tzinfo=timezone.utc),
                        event_kind="session_connected",
                        payload={
                            "_report_kind": "session_connected",
                            "account_id": "acct-real",
                            "status": "connected",
                        },
                    ),
                    BrokerRuntimeEvent(
                        event_id="account_status:acct-real:connected:2024-01-15T14:59:00+00:00",
                        broker_type="qmt",
                        as_of=datetime(2024, 1, 15, 14, 59, tzinfo=timezone.utc),
                        event_kind="account_status",
                        payload={
                            "_report_kind": "account_status",
                            "account_id": "acct-real",
                            "account_type": "STOCK",
                            "status": "connected",
                        },
                    ),
                    BrokerRuntimeEvent(
                        event_id="session_consumer_state:acct-real:2024-01-15T14:59:30+00:00",
                        broker_type="qmt",
                        as_of=datetime(2024, 1, 15, 14, 59, 30, tzinfo=timezone.utc),
                        event_kind="session_consumer_state",
                        payload={
                            "_report_kind": "session_consumer_state",
                            "account_id": "acct-real",
                            "status": "connected",
                            "consumer_status": "running",
                            "account_sync_mode": "callback_preferred",
                            "asset_callback_freshness": "fresh",
                        },
                    ),
                ]
                if self._state.cancel_requested:
                    events.append(
                        BrokerRuntimeEvent(
                            event_id="cancel_async:acct-real:SYS-REAL-001:2024-01-15T15:00:30+00:00",
                            broker_type="qmt",
                            as_of=datetime(2024, 1, 15, 15, 0, 30, tzinfo=timezone.utc),
                            event_kind="cancel_order_stock_async_response",
                            payload={
                                "_report_kind": "cancel_order_stock_async_response",
                                "account_id": "acct-real",
                                "account_type": "STOCK",
                                "client_order_id": "dep-real:2024-01-15:000001.SZ:buy",
                                "order_id": 700,
                                "order_sysid": "SYS-REAL-001",
                                "cancel_result": 0,
                                "seq": 900,
                            },
                        )
                    )
                if since is not None:
                    events = [event for event in events if event.as_of >= since]
                return events

            def list_execution_reports(self, *, since=None):
                reports = [
                    BrokerExecutionReport(
                        report_id="qmt:SYS-REAL-001:reported:0:1000:2024-01-15T15:00:00+00:00",
                        broker_type="qmt",
                        as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                        client_order_id="dep-real:2024-01-15:000001.SZ:buy",
                        broker_order_id="SYS-REAL-001",
                        symbol="000001.SZ",
                        side="buy",
                        status="reported",
                        filled_shares=0,
                        remaining_shares=1000,
                        avg_price=12.34,
                        message="submitted",
                        raw_payload={
                            "order_sysid": "SYS-REAL-001",
                            "order_remark": "dep-real:2024-01-15:000001.SZ:buy",
                        },
                    )
                ]
                if self._state.cancel_finalized:
                    reports.append(
                        BrokerExecutionReport(
                            report_id="qmt:SYS-REAL-001:canceled:0:0:2024-01-16T15:00:00+00:00",
                            broker_type="qmt",
                            as_of=datetime(2024, 1, 16, 15, 0, tzinfo=timezone.utc),
                            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
                            broker_order_id="SYS-REAL-001",
                            symbol="000001.SZ",
                            side="buy",
                            status="canceled",
                            filled_shares=0,
                            remaining_shares=0,
                            avg_price=12.34,
                            message="canceled",
                            raw_payload={
                                "order_sysid": "SYS-REAL-001",
                                "order_remark": "dep-real:2024-01-15:000001.SZ:buy",
                                "status": "canceled",
                            },
                        )
                    )
                if since is not None:
                    reports = [report for report in reports if report.as_of >= since]
                return reports

            def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
                del symbol
                self._state.request_cancel()
                return order_id == "SYS-REAL-001"

        state = _CancelState()
        shadow_broker = _AccountScopedOwningQmtBroker(
            account_id="acct-shadow",
            total_asset=140_000.0,
            capabilities=frozenset(
                {
                    BrokerCapability.READ_ACCOUNT_STATE,
                    BrokerCapability.STREAM_EXECUTION_REPORTS,
                    BrokerCapability.SHADOW_MODE,
                    BrokerCapability.CANCEL_ORDER,
                }
            ),
        )
        real_broker = _RealCancelBroker(state=state)
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._build_execution_broker = MagicMock(return_value=real_broker)
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.start_deployment(record.deployment_id)
        cancel_result = await scheduler.cancel_order(
            record.deployment_id,
            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
        )
        assert cancel_result["status"] == "cancel_requested"

        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
        )
        assert link is not None
        assert link["latest_status"] == "reported_cancel_pending"

        await scheduler.tick(date(2024, 1, 16))

        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
        )
        assert link is not None
        assert link["latest_status"] == "reported_cancel_pending"
        runtime_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_RUNTIME_RECORDED
        ]
        assert any(
            event.payload["runtime_kind"] == "cancel_order_stock_async_response"
            for event in runtime_events
        )

        state.finalize_cancel()
        await scheduler.tick(date(2024, 1, 17))

        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
        )
        assert link is not None
        assert link["latest_status"] == "canceled"

    @pytest.mark.asyncio
    async def test_real_qmt_collect_sync_state_uses_callback_cursor_for_out_of_order_callbacks(self):
        class _CursorAwareRealQmtClient:
            def __init__(self):
                self.calls: list[dict[str, object]] = []

            def query_stock_asset(self, account_id):
                del account_id
                return {
                    "update_time": "2024-01-15T15:00:00+00:00",
                    "cash": 1_000_000.0,
                    "total_asset": 1_000_000.0,
                }

            def query_stock_positions(self, account_id):
                del account_id
                return []

            def query_stock_orders(self, account_id):
                del account_id
                return []

            def query_stock_trades(self, account_id):
                del account_id
                return []

            def collect_sync_state(self, *, since_reports=None, since_runtime=None, cursor_state=None):
                self.calls.append(
                    {
                        "since_reports": since_reports,
                        "since_runtime": since_runtime,
                        "cursor_state": dict(cursor_state or {}),
                    }
                )
                runtime_seq = int((cursor_state or {}).get("callback_runtime_seq") or 0)
                execution_seq = int((cursor_state or {}).get("callback_execution_seq") or 0)
                if runtime_seq == 0 and execution_seq == 0:
                    return {
                        "asset": {
                            "update_time": "2024-01-15T15:00:00+00:00",
                            "cash": 1_000_000.0,
                            "total_asset": 1_000_000.0,
                        },
                        "positions": [],
                        "orders": [],
                        "trades": [],
                        "execution_reports": [
                            BrokerExecutionReport(
                                report_id="qmt:SYS-REAL-001:reported:0:1000:2024-01-15T15:00:00+00:00",
                                broker_type="qmt",
                                as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                                client_order_id="dep-real:2024-01-15:000001.SZ:buy",
                                broker_order_id="SYS-REAL-001",
                                symbol="000001.SZ",
                                side="buy",
                                status="reported",
                                filled_shares=0,
                                remaining_shares=1000,
                                avg_price=12.34,
                                message="submitted",
                                raw_payload={
                                    "order_sysid": "SYS-REAL-001",
                                    "order_remark": "dep-real:2024-01-15:000001.SZ:buy",
                                },
                            )
                        ],
                        "runtime_events": [
                            {
                                "_report_kind": "session_consumer_state",
                                "update_time": "2024-01-15T15:00:00+00:00",
                                "account_id": "acct-real",
                                "status": "connected",
                                "consumer_status": "running",
                                "account_sync_mode": "callback_preferred",
                                "asset_callback_freshness": "fresh",
                            }
                        ],
                        "cursor_state": {
                            "callback_runtime_seq": 1,
                            "callback_execution_seq": 1,
                        },
                    }
                assert runtime_seq == 1 and execution_seq == 1
                return {
                    "asset": {
                        "update_time": "2024-01-15T14:59:59+00:00",
                        "cash": 1_000_000.0,
                        "total_asset": 1_000_000.0,
                    },
                    "positions": [],
                    "orders": [],
                    "trades": [],
                    "execution_reports": [
                        BrokerExecutionReport(
                            report_id="qmt:SYS-REAL-001:canceled:0:0:2024-01-15T14:59:59+00:00",
                            broker_type="qmt",
                            as_of=datetime(2024, 1, 15, 14, 59, 59, tzinfo=timezone.utc),
                            client_order_id="dep-real:2024-01-15:000001.SZ:buy",
                            broker_order_id="SYS-REAL-001",
                            symbol="000001.SZ",
                            side="buy",
                            status="canceled",
                            filled_shares=0,
                            remaining_shares=0,
                            avg_price=12.34,
                            message="canceled",
                            raw_payload={
                                "order_sysid": "SYS-REAL-001",
                                "order_remark": "dep-real:2024-01-15:000001.SZ:buy",
                                "status": "canceled",
                            },
                        )
                    ],
                    "runtime_events": [
                        {
                            "_report_kind": "cancel_order_stock_async_response",
                            "update_time": "2024-01-15T14:59:59+00:00",
                            "account_id": "acct-real",
                            "account_type": "STOCK",
                            "client_order_id": "dep-real:2024-01-15:000001.SZ:buy",
                            "order_id": 700,
                            "order_sysid": "SYS-REAL-001",
                            "cancel_result": 0,
                            "seq": 900,
                        }
                    ],
                    "cursor_state": {
                        "callback_runtime_seq": 2,
                        "callback_execution_seq": 2,
                    },
                }

        client = _CursorAwareRealQmtClient()
        broker = QMTRealBroker(
            config=QMTBrokerConfig(account_id="acct-real", enable_cancel=True),
            client=client,
        )

        first = broker.collect_sync_state(
            cursor_state={
                "callback_runtime_seq": 0,
                "callback_execution_seq": 0,
            },
        )
        assert first.cursor_state == {
            "callback_runtime_seq": 1,
            "callback_execution_seq": 1,
        }
        assert [report.status for report in first.execution_reports] == ["reported"]
        assert [event.event_kind for event in first.runtime_events] == [
            "session_consumer_state",
        ]

        second = broker.collect_sync_state(
            cursor_state=first.cursor_state,
        )
        assert second.cursor_state == {
            "callback_runtime_seq": 2,
            "callback_execution_seq": 2,
        }
        assert client.calls[1]["cursor_state"] == first.cursor_state
        assert [report.status for report in second.execution_reports] == ["canceled"]
        assert [event.event_kind for event in second.runtime_events] == [
            "cancel_order_stock_async_response",
        ]
        assert second.execution_reports[0].report_id.endswith("14:59:59+00:00")
        assert second.runtime_events[0].event_id.endswith("14:59:59+00:00")

    @pytest.mark.asyncio
    async def test_cancel_order_followed_by_canceled_report_advances_link_status(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1", "enable_cancel": True}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _CancelableShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        await scheduler.tick(date(2024, 1, 15))
        await scheduler.cancel_order(
            record.deployment_id,
            broker_order_id="SYS-001",
        )
        await scheduler.tick(date(2024, 1, 16))

        link = store.find_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            broker_order_id="SYS-001",
        )
        assert link is not None
        assert link["latest_status"] == "canceled"
        broker_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.BROKER_EXECUTION_RECORDED
        ]
        assert any(event.payload["status"] == "canceled" for event in broker_events)


# ---------------------------------------------------------------------------
# V3 regression: tick atomicity, deployment lock serialization, CN-rule
# mismatch fail-close.
# ---------------------------------------------------------------------------


class TestTickAtomicity:
    """save_snapshot_with_events must roll back on failure — no orphan events."""

    @pytest.mark.asyncio
    async def test_tick_rolls_back_all_events_when_snapshot_write_fails(self):
        """If the atomic write raises mid-transaction, no events survive.

        Scheduler.tick() calls store.save_snapshot_with_events(...) which
        opens a DuckDB transaction and writes events + snapshot + broker
        order links in one go. Forcing the snapshot step to raise must
        abort the whole transaction so recovery never observes orphan
        events without a matching snapshot checkpoint.
        """
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        # Events visible to get_events() BEFORE the tick.
        events_before = store.get_events(record.deployment_id)
        assert events_before == []

        # Monkeypatch the atomic write to raise. The real implementation
        # wraps events + snapshot in a single DuckDB transaction and
        # rollbacks on any exception.
        original_write = store._write_snapshot_locked

        def _failing_write(**kwargs):
            raise RuntimeError("disk full")

        store._write_snapshot_locked = _failing_write
        try:
            # Tick must not raise — Scheduler catches the error and
            # records an error snapshot instead.
            await scheduler.tick(date(2024, 1, 15))
        finally:
            store._write_snapshot_locked = original_write

        # Transaction was rolled back — no orphan events.
        events_after = store.get_events(record.deployment_id)
        # Only error-path writes (via a separate store.save_error call)
        # are allowed to persist. No OMS / snapshot / tick_completed events.
        for event in events_after:
            assert event.event_type != EventType.SNAPSHOT_SAVED, (
                f"orphan snapshot event survived rollback: {event.event_id}"
            )
            assert event.event_type != EventType.TICK_COMPLETED, (
                f"orphan tick_completed event survived rollback: {event.event_id}"
            )
            # OMS events (submitted/filled) should also be rolled back
            # because they are part of the same atomic write.
            assert event.event_type != EventType.ORDER_SUBMITTED, (
                f"orphan order_submitted event survived rollback: {event.event_id}"
            )

        # Error count must have advanced (recovery path fired).
        assert store.get_error_count(record.deployment_id) == 1

        # save_error intentionally advances last_processed_date so the
        # scheduler does not re-attempt a known-failing day. The key
        # invariant this test protects is that NO snapshot + no OMS
        # events leaked through the rollback, which is asserted above.
        # last_processed_date should have advanced as the error path fired.
        last_date = store.get_last_processed_date(record.deployment_id)
        assert last_date == date(2024, 1, 15)


class TestSchedulerDeploymentLock:
    """asyncio.Lock must serialize tick() and cancel_order() across tasks."""

    @pytest.mark.asyncio
    async def test_concurrent_tick_and_cancel_are_serialized_via_shared_lock(self):
        """Start tick + cancel concurrently; both should complete without
        data corruption, and total work is serial (not interleaved).
        """
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1", "enable_cancel": True},
            },
        )
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        shadow_broker = _FakeShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset(
                        {BrokerCapability.TARGET_WEIGHT_EXECUTION}
                    )
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        # Seed a partial_filled link so cancel_order has a target.
        await scheduler.tick(date(2024, 1, 15))

        # Launch a second tick (same date -> idempotent skip) and a
        # cancel concurrently. Both must succeed because the lock
        # serializes them instead of letting them corrupt each other.
        tick_task = asyncio.create_task(scheduler.tick(date(2024, 1, 16)))
        cancel_task = asyncio.create_task(
            scheduler.cancel_order(
                record.deployment_id,
                broker_order_id="SYS-001",
            )
        )
        results = await asyncio.gather(tick_task, cancel_task, return_exceptions=True)
        # Neither task should have raised.
        for res in results:
            assert not isinstance(res, Exception), f"Task raised: {res!r}"

        # Link advanced to cancel-pending exactly once.
        link = store.get_broker_order_link(
            record.deployment_id,
            broker_type="qmt",
            client_order_id="dep-shadow:2024-01-15:000001.SZ:buy",
        )
        assert link is not None
        assert link["latest_status"] in {
            "partially_filled_cancel_pending",
            "reported_cancel_pending",
        }
        assert shadow_broker.cancel_calls == [("SYS-001", "000001.SZ")]

    @pytest.mark.asyncio
    async def test_lock_blocks_reentrant_tick_within_same_deployment(self):
        """Two tick() calls for the same business_date: first must run,
        second must observe the idempotent last_processed_date guard and
        skip. The asyncio.Lock ensures the guard is not raced.
        """
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        record = _make_record(spec, status="running")
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        engine = _make_mock_engine(spec)
        scheduler._engines[record.deployment_id] = engine
        scheduler._calendars["cn_stock"] = _weekday_calendar()

        biz_date = date(2024, 1, 15)
        tick_a = asyncio.create_task(scheduler.tick(biz_date))
        tick_b = asyncio.create_task(scheduler.tick(biz_date))
        result_a, result_b = await asyncio.gather(tick_a, tick_b)

        # Exactly one of the two ticks produced a result row.
        total_results = len(result_a) + len(result_b)
        assert total_results == 1, (
            f"Expected exactly one tick to execute, got {total_results}"
        )
        # engine.execute_day was called only once despite two concurrent ticks.
        assert engine.execute_day.call_count == 1


class TestStartDeploymentCnRuleMismatchFailClose:
    """Historical bad specs (non-CN market + CN defaults) must fail closed."""

    @pytest.mark.asyncio
    async def test_start_deployment_refuses_non_cn_spec_with_cn_defaults(self):
        """Specs built by the old _build_spec_from_run bug window carried
        T+1 / stamp_tax / limit_pct even on US / HK markets. Starting such
        a deployment must raise instead of silently running with CN rules.
        """
        store = _make_store()
        spec = DeploymentSpec(
            strategy_name="TopNRotation",
            strategy_params={"top_n": 5},
            symbols=("AAPL",),
            market="us_stock",
            freq="daily",
            initial_cash=100_000.0,
            # BUG shape: US market but CN rule defaults leaked through.
            t_plus_1=True,
            stamp_tax_rate=0.0005,
            price_limit_pct=0.1,
            lot_size=100,
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())

        with pytest.raises(ValueError, match="A-share market rules"):
            await scheduler.start_deployment(record.deployment_id)

        # Status must stay approved — no phantom "running".
        rec_after = store.get_record(record.deployment_id)
        assert rec_after.status == "approved"
        # Engine must NOT be loaded.
        assert record.deployment_id not in scheduler._engines

    @pytest.mark.asyncio
    async def test_start_deployment_allows_clean_us_spec_without_cn_rules(self):
        """Corresponding happy path: a non-CN spec with all CN rules zeroed
        out must start without the fail-close check firing.
        """
        store = _make_store()
        spec = DeploymentSpec(
            strategy_name="TopNRotation",
            strategy_params={"top_n": 5},
            symbols=("AAPL",),
            market="us_stock",
            freq="daily",
            initial_cash=100_000.0,
            t_plus_1=False,
            stamp_tax_rate=0.0,
            price_limit_pct=0.0,
            lot_size=0,
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        scheduler = Scheduler(store=store, data_chain=MagicMock())

        # Must NOT raise the CN-rule fail-close. Other failures (e.g.
        # missing strategy class in this test harness) are still OK to
        # swallow — what matters is that the fail-close branch is NOT the
        # one that fired.
        try:
            await scheduler.start_deployment(record.deployment_id)
        except ValueError as exc:
            assert "A-share market rules" not in str(exc), (
                f"Clean US spec should not trigger CN fail-close: {exc}"
            )
        except Exception:
            # Any other exception (NameError / strategy loader issues) is
            # fine — we only care that the CN-rule guard didn't trigger.
            pass


# ---------------------------------------------------------------------------
# V3.3.44 — position / trade reconcile scheduler wiring
# ---------------------------------------------------------------------------


class _PositionTradeShadowBroker(_FakeShadowBroker):
    """Shadow broker that reports positions/trades the scheduler can reconcile
    against the engine's local holdings/trades.
    """

    def __init__(
        self,
        *,
        positions: dict[str, int] | None = None,
        fills: list[dict] | None = None,
    ):
        super().__init__()
        self._positions = dict(positions or {"000001.SZ": 1000})
        self._fills = list(fills or [])

    def snapshot_account_state(self):
        return BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
            cash=900_000.0,
            total_asset=1_000_000.0,
            positions=dict(self._positions),
            open_orders=[],
            fills=list(self._fills),
        )

    def list_execution_reports(self, *, since=None):
        return []


class TestFourWayReconcileScheduler:
    @pytest.mark.asyncio
    async def test_pump_broker_state_persists_position_and_trade_risk_events(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        shadow_broker = _PositionTradeShadowBroker(
            positions={"000001.SZ": 1000},
            fills=[
                {
                    "traded_id": "T-1",
                    "symbol": "000001.SZ",
                    "side": "buy",
                    "shares": 1000,
                    "price": 10.0,
                }
            ],
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        # Engine matches broker position (1000 shares / 1 buy fill of 1000@10.0)
        engine.holdings = {"000001.SZ": 1000}
        engine.trades = [
            {
                "symbol": "000001.SZ",
                "side": "buy",
                "shares": 1000,
                "price": 10.0,
                "cost": 0.0,
                "amount": 10000.0,
            }
        ]
        scheduler._engines[record.deployment_id] = engine

        await scheduler.pump_broker_state(record.deployment_id)

        # Pull all risk events and filter for the V3.3.44 names.
        risk_events = [
            event
            for event in store.get_events(record.deployment_id)
            if event.event_type == EventType.RISK_RECORDED
        ]
        event_names = [
            event.payload.get("risk_event", {}).get("event") for event in risk_events
        ]
        assert "broker_reconcile" in event_names
        assert "broker_order_reconcile" in event_names
        assert "position_reconcile" in event_names
        assert "trade_reconcile" in event_names
        assert "qmt_reconcile_hard_gate" in event_names

    @pytest.mark.asyncio
    async def test_pump_broker_state_blocks_hard_gate_on_position_drift(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        # Broker says 800 shares but local says 1000 — position drift.
        shadow_broker = _PositionTradeShadowBroker(
            positions={"000001.SZ": 800},
            fills=[],
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        engine.holdings = {"000001.SZ": 1000}
        engine.trades = []
        scheduler._engines[record.deployment_id] = engine

        result = await scheduler.pump_broker_state(record.deployment_id)

        assert result["position_reconcile_status"] == "drift"
        assert result["qmt_hard_gate_status"] == "blocked"

        # Persisted projection also surfaces the drift.
        projection = store.get_broker_state_projection(
            record.deployment_id, broker_type="qmt"
        )
        assert projection is not None
        assert projection["latest_position_reconcile"]["status"] == "drift"
        assert projection["latest_qmt_hard_gate"]["status"] == "blocked"
        assert "position_reconcile_drift" in projection["latest_qmt_hard_gate"]["blockers"]

    @pytest.mark.asyncio
    async def test_pump_broker_state_blocks_hard_gate_on_trade_drift(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        # Broker-reported trade that local never acked.
        shadow_broker = _PositionTradeShadowBroker(
            positions={},
            fills=[
                {
                    "traded_id": "T-broker-only",
                    "symbol": "000001.SZ",
                    "side": "buy",
                    "shares": 500,
                    "price": 10.0,
                }
            ],
        )
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        engine.holdings = {}
        engine.trades = []  # Local has no fills
        scheduler._engines[record.deployment_id] = engine

        result = await scheduler.pump_broker_state(record.deployment_id)

        assert result["trade_reconcile_status"] == "drift"
        assert result["qmt_hard_gate_status"] == "blocked"

        projection = store.get_broker_state_projection(
            record.deployment_id, broker_type="qmt"
        )
        assert projection is not None
        assert projection["latest_trade_reconcile"]["status"] == "drift"
        assert "trade_reconcile_drift" in projection["latest_qmt_hard_gate"]["blockers"]

    @pytest.mark.asyncio
    async def test_pump_broker_state_projection_surfaces_position_trade_status(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={"shadow_broker_config": {"account_id": "acct-1"}},
        )
        store.save_spec(spec)
        record = _make_record(spec, status="approved")
        store.save_record(record)

        class _MatchingShadowBroker(_FakeShadowBroker):
            """Shadow broker whose snapshot matches a specific engine state."""

            def snapshot_account_state(self):
                return BrokerAccountSnapshot(
                    broker_type="qmt",
                    as_of=datetime(2024, 1, 15, 15, 0, tzinfo=timezone.utc),
                    cash=995_000.0,
                    total_asset=1_000_000.0,
                    positions={"000001.SZ": 500},
                    open_orders=[],
                    fills=[
                        {
                            "traded_id": "T-1",
                            "symbol": "000001.SZ",
                            "side": "buy",
                            "shares": 500,
                            "price": 10.0,
                        }
                    ],
                )

            def list_execution_reports(self, *, since=None):
                return []

        shadow_broker = _MatchingShadowBroker()
        scheduler = Scheduler(
            store=store,
            data_chain=MagicMock(),
            broker_factories={
                "paper": lambda _spec: MagicMock(
                    capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION})
                ),
                "qmt": lambda _spec: shadow_broker,
            },
        )
        engine = _make_mock_engine(spec)
        engine.shadow_broker = shadow_broker
        engine.cash = 995_000.0
        engine.holdings = {"000001.SZ": 500}
        engine._last_prices = {"000001.SZ": 10.0}
        engine._mark_to_market.return_value = 1_000_000.0
        engine.trades = [
            {
                "symbol": "000001.SZ",
                "side": "buy",
                "shares": 500,
                "price": 10.0,
                "cost": 0.0,
                "amount": 5000.0,
            }
        ]
        scheduler._engines[record.deployment_id] = engine

        result = await scheduler.pump_broker_state(record.deployment_id)

        assert result["position_reconcile_status"] == "ok"
        assert result["trade_reconcile_status"] == "ok"
        # All four reconciles ok → hard gate open.
        assert result["reconcile_status"] == "ok"
        assert result["order_reconcile_status"] == "ok"
        assert result["qmt_hard_gate_status"] == "open"
        projection = store.get_broker_state_projection(
            record.deployment_id, broker_type="qmt"
        )
        assert projection["position_reconcile_status"] == "ok"
        assert projection["trade_reconcile_status"] == "ok"
