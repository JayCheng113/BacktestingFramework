"""V2.7: Code validation sandbox for user/AI-generated strategies and factors.

Security:
  - Only writes to strategies/ directory
  - Validates Python syntax before saving
  - Runs contract test in subprocess with timeout
  - AST check for dangerous imports (os, subprocess, socket, etc.)
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import logging
import re
import threading
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STRATEGIES_DIR = _PROJECT_ROOT / "strategies"

# Modules that user code MUST NOT import
_FORBIDDEN_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "pathlib",
    "importlib", "ctypes", "multiprocessing", "threading",
    "signal", "pickle", "shelve", "tempfile", "glob",
    "http", "urllib", "requests", "httpx", "ftplib", "smtplib",
    "sqlite3", "duckdb", "builtins", "__builtin__",
    "code", "codeop", "compile", "compileall",
})

# Template for new strategies
_STRATEGY_TEMPLATE = '''"""User strategy: {class_name}"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import MA
from ez.strategy.base import Strategy


class {class_name}(Strategy):
    """{description}"""

    def __init__(self, period: int = 20):
        self.period = period

    @classmethod
    def get_description(cls) -> str:
        return "{description}"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {{
            "period": {{"type": "int", "default": 20, "min": 5, "max": 120, "label": "Period"}},
        }}

    def required_factors(self) -> list[Factor]:
        return [MA(period=self.period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        col = f"ma_{{self.period}}"
        return (data["adj_close"] > data[col]).astype(float)
'''

_FACTOR_TEMPLATE = '''"""User factor: {class_name}"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor


class {class_name}(Factor):
    """{description}"""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return "{factor_name}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data[self.name] = data["adj_close"].rolling(self._period).mean()
        return data
'''


def get_template(kind: str = "strategy", class_name: str = "", description: str = "") -> str:
    """Generate a template for a new strategy or factor."""
    if not class_name:
        class_name = "MyStrategy" if kind == "strategy" else "MyFactor"
    if not description:
        description = "Custom trading strategy" if kind == "strategy" else "Custom factor"

    if kind == "factor":
        factor_name = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
        return _FACTOR_TEMPLATE.format(
            class_name=class_name,
            description=description,
            factor_name=factor_name,
        )
    return _STRATEGY_TEMPLATE.format(class_name=class_name, description=description)


_FORBIDDEN_BUILTINS = frozenset({
    "__import__", "eval", "exec", "compile", "open",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
    "breakpoint", "exit", "quit", "help",
})
# NOTE: super, type, dir are intentionally NOT blocked (safe introspection)
# vars() IS blocked — at module scope it returns globals(), allowing sandbox escape
# - super() is essential for class inheritance
# - type() is used for type checking
# - dir() is read-only introspection, not dangerous

_FORBIDDEN_ATTR_CALLS = frozenset({
    "system", "popen", "exec_module", "load_module",
    "run", "call", "check_output", "Popen",
})

# Dunder attributes that are safe (needed for normal Python code)
_SAFE_DUNDERS = frozenset({
    "__init__", "__name__", "__doc__", "__class__", "__module__",
    "__str__", "__repr__", "__len__", "__iter__", "__next__",
    "__enter__", "__exit__", "__eq__", "__ne__", "__lt__", "__gt__",
    "__le__", "__ge__", "__hash__", "__bool__", "__add__", "__sub__",
    "__mul__", "__truediv__", "__floordiv__", "__mod__", "__pow__",
    "__getitem__", "__setitem__", "__contains__", "__call__",
    "__annotations__", "__dict__", "__slots__", "__all__",
    "__init_subclass__", "__post_init__", "__abstractmethods__",
    "__future__",
})


def check_syntax(code: str) -> list[str]:
    """Check Python syntax, forbidden imports, and dangerous function calls."""
    errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
        return errors

    for node in ast.walk(tree):
        # Forbidden import statements
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_MODULES:
                    errors.append(f"Forbidden import: {alias.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _FORBIDDEN_MODULES:
                    errors.append(f"Forbidden import: {node.module} (line {node.lineno})")

        # Forbidden builtin calls: __import__(), eval(), exec(), open(), etc.
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN_BUILTINS:
                errors.append(f"Forbidden call: {name}() (line {node.lineno})")
            if name in _FORBIDDEN_ATTR_CALLS:
                errors.append(f"Forbidden call: .{name}() (line {node.lineno})")

        # Block dangerous dunder attribute access (e.g. __subclasses__, __bases__)
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr.endswith("__") and attr not in _SAFE_DUNDERS:
                errors.append(f"Forbidden dunder access: .{attr} (line {node.lineno})")

        # Block __builtins__ name access (dict-style bypass)
        elif isinstance(node, ast.Name):
            if node.id == "__builtins__":
                errors.append(f"Forbidden name: __builtins__ (line {node.lineno})")

    return errors


def _safe_filename(filename: str) -> str | None:
    """Validate filename. Returns sanitized name or None if invalid."""
    if not filename.endswith(".py"):
        return None
    # Reject any path traversal attempts
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    name = filename
    if name.startswith("_") or name.startswith("."):
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*\.py$", name):
        return None
    return name


def save_and_validate_strategy(
    filename: str,
    code: str,
    overwrite: bool = False,
) -> dict:
    """Save code to strategies/ and run contract test.

    Returns dict with keys: success, errors, test_output, path.
    """
    safe_name = _safe_filename(filename)
    if not safe_name:
        return {"success": False, "errors": [f"Invalid filename: {filename}. Must be like 'my_strategy.py'"]}

    # Syntax + security check
    errors = check_syntax(code)
    if errors:
        return {"success": False, "errors": errors}

    target = _STRATEGIES_DIR / safe_name
    had_original = target.exists()
    if had_original and not overwrite:
        return {"success": False, "errors": [f"File already exists: {safe_name}. Use update_strategy to overwrite."]}

    # Back up original before overwriting
    original_code = ""
    if had_original:
        original_code = target.read_text(encoding="utf-8")

    # Write file
    _STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")

    # Run contract test in subprocess with timeout
    test_result = _run_contract_test(safe_name)
    if not test_result["passed"]:
        if had_original:
            # Restore the original file
            target.write_text(original_code, encoding="utf-8")
        else:
            # Remove new file that failed test
            target.unlink(missing_ok=True)
        return {
            "success": False,
            "errors": [f"Contract test failed: {test_result['output']}"],
            "test_output": test_result["output"],
        }

    # Hot-reload: make the strategy available immediately
    _reload_user_strategy(safe_name)

    return {
        "success": True,
        "errors": [],
        "path": f"strategies/{safe_name}",
        "test_output": test_result["output"],
    }


_reload_lock = threading.Lock()


def _reload_user_strategy(filename: str) -> None:
    """Hot-reload a user strategy after save (thread-safe).

    Steps:
    1. Remove old module from sys.modules (so re-import is fresh)
    2. Remove old class from Strategy._registry (avoid duplicate error)
    3. Re-import the module, triggering __init_subclass__ auto-registration
    """
    from ez.strategy.base import Strategy

    stem = filename.replace(".py", "")
    module_name = f"strategies.{stem}"

    with _reload_lock:
        # Find and remove old registry entries from this module
        old_keys = [k for k, v in Strategy._registry.items() if v.__module__ == module_name]
        for k in old_keys:
            del Strategy._registry[k]

        # Remove old module from sys.modules
        if module_name in sys.modules:
            del sys.modules[module_name]

        # Delete .pyc to defeat Python's mtime-based bytecode cache
        # (same-second writes produce same mtime → stale .pyc reuse)
        py_file = _STRATEGIES_DIR / filename
        pycache = _STRATEGIES_DIR / "__pycache__"
        if pycache.exists():
            for pyc in pycache.glob(f"{stem}*.pyc"):
                pyc.unlink(missing_ok=True)
        importlib.invalidate_caches()

        # Re-import via spec_from_file_location (same as loader fallback)
        py_file = _STRATEGIES_DIR / filename
        if not py_file.exists():
            return
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                logger.info("Hot-reloaded strategy: %s", filename)
        except Exception as e:
            logger.warning("Failed to hot-reload strategy %s: %s", filename, e)


def _run_contract_test(filename: str, timeout: int = 30) -> dict:
    """Run the strategy contract test in a subprocess.

    Falls back to syntax-only validation if pytest is not installed
    (e.g., production environment without dev dependencies).
    """
    # Check if pytest is available
    check = subprocess.run(
        [sys.executable, "-c", "import pytest"],
        capture_output=True, timeout=5,
    )
    if check.returncode != 0:
        # Fallback: try to import the file and verify it defines a Strategy subclass
        logger.warning("pytest not installed — running import-only validation")
        verify = subprocess.run(
            [sys.executable, "-c",
             f"import importlib.util, sys; "
             f"spec=importlib.util.spec_from_file_location('_check','{_STRATEGIES_DIR / filename}'); "
             f"mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
             f"from ez.strategy.base import Strategy; "
             f"classes=[v for v in vars(mod).values() if isinstance(v,type) and issubclass(v,Strategy) and v is not Strategy]; "
             f"assert classes, 'No Strategy subclass found'; print(f'OK: {{[c.__name__ for c in classes]}}')"],
            capture_output=True, text=True, timeout=15, cwd=str(_PROJECT_ROOT),
        )
        passed = verify.returncode == 0
        output = (verify.stdout + verify.stderr)[-1000:]
        return {"passed": passed, "output": f"(pytest不可用，import验证) {output}"}

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "tests/test_strategy/test_strategy_contract.py",
                "-v", "--tb=short", "-x",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_PROJECT_ROOT),
        )
        output = result.stdout + result.stderr
        passed = result.returncode == 0
        return {"passed": passed, "output": output[-2000:]}  # last 2K chars
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "Contract test timed out (30s)"}
    except Exception as e:
        return {"passed": False, "output": f"Failed to run test: {e}"}


def list_user_strategies() -> list[dict]:
    """List user strategy files in strategies/ directory."""
    if not _STRATEGIES_DIR.exists():
        return []
    results = []
    for py_file in sorted(_STRATEGIES_DIR.glob("*.py")):
        if py_file.name.startswith("_") or py_file.name.startswith("."):
            continue
        code = py_file.read_text(encoding="utf-8")
        # Extract class name from code
        class_name = ""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        base_name = ""
                        if isinstance(base, ast.Name):
                            base_name = base.id
                        elif isinstance(base, ast.Attribute):
                            base_name = base.attr
                        if base_name == "Strategy":
                            class_name = node.name
                            break
        except SyntaxError:
            pass
        results.append({
            "filename": py_file.name,
            "class_name": class_name,
            "size": len(code),
        })
    return results
