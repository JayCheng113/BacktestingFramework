"""V2.14 B3: StrategyEnsemble API integration tests."""
from __future__ import annotations

from fastapi.testclient import TestClient
from ez.api.app import app

client = TestClient(app)


class TestEnsembleAPI:

    def test_strategies_includes_ensemble(self):
        resp = client.get("/api/portfolio/strategies")
        assert resp.status_code == 200
        names = [s["name"] for s in resp.json()["strategies"]]
        assert "StrategyEnsemble" in names
        ens = next(s for s in resp.json()["strategies"] if s["name"] == "StrategyEnsemble")
        assert ens.get("is_ensemble") is True
        assert "mode" in ens["parameters"]

    def test_ensemble_less_than_2_strategies_400(self):
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "StrategyEnsemble",
            "symbols": ["000001.SZ", "000002.SZ", "600000.SH"],
            "strategy_params": {
                "mode": "equal",
                "sub_strategies": [
                    {"name": "TopNRotation", "params": {"factor": "momentum_rank_20", "top_n": 5}},
                ],
            },
        })
        assert resp.status_code == 400

    def test_ensemble_empty_sub_name_400(self):
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "StrategyEnsemble",
            "symbols": ["000001.SZ", "000002.SZ", "600000.SH"],
            "strategy_params": {
                "mode": "equal",
                "sub_strategies": [
                    {"name": "", "params": {}},
                    {"name": "TopNRotation", "params": {"factor": "momentum_rank_20", "top_n": 5}},
                ],
            },
        })
        assert resp.status_code == 400

    def test_ensemble_unknown_sub_strategy_404(self):
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "StrategyEnsemble",
            "symbols": ["000001.SZ", "000002.SZ"],
            "strategy_params": {
                "mode": "equal",
                "sub_strategies": [
                    {"name": "TopNRotation", "params": {"factor": "momentum_rank_20", "top_n": 5}},
                    {"name": "NonExistentStrategy", "params": {}},
                ],
            },
        })
        assert resp.status_code == 404

    def test_ensemble_run_accepted(self):
        """Full ensemble run — data fetch may fail (502) but _create_strategy must not."""
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "StrategyEnsemble",
            "symbols": ["000001.SZ", "000002.SZ", "600000.SH"],
            "market": "cn_stock",
            "start_date": "2023-01-01",
            "end_date": "2024-01-01",
            "strategy_params": {
                "mode": "equal",
                "sub_strategies": [
                    {"name": "TopNRotation", "params": {"factor": "momentum_rank_20", "top_n": 5}},
                    {"name": "TopNRotation", "params": {"factor": "reverse_vol_rank_20", "top_n": 3}},
                ],
            },
        })
        # 200 = success, 502 = data fetch fail — both OK. NOT 400/422.
        assert resp.status_code in (200, 502), f"Unexpected: {resp.status_code} {resp.json()}"

    def test_ensemble_manual_mode_accepted(self):
        resp = client.post("/api/portfolio/run", json={
            "strategy_name": "StrategyEnsemble",
            "symbols": ["000001.SZ", "000002.SZ", "600000.SH"],
            "market": "cn_stock",
            "start_date": "2023-01-01",
            "end_date": "2024-01-01",
            "strategy_params": {
                "mode": "manual",
                "ensemble_weights": [0.7, 0.3],
                "sub_strategies": [
                    {"name": "TopNRotation", "params": {"factor": "momentum_rank_20", "top_n": 5}},
                    {"name": "TopNRotation", "params": {"factor": "reverse_vol_rank_20", "top_n": 3}},
                ],
            },
        })
        assert resp.status_code in (200, 502), f"Unexpected: {resp.status_code} {resp.json()}"
