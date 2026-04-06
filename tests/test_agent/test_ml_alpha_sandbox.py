"""V2.13 Phase 4: ML Alpha sandbox integration tests.

Tests that the `ml_alpha` kind is properly wired into the sandbox:
- _KIND_DIR_MAP includes ml_alpha
- get_template("ml_alpha") produces valid Python
- check_syntax passes sklearn imports
- _get_all_registries_for_kind returns CrossSectionalFactor registries
- Hot-reload routes to cross_factor branch

**CI note**: These tests use sklearn for template validation.
"""
from __future__ import annotations

import ast

import pytest

pytest.importorskip(
    "sklearn",
    reason="V2.13 ML Alpha sandbox tests require scikit-learn",
)


def test_ml_alpha_in_kind_dir_map():
    from ez.agent.sandbox import _KIND_DIR_MAP
    assert "ml_alpha" in _KIND_DIR_MAP
    assert str(_KIND_DIR_MAP["ml_alpha"]).endswith("ml_alphas")


def test_ml_alpha_in_valid_kinds():
    from ez.agent.sandbox import _VALID_KINDS
    assert "ml_alpha" in _VALID_KINDS


def test_get_template_ml_alpha_produces_valid_python():
    from ez.agent.sandbox import get_template
    code = get_template(kind="ml_alpha", class_name="TestRidgeAlpha", description="Test alpha")

    # Must be valid Python
    compile(code, "<template>", "exec")

    # Must contain the class definition
    assert "class TestRidgeAlpha(MLAlpha)" in code
    assert "from ez.portfolio.ml_alpha import MLAlpha" in code
    assert "from sklearn.linear_model import Ridge" in code


def test_get_template_ml_alpha_default_class_name():
    from ez.agent.sandbox import get_template
    code = get_template(kind="ml_alpha")
    assert "class MyMLAlpha(MLAlpha)" in code


def test_sklearn_import_passes_syntax_check():
    """sklearn is NOT in _FORBIDDEN_MODULES — imports must pass."""
    from ez.agent.sandbox import check_syntax
    code = "from sklearn.linear_model import Ridge\nfrom sklearn.ensemble import RandomForestRegressor\n"
    errors = check_syntax(code)
    assert not errors, f"sklearn import rejected: {errors}"


def test_registries_for_ml_alpha_returns_cross_factor_registries():
    """ml_alpha uses CrossSectionalFactor's dual-dict registry."""
    from ez.api.routes.code import _get_all_registries_for_kind
    from ez.portfolio.cross_factor import CrossSectionalFactor

    registries = _get_all_registries_for_kind("ml_alpha")
    assert len(registries) == 2
    assert registries[0] is CrossSectionalFactor._registry
    assert registries[1] is CrossSectionalFactor._registry_by_key


def test_ml_alpha_template_can_be_instantiated():
    """The rendered template must produce a class that can be
    instantiated (triggers whitelist + n_jobs runtime check)."""
    from ez.agent.sandbox import get_template
    from ez.portfolio.ml_alpha import MLAlpha
    from ez.portfolio.cross_factor import CrossSectionalFactor

    code = get_template(kind="ml_alpha", class_name="SandboxTestAlpha", description="sandbox test")
    ns: dict = {}
    try:
        exec(code, ns)
    finally:
        CrossSectionalFactor._registry.pop("SandboxTestAlpha", None)
        for key in list(CrossSectionalFactor._registry_by_key.keys()):
            if key.endswith(".SandboxTestAlpha"):
                del CrossSectionalFactor._registry_by_key[key]

    cls = ns.get("SandboxTestAlpha")
    assert cls is not None
    assert issubclass(cls, MLAlpha)
    instance = cls()
    assert instance.warmup_period > 0
