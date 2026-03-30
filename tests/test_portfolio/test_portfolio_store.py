"""Tests for PortfolioStore (V2.9 P7)."""
import duckdb
import pytest

from ez.portfolio.portfolio_store import PortfolioStore


@pytest.fixture
def store():
    conn = duckdb.connect(":memory:")
    s = PortfolioStore(conn)
    yield s
    conn.close()


class TestSaveAndGet:
    def test_save_and_get(self, store):
        run_id = store.save_run({
            "strategy_name": "TopNRotation",
            "strategy_params": {"top_n": 10},
            "symbols": ["A", "B", "C"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "freq": "monthly",
            "initial_cash": 1_000_000,
            "metrics": {"sharpe_ratio": 1.5, "total_return": 0.15},
            "equity_curve": [1_000_000, 1_050_000, 1_150_000],
            "trade_count": 24,
            "rebalance_count": 12,
        })
        assert run_id

        result = store.get_run(run_id)
        assert result is not None
        assert result["strategy_name"] == "TopNRotation"
        assert result["symbols"] == ["A", "B", "C"]
        assert result["metrics"]["sharpe_ratio"] == 1.5
        assert result["equity_curve"] == [1_000_000, 1_050_000, 1_150_000]

    def test_get_nonexistent(self, store):
        assert store.get_run("nope") is None


class TestList:
    def test_list_empty(self, store):
        assert store.list_runs() == []

    def test_list_with_runs(self, store):
        for i in range(3):
            store.save_run({"run_id": f"r{i}", "strategy_name": f"S{i}",
                            "metrics": {"sharpe_ratio": i}})
        runs = store.list_runs()
        assert len(runs) == 3

    def test_pagination(self, store):
        for i in range(5):
            store.save_run({"run_id": f"r{i}", "strategy_name": f"S{i}"})
        assert len(store.list_runs(limit=2)) == 2
        assert len(store.list_runs(limit=10, offset=3)) == 2


class TestDelete:
    def test_delete(self, store):
        store.save_run({"run_id": "del1", "strategy_name": "X"})
        assert store.delete_run("del1") is True
        assert store.get_run("del1") is None

    def test_delete_nonexistent(self, store):
        assert store.delete_run("nope") is False


class TestRegistry:
    """Verify PortfolioStrategy auto-registration."""

    def test_builtins_registered(self):
        from ez.portfolio.portfolio_strategy import PortfolioStrategy
        assert "TopNRotation" in PortfolioStrategy._registry
        assert "MultiFactorRotation" in PortfolioStrategy._registry
