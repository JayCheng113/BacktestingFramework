"""End-to-end tests for V2.22 validation flow.

Uses REAL PortfolioStore (in-memory DuckDB) to verify the full path:
  save portfolio run → POST /api/validation/validate → full verdict
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest
import duckdb


@pytest.fixture
def in_memory_db(monkeypatch):
    """Create an in-memory DuckDB and inject it as the PortfolioStore singleton.

    V2.23 I1 fix: validation.py now reuses routes.portfolio._get_store()
    singleton, so we inject our in-memory store directly.
    """
    conn = duckdb.connect(":memory:")
    from ez.portfolio.portfolio_store import PortfolioStore
    from ez.api.routes import portfolio as portfolio_routes

    store = PortfolioStore(conn)
    monkeypatch.setattr(portfolio_routes, "_portfolio_store", store)
    return conn


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from ez.api.app import app
    return TestClient(app)


def _build_run_data(run_id: str, seed: int, mean: float = 0.0005, n_days: int = 1000) -> dict:
    """Build a synthetic portfolio run dict suitable for save_run."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(mean, 0.01, n_days)
    equity = [1_000_000.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    dates = pd.bdate_range("2020-01-01", periods=n_days + 1)
    return {
        "run_id": run_id,
        "strategy_name": "TestStrategy",
        "strategy_params": {},
        "symbols": ["AAA", "BBB"],
        "start_date": dates[0].strftime("%Y-%m-%d"),
        "end_date": dates[-1].strftime("%Y-%m-%d"),
        "freq": "weekly",
        "initial_cash": 1_000_000,
        "metrics": {"sharpe_ratio": 1.2, "total_return": 0.3},
        "equity_curve": list(equity),
        "trade_count": 50,
        "rebalance_count": 40,
        "rebalance_weights": [],
        "trades": [],
        "config": {},
        "warnings": [],
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "weights_history": [],
    }


class TestE2EValidationFlow:
    """Full flow: save → validate → inspect verdict."""

    def test_saves_and_validates_profitable_run(self, in_memory_db, client):
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)

        run_data = _build_run_data("e2e_profitable", seed=42, mean=0.0008)
        store.save_run(run_data)

        resp = client.post("/api/validation/validate", json={
            "run_id": "e2e_profitable",
            "n_bootstrap": 500,
            "block_size": 21,
        })
        assert resp.status_code == 200
        data = resp.json()

        # Full response structure
        assert data["run_id"] == "e2e_profitable"
        assert data["significance"]["observed_sharpe"] > 0
        assert data["deflated"]["sharpe"] > 0
        assert 0 <= data["deflated"]["deflated_sharpe"] <= 1
        assert data["annual"]["per_year"]
        assert len(data["annual"]["per_year"]) >= 3  # 1000 B-days ≈ 4 years
        assert data["verdict"]["result"] in ("pass", "warn", "fail")
        assert len(data["verdict"]["checks"]) >= 5  # at least 5 checks ran

    def test_paired_comparison_treatment_vs_baseline(self, in_memory_db, client):
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)

        # Treatment: strong strategy
        store.save_run(_build_run_data("e2e_treatment", seed=1, mean=0.001))
        # Control: weaker strategy
        store.save_run(_build_run_data("e2e_control", seed=2, mean=0.0003))

        resp = client.post("/api/validation/validate", json={
            "run_id": "e2e_treatment",
            "baseline_run_id": "e2e_control",
            "n_bootstrap": 500,
            "block_size": 21,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["comparison"] is not None
        assert "sharpe_diff" in data["comparison"]
        assert "ci_lower" in data["comparison"]
        assert "p_value" in data["comparison"]
        # Treatment is truly better → sharpe_diff should be positive
        assert data["comparison"]["sharpe_diff"] > 0

    def test_unprofitable_strategy_fails_verdict(self, in_memory_db, client):
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)

        # Losing strategy
        run_data = _build_run_data("e2e_losing", seed=123, mean=-0.0005)
        store.save_run(run_data)

        resp = client.post("/api/validation/validate", json={
            "run_id": "e2e_losing",
            "n_bootstrap": 500,
            "block_size": 21,
        })
        assert resp.status_code == 200
        v = resp.json()["verdict"]
        # A losing strategy should fail or warn, not pass
        assert v["result"] in ("fail", "warn")
        assert v["failed"] >= 1 or v["warned"] >= 1

    def test_with_stored_wf_metrics(self, in_memory_db, client):
        """If wf_metrics column is populated, verdict includes WF checks."""
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)

        run_data = _build_run_data("e2e_with_wf", seed=7, mean=0.0008)
        store.save_run(run_data)

        # Update wf_metrics column
        wf_data = {
            "degradation": 0.15,
            "oos_sharpe": 1.1,
            "avg_is_sharpe": 1.3,
            "overfitting_score": 0.1,
        }
        store.update_wf_metrics("e2e_with_wf", wf_data)

        resp = client.post("/api/validation/validate", json={
            "run_id": "e2e_with_wf",
            "n_bootstrap": 500,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["walk_forward"] is not None
        assert data["walk_forward"]["degradation"] == 0.15
        # Verdict should include WF-related checks
        wf_checks = [c for c in data["verdict"]["checks"] if "degradation" in c["name"].lower()]
        assert len(wf_checks) >= 1


class TestE2EFrontendSimulation:
    """Simulate the EXACT request shape the frontend will send.

    These tests encode the contract between frontend and backend. Any
    change to request/response shapes breaks these tests first.
    """

    def test_minimal_request_shape(self, in_memory_db, client):
        """Frontend sends just run_id when user clicks '验证' button."""
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)
        store.save_run(_build_run_data("fe_sim_1", seed=42))

        # Minimum possible request
        resp = client.post("/api/validation/validate", json={
            "run_id": "fe_sim_1",
        })
        assert resp.status_code == 200

    def test_response_shape_for_frontend(self, in_memory_db, client):
        """Frontend expects specific top-level keys for UI panels."""
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)
        store.save_run(_build_run_data("fe_sim_2", seed=42))

        resp = client.post("/api/validation/validate", json={
            "run_id": "fe_sim_2",
            "n_bootstrap": 200,
        })
        data = resp.json()

        # Top-level panels the frontend renders
        required_panels = [
            "significance",    # Bootstrap CI + Monte Carlo panel
            "deflated",        # Deflated Sharpe panel
            "min_btl",         # Minimum Backtest Length panel
            "annual",          # Annual breakdown chart
            "walk_forward",    # WF panel (may be None if no wf_metrics)
            "comparison",      # Paired comparison panel (may be None)
            "verdict",         # Overall verdict badge + summary
        ]
        for panel in required_panels:
            assert panel in data, f"Missing panel '{panel}' in response"

    def test_verdict_fields_for_ui_rendering(self, in_memory_db, client):
        """Frontend renders verdict as traffic light + per-check badges."""
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)
        store.save_run(_build_run_data("fe_sim_3", seed=42))

        resp = client.post("/api/validation/validate", json={
            "run_id": "fe_sim_3",
            "n_bootstrap": 200,
        })
        v = resp.json()["verdict"]

        # Top-level verdict fields the UI uses
        assert "result" in v          # pass / warn / fail
        assert "passed" in v          # int
        assert "warned" in v          # int
        assert "failed" in v          # int
        assert "total" in v           # int
        assert "summary" in v         # Chinese string for the banner
        assert "checks" in v          # list for per-check rendering

        # Each check's fields for the UI badge
        for c in v["checks"]:
            assert "name" in c
            assert "status" in c
            assert c["status"] in ("pass", "warn", "fail")
            assert "reason" in c
            assert "value" in c

    def test_annual_breakdown_chart_data(self, in_memory_db, client):
        """Frontend renders annual breakdown as a bar chart."""
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)
        store.save_run(_build_run_data("fe_sim_4", seed=42, n_days=1200))

        resp = client.post("/api/validation/validate", json={
            "run_id": "fe_sim_4",
            "n_bootstrap": 200,
        })
        annual = resp.json()["annual"]

        # Frontend needs per-year list with chart-ready fields
        assert "per_year" in annual
        for y in annual["per_year"]:
            assert "year" in y
            assert "sharpe" in y
            assert "ret" in y
            assert "mdd" in y
            assert "n_days" in y

        # Summary fields for annotations
        assert "worst_year" in annual
        assert "best_year" in annual
        assert "profitable_ratio" in annual

    def test_baseline_dropdown_flow(self, in_memory_db, client):
        """Frontend dropdown: user picks baseline run, then calls validate."""
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)

        # Simulate: user has two runs in history
        store.save_run(_build_run_data("fe_main", seed=1, mean=0.001))
        store.save_run(_build_run_data("fe_baseline", seed=2, mean=0.0003))

        # User selects baseline and clicks validate
        resp = client.post("/api/validation/validate", json={
            "run_id": "fe_main",
            "baseline_run_id": "fe_baseline",
            "n_bootstrap": 300,
        })
        cmp = resp.json()["comparison"]
        assert cmp is not None

        # Frontend renders side-by-side metrics table
        assert "treatment_metrics" in cmp
        assert "control_metrics" in cmp
        assert "sharpe" in cmp["treatment_metrics"]
        assert "sharpe" in cmp["control_metrics"]

        # Frontend renders CI as interval bar + significance badge
        assert "ci_lower" in cmp
        assert "ci_upper" in cmp
        assert "ci_excludes_zero" in cmp
        assert "is_significant" in cmp

    def test_error_response_shape(self, in_memory_db, client):
        """Frontend shows toast on 404/422. Verify error shape."""
        resp = client.post("/api/validation/validate", json={
            "run_id": "nonexistent_run",
            "n_bootstrap": 200,
        })
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body  # FastAPI standard error field
        assert isinstance(body["detail"], str)  # Frontend renders as toast message

    def test_response_json_serializable(self, in_memory_db, client):
        """No numpy arrays or non-JSON types in response (frontend = JSON only)."""
        from ez.portfolio.portfolio_store import PortfolioStore
        store = PortfolioStore(in_memory_db)
        store.save_run(_build_run_data("fe_json", seed=42))

        resp = client.post("/api/validation/validate", json={
            "run_id": "fe_json",
            "baseline_run_id": None,
            "n_bootstrap": 200,
        })
        # If response is not fully JSON-serializable, FastAPI would 500
        assert resp.status_code == 200
        # Full round-trip through JSON to ensure no raw numpy leaks
        serialized = json.dumps(resp.json())
        assert len(serialized) > 0
