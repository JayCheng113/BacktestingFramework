"""V2.15.1 S2: Restart recovery regression tests.

Tests that error -> restart -> _restore_full_state produces correct state,
including _last_prices and risk_manager replay.
"""
import pytest
import json
import duckdb
from datetime import date
from unittest.mock import MagicMock

from ez.live.deployment_spec import DeploymentSpec, DeploymentRecord
from ez.live.deployment_store import DeploymentStore
from ez.live.events import DeploymentEvent, EventType, utcnow
from ez.live.paper_engine import PaperTradingEngine
from ez.live.scheduler import Scheduler
from ez.portfolio.risk_manager import RiskManager, RiskConfig


def _make_store():
    conn = duckdb.connect(":memory:")
    return DeploymentStore(conn)


def _make_spec():
    return DeploymentSpec(
        strategy_name="TopN", strategy_params={"top_n": 5},
        symbols=("A", "B"), market="cn_stock", freq="monthly",
    )


class TestRestoreAfterError:
    """Error snapshot should NOT pollute restored state."""

    def test_error_then_restore_uses_last_good_snapshot(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test")
        store.save_record(rec)
        dep_id = rec.deployment_id

        # Day 1: good snapshot
        store.save_daily_snapshot(dep_id, date(2025, 1, 2), {
            "equity": 100000, "cash": 50000,
            "holdings": {"A": 500}, "weights": {"A": 0.5},
            "prev_returns": {"A": 0.01},
            "trades": [], "risk_events": [],
        })

        # Day 2: error (no zero-asset snapshot)
        store.save_error(dep_id, date(2025, 1, 3), "Connection timeout")

        # Restore: should use Day 1's snapshot
        all_snaps = store.get_all_snapshots(dep_id)
        assert len(all_snaps) == 1  # only the good snapshot
        latest = all_snaps[-1]
        assert latest["equity"] == 100000
        assert latest["cash"] == 50000
        assert json.loads(latest["holdings"]) if isinstance(latest["holdings"], str) else latest["holdings"] == {"A": 500}


class TestLastPricesRestore:
    """_last_prices must be reconstructed from weights+equity+holdings."""

    def test_last_prices_rebuilt_on_restore(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        # Snapshot with known holdings + weights + equity
        store.save_daily_snapshot(rec.deployment_id, date(2025, 1, 2), {
            "equity": 100000, "cash": 50000,
            "holdings": {"A": 500, "B": 200},
            "weights": {"A": 0.25, "B": 0.20},
            "prev_returns": {}, "trades": [], "risk_events": [],
        })

        # Create a mock engine and call _restore_full_state
        mock_engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock())

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        scheduler._restore_full_state(mock_engine, rec.deployment_id)

        # _last_prices should be reconstructed: price = equity * weight / shares
        assert "A" in mock_engine._last_prices
        assert "B" in mock_engine._last_prices
        expected_a = 100000 * 0.25 / 500  # = 50.0
        expected_b = 100000 * 0.20 / 200  # = 100.0
        assert abs(mock_engine._last_prices["A"] - expected_a) < 0.01
        assert abs(mock_engine._last_prices["B"] - expected_b) < 0.01


class TestRiskManagerReplay:
    """risk_manager.replay_equity must restore drawdown state."""

    def test_replay_restores_breached_state(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        # Equity curve that breaches 10% drawdown
        equities = [100000, 105000, 110000, 95000, 90000]
        for i, eq in enumerate(equities):
            store.save_daily_snapshot(rec.deployment_id, date(2025, 1, 2 + i), {
                "equity": eq, "cash": eq * 0.5,
                "holdings": {"A": 500}, "weights": {"A": 0.5},
                "prev_returns": {}, "trades": [], "risk_events": [],
            })

        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.1))
        mock_engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock(),
            risk_manager=rm)

        scheduler = Scheduler(store=store, data_chain=MagicMock())
        scheduler._restore_full_state(mock_engine, rec.deployment_id)

        # Risk manager should be breached (90000 is >10% below 110000)
        assert rm._is_breached is True
        assert rm._peak_equity == 110000


class TestEventAwareRestore:
    """Phase 2: restore path should hydrate order statuses and detect drift."""

    def test_restore_hydrates_order_statuses_from_events(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        store.save_daily_snapshot(rec.deployment_id, date(2025, 1, 2), {
            "equity": 100000, "cash": 950000,
            "holdings": {"A": 500}, "weights": {"A": 0.5},
            "prev_returns": {}, "trades": [], "risk_events": [],
        })
        store.append_events([
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-02:A:buy:{EventType.ORDER_SUBMITTED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-02:A:buy",
                payload={"symbol": "A", "side": "buy", "shares": 500},
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-02:A:buy:{EventType.ORDER_FILLED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_FILLED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-02:A:buy",
                payload={
                    "symbol": "A", "side": "buy", "shares": 500,
                    "amount": 50000.0, "cost": 0.0,
                },
            ),
        ])

        engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock()
        )
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        scheduler._restore_full_state(engine, rec.deployment_id)

        assert engine._order_statuses[f"{rec.deployment_id}:2025-01-02:A:buy"] == "filled"
        assert engine._recovery_warnings == []

    def test_restore_warns_when_snapshot_and_events_drift(self, caplog):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        store.save_daily_snapshot(rec.deployment_id, date(2025, 1, 2), {
            "equity": 100000, "cash": 50000,
            "holdings": {"A": 500}, "weights": {"A": 0.5},
            "prev_returns": {}, "trades": [], "risk_events": [],
        })
        store.append_events([
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-02:A:buy:{EventType.ORDER_SUBMITTED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-02:A:buy",
                payload={"symbol": "A", "side": "buy", "shares": 400},
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-02:A:buy:{EventType.ORDER_FILLED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_FILLED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-02:A:buy",
                payload={
                    "symbol": "A", "side": "buy", "shares": 400,
                    "amount": 40000.0, "cost": 0.0,
                },
            ),
        ])

        engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock()
        )
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with caplog.at_level("WARNING"):
            scheduler._restore_full_state(engine, rec.deployment_id)

        assert engine.holdings == {"A": 500}  # snapshot still wins
        assert len(engine._recovery_warnings) == 1
        assert "snapshot/event drift detected" in engine._recovery_warnings[0]
        assert "snapshot/event drift detected" in caplog.text

    def test_restore_checks_partial_event_history_from_prior_snapshot(self, caplog):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        store.save_daily_snapshot(rec.deployment_id, date(2025, 1, 2), {
            "equity": 100000, "cash": 50000,
            "holdings": {"A": 500}, "weights": {"A": 0.5},
            "prev_returns": {}, "trades": [], "risk_events": [],
        })
        store.save_daily_snapshot(rec.deployment_id, date(2025, 1, 3), {
            "equity": 102000, "cash": 40000,
            "holdings": {"A": 600}, "weights": {"A": 0.5882352941},
            "prev_returns": {}, "trades": [], "risk_events": [],
        })
        # Events begin only on day 2 — legacy deployment upgraded mid-stream.
        store.append_events([
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_SUBMITTED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={"symbol": "A", "side": "buy", "shares": 100, "business_date": "2025-01-03"},
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_FILLED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_FILLED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={
                    "symbol": "A", "side": "buy", "shares": 100,
                    "amount": 10000.0, "cost": 0.0, "business_date": "2025-01-03",
                },
            ),
        ])

        engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock()
        )
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with caplog.at_level("WARNING"):
            scheduler._restore_full_state(engine, rec.deployment_id)

        assert engine._recovery_warnings == []
        assert "snapshot/event drift detected" not in caplog.text
        assert engine._order_statuses[f"{rec.deployment_id}:2025-01-03:A:buy"] == "filled"

    def test_restore_applies_post_snapshot_events_to_account_state(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        store.save_daily_snapshot(rec.deployment_id, date(2025, 1, 2), {
            "equity": 100000, "cash": 50000,
            "holdings": {"A": 500}, "weights": {"A": 0.5},
            "prev_returns": {}, "trades": [], "risk_events": [],
        })
        store.append_events([
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_SUBMITTED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={"symbol": "A", "side": "buy", "shares": 100, "business_date": "2025-01-03"},
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_FILLED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_FILLED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={
                    "symbol": "A", "side": "buy", "shares": 100,
                    "amount": 10000.0, "cost": 0.0, "price": 100.0,
                    "business_date": "2025-01-03",
                },
            ),
        ])

        engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock()
        )
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        scheduler._restore_full_state(engine, rec.deployment_id)

        assert engine.cash == 40000.0
        assert engine.holdings == {"A": 600}
        assert engine._order_statuses[f"{rec.deployment_id}:2025-01-03:A:buy"] == "filled"
        assert engine.trades[-1]["symbol"] == "A"
        assert any("post-snapshot events" in msg for msg in engine._recovery_warnings)

    def test_restore_without_snapshots_replays_events_as_fallback(self, caplog):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        store.append_events([
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_SUBMITTED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={"symbol": "A", "side": "buy", "shares": 100, "business_date": "2025-01-03"},
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_FILLED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_FILLED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={
                    "symbol": "A", "side": "buy", "shares": 100,
                    "amount": 10000.0, "cost": 0.0, "price": 100.0,
                    "business_date": "2025-01-03",
                },
            ),
        ])

        engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock()
        )
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with caplog.at_level("WARNING"):
            scheduler._restore_full_state(engine, rec.deployment_id)

        assert engine.cash == spec.initial_cash - 10000.0
        assert engine.holdings == {"A": 100}
        assert engine.trades == [{
            "symbol": "A",
            "side": "buy",
            "shares": 100,
            "price": 100.0,
            "cost": 0.0,
            "amount": 10000.0,
        }]
        assert any("legacy events without snapshot checkpoints" in msg for msg in engine._recovery_warnings)
        assert "legacy events without snapshot checkpoints" in caplog.text

    def test_restore_without_snapshot_rows_uses_snapshot_event_checkpoint(self, caplog):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        store.append_events([
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-02:snapshot:{EventType.SNAPSHOT_SAVED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.SNAPSHOT_SAVED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-02:snapshot",
                payload={
                    "snapshot_date": "2025-01-02",
                    "equity": 100000.0,
                    "cash": 50000.0,
                    "holdings": {"A": 500},
                    "weights": {"A": 0.5},
                    "prev_returns": {"A": 0.01},
                    "rebalanced": True,
                    "trade_count": 1,
                },
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_SUBMITTED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={"symbol": "A", "side": "buy", "shares": 100, "business_date": "2025-01-03"},
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_FILLED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_FILLED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={
                    "symbol": "A", "side": "buy", "shares": 100,
                    "amount": 10000.0, "cost": 0.0, "price": 100.0,
                    "business_date": "2025-01-03",
                },
            ),
        ])

        engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock()
        )
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with caplog.at_level("WARNING"):
            scheduler._restore_full_state(engine, rec.deployment_id)

        assert engine.cash == 40000.0
        assert engine.holdings == {"A": 600}
        assert engine.prev_returns == {"A": 0.01}
        assert engine._last_prices["A"] == 100.0
        assert engine.prev_weights["A"] == pytest.approx(0.6)
        assert any("event ledger checkpoints" in msg for msg in engine._recovery_warnings)
        assert "event ledger checkpoints" in caplog.text

    def test_restore_prefers_event_ledger_over_stale_snapshot_rows(self, caplog):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Test", status="running")
        store.save_record(rec)

        # Stale snapshot row says the account still has 500 shares and 50k cash.
        store.save_daily_snapshot(rec.deployment_id, date(2025, 1, 2), {
            "equity": 100000, "cash": 50000,
            "holdings": {"A": 500}, "weights": {"A": 0.5},
            "prev_returns": {"A": 0.01}, "trades": [], "risk_events": [],
        })

        # Event ledger carries the authoritative checkpoint and a later fill.
        store.append_events([
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-02:snapshot:{EventType.SNAPSHOT_SAVED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.SNAPSHOT_SAVED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-02:snapshot",
                payload={
                    "snapshot_date": "2025-01-02",
                    "equity": 102000.0,
                    "cash": 40000.0,
                    "holdings": {"A": 600},
                    "weights": {"A": 0.5882352941},
                    "prev_returns": {"A": 0.02},
                    "rebalanced": True,
                    "trade_count": 1,
                },
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-02:risk:0:{EventType.RISK_RECORDED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.RISK_RECORDED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-02:risk:0",
                payload={
                    "business_date": "2025-01-02",
                    "risk_index": 0,
                    "risk_event": {"event": "runtime_allocator", "rule": "max_names"},
                },
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_SUBMITTED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_SUBMITTED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={"symbol": "A", "side": "buy", "shares": 100, "business_date": "2025-01-03"},
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:A:buy:{EventType.ORDER_FILLED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.ORDER_FILLED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:A:buy",
                payload={
                    "symbol": "A", "side": "buy", "shares": 100,
                    "amount": 10000.0, "cost": 0.0, "price": 100.0,
                    "business_date": "2025-01-03",
                },
            ),
            DeploymentEvent(
                event_id=f"{rec.deployment_id}:2025-01-03:snapshot:{EventType.SNAPSHOT_SAVED.value}",
                deployment_id=rec.deployment_id,
                event_type=EventType.SNAPSHOT_SAVED,
                event_ts=utcnow(),
                client_order_id=f"{rec.deployment_id}:2025-01-03:snapshot",
                payload={
                    "snapshot_date": "2025-01-03",
                    "equity": 103000.0,
                    "cash": 30000.0,
                    "holdings": {"A": 700},
                    "weights": {"A": 0.6796116505},
                    "prev_returns": {"A": 0.01},
                    "rebalanced": True,
                    "trade_count": 1,
                },
            ),
        ])

        engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock()
        )
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with caplog.at_level("WARNING"):
            scheduler._restore_full_state(engine, rec.deployment_id)

        assert engine.cash == 30000.0
        assert engine.holdings == {"A": 700}
        assert engine.prev_returns == {"A": 0.01}
        assert engine.risk_events == [{"event": "runtime_allocator", "rule": "max_names"}]
        assert engine.equity_curve == [102000.0, 103000.0]
        assert engine.dates == [date(2025, 1, 2), date(2025, 1, 3)]
        assert engine._order_statuses[f"{rec.deployment_id}:2025-01-03:A:buy"] == "filled"
        assert any("event-first restore" in msg for msg in engine._recovery_warnings)
        assert "Event ledger remains authoritative" in caplog.text


# ---------------------------------------------------------------------------
# V3 regression: a broker-order link that is in ``reported_cancel_pending``
# (or ``partially_filled_cancel_pending``) must NOT regress during restart.
# Recovery reloads the engine from the event ledger, but broker-order links
# live in a separate table that should remain untouched by the engine
# restore path. This is a guard test to detect any future regression.
# ---------------------------------------------------------------------------


class TestCancelPendingSurvivesRestart:
    """Link table state does not regress when a new Scheduler instance restores."""

    def test_cancel_pending_link_persists_across_scheduler_instance_swap(self):
        from datetime import datetime, timezone

        from ez.live.broker import BrokerExecutionReport
        from ez.live.events import make_broker_cancel_requested_event

        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = DeploymentRecord(spec_id=spec.spec_id, name="Cancel Pending", status="running")
        store.save_record(rec)

        # Seed a partial-filled link + cancel-requested event so the
        # store holds a ``partially_filled_cancel_pending`` link state.
        report_time = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
        client_order_id = f"{rec.deployment_id}:2026-04-14:A:buy"
        store.save_broker_sync_result(
            deployment_id=rec.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-PENDING:partially_filled:600:400:2026-04-14T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=report_time,
                    client_order_id=client_order_id,
                    broker_order_id="SYS-PENDING",
                    symbol="A",
                    side="buy",
                    status="partially_filled",
                    filled_shares=600,
                    remaining_shares=400,
                    avg_price=12.34,
                )
            ],
        )
        store.append_event(
            make_broker_cancel_requested_event(
                rec.deployment_id,
                broker_type="qmt",
                request_ts=datetime(2026, 4, 14, 15, 1, tzinfo=timezone.utc),
                client_order_id=client_order_id,
                broker_order_id="SYS-PENDING",
                symbol="A",
            )
        )

        # Pre-restart: link is cancel-pending.
        link_before = store.get_broker_order_link(
            rec.deployment_id,
            broker_type="qmt",
            client_order_id=client_order_id,
        )
        assert link_before is not None
        assert link_before["latest_status"] in {
            "reported_cancel_pending",
            "partially_filled_cancel_pending",
        }

        # Simulate restart: build a completely fresh Scheduler + engine
        # and call _restore_full_state (same path resume_all uses).
        engine = PaperTradingEngine(
            spec=spec, strategy=MagicMock(), data_chain=MagicMock()
        )
        fresh_scheduler = Scheduler(store=store, data_chain=MagicMock())
        fresh_scheduler._restore_full_state(engine, rec.deployment_id)

        # Post-restart: link state unchanged.
        link_after = store.get_broker_order_link(
            rec.deployment_id,
            broker_type="qmt",
            client_order_id=client_order_id,
        )
        assert link_after is not None
        assert link_after["latest_status"] == link_before["latest_status"]
        assert link_after["broker_order_id"] == "SYS-PENDING"
        # No regression to ``reported`` / ``partially_filled``.
        assert link_after["latest_status"] not in {"reported", "partially_filled"}
