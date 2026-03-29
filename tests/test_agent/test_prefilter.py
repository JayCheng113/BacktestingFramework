"""Tests for prefilter — F3."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ez.agent.candidate_search import ParamRange, SearchConfig, grid_search
from ez.agent.prefilter import PrefilterConfig, prefilter

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
        run_wfo=True,
    )
    return grid_search(config)


class TestPrefilter:
    def test_returns_results_for_all_specs(self, specs, sample_data):
        results = prefilter(specs, sample_data)
        assert len(results) == len(specs)

    def test_each_result_has_metrics(self, specs, sample_data):
        results = prefilter(specs, sample_data)
        for r in results:
            assert r.sharpe is not None or not r.passed
            assert r.spec in specs

    def test_strict_config_filters_more(self, specs, sample_data):
        lenient = PrefilterConfig(min_sharpe=-10, max_drawdown=1.0, min_trades=0)
        strict = PrefilterConfig(min_sharpe=5.0, max_drawdown=0.01, min_trades=1000)
        lenient_results = prefilter(specs, sample_data, lenient)
        strict_results = prefilter(specs, sample_data, strict)
        lenient_pass = sum(1 for r in lenient_results if r.passed)
        strict_pass = sum(1 for r in strict_results if r.passed)
        assert lenient_pass >= strict_pass

    def test_failed_backtest_does_not_pass(self, sample_data):
        from ez.agent.run_spec import RunSpec
        spec = RunSpec(
            strategy_name="NonExistentStrategy",
            strategy_params={},
            symbol="000001.SZ", market="cn_stock",
            start_date=date(2020, 1, 1), end_date=date(2024, 12, 31),
        )
        results = prefilter([spec], sample_data)
        assert len(results) == 1
        assert results[0].passed is False
        assert "failed" in results[0].reason

    def test_prefilter_preserves_original_spec(self, specs, sample_data):
        """Pre-filter should preserve the original spec (with WFO settings)."""
        results = prefilter(specs, sample_data)
        for r in results:
            assert r.spec.run_wfo is True  # original spec had WFO enabled
