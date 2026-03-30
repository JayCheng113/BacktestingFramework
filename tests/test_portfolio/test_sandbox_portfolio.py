"""Tests for sandbox portfolio extensions (V2.9 P8)."""
from __future__ import annotations

import pytest

from ez.agent.sandbox import (
    _get_dir, _VALID_KINDS, get_template, list_portfolio_files, save_and_validate_code,
)


class TestGetDir:
    def test_known_kinds(self):
        for kind in _VALID_KINDS:
            d = _get_dir(kind)
            assert d.name in ("strategies", "factors", "portfolio_strategies", "cross_factors")

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="Invalid kind"):
            _get_dir("garbage")


class TestTemplates:
    def test_portfolio_strategy_template(self):
        code = get_template("portfolio_strategy", "TestPortStrat", "A test portfolio strategy")
        assert "class TestPortStrat(PortfolioStrategy)" in code
        assert "generate_weights" in code
        assert "universe_data" in code

    def test_cross_factor_template(self):
        code = get_template("cross_factor", "TestCrossFactor", "A test cross factor")
        assert "class TestCrossFactor(CrossSectionalFactor)" in code
        assert "def compute" in code
        assert "pd.Series" in code

    def test_default_class_names(self):
        code1 = get_template("portfolio_strategy")
        assert "MyPortfolioStrategy" in code1
        code2 = get_template("cross_factor")
        assert "MyCrossFactor" in code2


class TestListPortfolioFiles:
    def test_empty_dir(self):
        # portfolio_strategies/ may be empty
        result = list_portfolio_files("portfolio_strategy")
        assert isinstance(result, list)

    def test_cross_factors_dir(self):
        result = list_portfolio_files("cross_factor")
        assert isinstance(result, list)


class TestSaveAndValidateCode:
    def test_invalid_kind(self):
        result = save_and_validate_code("test.py", "x=1", kind="garbage")
        assert result["success"] is False
        assert "Invalid kind" in result["errors"][0]

    def test_invalid_filename(self):
        result = save_and_validate_code("../bad.py", "x=1", kind="portfolio_strategy")
        assert result["success"] is False

    def test_strategy_kind_delegates(self):
        """kind='strategy' delegates to save_and_validate_strategy (existing path)."""
        result = save_and_validate_code("_invalid.py", "x=1", kind="strategy")
        assert result["success"] is False  # invalid filename

    def test_portfolio_strategy_contract_test(self, tmp_path, monkeypatch):
        """Valid portfolio strategy code passes contract test."""
        code = get_template("portfolio_strategy", "ContractTestStrat")
        # Redirect portfolio_strategies/ to tmp
        import ez.agent.sandbox as sb
        monkeypatch.setattr(sb, "_PORTFOLIO_STRATEGIES_DIR", tmp_path)
        monkeypatch.setattr(sb, "_KIND_DIR_MAP", {
            **sb._KIND_DIR_MAP, "portfolio_strategy": tmp_path,
        })
        result = save_and_validate_code("contract_test_strat.py", code, kind="portfolio_strategy")
        assert result["success"] is True, f"Contract test failed: {result}"

    def test_cross_factor_contract_test(self, tmp_path, monkeypatch):
        """Valid cross factor code passes contract test."""
        code = get_template("cross_factor", "ContractTestFactor")
        import ez.agent.sandbox as sb
        monkeypatch.setattr(sb, "_CROSS_FACTORS_DIR", tmp_path)
        monkeypatch.setattr(sb, "_KIND_DIR_MAP", {
            **sb._KIND_DIR_MAP, "cross_factor": tmp_path,
        })
        result = save_and_validate_code("contract_test_factor.py", code, kind="cross_factor")
        assert result["success"] is True, f"Contract test failed: {result}"
