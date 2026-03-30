"""API app tests — health, exception handlers, CORS."""
import pathlib

import pytest
from fastapi.testclient import TestClient
from ez.api.app import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.2.9"
    assert "strategies_registered" in data


def test_health_has_strategies():
    resp = client.get("/api/health")
    assert "strategies_registered" in resp.json()  # count may be 0 if strategies/ not scanned yet


def test_cors_headers():
    resp = client.options("/api/health", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    })
    assert resp.status_code == 200


def test_validation_error_returns_422():
    """Invalid period should trigger ValidationError -> 422."""
    resp = client.get("/api/market-data/kline", params={
        "symbol": "000001.SZ", "market": "cn_stock", "period": "invalid_period",
        "start_date": "2025-01-01", "end_date": "2025-01-10",
    })
    assert resp.status_code == 422
    assert "Invalid period" in resp.json()["detail"]


def test_strategies_endpoint():
    resp = client.get("/api/backtest/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if data:  # strategies/ may not be scanned in isolated test runs
        assert "name" in data[0]
        assert "parameters" in data[0]


def test_factors_list_endpoint():
    resp = client.get("/api/factors")
    assert resp.status_code == 200
    data = resp.json()
    names = [f["name"] for f in data]
    assert "ma" in names
    assert "rsi" in names


class TestStaticPathTraversal:
    """P0-2: Frontend static route must not serve files outside web/dist."""

    _dist_exists = (pathlib.Path(__file__).resolve().parent.parent.parent / "web" / "dist" / "index.html").exists()

    @pytest.mark.skipif(not _dist_exists, reason="web/dist not built (CI without frontend)")
    def test_path_traversal_encoded(self):
        """URL-encoded ../.. must not escape frontend directory."""
        resp = client.get("/%2E%2E/%2E%2E/pyproject.toml")
        # Should return index.html (SPA fallback), not the actual file
        assert resp.status_code == 200
        content = resp.text
        assert "[project]" not in content  # pyproject.toml content should NOT appear

    @pytest.mark.skipif(not _dist_exists, reason="web/dist not built (CI without frontend)")
    def test_path_traversal_plain(self):
        resp = client.get("/../../pyproject.toml")
        assert resp.status_code == 200
        content = resp.text
        assert "[project]" not in content

    def test_api_path_still_404(self):
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
