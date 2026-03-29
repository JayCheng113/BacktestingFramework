"""Tests for ExperimentStore — B5."""
from datetime import date, datetime

import duckdb
import pytest

from ez.agent.experiment_store import ExperimentStore
from ez.agent.gates import ResearchGate
from ez.agent.report import ExperimentReport
from ez.agent.run_spec import RunSpec
from ez.agent.runner import Runner

import ez.strategy.builtin.ma_cross  # noqa: F401
import numpy as np
import pandas as pd


@pytest.fixture
def conn():
    """In-memory DuckDB for testing."""
    c = duckdb.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def store(conn):
    return ExperimentStore(conn)


@pytest.fixture
def spec():
    return RunSpec(
        strategy_name="MACrossStrategy",
        strategy_params={"short_period": 5, "long_period": 20},
        symbol="000001.SZ", market="cn_stock",
        start_date=date(2022, 1, 1), end_date=date(2023, 12, 31),
        wfo_n_splits=3,
    )


@pytest.fixture
def sample_data():
    rng = np.random.default_rng(42)
    n = 500
    prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
    dates = pd.date_range("2022-01-03", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices * (1 + rng.normal(0, 0.002, n)),
        "high": prices * (1 + abs(rng.normal(0, 0.005, n))),
        "low": prices * (1 - abs(rng.normal(0, 0.005, n))),
        "close": prices, "adj_close": prices,
        "volume": rng.integers(100_000, 5_000_000, n),
    }, index=dates)


def _run_and_report(spec, data):
    result = Runner().run(spec, data)
    verdict = ResearchGate().evaluate(result)
    return ExperimentReport.from_result(result, verdict)


class TestExperimentStore:
    def test_save_and_retrieve_spec(self, store, spec):
        store.save_spec(spec.to_dict())
        # No error on duplicate (upsert)
        store.save_spec(spec.to_dict())

    def test_save_and_list_runs(self, store, spec, sample_data):
        report = _run_and_report(spec, sample_data)
        store.save_spec(spec.to_dict())
        store.save_run(report.to_dict())

        runs = store.list_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == report.run_id

    def test_get_run_by_id(self, store, spec, sample_data):
        report = _run_and_report(spec, sample_data)
        store.save_spec(spec.to_dict())
        store.save_run(report.to_dict())

        run = store.get_run(report.run_id)
        assert run is not None
        assert run["spec_id"] == spec.spec_id

    def test_get_nonexistent_run(self, store):
        assert store.get_run("nonexistent") is None

    def test_find_by_spec_id(self, store, spec, sample_data):
        store.save_spec(spec.to_dict())
        for _ in range(3):
            report = _run_and_report(spec, sample_data)
            store.save_run(report.to_dict())

        runs = store.find_by_spec_id(spec.spec_id)
        assert len(runs) == 3

    def test_count_by_spec_id(self, store, spec, sample_data):
        store.save_spec(spec.to_dict())
        assert store.count_by_spec_id(spec.spec_id) == 0

        report = _run_and_report(spec, sample_data)
        store.save_run(report.to_dict())
        assert store.count_by_spec_id(spec.spec_id) == 1

    def test_idempotency_check(self, store, spec, sample_data):
        """Same spec_id should be detectable for idempotency."""
        store.save_spec(spec.to_dict())
        report = _run_and_report(spec, sample_data)
        store.save_run(report.to_dict())

        # Caller can check count before submitting duplicate
        assert store.count_by_spec_id(spec.spec_id) >= 1

    def test_save_completed_run_blocks_duplicate(self, store, spec, sample_data):
        """Second completed run for same spec_id must return False."""
        store.save_spec(spec.to_dict())
        r1 = _run_and_report(spec, sample_data)
        assert store.save_completed_run(r1.to_dict()) is True

        r2 = _run_and_report(spec, sample_data)
        assert store.save_completed_run(r2.to_dict()) is False
        # Only 1 run in DB
        assert store.count_by_spec_id(spec.spec_id) == 1

    def test_concurrent_duplicate_two_connections(self, spec, sample_data):
        """Two independent connections: only one completed run survives."""
        import tempfile, os
        db_path = tempfile.mktemp(suffix=".db")
        try:
            c1 = duckdb.connect(db_path)
            c2 = duckdb.connect(db_path)
            s1 = ExperimentStore(c1)
            s2 = ExperimentStore(c2)

            s1.save_spec(spec.to_dict())
            r1 = _run_and_report(spec, sample_data)
            r2 = _run_and_report(spec, sample_data)

            result1 = s1.save_completed_run(r1.to_dict())
            result2 = s2.save_completed_run(r2.to_dict())

            assert (result1, result2).count(True) == 1, (
                f"Exactly one should succeed: {result1}, {result2}"
            )
            c1.close()
            c2.close()
        finally:
            os.unlink(db_path)

    def test_dirty_lock_rollback_on_save_run_failure(self, store, spec, sample_data):
        """If save_run fails after completed_specs INSERT, lock must be rolled back."""
        store.save_spec(spec.to_dict())
        report = _run_and_report(spec, sample_data)
        d = report.to_dict()

        # First: insert the run normally so run_id exists
        store.save_run(d)
        # Now try save_completed_run with SAME run_id — save_run will fail (PK dup)
        # but completed_specs should NOT retain the lock
        try:
            store.save_completed_run(d)
        except Exception:
            pass

        # Lock should be clean — no entry in completed_specs
        assert store.get_completed_run_id(spec.spec_id) is None

    def test_delete_run(self, store, spec, sample_data):
        """Delete a run removes it and cleans up completed_specs."""
        store.save_spec(spec.to_dict())
        report = _run_and_report(spec, sample_data)
        store.save_completed_run(report.to_dict())
        assert store.count_by_spec_id(spec.spec_id) == 1

        assert store.delete_run(report.run_id) is True
        assert store.count_by_spec_id(spec.spec_id) == 0
        assert store.get_completed_run_id(spec.spec_id) is None
        # Orphan spec also cleaned
        rows = store._conn.execute(
            "SELECT COUNT(*) FROM experiment_specs WHERE spec_id = ?", [spec.spec_id],
        ).fetchone()
        assert rows[0] == 0

    def test_delete_nonexistent_run(self, store):
        assert store.delete_run("nonexistent") is False

    def test_cleanup_old_runs(self, store, spec, sample_data):
        """Cleanup keeps most recent runs."""
        store.save_spec(spec.to_dict())
        run_ids = []
        for _ in range(5):
            report = _run_and_report(spec, sample_data)
            store.save_run(report.to_dict())
            run_ids.append(report.run_id)

        deleted = store.cleanup_old_runs(keep_last=2)
        assert deleted == 3
        runs = store.find_by_spec_id(spec.spec_id)
        assert len(runs) == 2

    def test_backfill_on_upgrade(self, spec, sample_data):
        """Pre-existing completed run is backfilled into completed_specs on init."""
        conn = duckdb.connect(":memory:")
        store = ExperimentStore(conn)
        store.save_spec(spec.to_dict())
        report = _run_and_report(spec, sample_data)
        store.save_run(report.to_dict())

        # Simulate pre-upgrade state: no completed_specs entry
        conn.execute("DELETE FROM completed_specs")
        assert store.get_completed_run_id(spec.spec_id) is None

        # Re-init (simulates upgrade) should backfill
        store2 = ExperimentStore(conn)
        assert store2.get_completed_run_id(spec.spec_id) is not None
        conn.close()
