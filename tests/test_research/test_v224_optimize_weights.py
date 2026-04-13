"""V2.24: Tests for /api/validation/optimize-weights endpoint.

Covers:
- Constructor / input validation
- Happy path (nested + walk-forward modes)
- Multi-sleeve loading + alignment
- Frontend contract (response shape stable)
- End-to-end with real in-memory PortfolioStore
"""
from __future__ import annotations

import json
from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from ez.api.app import app
    return TestClient(app)


@pytest.fixture
def in_memory_store(monkeypatch):
    """Inject an in-memory PortfolioStore singleton into routes.portfolio."""
    conn = duckdb.connect(":memory:")
    from ez.portfolio.portfolio_store import PortfolioStore
    from ez.api.routes import portfolio as portfolio_routes

    store = PortfolioStore(conn)
    monkeypatch.setattr(portfolio_routes, "_portfolio_store", store)
    return store


def _make_run_data(
    run_id: str,
    strategy_name: str,
    seed: int,
    mean: float = 0.0005,
    n_days: int = 1200,
) -> dict:
    """Build a synthetic portfolio_runs row."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(mean, 0.012, n_days)
    equity = [1_000_000.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    dates = pd.bdate_range("2020-01-01", periods=n_days + 1)
    return {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "strategy_params": {},
        "symbols": ["AAA", "BBB"],
        "start_date": dates[0].strftime("%Y-%m-%d"),
        "end_date": dates[-1].strftime("%Y-%m-%d"),
        "freq": "weekly",
        "initial_cash": 1_000_000,
        "metrics": {"sharpe_ratio": mean * 16, "total_return": 0.3},
        "equity_curve": list(equity),
        "trade_count": 0,
        "rebalance_count": 0,
        "rebalance_weights": [],
        "trades": [],
        "config": {},
        "warnings": [],
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "weights_history": [],
    }


@pytest.fixture
def three_sleeves(in_memory_store):
    """Seed the store with 3 sleeves (alpha/bond-like/gold-like)."""
    in_memory_store.save_run(_make_run_data("run_alpha", "EtfRotate", seed=1, mean=0.0008))
    in_memory_store.save_run(_make_run_data("run_bond", "511010_BuyHold", seed=2, mean=0.0002))
    in_memory_store.save_run(_make_run_data("run_gold", "518880_BuyHold", seed=3, mean=0.0003))
    return ["run_alpha", "run_bond", "run_gold"]


# ============================================================
# Input validation
# ============================================================

class TestInputValidation:
    def test_too_few_run_ids_rejected(self, client, in_memory_store):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": ["only_one"],
            "mode": "nested",
            "is_window": ["2020-01-01", "2023-12-31"],
            "oos_window": ["2024-01-01", "2024-12-31"],
        })
        assert resp.status_code == 422

    def test_too_many_run_ids_rejected(self, client, in_memory_store):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": [f"r{i}" for i in range(11)],  # > 10
            "mode": "nested",
            "is_window": ["2020-01-01", "2023-12-31"],
            "oos_window": ["2024-01-01", "2024-12-31"],
        })
        assert resp.status_code == 422

    def test_invalid_mode_rejected(self, client, in_memory_store):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": ["a", "b"],
            "mode": "bogus",
        })
        assert resp.status_code == 422

    def test_labels_length_mismatch_rejected(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "labels": ["A", "B"],  # only 2, but 3 run_ids
            "mode": "walk_forward",
        })
        assert resp.status_code == 422
        assert "labels length" in resp.json()["detail"]

    def test_missing_run_returns_404(self, client, in_memory_store):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": ["nonexistent_1", "nonexistent_2"],
            "mode": "walk_forward",
        })
        assert resp.status_code == 404

    def test_nested_requires_windows(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "nested",
            # Missing is_window / oos_window
        })
        assert resp.status_code == 422
        assert "is_window" in resp.json()["detail"]

    def test_unknown_objective_rejected(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "walk_forward",
            "objectives": ["MaxEverything"],
        })
        assert resp.status_code == 422
        assert "MaxEverything" in resp.json()["detail"]

    def test_baseline_weights_unknown_label_rejected(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "walk_forward",
            "baseline_weights": {"XYZ": 1.0},  # not in labels
        })
        assert resp.status_code == 422
        assert "baseline_weights" in resp.json()["detail"]


# ============================================================
# Happy path — nested mode
# ============================================================

class TestNestedMode:
    def test_runs_nested_oos_optimization(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "nested",
            "is_window": ["2020-01-01", "2022-12-31"],
            "oos_window": ["2023-01-01", "2024-06-30"],
            "objectives": ["MaxSharpe", "MaxCalmar"],
            "seed": 42,
            "max_iter": 50,  # speed up tests
        })
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["mode"] == "nested"
        assert len(data["labels"]) == 3
        assert data["nested_oos_results"] is not None
        nested = data["nested_oos_results"]
        assert "candidates" in nested
        assert len(nested["candidates"]) == 2  # one per objective

    def test_candidates_have_weights(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "nested",
            "is_window": ["2020-01-01", "2022-12-31"],
            "oos_window": ["2023-01-01", "2024-06-30"],
            "objectives": ["MaxSharpe"],
            "seed": 42,
            "max_iter": 50,
        })
        data = resp.json()
        cand = data["nested_oos_results"]["candidates"][0]
        assert "weights" in cand
        weights = cand["weights"]
        # Weights cover all labels
        assert set(weights.keys()) == set(data["labels"])
        # Sum in [0, 1] (stick-breaking simplex)
        assert 0.0 <= sum(weights.values()) <= 1.0 + 1e-6

    def test_baseline_weights_comparison(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "nested",
            "is_window": ["2020-01-01", "2022-12-31"],
            "oos_window": ["2023-01-01", "2024-06-30"],
            "objectives": ["MaxSharpe"],
            "baseline_weights": {
                "EtfRotate": 0.7,
                "511010_BuyHold": 0.15,
                "518880_BuyHold": 0.15,
            },
            "seed": 42,
            "max_iter": 50,
        })
        assert resp.status_code == 200
        nested = resp.json()["nested_oos_results"]
        # Baseline IS + OOS metrics should be populated
        assert nested.get("baseline_is") is not None
        assert nested.get("baseline_oos") is not None


# ============================================================
# Happy path — walk_forward mode
# ============================================================

class TestWalkForwardMode:
    def test_runs_walk_forward_optimization(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "walk_forward",
            "n_splits": 3,
            "train_ratio": 0.75,
            "objectives": ["MaxSharpe"],
            "seed": 42,
            "max_iter": 50,
        })
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["mode"] == "walk_forward"
        assert data["walk_forward_results"] is not None
        wf = data["walk_forward_results"]
        assert wf["n_splits"] == 3
        assert len(wf["folds"]) <= 3

    def test_walk_forward_aggregate_present(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "walk_forward",
            "n_splits": 3,
            "objectives": ["MaxSharpe"],
            "seed": 42,
            "max_iter": 50,
        })
        wf = resp.json()["walk_forward_results"]
        agg = wf["aggregate"]
        # Expect at least oos_sharpe to be computed
        assert "oos_sharpe" in agg or "avg_is_sharpe" in agg


# ============================================================
# Labels resolution
# ============================================================

class TestLabels:
    def test_custom_labels_used(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "labels": ["A", "E", "F"],
            "mode": "walk_forward",
            "n_splits": 3,
            "seed": 42,
            "max_iter": 50,
        })
        assert resp.status_code == 200
        assert resp.json()["labels"] == ["A", "E", "F"]

    def test_default_labels_from_strategy_name(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "walk_forward",
            "n_splits": 3,
            "seed": 42,
            "max_iter": 50,
        })
        labels = resp.json()["labels"]
        # Default to strategy_name
        assert labels == ["EtfRotate", "511010_BuyHold", "518880_BuyHold"]

    def test_duplicate_labels_deduplicated(self, client, in_memory_store):
        # Two runs with same strategy_name
        in_memory_store.save_run(_make_run_data("r1", "Same", seed=1))
        in_memory_store.save_run(_make_run_data("r2", "Same", seed=2))
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": ["r1", "r2"],
            "mode": "walk_forward",
            "n_splits": 3,
            "seed": 42,
            "max_iter": 50,
        })
        labels = resp.json()["labels"]
        # Second occurrence gets suffix
        assert len(set(labels)) == 2
        assert "Same" in labels


# ============================================================
# Frontend contract
# ============================================================

class TestFrontendContract:
    """These tests pin the response shape the frontend depends on."""

    def test_nested_response_fields_stable(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "nested",
            "is_window": ["2020-01-01", "2022-12-31"],
            "oos_window": ["2023-01-01", "2024-06-30"],
            "objectives": ["MaxSharpe"],
            "seed": 42,
            "max_iter": 50,
        })
        data = resp.json()
        required = ["mode", "labels", "n_observations", "date_range"]
        for key in required:
            assert key in data, f"Missing '{key}' for frontend"
        assert "nested_oos_results" in data
        # Nested result shape
        nested = data["nested_oos_results"]
        for key in ["candidates", "is_window", "oos_window"]:
            assert key in nested

    def test_walk_forward_response_fields_stable(self, client, three_sleeves):
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "walk_forward",
            "n_splits": 3,
            "objectives": ["MaxSharpe"],
            "seed": 42,
            "max_iter": 50,
        })
        data = resp.json()
        wf = data["walk_forward_results"]
        assert wf is not None
        for key in ["n_splits", "train_ratio", "n_folds_completed", "folds", "aggregate"]:
            assert key in wf

    def test_json_serializable(self, client, three_sleeves):
        """Response must be pure JSON (no numpy leaks)."""
        resp = client.post("/api/validation/optimize-weights", json={
            "run_ids": three_sleeves,
            "mode": "walk_forward",
            "n_splits": 3,
            "seed": 42,
            "max_iter": 50,
        })
        assert resp.status_code == 200
        json.dumps(resp.json())  # must not raise
