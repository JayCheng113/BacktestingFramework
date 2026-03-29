"""Tests for RunSpec — B1."""
from datetime import date

import pytest

from ez.agent.run_spec import RunSpec


def _make_spec(**overrides) -> RunSpec:
    defaults = dict(
        strategy_name="MACrossStrategy",
        strategy_params={"short_period": 5, "long_period": 20},
        symbol="000001.SZ",
        market="cn_stock",
        start_date=date(2020, 1, 1),
        end_date=date(2023, 12, 31),
    )
    defaults.update(overrides)
    return RunSpec(**defaults)


class TestRunSpec:
    def test_basic_creation(self):
        spec = _make_spec()
        assert spec.strategy_name == "MACrossStrategy"
        assert spec.initial_capital == 100_000.0

    def test_spec_id_deterministic(self):
        s1 = _make_spec()
        s2 = _make_spec()
        assert s1.spec_id == s2.spec_id

    def test_spec_id_changes_on_param_change(self):
        s1 = _make_spec()
        s2 = _make_spec(strategy_params={"short_period": 10, "long_period": 20})
        assert s1.spec_id != s2.spec_id

    def test_spec_id_ignores_metadata(self):
        s1 = _make_spec(tags=["v1"], description="first try")
        s2 = _make_spec(tags=["v2"], description="second try")
        assert s1.spec_id == s2.spec_id

    def test_spec_id_param_order_irrelevant(self):
        s1 = _make_spec(strategy_params={"a": 1, "b": 2})
        s2 = _make_spec(strategy_params={"b": 2, "a": 1})
        assert s1.spec_id == s2.spec_id

    def test_to_dict_contains_spec_id(self):
        spec = _make_spec()
        d = spec.to_dict()
        assert d["spec_id"] == spec.spec_id
        assert d["strategy_name"] == "MACrossStrategy"

    def test_validation_empty_strategy(self):
        with pytest.raises(ValueError, match="strategy_name"):
            _make_spec(strategy_name="")

    def test_validation_dates(self):
        with pytest.raises(ValueError, match="start_date"):
            _make_spec(start_date=date(2024, 1, 1), end_date=date(2023, 1, 1))

    def test_validation_capital(self):
        with pytest.raises(ValueError, match="initial_capital"):
            _make_spec(initial_capital=-100)

    def test_validation_costs(self):
        with pytest.raises(ValueError, match="cost"):
            _make_spec(commission_rate=-0.01)

    def test_validation_no_run_mode(self):
        with pytest.raises(ValueError, match="run_backtest"):
            _make_spec(run_backtest=False, run_wfo=False)

    def test_validation_price_limit_pct(self):
        with pytest.raises(ValueError, match="price_limit_pct"):
            _make_spec(price_limit_pct=-0.1)
        with pytest.raises(ValueError, match="price_limit_pct"):
            _make_spec(price_limit_pct=1.5)

    def test_validation_lot_size(self):
        with pytest.raises(ValueError, match="lot_size"):
            _make_spec(lot_size=-1)
