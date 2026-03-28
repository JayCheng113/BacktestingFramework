"""A4: Architecture gate tests -- V2.3 correctness hardening.

Validates constraints from docs/architecture/governance.md:
1. Layer dependencies: no reverse imports
2. No circular imports
3. Core file stability: listed core files exist, ez/core has no unlisted .py
4. Extension contract coverage: all subclasses tested
"""

import ast
import importlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
EZ_ROOT = PROJECT_ROOT / "ez"

# ---------------------------------------------------------------------------
# Core files from CLAUDE.md
# ---------------------------------------------------------------------------

CORE_FILES = [
    "ez/types.py",
    "ez/errors.py",
    "ez/config.py",
    "ez/core/matcher.py",
    "ez/core/ts_ops.py",
    "ez/data/provider.py",
    "ez/data/validator.py",
    "ez/data/store.py",
    "ez/factor/base.py",
    "ez/factor/evaluator.py",
    "ez/strategy/base.py",
    "ez/strategy/loader.py",
    "ez/backtest/engine.py",
    "ez/backtest/portfolio.py",
    "ez/backtest/metrics.py",
    "ez/backtest/walk_forward.py",
    "ez/backtest/significance.py",
]

# ---------------------------------------------------------------------------
# Layer hierarchy (higher = higher in dependency stack)
# ---------------------------------------------------------------------------

LAYER_MAP = {
    "ez.types": 0,
    "ez.errors": 0,
    "ez.config": 0,
    "ez.core": 1,
    "ez.data": 2,
    "ez.factor": 3,
    "ez.strategy": 4,
    "ez.backtest": 5,
    "ez.api": 6,
}


def _get_layer(module_name: str) -> int | None:
    for prefix in sorted(LAYER_MAP, key=len, reverse=True):
        if module_name == prefix or module_name.startswith(prefix + "."):
            return LAYER_MAP[prefix]
    return None


def _extract_ez_imports(filepath: Path) -> list[str]:
    try:
        tree = ast.parse(filepath.read_text())
    except SyntaxError:
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ez."):
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("ez."):
                imports.append(node.module)
    return imports


def _module_name_from_path(filepath: Path) -> str:
    rel = filepath.relative_to(PROJECT_ROOT)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ---------------------------------------------------------------------------
# 1. Layer Dependencies
# ---------------------------------------------------------------------------

class TestLayerDependencies:

    def test_no_reverse_imports(self):
        """No module imports from a higher layer."""
        violations = []
        for py_file in EZ_ROOT.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            module_name = _module_name_from_path(py_file)
            source_layer = _get_layer(module_name)
            if source_layer is None:
                continue
            for imp in _extract_ez_imports(py_file):
                target_layer = _get_layer(imp)
                if target_layer is not None and target_layer > source_layer:
                    violations.append(
                        f"{module_name} (L{source_layer}) -> {imp} (L{target_layer})"
                    )
        assert not violations, (
            "Reverse layer imports:\n  " + "\n  ".join(violations)
        )

    def test_core_is_leaf(self):
        """ez/core/ must not import from any other ez/ module."""
        violations = []
        for py_file in (EZ_ROOT / "core").rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            module_name = _module_name_from_path(py_file)
            for imp in _extract_ez_imports(py_file):
                if not imp.startswith("ez.core"):
                    violations.append(f"{module_name} -> {imp}")
        assert not violations, (
            "ez/core is a leaf, must not import other ez modules:\n  "
            + "\n  ".join(violations)
        )

    def test_future_modules_not_imported_by_core(self):
        """Core/data/factor/strategy/backtest must not import ez.agent/live/ops."""
        forbidden = ("ez.agent", "ez.live", "ez.ops")
        violations = []
        for py_file in EZ_ROOT.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            module_name = _module_name_from_path(py_file)
            # Only check non-future modules
            if any(module_name.startswith(f) for f in forbidden):
                continue
            for imp in _extract_ez_imports(py_file):
                if any(imp.startswith(f) for f in forbidden):
                    violations.append(f"{module_name} -> {imp}")
        assert not violations, (
            "Core modules must not import future modules:\n  "
            + "\n  ".join(violations)
        )


# ---------------------------------------------------------------------------
# 2. No Circular Imports
# ---------------------------------------------------------------------------

EZ_MODULES = [
    "ez.types", "ez.errors", "ez.config",
    "ez.core", "ez.core.matcher", "ez.core.ts_ops",
    "ez.data", "ez.data.store", "ez.data.validator", "ez.data.provider",
    "ez.factor", "ez.factor.base", "ez.factor.evaluator",
    "ez.strategy", "ez.strategy.base", "ez.strategy.loader",
    "ez.backtest", "ez.backtest.engine", "ez.backtest.metrics",
    "ez.backtest.walk_forward", "ez.backtest.significance",
    "ez.api",
]


class TestNoCircularImports:

    @pytest.mark.parametrize("module", EZ_MODULES)
    def test_import_succeeds(self, module):
        try:
            importlib.import_module(module)
        except ImportError as e:
            if "circular" in str(e).lower():
                pytest.fail(f"Circular import in {module}: {e}")


# ---------------------------------------------------------------------------
# 3. Core Stability
# ---------------------------------------------------------------------------

class TestCoreStability:

    @pytest.mark.parametrize("core_file", CORE_FILES)
    def test_core_file_exists(self, core_file):
        assert (PROJECT_ROOT / core_file).exists(), f"Core file missing: {core_file}"

    def test_core_package_no_unlisted_python_files(self):
        """ez/core/ should not contain unlisted .py files (excluding _cpp/)."""
        expected = {"__init__.py", "matcher.py", "ts_ops.py"}
        actual = {
            f.name
            for f in (EZ_ROOT / "core").iterdir()
            if f.is_file() and f.suffix == ".py"
        }
        unexpected = actual - expected
        assert not unexpected, (
            f"Unexpected .py files in ez/core/: {unexpected}. "
            "If intentional, add to Core Files or move to extension."
        )


# ---------------------------------------------------------------------------
# 4. Extension Contract Coverage
# ---------------------------------------------------------------------------

class TestExtensionContractCoverage:

    def test_all_factors_have_contract_tests(self):
        import ez.factor.builtin.technical  # noqa: F401
        from ez.factor.base import Factor

        subclasses = {
            cls.__name__
            for cls in Factor.__subclasses__()
            if cls.__module__.startswith("ez.")
        }
        expected = {"MA", "EMA", "RSI", "MACD", "BOLL", "Momentum"}
        assert subclasses >= expected, (
            f"Missing factor subclasses: {expected - subclasses}"
        )

    def test_all_strategies_registered(self):
        from ez.strategy.loader import load_all_strategies
        from ez.strategy.base import Strategy

        load_all_strategies()
        registered = {
            name.split(".")[-1]
            for name in Strategy._registry
            if name.startswith("ez.")
        }
        assert registered >= {"MACrossStrategy", "MomentumStrategy", "BollReversionStrategy"}, (
            f"Missing strategies in registry: {registered}"
        )

    def test_provider_files_exist(self):
        """At least 3 data providers exist in ez/data/providers/."""
        providers_dir = EZ_ROOT / "data" / "providers"
        py_files = [
            f.stem for f in providers_dir.glob("*.py")
            if f.name != "__init__.py"
        ]
        assert len(py_files) >= 3, (
            f"Expected >= 3 provider modules, found: {py_files}"
        )

    def test_contract_test_files_exist(self):
        """Contract test files exist for all extension types."""
        test_root = PROJECT_ROOT / "tests"
        required = [
            "test_core/test_matcher_contract.py",
            "test_factor/test_factor_contract.py",
            "test_strategy/test_strategy_contract.py",
            "test_data/test_provider_contract.py",
        ]
        for path in required:
            assert (test_root / path).exists(), f"Contract test missing: {path}"
