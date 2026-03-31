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
