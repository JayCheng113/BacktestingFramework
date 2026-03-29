"""Tests for batch_runner — F2+F4."""
from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from ez.agent.batch_runner import BatchConfig, run_batch
from ez.agent.candidate_search import ParamRange, SearchConfig, grid_search
from ez.agent.experiment_store import ExperimentStore
from ez.agent.prefilter import PrefilterConfig

import ez.strategy.builtin.ma_cross  # noqa: F401


@pytest.fixture
def sample_data():
    rng = np.random.default_rng(42)
    n = 500
    prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
    dates = pd.date_range("2020-01-03", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices * (1 + rng.normal(0, 0.002, n)),
        "high": prices * (1 + abs(rng.normal(0, 0.005, n))),
        "low": prices * (1 - abs(rng.normal(0, 0.005, n))),
        "close": prices, "adj_close": prices,
        "volume": rng.integers(100_000, 5_000_000, n),
    }, index=dates)


@pytest.fixture
def specs():
    config = SearchConfig(
        strategy_name="MACrossStrategy",
        param_ranges=[
            ParamRange("short_period", [3, 5]),
            ParamRange("long_period", [15, 20]),
        ],
        symbol="000001.SZ",
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
        run_wfo=False,
    )
    return grid_search(config)


@pytest.fixture
def store():
    conn = duckdb.connect(":memory:")
    s = ExperimentStore(conn)
    yield s
    conn.close()


class TestBatchRunner:
    def test_runs_all_specs(self, specs, sample_data):
        result = run_batch(specs, sample_data, BatchConfig(skip_prefilter=True))
        assert result.total_specs == 4
        assert result.executed == 4
        assert len(result.candidates) == 4

    def test_prefilter_reduces_execution(self, specs, sample_data):
        strict = BatchConfig(
            prefilter_config=PrefilterConfig(min_sharpe=999),
        )
        result = run_batch(specs, sample_data, strict)
        assert result.prefiltered == 4
        assert result.executed == 0

    def test_ranked_sorted_by_sharpe(self, specs, sample_data):
        result = run_batch(specs, sample_data, BatchConfig(skip_prefilter=True))
        ranked = result.ranked
        sharpes = [c.sharpe for c in ranked]
        # Gate-passed first, then by sharpe descending
        for i in range(len(sharpes) - 1):
            if ranked[i].gate_passed == ranked[i + 1].gate_passed:
                assert sharpes[i] >= sharpes[i + 1]

    def test_persist_to_store(self, specs, sample_data, store):
        result = run_batch(specs, sample_data, BatchConfig(skip_prefilter=True), store=store)
        assert result.executed == 4
        runs = store.list_runs()
        assert len(runs) == 4

    def test_duplicate_detection_with_store(self, specs, sample_data, store):
        run_batch(specs, sample_data, BatchConfig(skip_prefilter=True), store=store)
        # Run same specs again
        result2 = run_batch(specs, sample_data, BatchConfig(skip_prefilter=True), store=store)
        assert result2.duplicates == 4
        assert result2.executed == 0

    def test_passed_only_gate_passed(self, specs, sample_data):
        result = run_batch(specs, sample_data, BatchConfig(skip_prefilter=True))
        for c in result.passed:
            assert c.gate_passed is True

    def test_save_completed_run_false_counts_as_duplicate(self, specs, sample_data, store):
        """If save_completed_run returns False (race), batch should count it as duplicate."""
        from unittest.mock import patch

        # First run succeeds
        run_batch(specs[:1], sample_data, BatchConfig(skip_prefilter=True), store=store)

        # Patch save_completed_run to always return False (simulate race)
        with patch.object(store, "save_completed_run", return_value=False), \
             patch.object(store, "get_completed_run_id", return_value=None):
            result = run_batch(specs[:1], sample_data, BatchConfig(skip_prefilter=True), store=store)

        assert result.duplicates == 1
        assert result.executed == 0
