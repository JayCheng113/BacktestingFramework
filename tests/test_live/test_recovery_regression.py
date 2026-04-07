"""V2.16 S2: Restart recovery regression tests.

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
