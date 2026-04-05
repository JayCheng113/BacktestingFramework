"""Backtest route tests with mock data."""
from unittest.mock import patch, MagicMock
from datetime import date, datetime
from fastapi.testclient import TestClient
from ez.api.app import app
from ez.types import Bar

# Ensure strategies are loaded (TestClient doesn't trigger lifespan)
from ez.strategy.loader import load_all_strategies
load_all_strategies()

client = TestClient(app)


def _mock_bars(n=50):
    """Generate n mock bars for testing."""
    import random
    random.seed(42)
    bars = []
    price = 10.0
    for i in range(n):
        dt = datetime(2024, 6, 1 + i % 28, 0, 0) if i < 28 else datetime(2024, 7, 1 + (i - 28) % 28, 0, 0)
        change = random.gauss(0, 0.2)
        c = round(price + change, 2)
        bars.append(Bar(
            time=dt, symbol="TEST.SZ", market="cn_stock",
            open=round(price, 2), high=round(max(price, c) + 0.1, 2),
            low=round(min(price, c) - 0.1, 2), close=c, adj_close=c,
            volume=1000000,
        ))
        price = c
    return bars


@patch("ez.api.deps.get_chain")
def test_run_backtest_success(mock_chain):
    mock_chain.return_value.get_kline.return_value = _mock_bars(80)
    resp = client.post("/api/backtest/run", json={
        "symbol": "TEST.SZ", "market": "cn_stock", "period": "daily",
        "strategy_name": "MACrossStrategy",
        "strategy_params": {"short_period": 3, "long_period": 5},
        "start_date": "2024-06-01", "end_date": "2024-08-31",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "metrics" in data
    assert "equity_curve" in data
    assert "significance" in data
    assert len(data["equity_curve"]) > 0


@patch("ez.api.deps.get_chain")
def test_run_backtest_no_data(mock_chain):
    mock_chain.return_value.get_kline.return_value = []
    resp = client.post("/api/backtest/run", json={
        "symbol": "NONE.SZ", "market": "cn_stock", "period": "daily",
        "strategy_name": "MACrossStrategy", "start_date": "2024-06-01", "end_date": "2024-08-31",
    })
    assert resp.status_code == 404
    assert "No data" in resp.json()["detail"]


def test_run_backtest_unknown_strategy():
    resp = client.post("/api/backtest/run", json={
        "symbol": "000001.SZ", "market": "cn_stock", "period": "daily",
        "strategy_name": "NonExistentStrategy", "start_date": "2024-06-01", "end_date": "2024-08-31",
    })
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


@patch("ez.api.deps.get_chain")
def test_walk_forward_success(mock_chain):
    mock_chain.return_value.get_kline.return_value = _mock_bars(100)
    resp = client.post("/api/backtest/walk-forward", json={
        "symbol": "TEST.SZ", "market": "cn_stock", "period": "daily",
        "strategy_name": "MACrossStrategy",
        "strategy_params": {"short_period": 3, "long_period": 5},
        "start_date": "2024-06-01", "end_date": "2024-08-31",
        "n_splits": 2,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "oos_metrics" in data
    assert "overfitting_score" in data


def test_run_backtest_missing_fields():
    resp = client.post("/api/backtest/run", json={"symbol": "TEST"})
    assert resp.status_code == 422


# --- Regression tests for codex finding: strategy name collision ---

def test_get_strategy_by_full_key_is_preferred():
    """Regression test for codex finding: _get_strategy should prefer exact key
    (module.class) match over class-name match.

    Prior version scanned the registry and returned the FIRST class matching
    either cls.__name__ or key == name, giving non-deterministic results when
    two files registered classes with the same __name__.
    """
    from ez.api.routes.backtest import _get_strategy
    from ez.strategy.base import Strategy

    # Find any existing registered strategy to test with
    assert len(Strategy._registry) > 0
    full_key = next(iter(Strategy._registry.keys()))
    cls = Strategy._registry[full_key]
    # Resolve by exact key — must work
    inst = _get_strategy(full_key, {})
    assert isinstance(inst, cls)


def test_get_strategy_by_name_works_when_unique():
    """Class-name resolution is backward-compatible when the name is unique."""
    from ez.api.routes.backtest import _get_strategy
    from ez.strategy.base import Strategy

    assert len(Strategy._registry) > 0
    # Find a strategy whose __name__ is unique in the registry
    name_counts: dict[str, int] = {}
    for cls in Strategy._registry.values():
        name_counts[cls.__name__] = name_counts.get(cls.__name__, 0) + 1
    unique_name = next((n for n, c in name_counts.items() if c == 1), None)
    if unique_name is None:
        return  # all names collide — nothing to test
    inst = _get_strategy(unique_name, {})
    assert type(inst).__name__ == unique_name


def test_get_strategy_ambiguous_name_raises_409():
    """Regression test: when two files register Strategy subclasses with the
    same __name__, _get_strategy must raise 409 instead of silently picking one.
    """
    from fastapi import HTTPException
    from ez.api.routes.backtest import _get_strategy
    from ez.strategy.base import Strategy

    # Create two synthetic strategy classes with the same __name__ under
    # different fake modules, register them temporarily
    class _Fake1(Strategy):
        def required_factors(self):
            return []
        def generate_signals(self, data):
            import pandas as pd
            return pd.Series([0.0] * len(data), index=data.index)
    class _Fake2(Strategy):
        def required_factors(self):
            return []
        def generate_signals(self, data):
            import pandas as pd
            return pd.Series([0.0] * len(data), index=data.index)

    # Force both to have the same __name__ but distinct keys
    _Fake1.__name__ = "AmbiguousTestStrat"
    _Fake2.__name__ = "AmbiguousTestStrat"
    key1 = "tests.fake_mod_1.AmbiguousTestStrat"
    key2 = "tests.fake_mod_2.AmbiguousTestStrat"
    Strategy._registry[key1] = _Fake1
    Strategy._registry[key2] = _Fake2

    try:
        # Submitting just the __name__ is ambiguous → 409
        try:
            _get_strategy("AmbiguousTestStrat", {})
            raise AssertionError("Expected 409 HTTPException for ambiguous name")
        except HTTPException as e:
            assert e.status_code == 409
            assert "ambiguous" in e.detail.lower()
            assert key1 in e.detail and key2 in e.detail
        # But submitting the full key resolves unambiguously
        inst1 = _get_strategy(key1, {})
        assert inst1.__class__ is _Fake1
        inst2 = _get_strategy(key2, {})
        assert inst2.__class__ is _Fake2
    finally:
        Strategy._registry.pop(key1, None)
        Strategy._registry.pop(key2, None)
