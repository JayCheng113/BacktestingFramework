"""Tests for ez/live/scheduler.py — Scheduler idempotent tick, pause/resume, auto-recovery."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
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

    @pytest.mark.asyncio
    async def test_not_found_rejected(self):
        store = _make_store()
        scheduler = Scheduler(store=store, data_chain=MagicMock())
        with pytest.raises(ValueError, match="not found"):
            await scheduler.start_deployment("nonexistent-id")

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
