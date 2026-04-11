"""End-to-end integration tests: guard framework + sandbox save flow.

These tests monkey-patch sandbox directories to tmp_path so they don't
pollute real `strategies/`, `factors/`, etc. They exercise the real
`save_and_validate_code` path including contract test + guard + rollback.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

from ez.agent.sandbox import save_and_validate_code


@pytest.fixture
def sandbox_tmp(tmp_path, monkeypatch):
    """Redirect sandbox directories to tmp_path."""
    from ez.agent import sandbox

    dirs = {
        "strategy": tmp_path / "strategies",
        "factor": tmp_path / "factors",
        "portfolio_strategy": tmp_path / "portfolio_strategies",
        "cross_factor": tmp_path / "cross_factors",
        "ml_alpha": tmp_path / "ml_alphas",
    }
    for d in dirs.values():
        d.mkdir()

    monkeypatch.setattr(sandbox, "_STRATEGIES_DIR", dirs["strategy"])
    monkeypatch.setattr(sandbox, "_FACTORS_DIR", dirs["factor"])
    monkeypatch.setattr(sandbox, "_PORTFOLIO_STRATEGIES_DIR", dirs["portfolio_strategy"])
    monkeypatch.setattr(sandbox, "_CROSS_FACTORS_DIR", dirs["cross_factor"])
    monkeypatch.setattr(sandbox, "_ML_ALPHAS_DIR", dirs["ml_alpha"])
    # Rebuild the kind→dir map so save_and_validate_code resolves to tmp dirs.
    monkeypatch.setattr(sandbox, "_KIND_DIR_MAP", {
        "strategy": dirs["strategy"],
        "factor": dirs["factor"],
        "portfolio_strategy": dirs["portfolio_strategy"],
        "cross_factor": dirs["cross_factor"],
        "ml_alpha": dirs["ml_alpha"],
    })

    yield tmp_path, dirs

    # Clean up registry pollution from imports done during the test.
    for kind in ["factor", "strategy", "cross_factor", "portfolio_strategy", "ml_alpha"]:
        from ez.agent.sandbox import _sandbox_registries_for_kind
        prefix = dirs[kind].name
        for reg in _sandbox_registries_for_kind(kind):
            for k in list(reg.keys()):
                try:
                    mod = reg[k].__module__
                except Exception:
                    continue
                if mod.startswith(f"{prefix}."):
                    reg.pop(k, None)
    # Clean sys.modules entries we created
    to_drop = [m for m in list(sys.modules) if any(
        m.startswith(f"{d.name}.") for d in dirs.values()
    )]
    for m in to_drop:
        sys.modules.pop(m, None)


# ============================================================
# Test 1: strategy clean passes
# ============================================================

def test_strategy_clean_passes_all_guards(sandbox_tmp):
    tmp_path, dirs = sandbox_tmp
    code = '''
from ez.strategy.base import Strategy
import pandas as pd

class GuardsCleanStrategy(Strategy):
    def required_factors(self):
        return []
    def generate_signals(self, df):
        return (df["adj_close"] > df["adj_close"].rolling(5).mean()).astype(float)
'''
    result = save_and_validate_code("guards_clean_strategy.py", code, "strategy")
    assert result["success"] is True, result.get("errors")
    assert "guard_result" in result
    assert not result["guard_result"]["blocked"]


# ============================================================
# Test 2: factor clean passes
# ============================================================

def test_factor_clean_passes(sandbox_tmp):
    tmp_path, dirs = sandbox_tmp
    code = '''
from ez.factor.base import Factor
import pandas as pd

class GuardsCleanFactor(Factor):
    name = "guards_clean_factor"
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["adj_close"].rolling(5).mean()
        return out
'''
    result = save_and_validate_code("guards_clean_factor.py", code, "factor")
    assert result["success"] is True, result.get("errors")
    assert not result["guard_result"]["blocked"]


# ============================================================
# Test 3: factor NaN is blocked + file rolled back
# ============================================================

def test_factor_nan_is_blocked(sandbox_tmp):
    tmp_path, dirs = sandbox_tmp
    code = '''
from ez.factor.base import Factor
import numpy as np
import pandas as pd

class GuardsNanFactor(Factor):
    name = "guards_nan_factor"
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = np.log(df["close"] - df["close"])
        return out
'''
    result = save_and_validate_code("guards_nan_factor.py", code, "factor")
    assert result["success"] is False
    errs = " ".join(result.get("errors", []))
    assert "NaNInfGuard" in errs
    assert not (dirs["factor"] / "guards_nan_factor.py").exists()


# ============================================================
# Test 4: portfolio over-leverage blocked
# ============================================================

def test_portfolio_over_leverage_blocked(sandbox_tmp):
    """Contract test is a single-date check (2024-03-15). To reach the
    WeightSumGuard (multi-date), use a date-dependent bug: clean at
    2024-03-15 but over-levered at later dates."""
    tmp_path, dirs = sandbox_tmp
    code = '''
import pandas as pd
from ez.portfolio.portfolio_strategy import PortfolioStrategy

class GuardsDateDependentOverLever(PortfolioStrategy):
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        # Clean on 2024-03-15 (contract test uses this date).
        if pd.Timestamp(target_date) < pd.Timestamp("2024-04-01"):
            syms = list(panel)[:1]
            return {s: 1.0 for s in syms}
        # Over-levered at later dates.
        return {s: 0.5 for s in panel}
'''
    result = save_and_validate_code("guards_date_over_levered.py", code, "portfolio_strategy")
    assert result["success"] is False
    errs = " ".join(result.get("errors", []))
    assert "WeightSumGuard" in errs, f"Unexpected errors: {errs}"


# ============================================================
# Test 5: portfolio negative weight → warning, save still passes
# ============================================================

def test_portfolio_negative_weight_warns_but_passes(sandbox_tmp):
    """Contract test checks single date. Use date-dependent negative weight
    that's clean at 2024-03-15 (contract test date) but negative at later
    dates — guard catches it via 5-date sweep."""
    tmp_path, dirs = sandbox_tmp
    code = '''
import pandas as pd
from ez.portfolio.portfolio_strategy import PortfolioStrategy

class GuardsDateDependentNegative(PortfolioStrategy):
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        if pd.Timestamp(target_date) < pd.Timestamp("2024-04-01"):
            return {list(panel)[0]: 1.0}
        syms = list(panel)
        return {syms[0]: -0.1, syms[1]: 0.5, syms[2]: 0.6}
'''
    result = save_and_validate_code("guards_date_neg_weight.py", code, "portfolio_strategy")
    assert result["success"] is True, result.get("errors")
    assert result["guard_result"]["n_warnings"] >= 1


# ============================================================
# Test 6: factor lookahead blocked
# ============================================================

def test_factor_lookahead_blocked(sandbox_tmp):
    tmp_path, dirs = sandbox_tmp
    code = '''
from ez.factor.base import Factor
import pandas as pd

class GuardsLookaheadFactor(Factor):
    name = "guards_lookahead_factor"
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["close"].shift(-1)  # BUG: reads future
        return out
'''
    result = save_and_validate_code("guards_lookahead_factor.py", code, "factor")
    assert result["success"] is False
    errs = " ".join(result.get("errors", []))
    assert "LookaheadGuard" in errs


# ============================================================
# Test 7: guard itself raises — surfaces as block with "guard bug"
# ============================================================

def test_guard_exception_surfaces_as_block(monkeypatch, sandbox_tmp):
    tmp_path, dirs = sandbox_tmp
    from ez.testing.guards import LookaheadGuard
    original_check = LookaheadGuard.check
    def broken_check(self, ctx):
        raise RuntimeError("guard bug")
    monkeypatch.setattr(LookaheadGuard, "check", broken_check)

    code = '''
from ez.factor.base import Factor
import pandas as pd

class GuardsBrokenProbe(Factor):
    name = "guards_broken_probe"
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["adj_close"].rolling(5).mean()
        return out
'''
    try:
        result = save_and_validate_code("guards_broken_probe.py", code, "factor")
        assert result["success"] is False
        errs = " ".join(result.get("errors", [])).lower()
        assert "guard bug" in errs
    finally:
        monkeypatch.setattr(LookaheadGuard, "check", original_check)


# ============================================================
# Test 8: overwrite clean with buggy → original restored
# ============================================================

def test_overwrite_with_buggy_restores_backup(sandbox_tmp):
    tmp_path, dirs = sandbox_tmp
    clean = '''
from ez.factor.base import Factor
import pandas as pd

class GuardsClobberFactor(Factor):
    name = "guards_clobber_factor"
    warmup_period = 5
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = df["adj_close"].rolling(5).mean()
        return out
'''
    buggy = '''
from ez.factor.base import Factor
import numpy as np
import pandas as pd

class GuardsClobberFactor(Factor):
    name = "guards_clobber_factor"
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = np.log(df["close"] - df["close"])
        return out
'''
    r1 = save_and_validate_code("guards_clobber_factor.py", clean, "factor")
    assert r1["success"] is True, r1.get("errors")
    r2 = save_and_validate_code("guards_clobber_factor.py", buggy, "factor", overwrite=True)
    assert r2["success"] is False
    disk = (dirs["factor"] / "guards_clobber_factor.py").read_text(encoding="utf-8")
    assert "rolling(5)" in disk
    assert "log(df" not in disk


# ============================================================
# Test 9: new-file block → file deleted
# ============================================================

def test_new_file_block_deletes_file(sandbox_tmp):
    tmp_path, dirs = sandbox_tmp
    from ez.factor.base import Factor
    code = '''
from ez.factor.base import Factor
import numpy as np
import pandas as pd

class GuardsFreshBadFactor(Factor):
    name = "guards_fresh_bad_factor"
    warmup_period = 0
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[self.name] = np.log(df["close"] - df["close"])
        return out
'''
    result = save_and_validate_code("guards_fresh_bad_factor.py", code, "factor")
    assert result["success"] is False
    assert not (dirs["factor"] / "guards_fresh_bad_factor.py").exists()


# ============================================================
# Test 10: portfolio clean passes
# ============================================================

def test_portfolio_clean_passes(sandbox_tmp):
    tmp_path, dirs = sandbox_tmp
    code = '''
from ez.portfolio.portfolio_strategy import PortfolioStrategy

class GuardsCleanPortfolio(PortfolioStrategy):
    def generate_weights(self, panel, target_date, prev_w, prev_r):
        n = len(panel)
        return {s: 1.0 / n for s in panel}
'''
    result = save_and_validate_code("guards_clean_portfolio.py", code, "portfolio_strategy")
    assert result["success"] is True, result.get("errors")
    assert not result["guard_result"]["blocked"]


# ============================================================
# V2.19.0 post-review C1: template-style in-place mutation bypass
# ============================================================

def test_template_style_inplace_lookahead_is_blocked(sandbox_tmp):
    """Regression for C1: the auto-generated factor template idiom
    ``data[col] = ...; return data`` (in-place mutation) must NOT
    silently bypass LookaheadGuard.
    """
    tmp_path, dirs = sandbox_tmp
    code = '''
from ez.factor.base import Factor
import pandas as pd

class GuardsTemplateInPlaceLookahead(Factor):
    name = "guards_template_inplace_lookahead"
    warmup_period = 0
    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        # Template-style in-place mutation + obvious shift(-1) lookahead.
        data[self.name] = data["close"].shift(-1)
        return data
'''
    result = save_and_validate_code(
        "guards_template_inplace_lookahead.py", code, "factor",
    )
    assert result["success"] is False, (
        "C1 regression: in-place template-style lookahead slipped past guards"
    )
    errs = " ".join(result.get("errors", []))
    assert "LookaheadGuard" in errs, f"Expected LookaheadGuard block; got: {errs}"


def test_template_style_inplace_nan_is_blocked(sandbox_tmp):
    """Regression for C1: template idiom + NaN-generating op must block."""
    tmp_path, dirs = sandbox_tmp
    code = '''
from ez.factor.base import Factor
import numpy as np
import pandas as pd

class GuardsTemplateInPlaceNaN(Factor):
    name = "guards_template_inplace_nan"
    warmup_period = 0
    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data[self.name] = np.log(data["close"] - data["close"])
        return data
'''
    result = save_and_validate_code(
        "guards_template_inplace_nan.py", code, "factor",
    )
    assert result["success"] is False
    errs = " ".join(result.get("errors", []))
    assert "NaNInfGuard" in errs, f"Expected NaNInfGuard block; got: {errs}"
