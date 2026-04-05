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


class TestConfigAndWarnings:
    """V2.12.2 codex: config + warnings columns preserve run context."""

    def test_save_and_get_config(self, store):
        run_id = store.save_run({
            "strategy_name": "TopNRotation",
            "config": {
                "market": "cn_stock",
                "_optimizer": {"kind": "mean_variance", "risk_aversion": 2.0},
                "_risk": {"enabled": True, "max_drawdown": 0.15},
                "_index": {"benchmark": "000300", "max_tracking_error": 0.05},
            },
            "warnings": ["优化器在 3 次再平衡中退化为等权"],
        })
        result = store.get_run(run_id)
        assert result is not None
        assert result["config"]["market"] == "cn_stock"
        assert result["config"]["_optimizer"]["kind"] == "mean_variance"
        assert result["config"]["_optimizer"]["risk_aversion"] == 2.0
        assert result["config"]["_risk"]["enabled"] is True
        assert result["config"]["_risk"]["max_drawdown"] == 0.15
        assert result["config"]["_index"]["benchmark"] == "000300"
        assert result["warnings"] == ["优化器在 3 次再平衡中退化为等权"]

    def test_empty_config_and_warnings_default_gracefully(self, store):
        """Missing config/warnings fields must not break save/get."""
        run_id = store.save_run({"strategy_name": "X", "metrics": {}})
        result = store.get_run(run_id)
        assert result is not None
        # Empty dict / list are stored as JSON, read back as dict/list or None.
        assert result.get("config") in ({}, None)
        assert result.get("warnings") in ([], None)


class TestDatesPersistence:
    """V2.12.2 codex: per-bar `dates` column enables real-date compare charts."""

    def test_save_and_get_dates(self, store):
        run_id = store.save_run({
            "strategy_name": "TopNRotation",
            "equity_curve": [1_000_000, 1_005_000, 1_010_000],
            "dates": ["2024-01-02", "2024-01-03", "2024-01-04"],
        })
        result = store.get_run(run_id)
        assert result is not None
        assert result["dates"] == ["2024-01-02", "2024-01-03", "2024-01-04"]
        assert len(result["dates"]) == len(result["equity_curve"])

    def test_legacy_row_missing_dates_is_graceful(self, store):
        """Run stored without dates returns empty list / None so frontend
        can detect legacy rows and fall back to index-based rendering."""
        run_id = store.save_run({"strategy_name": "Y", "equity_curve": [100, 101]})
        result = store.get_run(run_id)
        assert result is not None
        assert result.get("dates") in ([], None)
