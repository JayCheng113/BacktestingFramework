"""API app tests — health, exception handlers, CORS."""
from fastapi.testclient import TestClient
from ez.api.app import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.2.7.1"
    assert "strategies_registered" in data


def test_health_has_strategies():
    resp = client.get("/api/health")
    assert resp.json()["strategies_registered"] >= 1


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
    assert len(data) >= 1
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

    def test_path_traversal_encoded(self):
        """URL-encoded ../.. must not escape frontend directory."""
        resp = client.get("/%2E%2E/%2E%2E/pyproject.toml")
        # Should return index.html (SPA fallback), not the actual file
        assert resp.status_code == 200
        content = resp.text
        assert "[project]" not in content  # pyproject.toml content should NOT appear

    def test_path_traversal_plain(self):
        resp = client.get("/../../pyproject.toml")
        assert resp.status_code == 200
        content = resp.text
        assert "[project]" not in content

    def test_api_path_still_404(self):
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
        assert "API endpoint not found" in resp.json()["detail"]
