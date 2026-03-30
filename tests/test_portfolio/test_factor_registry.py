"""V2.10 regression tests: factor/cross_factor save-register-delete-visibility cycle."""
import pytest
import tempfile
from pathlib import Path

from ez.factor.base import Factor
from ez.portfolio.cross_factor import CrossSectionalFactor
from ez.agent.sandbox import save_and_validate_code, _get_dir


class TestFactorSaveValidation:
    """Factor save must validate it's actually a Factor subclass."""

    def test_non_factor_code_rejected(self):
        """Code without Factor subclass must fail."""
        result = save_and_validate_code("tmp_not_factor.py", "x = 1", kind="factor", overwrite=True)
        assert not result["success"]
        assert any("Factor" in e for e in result["errors"])

    def test_valid_factor_accepted(self):
        """Valid Factor subclass must succeed."""
        code = '''
import pandas as pd
from ez.factor.base import Factor

class TmpTestFactor(Factor):
    @property
    def name(self): return "tmp_test"
    @property
    def warmup_period(self): return 5
    def compute(self, data):
        data["tmp_test"] = data["adj_close"].rolling(5).mean()
        return data
'''
        result = save_and_validate_code("tmp_test_factor.py", code, kind="factor", overwrite=True)
        assert result["success"], result.get("errors")
        # Clean up
        assert "TmpTestFactor" in Factor._registry
        target = _get_dir("factor") / "tmp_test_factor.py"
        if target.exists():
            target.unlink()
        # Remove from registry
        Factor._registry.pop("TmpTestFactor", None)

    def test_bad_overwrite_rollback(self):
        """Failed overwrite must restore old version."""
        good_code = '''
import pandas as pd
from ez.factor.base import Factor

class TmpRollbackFactor(Factor):
    VERSION = 1
    @property
    def name(self): return "tmp_rollback"
    @property
    def warmup_period(self): return 1
    def compute(self, data):
        data["tmp_rollback"] = data["adj_close"]
        return data
'''
        bad_code = '''
import pandas as pd
from ez.factor.base import Factor
# This will fail: syntax ok but no valid Factor
class NotAFactor:
    pass
'''
        # Save good version
        r1 = save_and_validate_code("tmp_rollback.py", good_code, kind="factor", overwrite=True)
        assert r1["success"]
        assert "TmpRollbackFactor" in Factor._registry

        # Overwrite with bad version
        r2 = save_and_validate_code("tmp_rollback.py", bad_code, kind="factor", overwrite=True)
        assert not r2["success"]

        # Old version must still be registered
        assert "TmpRollbackFactor" in Factor._registry

        # Clean up
        target = _get_dir("factor") / "tmp_rollback.py"
        if target.exists():
            target.unlink()
        Factor._registry.pop("TmpRollbackFactor", None)


class TestFactorDeleteRegistry:
    """Delete must clean registry."""

    def test_factor_delete_cleans_registry(self):
        from fastapi.testclient import TestClient
        from ez.api.app import app
        client = TestClient(app)

        code = '''
import pandas as pd
from ez.factor.base import Factor

class TmpDeleteFactor(Factor):
    @property
    def name(self): return "tmp_delete"
    @property
    def warmup_period(self): return 1
    def compute(self, data):
        data["tmp_delete"] = 0.0
        return data
'''
        # Save
        resp = client.post("/api/code/save", json={"filename": "tmp_delete_factor.py", "code": code, "kind": "factor", "overwrite": True})
        assert resp.status_code == 200
        assert "TmpDeleteFactor" in Factor._registry

        # Delete
        resp = client.delete("/api/code/files/tmp_delete_factor.py?kind=factor")
        assert resp.status_code == 200
        # Must be removed from registry
        assert "TmpDeleteFactor" not in Factor._registry

    def test_cross_factor_delete_cleans_registry(self):
        """Test registry cleanup on cross_factor delete (manual registration)."""
        # Manually register to avoid subprocess contract test (env-dependent)
        from ez.portfolio.cross_factor import CrossSectionalFactor
        target_dir = _get_dir("cross_factor")
        target_dir.mkdir(parents=True, exist_ok=True)
        code = '''
from datetime import datetime
import pandas as pd
from ez.portfolio.cross_factor import CrossSectionalFactor

class TmpGhostCross(CrossSectionalFactor):
    @property
    def name(self): return "tmp_ghost"
    def compute(self, universe_data, date):
        return pd.Series(dtype=float)
'''
        # Write file + manually trigger registration
        target = target_dir / "tmp_ghost_cross.py"
        target.write_text(code, encoding="utf-8")
        from ez.agent.sandbox import _reload_portfolio_code
        _reload_portfolio_code("tmp_ghost_cross.py", "cross_factor", target_dir)
        assert "TmpGhostCross" in CrossSectionalFactor._registry

        # Delete via API
        from fastapi.testclient import TestClient
        from ez.api.app import app
        client = TestClient(app)
        resp = client.delete("/api/code/files/tmp_ghost_cross.py?kind=cross_factor")
        assert resp.status_code == 200
        assert "TmpGhostCross" not in CrossSectionalFactor._registry


class TestFactorAPIErrors:
    """Dynamic factor construction errors must return 4xx."""

    def test_portfolio_evaluate_bad_factor_400(self):
        from fastapi.testclient import TestClient
        from ez.api.app import app
        client = TestClient(app)
        resp = client.post("/api/portfolio/evaluate-factors", json={
            "symbols": ["000001.SZ"],
            "factor_names": ["nonexistent"],
        })
        assert resp.status_code == 400

    def test_factors_evaluate_bad_factor_404(self):
        from fastapi.testclient import TestClient
        from ez.api.app import app
        client = TestClient(app)
        resp = client.post("/api/factors/evaluate", json={
            "symbol": "000001.SZ", "market": "cn_stock",
            "factor_name": "nonexistent",
            "start_date": "2024-01-01", "end_date": "2024-06-01",
        })
        assert resp.status_code == 404
