"""V2.7: Code validation sandbox for user/AI-generated strategies and factors.

Security:
  - Only writes to strategies/ directory
  - Validates Python syntax before saving
  - Runs contract test in subprocess with timeout
  - AST check for dangerous imports (os, subprocess, socket, etc.)
"""
from __future__ import annotations

import ast
import logging
import re
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


def check_syntax(code: str) -> list[str]:
    """Check Python syntax and forbidden imports. Returns list of errors."""
    errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
        return errors

    for node in ast.walk(tree):
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
    if target.exists() and not overwrite:
        return {"success": False, "errors": [f"File already exists: {safe_name}. Use update_strategy to overwrite."]}

    # Write file
    _STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")

    # Run contract test in subprocess with timeout
    test_result = _run_contract_test(safe_name)
    if not test_result["passed"]:
        # Remove the file if test failed
        target.unlink(missing_ok=True)
        return {
            "success": False,
            "errors": [f"Contract test failed: {test_result['output']}"],
            "test_output": test_result["output"],
        }

    return {
        "success": True,
        "errors": [],
        "path": f"strategies/{safe_name}",
        "test_output": test_result["output"],
    }


def _run_contract_test(filename: str, timeout: int = 30) -> dict:
    """Run the strategy contract test in a subprocess."""
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                "tests/test_strategy/test_strategy_contract.py",
                "-v", "--tb=short", "-x",
                "--timeout=20",
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
