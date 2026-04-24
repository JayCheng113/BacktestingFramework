"""V3 end-to-end lifecycle regression through the real Scheduler.

Complements ``tests/test_api/test_live_api.py::TestLiveApiRealE2E`` (which
drives the full HTTP API) with scheduler-level lifecycle coverage. The two
test classes share the paper+shadow-broker fixture shape, but this one
skips the FastAPI stack and exercises ``Scheduler`` methods directly so a
regression that bypasses the API (for example a programmatic consumer of
``ez.live.scheduler.Scheduler``) is still caught.

Scope:

1. ``deploy -> approve -> start -> tick -> cancel -> pause -> resume ->
   stop(liquidate=True)`` on a paper-broker scheduler with store-level
   assertions at every step.
2. Idempotency canary: rerunning the same tick date must not double-book
   snapshots or emit redundant ``tick_completed`` events.
3. Restart recovery canary: swapping the ``Scheduler`` instance mid-run
   must rehydrate ``_engines`` via ``resume_all()`` without losing the
   broker-order link shape.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import duckdb
import pandas as pd
import pytest

from ez.live.broker import (
    BrokerAccountSnapshot,
    BrokerCapability,
    BrokerExecutionReport,
    BrokerRuntimeEvent,
)
from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.events import EventType
from ez.live.paper_broker import PaperBroker
from ez.live.scheduler import Scheduler
from ez.portfolio.calendar import TradingCalendar


# ---------------------------------------------------------------------------
# Fake data chain + fake bar + fake cancel-capable shadow broker
# ---------------------------------------------------------------------------


class _FakeBar:
    def __init__(self, time, open, high, low, close, adj_close, volume):
        self.time = time
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.adj_close = adj_close
        self.volume = volume


class _FakeDataChain:
    def __init__(self, symbol_bars: dict):
        self._symbol_bars = symbol_bars

    def get_kline(self, symbol, market, period, start_date, end_date):
        bars = self._symbol_bars.get(symbol, [])
        return [b for b in bars if start_date <= b.time.date() <= end_date]


def _make_symbol_bars(symbols: list[str], trading_days: list[date]) -> dict:
    out: dict = {}
    for idx, symbol in enumerate(symbols):
        rows = []
        for day_idx, day in enumerate(trading_days):
            price = 10.0 + idx * 0.5 + day_idx * (0.02 + idx * 0.005)
            rows.append(
                _FakeBar(
                    time=datetime.combine(day, datetime.min.time()),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    adj_close=price,
                    volume=1000,
                )
            )
        out[symbol] = rows
    return out


class _V3CancelShadowBroker:
    """QMT-compatible cancel-capable shadow broker over PaperBroker semantics.

    PaperBroker itself has no CANCEL_ORDER capability; this shadow companion
    exposes a fake open order that the scheduler's cancel path can target.
    """

    broker_type = "qmt"

    def __init__(self):
        self._open_orders: list[dict] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self._as_of = datetime(2024, 6, 28, 15, 2, tzinfo=timezone.utc)

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

    def add_open_order(self, *, client_order_id: str, symbol: str, shares: int) -> None:
        self._open_orders.append(
            {
                "client_order_id": client_order_id,
                "broker_order_id": f"SYS-V3E2E-{len(self._open_orders) + 1:03d}",
                "symbol": symbol,
                "status": "reported",
                "requested_shares": int(shares),
                "filled_shares": 0,
                "remaining_shares": int(shares),
                "avg_price": 10.0,
                "updated_at": self._as_of.isoformat(),
            }
        )

    def snapshot_account_state(self):
        return BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=self._as_of,
            cash=0.0,
            total_asset=0.0,
            positions={},
            open_orders=list(self._open_orders),
            fills=[],
        )

    def list_runtime_events(self, *, since=None):
        evts = [
            BrokerRuntimeEvent(
                event_id="account_status:acct-v3:connected:2024-06-28T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-v3",
                    "status": "connected",
                },
            )
        ]
        if since is not None:
            evts = [e for e in evts if e.as_of >= since]
        return evts

    def list_execution_reports(self, *, since=None):
        reports = []
        for order in self._open_orders:
            reports.append(
                BrokerExecutionReport(
                    report_id=(
                        f"qmt:{order['broker_order_id']}:reported:0:"
                        f"{order['requested_shares']}:2024-06-28T15:00:00+00:00"
                    ),
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 28, 15, 0, tzinfo=timezone.utc),
                    client_order_id=order["client_order_id"],
                    broker_order_id=order["broker_order_id"],
                    symbol=order["symbol"],
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=int(order["requested_shares"]),
                    avg_price=float(order["avg_price"]),
                    message="pending",
                    raw_payload={"order_sysid": order["broker_order_id"]},
                )
            )
        if since is not None:
            reports = [r for r in reports if r.as_of >= since]
        return reports

    def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
        self.cancel_calls.append((order_id, symbol))
        return True


# ---------------------------------------------------------------------------
# Spec + record builders
# ---------------------------------------------------------------------------


def _make_v3_spec() -> DeploymentSpec:
    return DeploymentSpec(
        strategy_name="TopNRotation",
        strategy_params={"factor": "momentum_rank_20", "top_n": 3},
        symbols=("000001.SZ", "000002.SZ", "600000.SH"),
        market="cn_stock",
        freq="daily",
        initial_cash=1_000_000.0,
        buy_commission_rate=0.00008,
        sell_commission_rate=0.00008,
        stamp_tax_rate=0.0005,
        slippage_rate=0.0,
        min_commission=0.0,
        price_limit_pct=0.1,
        lot_size=100,
        t_plus_1=True,
        shadow_broker_type="qmt",
        risk_params={
            "shadow_broker_config": {
                "account_id": "acct-v3",
                "enable_cancel": True,
            },
        },
    )


def _make_record(spec: DeploymentSpec) -> DeploymentRecord:
    return DeploymentRecord(
        spec_id=spec.spec_id,
        name="V3 E2E lifecycle",
        status="approved",
        gate_verdict='{"passed": true, "reason": "test harness"}',
    )


@pytest.fixture
def v3_runtime():
    """Real scheduler + in-memory DuckDB + PaperBroker + cancel shadow."""
    trading_days = list(pd.bdate_range("2023-05-01", "2024-07-05").date)
    spec = _make_v3_spec()
    record = _make_record(spec)
    store = DeploymentStore(duckdb.connect(":memory:"))
    store.save_spec(spec)
    store.save_record(record)

    shadow_broker = _V3CancelShadowBroker()
    data_chain = _FakeDataChain(
        _make_symbol_bars(list(spec.symbols), trading_days)
    )
    scheduler = Scheduler(
        store=store,
        data_chain=data_chain,
        broker_factories={
            "paper": lambda _spec: PaperBroker(),
            "qmt": lambda _spec: shadow_broker,
        },
    )
    scheduler._calendars["cn_stock"] = TradingCalendar.weekday_fallback(
        date(2024, 1, 1), date(2024, 12, 31)
    )

    try:
        yield {
            "store": store,
            "scheduler": scheduler,
            "spec": spec,
            "record": record,
            "shadow_broker": shadow_broker,
            "trading_days": trading_days,
        }
    finally:
        store.close()


# ---------------------------------------------------------------------------
# E2E scheduler lifecycle coverage
# ---------------------------------------------------------------------------


class TestV3SchedulerLifecycleE2E:
    """End-to-end lifecycle through the real Scheduler API.

    Complements ``TestLiveApiRealE2E`` (HTTP path) so a regression that
    bypasses FastAPI is still caught at the scheduler layer.
    """

    @pytest.mark.asyncio
    async def test_full_lifecycle_start_tick_cancel_pause_resume_stop_liquidate(
        self, v3_runtime
    ):
        store = v3_runtime["store"]
        scheduler = v3_runtime["scheduler"]
        record = v3_runtime["record"]
        shadow_broker = v3_runtime["shadow_broker"]
        dep_id = record.deployment_id

        # --- Step 1: start_deployment ---
        await scheduler.start_deployment(dep_id)
        assert store.get_record(dep_id).status == "running"
        assert dep_id in scheduler._engines

        # --- Step 2: tick ---
        biz_date = date(2024, 6, 28)
        results = await scheduler.tick(biz_date)
        assert len(results) == 1
        assert results[0]["deployment_id"] == dep_id
        snapshots = store.get_all_snapshots(dep_id)
        assert len(snapshots) >= 1
        assert snapshots[-1]["equity"] > 0
        events_after_tick = store.get_events(dep_id)
        event_types = {event.event_type.value for event in events_after_tick}
        assert "tick_completed" in event_types
        assert "snapshot_saved" in event_types
        assert "order_submitted" in event_types
        assert store.get_last_processed_date(dep_id) == biz_date

        # --- Step 3: broker-sync + cancel_order ---
        canonical_cid = f"{dep_id}:2024-06-28:000001.SZ:buy"
        shadow_broker.add_open_order(
            client_order_id=canonical_cid,
            symbol="000001.SZ",
            shares=1000,
        )
        await scheduler.pump_broker_state(dep_id)
        links_before_cancel = store.list_broker_order_links(dep_id, broker_type="qmt")
        assert len(links_before_cancel) >= 1
        assert any(link["client_order_id"] == canonical_cid for link in links_before_cancel)

        cancel_result = await scheduler.cancel_order(
            dep_id,
            client_order_id=canonical_cid,
        )
        assert cancel_result["status"] == "cancel_requested"
        assert any(
            call[0].startswith("SYS-V3E2E-")
            for call in shadow_broker.cancel_calls
        )
        events_after_cancel = store.get_events(dep_id)
        cancel_events = [
            event for event in events_after_cancel
            if event.event_type == EventType.BROKER_CANCEL_REQUESTED
        ]
        assert len(cancel_events) == 1
        assert cancel_events[0].client_order_id == canonical_cid

        # --- Step 4: pause / resume ---
        await scheduler.pause_deployment(dep_id)
        assert store.get_record(dep_id).status == "paused"
        assert dep_id in scheduler._paused
        assert dep_id in scheduler._engines

        await scheduler.resume_deployment(dep_id)
        assert store.get_record(dep_id).status == "running"
        assert dep_id not in scheduler._paused

        # --- Step 5: stop with liquidate=True ---
        await scheduler.stop_deployment(dep_id, liquidate=True, reason="e2e complete")
        final_snapshots = store.get_all_snapshots(dep_id)
        assert final_snapshots[-1]["liquidation"] is True
        assert store.get_record(dep_id).status == "stopped"
        assert dep_id not in scheduler._engines

    @pytest.mark.asyncio
    async def test_tick_is_idempotent_for_same_business_date(self, v3_runtime):
        """Second tick for the same business_date must be a no-op.

        ``last_processed_date`` guards against double-booking — the second
        tick should return zero results and not emit a second
        ``tick_completed`` / ``snapshot_saved`` event pair.
        """
        store = v3_runtime["store"]
        scheduler = v3_runtime["scheduler"]
        dep_id = v3_runtime["record"].deployment_id

        await scheduler.start_deployment(dep_id)
        biz_date = date(2024, 6, 28)

        results_a = await scheduler.tick(biz_date)
        events_after_first = store.get_events(dep_id)
        tick_completed_count_first = sum(
            1 for event in events_after_first
            if event.event_type == EventType.TICK_COMPLETED
        )

        results_b = await scheduler.tick(biz_date)
        events_after_second = store.get_events(dep_id)
        tick_completed_count_second = sum(
            1 for event in events_after_second
            if event.event_type == EventType.TICK_COMPLETED
        )

        assert len(results_a) == 1
        assert results_b == []
        assert tick_completed_count_first == 1
        # Second tick for the same date must not emit another tick_completed.
        assert tick_completed_count_second == tick_completed_count_first

    @pytest.mark.asyncio
    async def test_scheduler_restart_preserves_running_deployments_via_resume_all(
        self, v3_runtime
    ):
        """Swap the Scheduler instance mid-run; ``resume_all()`` must
        rehydrate the deployment and leave the broker-order link shape
        intact.
        """
        store = v3_runtime["store"]
        scheduler = v3_runtime["scheduler"]
        record = v3_runtime["record"]
        shadow_broker = v3_runtime["shadow_broker"]
        dep_id = record.deployment_id

        await scheduler.start_deployment(dep_id)
        await scheduler.tick(date(2024, 6, 28))

        # Seed a persisted broker-order link so we can verify it survives.
        canonical_cid = f"{dep_id}:2024-06-28:000001.SZ:buy"
        shadow_broker.add_open_order(
            client_order_id=canonical_cid,
            symbol="000001.SZ",
            shares=1000,
        )
        await scheduler.pump_broker_state(dep_id)
        links_before_restart = store.list_broker_order_links(
            dep_id, broker_type="qmt"
        )
        assert len(links_before_restart) >= 1

        # --- Simulate a process restart: build a new Scheduler on the
        #     same store + same broker fixtures and call resume_all(). ---
        new_shadow = _V3CancelShadowBroker()
        # Seed the fresh shadow broker so resume doesn't drop the link.
        new_shadow._open_orders = list(shadow_broker._open_orders)
        trading_days = v3_runtime["trading_days"]
        spec = v3_runtime["spec"]
        new_data_chain = _FakeDataChain(
            _make_symbol_bars(list(spec.symbols), trading_days)
        )
        new_scheduler = Scheduler(
            store=store,
            data_chain=new_data_chain,
            broker_factories={
                "paper": lambda _spec: PaperBroker(),
                "qmt": lambda _spec: new_shadow,
            },
        )
        new_scheduler._calendars["cn_stock"] = TradingCalendar.weekday_fallback(
            date(2024, 1, 1), date(2024, 12, 31)
        )

        restored = await new_scheduler.resume_all()
        assert restored == 1
        # Engine is rehydrated in the new scheduler.
        assert dep_id in new_scheduler._engines
        # Broker-order link row survived the swap.
        links_after_restart = store.list_broker_order_links(
            dep_id, broker_type="qmt"
        )
        assert len(links_after_restart) == len(links_before_restart)
        assert any(
            link["client_order_id"] == canonical_cid
            for link in links_after_restart
        )
