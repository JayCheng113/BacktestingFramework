"""Tests for candidate_search — F1."""
from datetime import date

import pytest

from ez.agent.candidate_search import (
    ParamRange,
    SearchConfig,
    grid_search,
    random_search,
)


@pytest.fixture
def config():
    return SearchConfig(
        strategy_name="MACrossStrategy",
        param_ranges=[
            ParamRange("short_period", [3, 5, 10]),
            ParamRange("long_period", [15, 20, 30]),
        ],
        symbol="000001.SZ",
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
    )


class TestGridSearch:
    def test_generates_all_combinations(self, config):
        specs = grid_search(config)
        assert len(specs) == 9  # 3 x 3

    def test_unique_spec_ids(self, config):
        specs = grid_search(config)
        ids = [s.spec_id for s in specs]
        assert len(set(ids)) == 9

    def test_params_are_correct(self, config):
        specs = grid_search(config)
        params_set = {(s.strategy_params["short_period"], s.strategy_params["long_period"]) for s in specs}
        assert (3, 15) in params_set
        assert (10, 30) in params_set

    def test_empty_ranges(self):
        config = SearchConfig(
            strategy_name="TestStrat",
            param_ranges=[],
            symbol="000001.SZ",
            start_date=date(2020, 1, 1),
            end_date=date(2024, 12, 31),
        )
        specs = grid_search(config)
        assert len(specs) == 1
        assert specs[0].strategy_params == {}

    def test_spec_inherits_config(self, config):
        specs = grid_search(config)
        for s in specs:
            assert s.strategy_name == "MACrossStrategy"
            assert s.symbol == "000001.SZ"
            assert s.run_wfo is True
            assert s.wfo_n_splits == 3


class TestRandomSearch:
    def test_returns_n_samples(self, config):
        specs = random_search(config, n_samples=5, seed=42)
        assert len(specs) == 5

    def test_no_duplicates(self, config):
        specs = random_search(config, n_samples=5, seed=42)
        ids = [s.spec_id for s in specs]
        assert len(set(ids)) == 5

    def test_falls_back_to_grid_when_n_exceeds_total(self, config):
        specs = random_search(config, n_samples=100, seed=42)
        assert len(specs) == 9  # 3x3 = total space

    def test_deterministic_with_seed(self, config):
        s1 = random_search(config, n_samples=3, seed=123)
        s2 = random_search(config, n_samples=3, seed=123)
        assert [s.spec_id for s in s1] == [s.spec_id for s in s2]
