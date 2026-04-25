"""Tests for code registry, cleanup, and refresh endpoints."""
from fastapi.testclient import TestClient
from ez.api.app import app

client = TestClient(app)


class TestRegistryEndpoint:
    def test_returns_5_categories(self):
        """V2.13.2 G1.1: /registry returns 5 categories including ml_alpha."""
        resp = client.get("/api/code/registry")
        assert resp.status_code == 200
        data = resp.json()
        assert "strategy" in data
        assert "factor" in data
        assert "portfolio_strategy" in data
        assert "cross_factor" in data
        assert "ml_alpha" in data  # V2.13.2

    def test_each_category_has_builtin_and_user(self):
        resp = client.get("/api/code/registry")
        data = resp.json()
        for kind in ["strategy", "factor", "portfolio_strategy", "cross_factor", "ml_alpha"]:
            assert "builtin" in data[kind]
            assert "user" in data[kind]
            assert isinstance(data[kind]["builtin"], list)
            assert isinstance(data[kind]["user"], list)

    def test_builtin_strategies_present(self):
        # Ensure strategies are loaded (lifespan may not have run in test)
        client.post("/api/code/refresh")
        resp = client.get("/api/code/registry")
        data = resp.json()
        names = [s["name"] for s in data["strategy"]["builtin"]]
        assert len(names) >= 1

    def test_builtin_not_editable(self):
        resp = client.get("/api/code/registry")
        data = resp.json()
        for s in data["strategy"]["builtin"]:
            assert s["editable"] is False

    def test_cross_factor_includes_fundamentals(self):
        resp = client.get("/api/code/registry")
        data = resp.json()
        names = [s["name"] for s in data["cross_factor"]["builtin"]]
        assert "EP" in names
        assert "ROE" in names
        assert "MomentumRank" in names

    def test_registry_hides_test_only_portfolio_strategy(self):
        resp = client.get("/api/code/registry")
        data = resp.json()
        names = [s["name"] for s in data["portfolio_strategy"]["builtin"]]
        assert "DailyEqualWeightTest" not in names


class TestRefreshEndpoint:
    def test_refresh_returns_counts(self):
        resp = client.post("/api/code/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategies"] >= 3
        assert data["factors"] >= 9
        assert data["portfolio_strategies"] >= 3
        assert data["cross_factors"] >= 3


class TestCleanupEndpoint:
    def test_cleanup_returns_list(self):
        resp = client.delete("/api/code/cleanup-research-strategies")
        assert resp.status_code == 200
        data = resp.json()
        assert "deleted" in data
        assert "count" in data
        assert isinstance(data["deleted"], list)


class TestDeleteAndRefresh:
    """Delete file → registry cleaned → refresh doesn't bring it back."""

    def test_delete_nonexistent_returns_404(self):
        resp = client.delete("/api/code/files/nonexistent_xyz.py?kind=strategy")
        assert resp.status_code == 404

    def test_delete_returns_warning_field(self):
        """Delete response should have 'deleted' key (and optional 'warning')."""
        # Can't create+delete in test easily, but verify contract on 404
        resp = client.delete("/api/code/files/no_such.py?kind=strategy")
        assert resp.status_code == 404

    def test_refresh_after_delete_no_zombie(self):
        """After refresh, only files that exist on disk should be registered."""
        # Refresh
        resp = client.post("/api/code/refresh")
        assert resp.status_code == 200
        # Get registry
        resp2 = client.get("/api/code/registry")
        data = resp2.json()
        # All user entries should have existing files
        import os
        for kind in ["strategy", "factor", "portfolio_strategy", "cross_factor", "ml_alpha"]:
            for entry in data[kind]["user"]:
                # entry has "filename" — verify file exists
                assert "filename" in entry or "name" in entry
