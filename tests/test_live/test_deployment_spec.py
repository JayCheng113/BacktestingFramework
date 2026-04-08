"""Tests for DeploymentSpec, DeploymentRecord, and DeploymentStore."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import duckdb
import pytest

from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec


# ---------------------------------------------------------------------------
# DeploymentSpec
# ---------------------------------------------------------------------------

class TestDeploymentSpec:
    def test_spec_id_deterministic(self):
        """Same config with different symbol order -> same spec_id."""
        s1 = DeploymentSpec(
            strategy_name="TopN", strategy_params={"top_n": 5},
            symbols=("B", "A"), market="cn_stock", freq="monthly",
        )
        s2 = DeploymentSpec(
            strategy_name="TopN", strategy_params={"top_n": 5},
            symbols=("A", "B"), market="cn_stock", freq="monthly",
        )
        assert s1.spec_id == s2.spec_id

    def test_spec_id_changes_with_params(self):
        s1 = DeploymentSpec(
            strategy_name="TopN", strategy_params={"top_n": 5},
            symbols=("A",), market="cn_stock", freq="monthly",
        )
        s2 = DeploymentSpec(
            strategy_name="TopN", strategy_params={"top_n": 10},
            symbols=("A",), market="cn_stock", freq="monthly",
        )
        assert s1.spec_id != s2.spec_id

    def test_spec_id_includes_market_rules(self):
        s1 = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly", t_plus_1=True,
        )
        s2 = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly", t_plus_1=False,
        )
        assert s1.spec_id != s2.spec_id

    def test_immutable_after_construction(self):
        s = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly",
        )
        with pytest.raises(AttributeError):
            s._strategy_name = "changed"

    def test_to_json_roundtrip(self):
        s = DeploymentSpec(
            strategy_name="TopN", strategy_params={"top_n": 5},
            symbols=("A", "B"), market="cn_stock", freq="monthly",
        )
        j = s.to_json()
        s2 = DeploymentSpec.from_json(j)
        assert s.spec_id == s2.spec_id

    def test_strategy_params_deep_frozen(self):
        """Modifying the input dict after construction doesn't change spec."""
        params = {"top_n": 5}
        s = DeploymentSpec(
            strategy_name="TopN", strategy_params=params,
            symbols=("A",), market="cn_stock", freq="monthly",
        )
        original_id = s.spec_id
        params["top_n"] = 999  # mutate input
        assert s.spec_id == original_id  # spec unchanged

    def test_strategy_params_property_returns_dict(self):
        s = DeploymentSpec(
            strategy_name="TopN", strategy_params={"top_n": 5, "z": 1},
            symbols=("A",), market="cn_stock", freq="monthly",
        )
        p = s.strategy_params
        assert isinstance(p, dict)
        assert p == {"top_n": 5, "z": 1}

    def test_spec_id_length(self):
        s = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly",
        )
        assert len(s.spec_id) == 16

    def test_optimizer_params_affect_spec_id(self):
        s1 = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly",
            optimizer="mean_variance",
            optimizer_params={"max_weight": 0.2},
        )
        s2 = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly",
            optimizer="mean_variance",
            optimizer_params={"max_weight": 0.5},
        )
        assert s1.spec_id != s2.spec_id

    def test_risk_params_affect_spec_id(self):
        s1 = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly",
            risk_control=True, risk_params={"max_drawdown": 0.1},
        )
        s2 = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly",
            risk_control=True, risk_params={"max_drawdown": 0.2},
        )
        assert s1.spec_id != s2.spec_id

    def test_from_json_invalid_raises(self):
        with pytest.raises((json.JSONDecodeError, KeyError, TypeError)):
            DeploymentSpec.from_json("{bad json")

    def test_default_cost_fields(self):
        s = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="monthly",
        )
        assert s.buy_commission_rate == 0.00008
        assert s.sell_commission_rate == 0.00008
        assert s.stamp_tax_rate == 0.0005
        assert s.slippage_rate == 0.001
        assert s.min_commission == 0.0
        assert s.initial_cash == 1_000_000.0

    def test_rebal_weekday_round_trip(self):
        """rebal_weekday survives to_json / from_json round-trip."""
        s1 = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A", "B"), market="cn_stock", freq="weekly",
            rebal_weekday=3,
        )
        assert s1.rebal_weekday == 3
        s2 = DeploymentSpec.from_json(s1.to_json())
        assert s2.rebal_weekday == 3
        assert s1.spec_id == s2.spec_id

    def test_rebal_weekday_none_round_trip(self):
        """rebal_weekday=None (default) also round-trips cleanly."""
        s = DeploymentSpec(
            strategy_name="TopN", strategy_params={},
            symbols=("A",), market="cn_stock", freq="weekly",
        )
        assert s.rebal_weekday is None
        s2 = DeploymentSpec.from_json(s.to_json())
        assert s2.rebal_weekday is None

    def test_rebal_weekday_affects_spec_id(self):
        """Different rebal_weekday → different spec_id."""
        base = dict(strategy_name="TopN", strategy_params={},
                    symbols=("A",), market="cn_stock", freq="weekly")
        s_none = DeploymentSpec(**base)
        s_thu = DeploymentSpec(**base, rebal_weekday=3)
        s_fri = DeploymentSpec(**base, rebal_weekday=4)
        assert s_none.spec_id != s_thu.spec_id
        assert s_thu.spec_id != s_fri.spec_id


# ---------------------------------------------------------------------------
# DeploymentRecord
# ---------------------------------------------------------------------------

class TestDeploymentRecord:
    def test_default_status_pending(self):
        r = DeploymentRecord(deployment_id="test", spec_id="abc", name="Test")
        assert r.status == "pending"

    def test_created_at_is_utc(self):
        r = DeploymentRecord(deployment_id="test", spec_id="abc", name="Test")
        assert r.created_at.tzinfo is not None

    def test_optional_fields_default_none(self):
        r = DeploymentRecord(deployment_id="test", spec_id="abc", name="Test")
        assert r.stop_reason == ""
        assert r.source_run_id is None
        assert r.code_commit is None
        assert r.gate_verdict is None
        assert r.approved_at is None
        assert r.started_at is None
        assert r.stopped_at is None

    def test_deployment_record_auto_uuid(self):
        """DeploymentRecord auto-generates a UUID when deployment_id is omitted."""
        r1 = DeploymentRecord(spec_id="abc", name="Test1")
        r2 = DeploymentRecord(spec_id="abc", name="Test2")
        assert r1.deployment_id != ""
        assert r2.deployment_id != ""
        assert r1.deployment_id != r2.deployment_id  # each call gets a unique UUID


# ---------------------------------------------------------------------------
# DeploymentStore
# ---------------------------------------------------------------------------

class TestDeploymentStore:
    @pytest.fixture
    def store(self):
        from ez.live.deployment_store import DeploymentStore
        conn = duckdb.connect(":memory:")
        s = DeploymentStore(conn)
        yield s
        s.close()

    @pytest.fixture
    def sample_spec(self):
        return DeploymentSpec(
            strategy_name="TopN", strategy_params={"top_n": 5},
            symbols=("000001.SZ", "600000.SH"), market="cn_stock", freq="monthly",
        )

    def test_save_and_get_spec(self, store, sample_spec):
        store.save_spec(sample_spec)
        got = store.get_spec(sample_spec.spec_id)
        assert got is not None
        assert got.spec_id == sample_spec.spec_id
        assert got.strategy_params == sample_spec.strategy_params

    def test_save_spec_idempotent(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_spec(sample_spec)  # no error
        got = store.get_spec(sample_spec.spec_id)
        assert got is not None

    def test_get_spec_not_found(self, store):
        assert store.get_spec("nonexistent") is None

    def test_save_and_get_record(self, store, sample_spec):
        store.save_spec(sample_spec)
        rec = DeploymentRecord(
            deployment_id="d001", spec_id=sample_spec.spec_id, name="Paper Test",
        )
        store.save_record(rec)
        got = store.get_record("d001")
        assert got is not None
        assert got.deployment_id == "d001"
        assert got.name == "Paper Test"
        assert got.status == "pending"

    def test_get_record_not_found(self, store):
        assert store.get_record("nonexistent") is None

    def test_list_deployments_all(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        store.save_record(DeploymentRecord(
            deployment_id="d2", spec_id=sample_spec.spec_id, name="B", status="running",
        ))
        lst = store.list_deployments()
        assert len(lst) == 2

    def test_list_deployments_filter_status(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        store.save_record(DeploymentRecord(
            deployment_id="d2", spec_id=sample_spec.spec_id, name="B", status="running",
        ))
        lst = store.list_deployments(status="running")
        assert len(lst) == 1
        assert lst[0].deployment_id == "d2"

    def test_update_status(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        store.update_status("d1", "running")
        got = store.get_record("d1")
        assert got.status == "running"

    def test_update_status_with_stop_reason(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        store.update_status("d1", "stopped", stop_reason="max_drawdown_exceeded")
        got = store.get_record("d1")
        assert got.status == "stopped"
        assert got.stop_reason == "max_drawdown_exceeded"

    def test_save_daily_snapshot(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        snap = {
            "equity": 1_000_000.0, "cash": 500_000.0,
            "holdings": {"000001.SZ": 1000}, "weights": {"000001.SZ": 0.5},
            "trades": [], "risk_events": [], "rebalanced": False,
            "execution_ms": 42.5,
        }
        store.save_daily_snapshot("d1", date(2025, 1, 6), snap)
        got = store.get_latest_snapshot("d1")
        assert got is not None
        assert got["equity"] == 1_000_000.0
        assert got["snapshot_date"] == date(2025, 1, 6)

    def test_save_daily_snapshot_updates_last_processed(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        store.save_daily_snapshot("d1", date(2025, 1, 6), {
            "equity": 1e6, "cash": 1e6, "holdings": {}, "weights": {},
        })
        last = store.get_last_processed_date("d1")
        assert last == date(2025, 1, 6)

    def test_get_all_snapshots(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        for d in [date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8)]:
            store.save_daily_snapshot("d1", d, {
                "equity": 1e6, "cash": 1e6, "holdings": {}, "weights": {},
            })
        snaps = store.get_all_snapshots("d1")
        assert len(snaps) == 3
        # Ordered by date
        assert snaps[0]["snapshot_date"] <= snaps[-1]["snapshot_date"]

    def test_get_latest_snapshot_none(self, store):
        assert store.get_latest_snapshot("nonexistent") is None

    def test_save_error_no_zero_snapshot(self, store, sample_spec):
        """save_error does NOT create zero-asset snapshot (would corrupt restore).
        Only advances last_processed_date."""
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        store.save_error("d1", date(2025, 1, 6), "Connection timeout")
        snap = store.get_latest_snapshot("d1")
        assert snap is None  # no zero-asset snapshot created
        # But last_processed_date still advanced
        assert store.get_last_processed_date("d1") == date(2025, 1, 6)

    def test_increment_and_reset_error_count(self, store, sample_spec):
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        assert store.increment_error_count("d1") == 1
        assert store.increment_error_count("d1") == 2
        store.reset_error_count("d1")
        assert store.increment_error_count("d1") == 1

    def test_update_status_invalid_raises(self, store, sample_spec):
        """update_status rejects unknown status strings."""
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        with pytest.raises(ValueError, match="Invalid deployment status"):
            store.update_status("d1", "unknown_status")

    def test_save_error_advances_last_processed_date(self, store, sample_spec):
        """save_error should update last_processed_date on the record."""
        store.save_spec(sample_spec)
        store.save_record(DeploymentRecord(
            deployment_id="d1", spec_id=sample_spec.spec_id, name="A",
        ))
        store.save_error("d1", date(2025, 1, 6), "Network error")
        last = store.get_last_processed_date("d1")
        assert last == date(2025, 1, 6)
