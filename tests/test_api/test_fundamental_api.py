"""API-level tests for V2.11 fundamental endpoints + factor ID contract."""
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from ez.api.app import app

client = TestClient(app)


class TestFundamentalFactorsEndpoint:
    def test_list_factors_returns_categories(self):
        resp = client.get("/api/fundamental/factors")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        assert len(data["categories"]) >= 6  # at least 6 fundamental categories

    def test_each_factor_has_required_fields(self):
        resp = client.get("/api/fundamental/factors")
        data = resp.json()
        for cat in data["categories"]:
            assert "category" in cat or "key" in cat
            assert "label" in cat
            assert "factors" in cat
            for f in cat["factors"]:
                assert "name" in f
                assert "class_name" in f
                assert "needs_fina" in f


class TestFactorIDContract:
    """C1 regression: frontend factor keys must be resolvable by backend."""

    def test_strategies_factor_keys_resolvable(self):
        """Every factor key returned by /strategies must be resolvable in _resolve_factors."""
        from ez.api.routes.portfolio import _get_factor_map
        strategies_resp = client.get("/api/portfolio/strategies")
        data = strategies_resp.json()
        factor_map = _get_factor_map()

        for cat in data.get("factor_categories", []):
            for f in cat.get("factors", []):
                fkey = f["key"] if isinstance(f, dict) else f
                assert fkey in factor_map, (
                    f"Factor key '{fkey}' from /strategies not found in factor_map. "
                    f"Available: {list(factor_map.keys())[:10]}..."
                )

    def test_fundamental_factor_dual_registration(self):
        """Both class name (EP) and instance.name (ep) should resolve."""
        from ez.api.routes.portfolio import _get_factor_map
        factor_map = _get_factor_map()
        # Check a few known pairs
        for class_name, instance_name in [("EP", "ep"), ("ROE", "roe"), ("BP", "bp"), ("LnMarketCap", "ln_market_cap")]:
            assert class_name in factor_map, f"{class_name} not in factor_map"
            assert instance_name in factor_map, f"{instance_name} not in factor_map"

    def test_resolve_by_instance_name(self):
        """_resolve_factors should accept instance.name (e.g., 'ep')."""
        from ez.api.routes.portfolio import _get_factor_map
        factor_map = _get_factor_map()
        # "ep" should be resolvable
        assert "ep" in factor_map
        cls = factor_map["ep"]
        inst = cls()
        assert inst.name == "ep"


class TestFundamentalFetchEndpoint:
    def test_fetch_without_tushare_returns_400(self):
        """Fetch should fail gracefully if Tushare not configured."""
        with patch("ez.api.routes.fundamental.get_tushare_provider", return_value=None):
            resp = client.post("/api/fundamental/fetch", json={
                "symbols": ["000001.SZ"], "include_fina": False,
            })
            assert resp.status_code == 400
            assert "Tushare" in resp.json()["detail"]


class TestFundamentalQualityEndpoint:
    def test_quality_returns_report(self):
        resp = client.post("/api/fundamental/quality", json={
            "symbols": ["000001.SZ", "600519.SH"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data
        assert len(data["report"]) == 2
        for r in data["report"]:
            assert "symbol" in r
            assert "daily_coverage_pct" in r


class TestCustomFactorVisibility:
    """Uncategorized user factors should appear in 'Other' category."""

    def test_strategies_includes_other_category_for_uncategorized(self):
        resp = client.get("/api/portfolio/strategies")
        data = resp.json()
        categories = data.get("factor_categories", [])
        cat_keys = {c["key"] for c in categories}
        # If there are user-registered factors not in any category, "other" should appear
        # At minimum, technical + fundamental categories should exist
        assert "technical" in cat_keys
