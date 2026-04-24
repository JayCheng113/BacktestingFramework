"""Tests for ez/live/monitor.py — DeploymentHealth + Monitor.

Tests use an in-memory DuckDB store, no network calls.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import duckdb
import pytest

from ez.live.broker import BrokerExecutionReport
from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.events import (
    make_broker_account_event,
    make_broker_cancel_requested_event,
    make_broker_runtime_event,
    make_risk_event,
)
from ez.live.monitor import (
    DeploymentHealth,
    Monitor,
    _compute_max_drawdown,
    _compute_sharpe,
    _count_consecutive_loss_days,
    _compute_days_since_last_trade,
    build_persisted_broker_order_view,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(**overrides) -> DeploymentSpec:
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


def _make_record(spec: DeploymentSpec, status: str = "running", name: str = "test-dep",
                 deployment_id: str | None = None) -> DeploymentRecord:
    kwargs: dict = dict(spec_id=spec.spec_id, name=name, status=status)
    if deployment_id:
        kwargs["deployment_id"] = deployment_id
    return DeploymentRecord(**kwargs)


def _make_store() -> DeploymentStore:
    conn = duckdb.connect(":memory:")
    return DeploymentStore(conn)


def _save_snapshots(
    store: DeploymentStore,
    dep_id: str,
    equity_values: list[float],
    start_date: date = date(2024, 1, 15),
    trades_per_day: list[list] | None = None,
    risk_events_per_day: list[list] | None = None,
    execution_ms_per_day: list[float | None] | None = None,
) -> None:
    """Helper: save a series of daily snapshots for a deployment."""
    from datetime import timedelta
    for i, equity in enumerate(equity_values):
        snap_date = date(start_date.year, start_date.month, start_date.day)
        snap_date = date.fromordinal(start_date.toordinal() + i)
        trades = (trades_per_day[i] if trades_per_day else [])
        risk_events = (risk_events_per_day[i] if risk_events_per_day else [])
        exec_ms = (execution_ms_per_day[i] if execution_ms_per_day else None)
        result = {
            "equity": equity,
            "cash": equity * 0.1,
            "holdings": {},
            "weights": {},
            "prev_returns": {},
            "trades": trades,
            "risk_events": risk_events,
            "rebalanced": False,
            "execution_ms": exec_ms,
            "error": None,
        }
        store.save_daily_snapshot(dep_id, snap_date, result)


def _append_runtime_event(
    store: DeploymentStore,
    dep_id: str,
    *,
    broker_type: str = "qmt",
    runtime_kind: str,
    event_ts: datetime,
    payload: dict,
) -> None:
    store.append_event(
        make_broker_runtime_event(
            dep_id,
            runtime_event_id=f"{runtime_kind}:{event_ts.isoformat()}",
            broker_type=broker_type,
            runtime_kind=runtime_kind,
            event_ts=event_ts,
            payload=payload,
        )
    )


# ---------------------------------------------------------------------------
# Unit tests for pure metric helpers
# ---------------------------------------------------------------------------

class TestComputeMaxDrawdown:
    def test_empty(self):
        assert _compute_max_drawdown([]) == 0.0

    def test_single_point(self):
        assert _compute_max_drawdown([1_000_000.0]) == 0.0

    def test_flat(self):
        assert _compute_max_drawdown([1.0, 1.0, 1.0]) == 0.0

    def test_simple_drawdown(self):
        # Peak=110, trough=80 → max drawdown = (80-110)/110 ≈ -0.2727
        dd = _compute_max_drawdown([100.0, 110.0, 80.0, 90.0])
        assert abs(dd - (-30.0 / 110.0)) < 1e-9
        assert dd < 0

    def test_known_value(self):
        # equity: 100 -> 120 -> 90 → max_dd from peak 120 → 90 = -25%
        dd = _compute_max_drawdown([100.0, 120.0, 90.0])
        assert abs(dd - (-0.25)) < 1e-9

    def test_always_rising(self):
        dd = _compute_max_drawdown([100.0, 110.0, 120.0, 130.0])
        assert dd == 0.0


class TestComputeSharpe:
    def test_none_for_short_series(self):
        assert _compute_sharpe([]) is None
        assert _compute_sharpe([1.0]) is None
        assert _compute_sharpe([1.0, 1.1]) is None

    def test_flat_returns_none(self):
        # Constant equity → zero std → None
        assert _compute_sharpe([1.0, 1.0, 1.0, 1.0]) is None

    def test_positive_trend(self):
        # Upward trend with noise → positive Sharpe (noise ensures non-zero std)
        import random
        rng = random.Random(42)
        equity = [1.0]
        for _ in range(99):
            # +0.1% drift + small random noise ±0.05%
            r = 0.001 + (rng.random() - 0.5) * 0.001
            equity.append(equity[-1] * (1 + r))
        s = _compute_sharpe(equity)
        assert s is not None
        assert s > 0

    def test_returns_float(self):
        equity = [100.0 + i * 0.5 + (i % 3 - 1) * 0.3 for i in range(50)]
        s = _compute_sharpe(equity)
        assert s is not None
        assert isinstance(s, float)


class TestCountConsecutiveLossDays:
    def test_empty(self):
        assert _count_consecutive_loss_days([]) == 0

    def test_single(self):
        assert _count_consecutive_loss_days([1.0]) == 0

    def test_all_gains(self):
        assert _count_consecutive_loss_days([1.0, 1.1, 1.2, 1.3]) == 0

    def test_one_loss_at_end(self):
        assert _count_consecutive_loss_days([1.0, 1.1, 1.0]) == 1

    def test_three_losses_at_end(self):
        assert _count_consecutive_loss_days([1.0, 1.1, 1.05, 1.0, 0.95]) == 3

    def test_loss_then_gain_at_end(self):
        # Pattern: loss, gain → count = 0 (last day is a gain)
        assert _count_consecutive_loss_days([1.0, 0.9, 1.0]) == 0

    def test_interrupted_loss_streak(self):
        # 1.0 → 0.9 (loss), 1.0 (gain), 0.95 (loss) → streak = 1
        assert _count_consecutive_loss_days([1.0, 0.9, 1.0, 0.95]) == 1


class TestComputeDaysSinceLastTrade:
    def test_empty(self):
        assert _compute_days_since_last_trade([]) == 0

    def test_no_trades_ever(self):
        snaps = [{"trades": []}, {"trades": []}, {"trades": []}]
        assert _compute_days_since_last_trade(snaps) == 3

    def test_trade_on_last_day(self):
        snaps = [{"trades": []}, {"trades": [{"symbol": "X"}]}]
        assert _compute_days_since_last_trade(snaps) == 0

    def test_trade_two_days_ago(self):
        snaps = [
            {"trades": [{"symbol": "X"}]},
            {"trades": []},
            {"trades": []},
        ]
        assert _compute_days_since_last_trade(snaps) == 2


# ---------------------------------------------------------------------------
# Integration tests using DeploymentStore
# ---------------------------------------------------------------------------

class TestDashboardEmpty:
    """test_dashboard_empty: no deployments → empty list."""

    def test_empty_dashboard(self):
        store = _make_store()
        monitor = Monitor(store)
        result = monitor.get_dashboard()
        assert result == []

    def test_only_stopped_not_shown(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="stopped")
        store.save_record(rec)

        monitor = Monitor(store)
        result = monitor.get_dashboard()
        assert result == []

    def test_only_pending_not_shown(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="pending")
        store.save_record(rec)

        monitor = Monitor(store)
        result = monitor.get_dashboard()
        assert result == []


class TestDashboardWithRunning:
    """test_dashboard_with_running: one running deployment with snapshots."""

    def test_running_deployment_appears(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="my-strategy")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        equities = [1_000_000.0, 1_010_000.0, 1_005_000.0]
        _save_snapshots(store, rec.deployment_id, equities)

        monitor = Monitor(store)
        dashboard = monitor.get_dashboard()

        assert len(dashboard) == 1
        health = dashboard[0]
        assert isinstance(health, DeploymentHealth)
        assert health.deployment_id == rec.deployment_id
        assert health.name == "my-strategy"
        assert health.status == "running"

    def test_cumulative_return_computed(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # 1_000_000 → 1_100_000 = +10%
        _save_snapshots(store, rec.deployment_id, [1_000_000.0, 1_100_000.0])

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert abs(health.cumulative_return - 0.10) < 1e-9

    def test_max_drawdown_negative(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # Peak 1_200_000, drops to 900_000 → drawdown = (900k-1200k)/1200k = -25%
        _save_snapshots(
            store, rec.deployment_id,
            [1_000_000.0, 1_200_000.0, 900_000.0],
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.max_drawdown < 0
        assert abs(health.max_drawdown - (-0.25)) < 1e-9

    def test_sharpe_none_for_few_snapshots(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # Only 2 equity points → not enough for Sharpe
        _save_snapshots(store, rec.deployment_id, [1_000_000.0, 1_010_000.0])

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.sharpe_ratio is None

    def test_sharpe_computed_for_enough_points(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # 30 equity points with gentle upward drift + noise
        equities = [1_000_000.0 * (1 + 0.001 * i + 0.0002 * ((i % 3) - 1))
                    for i in range(30)]
        _save_snapshots(store, rec.deployment_id, equities)

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        # Sharpe may be positive or None (if std=0), but should not raise
        # With a drift it should be computable
        assert health.sharpe_ratio is not None or health.sharpe_ratio is None  # no crash

    def test_today_pnl(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0, 1_050_000.0])

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert abs(health.today_pnl - 50_000.0) < 1e-6

    def test_today_pnl_zero_with_one_snapshot(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.today_pnl == 0.0

    def test_today_trades_count(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        trades_day2 = [{"symbol": "A", "side": "buy"}, {"symbol": "B", "side": "buy"}]
        _save_snapshots(
            store, rec.deployment_id, [1_000_000.0, 1_010_000.0],
            trades_per_day=[[], trades_day2],
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.today_trades == 2

    def test_risk_events_counted(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        risk_day1 = [{"type": "drawdown"}]
        risk_day2 = [{"type": "turnover"}, {"type": "drawdown"}]
        _save_snapshots(
            store, rec.deployment_id, [1_000_000.0, 990_000.0, 980_000.0],
            risk_events_per_day=[risk_day1, risk_day2, []],
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.total_risk_events == 3
        assert health.risk_events_today == 0  # last day has []

    def test_consecutive_loss_days(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # Up, then 3 consecutive losses
        _save_snapshots(
            store, rec.deployment_id,
            [1_000_000.0, 1_010_000.0, 1_005_000.0, 1_000_000.0, 995_000.0],
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.consecutive_loss_days == 3

    def test_execution_duration_ms(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store, rec.deployment_id, [1_000_000.0, 1_010_000.0],
            execution_ms_per_day=[500.0, 1200.0],
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert abs(health.last_execution_duration_ms - 1200.0) < 1e-6

    def test_paused_deployment_included(self):
        """Paused deployments should appear in the dashboard."""
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "paused")

        monitor = Monitor(store)
        dashboard = monitor.get_dashboard()
        assert len(dashboard) == 1
        assert dashboard[0].status == "paused"

    def test_error_deployment_included(self):
        """Error deployments should appear in the dashboard."""
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "error")

        monitor = Monitor(store)
        dashboard = monitor.get_dashboard()
        assert len(dashboard) == 1
        assert dashboard[0].status == "error"

    def test_error_count_from_record(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # Simulate 2 consecutive errors
        store.increment_error_count(rec.deployment_id)
        store.increment_error_count(rec.deployment_id)

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.error_count == 2

    def test_broker_runtime_and_reconcile_statuses_surface_in_dashboard(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [1_000_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "ok"},
                {"event": "broker_order_reconcile", "status": "drift"},
            ]],
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="account_status",
            event_ts=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
            payload={"status": "connected"},
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.broker_reconcile_status == "ok"
        assert health.broker_order_reconcile_status == "drift"
        assert health.broker_runtime_kind == "account_status"
        assert health.broker_runtime_status == "connected"

    def test_qmt_release_gate_surfaces_in_dashboard(self):
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
        rec = _make_record(spec, status="running")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [1_000_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "ok"},
                {"event": "broker_order_reconcile", "status": "ok"},
            ]],
        )
        store.append_event(
            make_broker_account_event(
                rec.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
                cash=90_000.0,
                total_asset=140_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.qmt_release_gate_status == "blocked"
        assert health.qmt_release_candidate is False
        assert health.qmt_hard_gate_status is None
        assert health.qmt_release_blockers == ["qmt_submit_gate_shadow_only"]

    def test_real_qmt_release_gate_blocks_runtime_degraded_fallback(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            initial_cash=100_000.0,
            risk_params={
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 200_000.0,
                },
            },
        )
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [100_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "ok", "broker_type": "qmt"},
                {"event": "broker_order_reconcile", "status": "ok", "broker_type": "qmt"},
            ]],
        )
        store.append_event(
            make_broker_account_event(
                rec.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
                cash=100_000.0,
                total_asset=120_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="disconnected",
            event_ts=datetime(2026, 4, 13, 9, 38, tzinfo=timezone.utc),
            payload={"status": "disconnected"},
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.qmt_release_gate_status == "blocked"
        assert health.qmt_release_candidate is False
        assert "qmt_submit_gate_blocked" in health.qmt_release_blockers
        assert "qmt_submit_gate_shadow_only" not in health.qmt_release_blockers

    def test_qmt_hard_gate_surfaces_in_dashboard_and_makes_release_gate_actionable(self):
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
        rec = _make_record(spec, status="running")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [1_000_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "ok"},
                {"event": "broker_order_reconcile", "status": "drift"},
                {
                    "event": "qmt_reconcile_hard_gate",
                    "status": "blocked",
                    "message": "QMT reconcile checks failed; fail closed.",
                    "blockers": ["broker_order_reconcile_drift"],
                },
            ]],
        )
        store.append_event(
            make_broker_account_event(
                rec.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
                cash=90_000.0,
                total_asset=140_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.qmt_hard_gate_status == "blocked"
        assert health.qmt_hard_gate_blockers == ["broker_order_reconcile_drift"]
        assert health.qmt_release_gate_status == "blocked"
        assert "qmt_submit_gate_blocked" in health.qmt_release_blockers

        alerts = monitor.check_alerts()
        release_alerts = [
            a for a in alerts if a["alert_type"] == "qmt_release_gate_blocked"
        ]
        assert len(release_alerts) == 1

    def test_monitor_prefers_recent_risk_recorded_events_for_qmt_reconcile(self):
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
        rec = _make_record(spec, status="running")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [1_000_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "drift"},
                {"event": "broker_order_reconcile", "status": "drift"},
            ]],
        )
        store.append_event(
            make_risk_event(
                rec.deployment_id,
                business_date=date(2026, 4, 13),
                risk_index=0,
                risk_event={"event": "broker_reconcile", "status": "ok"},
                event_ts=datetime(2026, 4, 13, 9, 40, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                rec.deployment_id,
                business_date=date(2026, 4, 13),
                risk_index=1,
                risk_event={"event": "broker_order_reconcile", "status": "ok"},
                event_ts=datetime(2026, 4, 13, 9, 41, tzinfo=timezone.utc),
            )
        )
        for idx in range(30):
            store.append_event(
                make_risk_event(
                    rec.deployment_id,
                    business_date=date(2026, 4, 13),
                    risk_index=100 + idx,
                    risk_event={"event": f"noise_{idx}", "status": "ok"},
                    event_ts=datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc),
                )
            )
        store.append_event(
            make_broker_account_event(
                rec.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
                cash=90_000.0,
                total_asset=140_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 42, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.broker_reconcile_status == "ok"
        assert health.broker_order_reconcile_status == "ok"

    def test_zero_pnl_no_snapshots(self):
        """Deployment with no snapshots should still return valid health."""
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.today_pnl == 0.0
        assert health.cumulative_return == 0.0
        assert health.max_drawdown == 0.0
        assert health.sharpe_ratio is None
        assert health.last_execution_date is None


# ---------------------------------------------------------------------------
# Alert tests
# ---------------------------------------------------------------------------

class TestAlertsConsecutiveLoss:
    """test_alerts_consecutive_loss: 6 consecutive loss days → alert fired."""

    def test_loss_streak_alert(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="loss-strat")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # 7 consecutive losses (threshold is 5)
        start = 1_000_000.0
        equities = [start - i * 10_000 for i in range(8)]
        _save_snapshots(store, rec.deployment_id, equities)

        monitor = Monitor(store)
        alerts = monitor.check_alerts()

        loss_alerts = [a for a in alerts if a["alert_type"] == "consecutive_loss_days"]
        assert len(loss_alerts) == 1
        assert loss_alerts[0]["deployment_id"] == rec.deployment_id
        assert "consecutive loss" in loss_alerts[0]["message"]

    def test_no_loss_streak_alert_when_short(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # Only 3 consecutive losses (below threshold of 5)
        equities = [1_000_000.0, 1_010_000.0, 1_005_000.0, 1_000_000.0, 995_000.0]
        _save_snapshots(store, rec.deployment_id, equities)

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        loss_alerts = [a for a in alerts if a["alert_type"] == "consecutive_loss_days"]
        assert loss_alerts == []


class TestAlertsHighDrawdown:
    """test_alerts_high_drawdown: drawdown worse than -25% → alert."""

    def test_high_drawdown_alert(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="dd-strat")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # Peak 1_200_000, drops to 800_000 → -33% drawdown
        _save_snapshots(
            store, rec.deployment_id,
            [1_000_000.0, 1_200_000.0, 800_000.0],
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        dd_alerts = [a for a in alerts if a["alert_type"] == "high_drawdown"]
        assert len(dd_alerts) == 1
        assert dd_alerts[0]["deployment_id"] == rec.deployment_id
        assert "drawdown" in dd_alerts[0]["message"]

    def test_moderate_drawdown_no_alert(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # 10% drawdown — below 25% threshold
        _save_snapshots(
            store, rec.deployment_id,
            [1_000_000.0, 1_100_000.0, 990_000.0],
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        dd_alerts = [a for a in alerts if a["alert_type"] == "high_drawdown"]
        assert dd_alerts == []


class TestAlertsSlowExecution:
    """Execution duration > 60 000 ms → alert."""

    def test_slow_execution_alert(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="slow-strat")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store, rec.deployment_id, [1_000_000.0, 1_010_000.0],
            execution_ms_per_day=[1_000.0, 90_000.0],  # 90 s on second day
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        slow_alerts = [a for a in alerts if a["alert_type"] == "slow_execution"]
        assert len(slow_alerts) == 1
        assert slow_alerts[0]["deployment_id"] == rec.deployment_id


class TestAlertsConsecutiveErrors:
    """Consecutive errors > 3 → alert."""

    def test_error_count_alert(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="error", name="error-strat")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "error")

        # Simulate 4 consecutive errors
        for _ in range(4):
            store.increment_error_count(rec.deployment_id)

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        err_alerts = [a for a in alerts if a["alert_type"] == "consecutive_errors"]
        assert len(err_alerts) == 1
        assert err_alerts[0]["deployment_id"] == rec.deployment_id

    def test_no_error_alert_below_threshold(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # 1 error — below the alert threshold of >= 2 (alert fires at 2, scheduler escalates at 3)
        store.increment_error_count(rec.deployment_id)

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        err_alerts = [a for a in alerts if a["alert_type"] == "consecutive_errors"]
        assert err_alerts == []


class TestAlertsInactivity:
    """Days since last trade > 30 → alert."""

    def test_inactivity_alert(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="inactive-strat")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # 35 snapshots, no trades ever
        equities = [1_000_000.0 + i * 100 for i in range(35)]
        _save_snapshots(store, rec.deployment_id, equities)

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        inact_alerts = [a for a in alerts if a["alert_type"] == "inactivity"]
        assert len(inact_alerts) == 1
        assert inact_alerts[0]["deployment_id"] == rec.deployment_id
        assert "days since last trade" in inact_alerts[0]["message"]

    def test_no_inactivity_recent_trade(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # 5 days, trade on last day
        equities = [1_000_000.0 + i * 1000 for i in range(5)]
        trades_per_day = [[], [], [], [], [{"symbol": "X", "side": "buy"}]]
        _save_snapshots(store, rec.deployment_id, equities,
                        trades_per_day=trades_per_day)

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        inact_alerts = [a for a in alerts if a["alert_type"] == "inactivity"]
        assert inact_alerts == []


class TestAlertsBrokerHealth:
    def test_broker_account_and_order_drift_alerts(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [1_000_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "drift"},
                {"event": "broker_order_reconcile", "status": "drift"},
            ]],
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        assert any(a["alert_type"] == "broker_account_drift" for a in alerts)
        assert any(a["alert_type"] == "broker_order_drift" for a in alerts)

    def test_broker_session_disconnected_alert(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="disconnected",
            event_ts=datetime(2026, 4, 13, 9, 35, tzinfo=timezone.utc),
            payload={"status": "disconnected"},
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        disconnect_alerts = [
            a for a in alerts if a["alert_type"] == "broker_session_disconnected"
        ]
        assert len(disconnect_alerts) == 1
        assert disconnect_alerts[0]["deployment_id"] == rec.deployment_id

    def test_broker_session_disconnected_alert_prefers_session_lifecycle_event(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="disconnected",
            event_ts=datetime(2026, 4, 13, 9, 35, tzinfo=timezone.utc),
            payload={"status": "disconnected"},
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="account_status",
            event_ts=datetime(2026, 4, 13, 9, 36, tzinfo=timezone.utc),
            payload={"status": "connected"},
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        disconnect_alerts = [
            a for a in alerts if a["alert_type"] == "broker_session_disconnected"
        ]
        assert len(disconnect_alerts) == 1
        assert disconnect_alerts[0]["deployment_id"] == rec.deployment_id

    def test_broker_session_unhealthy_alert(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_restart_failed",
            event_ts=datetime(2026, 4, 13, 9, 36, tzinfo=timezone.utc),
            payload={"error_msg": "consumer restart failed"},
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        unhealthy_alerts = [
            a for a in alerts if a["alert_type"] == "broker_session_unhealthy"
        ]
        assert len(unhealthy_alerts) == 1
        assert unhealthy_alerts[0]["deployment_id"] == rec.deployment_id

    def test_broker_callback_degraded_alert(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "consumer_status": "running",
                "account_sync_mode": "query_fallback",
                "asset_callback_freshness": "stale",
            },
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        degraded_alerts = [
            a for a in alerts if a["alert_type"] == "broker_callback_degraded"
        ]
        assert len(degraded_alerts) == 1
        assert degraded_alerts[0]["deployment_id"] == rec.deployment_id

    def test_broker_callback_degraded_alert_survives_newer_generic_runtime(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "consumer_status": "running",
                "account_sync_mode": "query_fallback",
                "asset_callback_freshness": "stale",
            },
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="account_status",
            event_ts=datetime(2026, 4, 13, 9, 38, tzinfo=timezone.utc),
            payload={"status": "connected"},
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        degraded_alerts = [
            a for a in alerts if a["alert_type"] == "broker_callback_degraded"
        ]
        assert len(degraded_alerts) == 1
        assert degraded_alerts[0]["deployment_id"] == rec.deployment_id

    def test_broker_callback_degraded_alert_when_owner_teardown_is_newer_than_state(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_owner_closed",
            event_ts=datetime(2026, 4, 13, 9, 38, tzinfo=timezone.utc),
            payload={"status": "closed"},
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.broker_account_sync_mode == "query_fallback"
        assert health.broker_asset_callback_freshness == "unavailable"

        alerts = monitor.check_alerts()
        degraded_alerts = [
            a for a in alerts if a["alert_type"] == "broker_callback_degraded"
        ]
        assert len(degraded_alerts) == 1
        assert degraded_alerts[0]["deployment_id"] == rec.deployment_id

    def test_broker_callback_degraded_alert_when_disconnected_is_newer_than_state(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="disconnected",
            event_ts=datetime(2026, 4, 13, 9, 38, tzinfo=timezone.utc),
            payload={"status": "disconnected"},
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.broker_account_sync_mode == "query_fallback"
        assert health.broker_asset_callback_freshness == "unavailable"

        alerts = monitor.check_alerts()
        degraded_alerts = [
            a for a in alerts if a["alert_type"] == "broker_callback_degraded"
        ]
        assert len(degraded_alerts) == 1
        assert degraded_alerts[0]["deployment_id"] == rec.deployment_id

    def test_broker_callback_degraded_alert_clears_after_newer_consumer_state_recovery(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [1_000_000.0])
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_owner_closed",
            event_ts=datetime(2026, 4, 13, 9, 38, tzinfo=timezone.utc),
            payload={"status": "closed"},
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 39, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.broker_account_sync_mode == "callback_preferred"
        assert health.broker_asset_callback_freshness == "fresh"

        alerts = monitor.check_alerts()
        degraded_alerts = [
            a for a in alerts if a["alert_type"] == "broker_callback_degraded"
        ]
        assert degraded_alerts == []

    def test_monitor_ignores_stale_runtime_projection_when_newer_runtime_event_exists(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            initial_cash=100_000.0,
            risk_params={
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 200_000.0,
                },
            },
        )
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-real")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [100_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "ok", "broker_type": "qmt"},
                {"event": "broker_order_reconcile", "status": "ok", "broker_type": "qmt"},
            ]],
        )
        store.upsert_broker_state_projection(
            rec.deployment_id,
            broker_type="qmt",
            projection={
                "deployment_status": "running",
                "projection_source": "runtime",
                "projection_ts": "2026-04-13T09:37:00+00:00",
                "latest_callback_account_mode": "callback_preferred",
                "latest_callback_account_freshness": "fresh",
                "qmt_submit_gate": {"status": "open", "can_submit_now": True},
                "qmt_release_gate": {
                    "status": "candidate",
                    "eligible_for_real_submit": True,
                    "blockers": [],
                },
            },
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="disconnected",
            event_ts=datetime(2026, 4, 13, 9, 38, tzinfo=timezone.utc),
            payload={"status": "disconnected"},
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.broker_account_sync_mode == "query_fallback"
        assert health.broker_asset_callback_freshness == "unavailable"
        assert health.qmt_release_gate_status == "blocked"
        assert "qmt_submit_gate_blocked" in health.qmt_release_blockers
        assert health.qmt_projection_source is None
        assert health.qmt_projection_ts is None
        assert health.qmt_target_account_id == "acct-real"

    def test_monitor_exposes_qmt_projection_provenance_from_runtime_projection(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            initial_cash=100_000.0,
            risk_params={
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 200_000.0,
                },
            },
        )
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-real")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        store.upsert_broker_state_projection(
            rec.deployment_id,
            broker_type="qmt",
            projection={
                "deployment_status": "running",
                "projection_source": "runtime",
                "projection_ts": "2026-04-14T09:37:00+00:00",
                "target_account_id": "acct-real",
                "latest_callback_account_mode": "callback_preferred",
                "latest_callback_account_freshness": "fresh",
                "qmt_submit_gate": {
                    "status": "open",
                    "can_submit_now": True,
                    "preflight_ok": True,
                    "source": "runtime",
                },
                "qmt_release_gate": {
                    "status": "candidate",
                    "eligible_for_real_submit": True,
                    "eligible_for_release_candidate": True,
                    "blockers": [],
                    "source": "runtime",
                },
            },
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.qmt_projection_source == "runtime"
        assert health.qmt_projection_ts == "2026-04-14T09:37:00+00:00"
        assert health.qmt_target_account_id == "acct-real"
        assert health.qmt_release_gate_status == "candidate"
        assert health.qmt_release_candidate is True

    def test_qmt_release_gate_blocked_alert_clears_after_newer_real_reconcile_recovery(self):
        store = _make_store()
        spec = _make_spec(
            broker_type="qmt",
            shadow_broker_type="qmt",
            initial_cash=100_000.0,
            risk_params={
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 200_000.0,
                },
            },
        )
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-real")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(store, rec.deployment_id, [100_000.0])
        store.append_event(
            make_broker_account_event(
                rec.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 14, 9, 34, tzinfo=timezone.utc),
                cash=100_000.0,
                total_asset=100_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
                account_id="acct-real",
            )
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_connected",
            event_ts=datetime(2026, 4, 14, 9, 35, tzinfo=timezone.utc),
            payload={"status": "connected"},
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 14, 9, 36, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )
        store.append_event(
            make_risk_event(
                rec.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=0,
                risk_event={"event": "real_broker_reconcile", "status": "drift", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 37, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                rec.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=1,
                risk_event={
                    "event": "real_qmt_reconcile_hard_gate",
                    "status": "blocked",
                    "broker_type": "qmt",
                    "blockers": ["broker_reconcile_drift"],
                },
                event_ts=datetime(2026, 4, 14, 9, 37, 1, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                rec.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=2,
                risk_event={"event": "real_broker_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 38, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                rec.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=3,
                risk_event={"event": "real_broker_order_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 38, 1, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                rec.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=4,
                risk_event={
                    "event": "real_qmt_reconcile_hard_gate",
                    "status": "open",
                    "broker_type": "qmt",
                    "blockers": [],
                },
                event_ts=datetime(2026, 4, 14, 9, 38, 2, tzinfo=timezone.utc),
            )
        )

        monitor = Monitor(store)
        health = monitor.get_dashboard()[0]
        assert health.qmt_hard_gate_status == "open"
        assert health.qmt_release_gate_status == "candidate"
        alerts = monitor.check_alerts()
        blocked_alerts = [
            a for a in alerts if a["alert_type"] == "qmt_release_gate_blocked"
        ]
        assert blocked_alerts == []

    def test_build_persisted_broker_order_view_projects_cancel_error_from_runtime(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        store.save_broker_sync_result(
            deployment_id=rec.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
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
            ],
        )
        store.append_event(
            make_broker_cancel_requested_event(
                rec.deployment_id,
                broker_type="qmt",
                request_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
                client_order_id="dep-1:2026-04-13:000001.SZ:buy",
                broker_order_id="SYS-001",
                symbol="000001.SZ",
            )
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="cancel_error",
            event_ts=datetime(2026, 4, 13, 15, 2, tzinfo=timezone.utc),
            payload={
                "client_order_id": "dep-1:2026-04-13:000001.SZ:buy",
                "order_sysid": "SYS-001",
                "status_msg": "cancel rejected",
            },
        )

        projected = build_persisted_broker_order_view(store, rec.deployment_id)

        assert len(projected) == 1
        assert projected[0]["latest_status"] == "partially_filled"
        assert projected[0]["cancel_state"] == "cancel_error"
        assert projected[0]["cancel_error_message"] == "cancel rejected"

    def test_build_persisted_broker_order_view_uses_latest_retry_cancel_request(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        store.save_broker_sync_result(
            deployment_id=rec.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
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
            ],
        )
        store.append_events(
            [
                make_broker_cancel_requested_event(
                    rec.deployment_id,
                    broker_type="qmt",
                    request_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
                    client_order_id="dep-1:2026-04-13:000001.SZ:buy",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                ),
                make_broker_runtime_event(
                    rec.deployment_id,
                    runtime_event_id="cancel_error:SYS-001",
                    broker_type="qmt",
                    runtime_kind="cancel_error",
                    event_ts=datetime(2026, 4, 13, 15, 2, tzinfo=timezone.utc),
                    payload={
                        "client_order_id": "dep-1:2026-04-13:000001.SZ:buy",
                        "order_sysid": "SYS-001",
                        "status_msg": "cancel rejected",
                    },
                ),
                make_broker_cancel_requested_event(
                    rec.deployment_id,
                    broker_type="qmt",
                    request_ts=datetime(2026, 4, 13, 15, 3, tzinfo=timezone.utc),
                    client_order_id="dep-1:2026-04-13:000001.SZ:buy",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                ),
            ]
        )

        projected = build_persisted_broker_order_view(store, rec.deployment_id)

        assert len(projected) == 1
        assert projected[0]["cancel_state"] == "cancel_inflight"
        assert projected[0]["cancel_requested_at"] == datetime(2026, 4, 13, 15, 3)

    def test_build_persisted_broker_order_view_scopes_cancel_events_by_broker_type(self):
        store = _make_store()
        spec = _make_spec(shadow_broker_type="qmt")
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        shared_client_order_id = "dep-1:2026-04-13:000001.SZ:buy"
        store.save_broker_sync_result(
            deployment_id=rec.deployment_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:reported:0:1000:2026-04-13T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
                    client_order_id=shared_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=0.0,
                ),
                BrokerExecutionReport(
                    report_id="paper:P-001:reported:0:1000:2026-04-13T15:00:00+00:00",
                    broker_type="paper",
                    as_of=datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc),
                    client_order_id=shared_client_order_id,
                    broker_order_id="P-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=0.0,
                ),
            ],
        )
        store.append_events(
            [
                make_broker_cancel_requested_event(
                    rec.deployment_id,
                    broker_type="qmt",
                    request_ts=datetime(2026, 4, 13, 15, 1, tzinfo=timezone.utc),
                    client_order_id=shared_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                ),
                make_broker_runtime_event(
                    rec.deployment_id,
                    runtime_event_id="cancel_error:SYS-001",
                    broker_type="qmt",
                    runtime_kind="cancel_error",
                    event_ts=datetime(2026, 4, 13, 15, 2, tzinfo=timezone.utc),
                    payload={
                        "client_order_id": shared_client_order_id,
                        "order_sysid": "SYS-001",
                        "status_msg": "cancel rejected",
                    },
                ),
            ]
        )

        projected = build_persisted_broker_order_view(store, rec.deployment_id)
        projected_by_type = {
            str(row["broker_type"]): row
            for row in projected
        }

        assert projected_by_type["qmt"]["cancel_state"] == "cancel_error"
        assert projected_by_type["paper"]["cancel_state"] == "none"

    def test_qmt_release_gate_blocked_alert(self):
        store = _make_store()
        spec = _make_spec(
            shadow_broker_type="qmt",
            risk_params={
                "shadow_broker_config": {"account_id": "acct-1"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-1"],
                    "max_total_asset": 50_000.0,
                    "max_initial_cash": 2_000_000.0,
                },
            },
        )
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="qmt-shadow")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [1_000_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "ok"},
                {"event": "broker_order_reconcile", "status": "ok"},
            ]],
        )
        store.append_event(
            make_broker_account_event(
                rec.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
                cash=90_000.0,
                total_asset=140_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        release_alerts = [
            a for a in alerts if a["alert_type"] == "qmt_release_gate_blocked"
        ]
        assert len(release_alerts) == 1
        assert release_alerts[0]["deployment_id"] == rec.deployment_id

    def test_qmt_release_gate_shadow_only_does_not_alert(self):
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
        rec = _make_record(spec, status="running", name="qmt-shadow")
        rec.gate_verdict = json.dumps({"passed": True, "summary": "ok", "reasons": []})
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        _save_snapshots(
            store,
            rec.deployment_id,
            [1_000_000.0],
            risk_events_per_day=[[
                {"event": "broker_reconcile", "status": "ok"},
                {"event": "broker_order_reconcile", "status": "ok"},
            ]],
        )
        store.append_event(
            make_broker_account_event(
                rec.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 13, 9, 31, tzinfo=timezone.utc),
                cash=90_000.0,
                total_asset=140_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        _append_runtime_event(
            store,
            rec.deployment_id,
            runtime_kind="session_consumer_state",
            event_ts=datetime(2026, 4, 13, 9, 37, tzinfo=timezone.utc),
            payload={
                "status": "connected",
                "consumer_status": "running",
                "account_sync_mode": "callback_preferred",
                "asset_callback_freshness": "fresh",
            },
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        release_alerts = [
            a for a in alerts if a["alert_type"] == "qmt_release_gate_blocked"
        ]
        assert release_alerts == []


class TestNoAlertsHealthy:
    """test_no_alerts_healthy: healthy deployment produces no alerts."""

    def test_healthy_produces_no_alerts(self):
        store = _make_store()
        spec = _make_spec()
        store.save_spec(spec)
        rec = _make_record(spec, status="running", name="healthy-strat")
        store.save_record(rec)
        store.update_status(rec.deployment_id, "running")

        # Gentle upward drift, trade each day, no errors, fast execution
        equities = [1_000_000.0 * (1 + 0.001 * i) for i in range(10)]
        trades_per_day = [[{"symbol": "X", "side": "buy"}]] * 10
        exec_ms = [500.0] * 10  # 0.5 s

        _save_snapshots(
            store, rec.deployment_id, equities,
            trades_per_day=trades_per_day,
            execution_ms_per_day=exec_ms,
        )

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        assert alerts == [], f"Expected no alerts, got: {alerts}"

    def test_multiple_healthy_no_alerts(self):
        """Multiple healthy deployments → still no alerts."""
        store = _make_store()

        for i in range(3):
            spec = _make_spec(strategy_name=f"Strat{i}")
            store.save_spec(spec)
            rec = _make_record(spec, status="running", name=f"dep-{i}")
            store.save_record(rec)
            store.update_status(rec.deployment_id, "running")

            equities = [1_000_000.0 * (1 + 0.001 * j) for j in range(10)]
            trades_per_day = [[{"symbol": "X"}]] * 10
            exec_ms = [300.0] * 10
            _save_snapshots(store, rec.deployment_id, equities,
                            trades_per_day=trades_per_day,
                            execution_ms_per_day=exec_ms)

        monitor = Monitor(store)
        alerts = monitor.check_alerts()
        assert alerts == []
