"""Auto-discover and validate all Strategy subclasses."""
from __future__ import annotations

import pandas as pd
import pytest

from ez.strategy.base import Strategy
from ez.strategy.loader import load_all_strategies

# Trigger auto-discovery
load_all_strategies()


def discover_strategies() -> list[type[Strategy]]:
    return list(Strategy._registry.values())


@pytest.fixture(params=discover_strategies(), ids=lambda s: s.__name__)
def strategy_cls(request):
    return request.param


def _default_params(cls: type[Strategy]) -> dict:
    return {k: v["default"] for k, v in cls.get_parameters_schema().items()}


class TestStrategyContract:
    def test_has_required_factors(self, strategy_cls):
        instance = strategy_cls(**_default_params(strategy_cls))
        factors = instance.required_factors()
        assert isinstance(factors, list)
        assert all(hasattr(f, "compute") for f in factors)

    def test_generate_signals_returns_series(self, strategy_cls, sample_df):
        instance = strategy_cls(**_default_params(strategy_cls))
        data = sample_df.copy()
        for factor in instance.required_factors():
            data = factor.compute(data)
        signals = instance.generate_signals(data)
        assert isinstance(signals, pd.Series)
        assert len(signals) == len(data)

    def test_signals_in_valid_range(self, strategy_cls, sample_df):
        instance = strategy_cls(**_default_params(strategy_cls))
        data = sample_df.copy()
        for factor in instance.required_factors():
            data = factor.compute(data)
        signals = instance.generate_signals(data)
        valid = signals.dropna()
        assert (valid >= 0.0).all() and (valid <= 1.0).all()

    def test_parameters_schema_valid(self, strategy_cls):
        schema = strategy_cls.get_parameters_schema()
        assert isinstance(schema, dict)
        for name, spec in schema.items():
            assert "type" in spec
            assert "default" in spec
