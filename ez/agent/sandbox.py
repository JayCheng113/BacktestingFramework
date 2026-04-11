"""V2.7+V2.10: Code validation sandbox for user/AI-generated code.

Security layers:
  1. AST blacklist (defense-in-depth): forbidden imports, dunders, builtins, dict-dunder access
  2. Subprocess isolation (security boundary): contract test + import validation in subprocess
  3. Factor kind: NO exec_module in main process (subprocess validates, main uses AST stubs)
     Exception: frozen mode without standalone Python falls back to in-process validation
  4. Strategy/portfolio kinds: contract test in subprocess, hot-reload in main process
     (strategy/portfolio still exec_module in main — tracked for future subprocess-only migration)
  - Only writes to whitelisted directories: strategies/, factors/, portfolio_strategies/, cross_factors/
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import logging
import re
import threading
import subprocess
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from ez.config import get_project_root
_PROJECT_ROOT = get_project_root()

# In frozen mode, user dirs are next to exe, not inside _MEIPASS
def _user_dir_root() -> Path:
    data_dir = os.environ.get("EZ_DATA_DIR")
    if getattr(sys, "frozen", False) and data_dir and Path(data_dir).parent.exists():
        return Path(data_dir).parent
    return _PROJECT_ROOT

_STRATEGIES_DIR = _user_dir_root() / "strategies"
_PORTFOLIO_STRATEGIES_DIR = _user_dir_root() / "portfolio_strategies"
_CROSS_FACTORS_DIR = _user_dir_root() / "cross_factors"

_FACTORS_DIR = _user_dir_root() / "factors"
_ML_ALPHAS_DIR = _user_dir_root() / "ml_alphas"  # V2.13 Phase 4


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _get_python_executable() -> str:
    """Get the real Python interpreter, not the frozen launcher.

    In frozen mode (PyInstaller), sys.executable is the launcher exe.
    PyInstaller onedir may or may not expose a standalone python.exe.
    If not found, returns None — callers must handle frozen fallback.
    """
    if not _is_frozen():
        return sys.executable
    base = Path(sys._MEIPASS)
    candidates = [
        base / "_internal" / "python.exe",
        base / "_internal" / "python3",
        base / "_internal" / "python",
        base / "python.exe",
        base / "python3",
        base / "python",
        Path(sys.executable).parent / "python.exe",
        Path(sys.executable).parent / "python3",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    # No standalone Python found — caller should use in-process fallback
    return ""

_KIND_DIR_MAP = {
    "strategy": _STRATEGIES_DIR,
    "factor": _FACTORS_DIR,
    "portfolio_strategy": _PORTFOLIO_STRATEGIES_DIR,
    "cross_factor": _CROSS_FACTORS_DIR,
    "ml_alpha": _ML_ALPHAS_DIR,  # V2.13 Phase 4
}


_VALID_KINDS = frozenset(_KIND_DIR_MAP.keys())


def _get_dir(kind: str) -> Path:
    """Resolve directory for a given code kind. Raises ValueError for unknown kinds."""
    if kind not in _KIND_DIR_MAP:
        raise ValueError(f"Invalid kind '{kind}'. Must be one of: {sorted(_VALID_KINDS)}")
    return _KIND_DIR_MAP[kind]


def _sandbox_registries_for_kind(kind: str) -> list[dict]:
    """Return all registry dicts that __init_subclass__ would populate for a kind.

    V2.19.0 guard framework helper. Mirrors `_get_all_registries_for_kind` in
    `ez/api/routes/code.py` but lives in the agent layer to avoid a layer
    violation (agent must NOT import from api). Kept in sync via
    `tests/test_guards/test_sandbox_registries.py` parity test.
    Returns empty list for unknown kinds — caller will no-op.
    """
    if kind == "strategy":
        from ez.strategy.base import Strategy
        return [Strategy._registry]
    if kind == "factor":
        from ez.factor.base import Factor
        return [Factor._registry, Factor._registry_by_key]
    if kind in ("cross_factor", "ml_alpha"):
        # MLAlpha IS-A CrossSectionalFactor — same registry.
        from ez.portfolio.cross_factor import CrossSectionalFactor
        return [CrossSectionalFactor._registry, CrossSectionalFactor._registry_by_key]
    if kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy
        return [PortfolioStrategy._registry, PortfolioStrategy._registry_by_key]
    return []


def _run_guards(filename: str, kind: str, target_dir: Path):
    """Run the GuardSuite against a just-saved user file.

    V2.19.0. Called by all three save flows. The guard imports the file
    under a **unique probe module name** (not the production
    `factors.foo` / `strategies.bar` etc.), so `__init_subclass__` does
    not emit a name-collision warning when the production class has
    already been registered by hot-reload. The probe module + its
    registry entries are cleaned up after the suite finishes.

    Graceful degradation: if `ez.testing.guards` cannot be imported (in a
    hypothetical minimal deployment), return `None`. Callers treat `None`
    as "no guards ran" and skip the block check.
    """
    try:
        from ez.testing.guards.suite import (
            GuardSuite, load_user_class, drop_probe_module,
            _unique_probe_module_name,
        )
        from ez.testing.guards.base import GuardContext
    except ImportError as imp_err:
        logger.warning("ez.testing.guards not available, skipping guard framework: %s", imp_err)
        return None

    stem = filename.replace(".py", "")
    probe_module_name = _unique_probe_module_name(stem)
    file_path = target_dir / filename
    user_class, err = load_user_class(file_path, probe_module_name, kind)  # type: ignore[arg-type]
    try:
        context = GuardContext(
            filename=filename,
            module_name=probe_module_name,
            file_path=file_path,
            kind=kind,  # type: ignore[arg-type]
            user_class=user_class,
            instantiation_error=err,
        )
        return GuardSuite().run(context)
    finally:
        # Always drop the probe module so its registry entries (even
        # abstract-class skeletons) don't accumulate in memory.
        drop_probe_module(probe_module_name, kind)  # type: ignore[arg-type]

# Modules that user code MUST NOT import
_FORBIDDEN_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "pathlib",
    "importlib", "ctypes", "multiprocessing", "threading",
    "signal", "pickle", "shelve", "tempfile", "glob",
    "http", "urllib", "requests", "httpx", "ftplib", "smtplib",
    "sqlite3", "duckdb", "builtins", "__builtin__",
    "code", "codeop", "compile", "compileall",
    # GC-based sandbox escapes: gc.get_referrers(type) → module objects → Popen
    "gc", "_thread", "py_compile", "runpy", "pty", "pipes",
    "webbrowser", "antigravity", "turtle",
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
        # Always start with a copy — never mutate the input frame.
        # The engine (and the guard framework) shares DataFrames across
        # calls; in-place mutation causes silent cross-call pollution.
        out = data.copy()
        out[self.name] = data["adj_close"].rolling(self._period).mean()
        return out
'''


_PORTFOLIO_STRATEGY_TEMPLATE = '''"""Portfolio strategy: {class_name}"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from ez.portfolio.portfolio_strategy import PortfolioStrategy
from ez.portfolio.cross_factor import MomentumRank


class {class_name}(PortfolioStrategy):
    """{description}"""

    def __init__(self, top_n: int = 10, **params):
        super().__init__(**params)
        self.top_n = top_n
        self._factor = MomentumRank(period=20)

    @classmethod
    def get_description(cls) -> str:
        return "{description}"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {{
            "top_n": {{"type": "int", "default": 10, "min": 1, "max": 100}},
        }}

    def generate_weights(self, universe_data, date, prev_weights, prev_returns):
        scores = self._factor.compute(universe_data, date)
        valid = scores.dropna()
        if len(valid) < 1:
            return {{}}
        n = min(self.top_n, len(valid))
        top = valid.nlargest(n).index
        w = 1.0 / n
        return {{sym: w for sym in top}}
'''

_CROSS_FACTOR_TEMPLATE = '''"""Cross-sectional factor: {class_name}"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from ez.portfolio.cross_factor import CrossSectionalFactor


class {class_name}(CrossSectionalFactor):
    """{description}"""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return "{factor_name}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, universe_data, date):
        scores = {{}}
        for sym, df in universe_data.items():
            if len(df) < self._period or "adj_close" not in df.columns:
                continue
            close = df["adj_close"]
            scores[sym] = (close.iloc[-1] - close.iloc[-self._period]) / close.iloc[-self._period]
        return pd.Series(scores).rank(pct=True) if scores else pd.Series(dtype=float)
'''


def get_template(kind: str = "strategy", class_name: str = "", description: str = "") -> str:
    """Generate a template for a new strategy or factor."""
    defaults = {
        "strategy": ("MyStrategy", "Custom trading strategy"),
        "factor": ("MyFactor", "Custom factor"),
        "portfolio_strategy": ("MyPortfolioStrategy", "Custom portfolio strategy"),
        "cross_factor": ("MyCrossFactor", "Custom cross-sectional factor"),
        "ml_alpha": ("MyMLAlpha", "Custom ML alpha factor"),  # V2.13 Phase 4
    }
    default_name, default_desc = defaults.get(kind, ("MyStrategy", "Custom strategy"))
    if not class_name:
        class_name = default_name
    if not description:
        description = default_desc

    if kind == "factor":
        factor_name = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
        return _FACTOR_TEMPLATE.format(class_name=class_name, description=description, factor_name=factor_name)
    if kind == "portfolio_strategy":
        return _PORTFOLIO_STRATEGY_TEMPLATE.format(class_name=class_name, description=description)
    if kind == "cross_factor":
        factor_name = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
        return _CROSS_FACTOR_TEMPLATE.format(class_name=class_name, description=description, factor_name=factor_name)
    if kind == "ml_alpha":
        # V2.13 Phase 4: use ML_ALPHA_TEMPLATE from ez.portfolio.ml_alpha
        from ez.portfolio.ml_alpha import ML_ALPHA_TEMPLATE
        factor_name = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
        return ML_ALPHA_TEMPLATE.format(
            class_name=class_name,
            description=description,
            name=factor_name,
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
    "__annotations__", "__slots__", "__all__",
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

        # Block dict-style dunder access: type.__dict__["__subclasses__"], vars()["__import__"], etc.
        elif isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                key = node.slice.value
                if key.startswith("__") and key.endswith("__"):
                    errors.append(f"Forbidden dict dunder access: [\"{key}\"] (line {node.lineno})")
            # Also block __dict__ attribute on subscript target
            if isinstance(node.value, ast.Attribute) and node.value.attr == "__dict__":
                errors.append(f"Forbidden __dict__ subscript access (line {node.lineno})")

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

    # V2.19.0: guard framework — run after contract test passes, before hot-reload.
    # Because the guard imports the file under a unique _guard_probe.* module
    # name (see _run_guards / load_user_class), the production module
    # `strategies.{stem}` is never touched by the probe. On block, we only
    # need to roll back the file (backup or delete) — no registry cleanup
    # is required because the strategy was never hot-reloaded on this path.
    # Graceful: `guard_result is None` means the guard framework is not
    # available — treat as "no guards ran".
    guard_result = _run_guards(safe_name, "strategy", _STRATEGIES_DIR)
    if guard_result is not None and guard_result.blocked:
        # Disk rollback FIRST (I2: don't leave registry cleaned and disk
        # dirty). Hook 1 runs guards BEFORE _reload_user_strategy, so
        # the production registry was never touched on this code path —
        # we only need to roll back the file. No registry surgery here.
        try:
            if had_original:
                target.write_text(original_code, encoding="utf-8")
            else:
                target.unlink(missing_ok=True)
        except Exception as disk_err:
            logger.error("Strategy guard rollback disk write failed: %s", disk_err)
            return {
                "success": False,
                "errors": [
                    f"Guard blocked save AND rollback failed: {disk_err}. "
                    f"Filesystem state may be inconsistent — please check "
                    f"strategies/{safe_name} manually.",
                ],
                "guard_result": guard_result.to_payload(),
            }
        return {
            "success": False,
            "errors": [
                f"Guard failed: {blk.guard_name}: {blk.message}"
                for blk in guard_result.blocks
            ],
            "test_output": test_result["output"],
            "guard_result": guard_result.to_payload(),
        }

    # Hot-reload: make the strategy available immediately.
    # V2.12.1 post-review (codex): prior version returned success=True even
    # when _reload_user_strategy raised, hiding the fact that the main
    # process registry still held the old implementation. Editor showed
    # "保存成功" but subsequent backtests ran the previous version.
    # Now reload failure surfaces as success=False with a clear reason.
    try:
        _reload_user_strategy(safe_name)
    except Exception as e:
        logger.warning("Strategy saved but hot-reload failed: %s", e)
        return {
            "success": False,
            "errors": [
                f"File saved but hot-reload failed — live registry still holds "
                f"the previous version. Details: {e}. Please restart the server "
                f"or use /api/code/refresh to force a full rescan."
            ],
            "path": f"strategies/{safe_name}",
            "test_output": f"Contract test passed. Hot-reload failed: {e}",
            "guard_result": guard_result.to_payload() if guard_result is not None else None,
        }

    return {
        "success": True,
        "errors": [],
        "path": f"strategies/{safe_name}",
        "test_output": test_result["output"],
        "guard_result": guard_result.to_payload() if guard_result is not None else None,
    }


_reload_lock = threading.Lock()


def _reload_user_strategy(filename: str) -> None:
    """Hot-reload a user strategy after save (thread-safe).

    Steps:
    1. Remove old registry entries for THIS user module only
    2. Remove old module from sys.modules
    3. Re-import the module, triggering __init_subclass__ auto-registration

    V2.12.1 post-review (codex #11 + #17): prior version also deleted
    registry entries where `__module__ == "ez.strategy.builtin.{stem}"`
    (erasing built-in strategies whose filename stem matched — e.g.,
    saving a user file `ma_cross.py` wiped the built-in MACrossStrategy)
    AND deleted any class with the same `__name__` globally (erasing
    unrelated modules' strategies just because they shared a class name).
    Both paths are removed: Strategy registry uses `module.class` keys, so
    two different modules with the same class name coexist safely — we
    only need to clean entries belonging to THIS user module.
    """
    from ez.strategy.base import Strategy

    stem = filename.replace(".py", "")
    module_name = f"strategies.{stem}"

    with _reload_lock:
        # Only clean entries from the user module being reloaded.
        # DO NOT touch ez.strategy.builtin.* or cross-module name matches —
        # they belong to different classes that should coexist with user code.
        old_keys = [k for k, v in Strategy._registry.items() if v.__module__ == module_name]
        for k in old_keys:
            Strategy._registry.pop(k, None)

        # Remove old module from sys.modules (user module only)
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
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                logger.info("Hot-reloaded strategy: %s", filename)
        except Exception as e:
            logger.warning("Failed to hot-reload strategy %s: %s", filename, e)
            raise


def _validate_strategy_inprocess(filename: str) -> dict:
    """In-process strategy validation for frozen mode (no subprocess Python)."""
    try:
        target = _STRATEGIES_DIR / filename
        spec = importlib.util.spec_from_file_location(f"_check_{filename.replace('.py', '')}", str(target))
        if not spec or not spec.loader:
            return {"passed": False, "output": f"Cannot create module spec for {filename}"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from ez.strategy.base import Strategy
        classes = [v for v in vars(mod).values()
                   if isinstance(v, type) and issubclass(v, Strategy) and v is not Strategy]
        if not classes:
            return {"passed": False, "output": "No Strategy subclass found"}
        return {"passed": True, "output": f"(frozen模式进程内验证) OK: {[c.__name__ for c in classes]}"}
    except Exception as e:
        return {"passed": False, "output": f"(frozen模式验证失败) {e}"}


def _validate_portfolio_inprocess(filename: str, kind: str, target_dir: Path) -> dict:
    """In-process portfolio/factor validation for frozen mode."""
    try:
        target = target_dir / filename
        module_name = f"_check_{filename.replace('.py', '')}"
        spec = importlib.util.spec_from_file_location(module_name, str(target))
        if not spec or not spec.loader:
            return {"passed": False, "output": f"Cannot create module spec for {filename}"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if kind == "portfolio_strategy":
            from ez.portfolio.portfolio_strategy import PortfolioStrategy
            classes = [v for v in vars(mod).values()
                       if isinstance(v, type) and issubclass(v, PortfolioStrategy) and v is not PortfolioStrategy]
            if not classes:
                return {"passed": False, "output": "No PortfolioStrategy subclass found"}
        else:
            from ez.portfolio.cross_factor import CrossSectionalFactor
            classes = [v for v in vars(mod).values()
                       if isinstance(v, type) and issubclass(v, CrossSectionalFactor) and v is not CrossSectionalFactor]
            if not classes:
                return {"passed": False, "output": "No CrossSectionalFactor subclass found"}
        return {"passed": True, "output": f"(frozen模式进程内验证) OK: {[c.__name__ for c in classes]}"}
    except Exception as e:
        return {"passed": False, "output": f"(frozen模式验证失败) {e}"}


def _run_contract_test(filename: str, timeout: int = 30) -> dict:
    """Run the strategy contract test in a subprocess.

    Falls back to in-process import validation if:
    - Frozen mode without standalone Python interpreter
    - pytest not installed
    """
    python_exe = _get_python_executable()

    # Frozen mode without Python interpreter: in-process import validation
    if not python_exe:
        return _validate_strategy_inprocess(filename)

    # Check if pytest is available
    check = subprocess.run(
        [python_exe, "-c", "import pytest"],
        capture_output=True, timeout=5,
    )
    if check.returncode != 0:
        # Fallback: try to import the file and verify it defines a Strategy subclass
        logger.warning("pytest not installed — running import-only validation")
        verify = subprocess.run(
            [python_exe, "-c",
             f"import importlib.util, sys; "
             f"spec=importlib.util.spec_from_file_location('_check',{repr(str(_STRATEGIES_DIR / filename))}); "
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
                python_exe, "-m", "pytest",
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


def save_and_validate_code(
    filename: str, code: str, kind: str = "strategy", overwrite: bool = False,
) -> dict:
    """Save code to the appropriate directory and run contract test.

    kind: "strategy" | "factor" | "portfolio_strategy" | "cross_factor"
    """
    if kind not in _VALID_KINDS:
        return {"success": False, "errors": [f"Invalid kind: {kind}. Must be one of: {sorted(_VALID_KINDS)}"]}
    target_dir = _get_dir(kind)
    if kind == "strategy":
        return save_and_validate_strategy(filename, code, overwrite=overwrite)
    if kind == "factor":
        safe_name = _safe_filename(filename)
        if not safe_name:
            return {"success": False, "errors": [f"Invalid filename: {filename}"]}
        errors = check_syntax(code)
        if errors:
            return {"success": False, "errors": errors}
        # Validate code contains a class inheriting from Factor (AST check)
        try:
            _tree = ast.parse(code)
            _has_factor_class = any(
                isinstance(node, ast.ClassDef) and
                any((isinstance(b, ast.Name) and b.id == "Factor") or
                    (isinstance(b, ast.Attribute) and b.attr == "Factor")
                    for b in node.bases)
                for node in ast.walk(_tree)
            )
        except SyntaxError:
            _has_factor_class = False
        if not _has_factor_class:
            return {"success": False, "errors": ["Code must contain a class inheriting from Factor (AST check)"]}
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        if target.exists() and not overwrite:
            return {"success": False, "errors": [f"File already exists: {safe_name}. Use overwrite=true."]}
        # Backup old file for rollback
        backup = None
        if target.exists():
            backup = target.read_text(encoding="utf-8")
        target.write_text(code, encoding="utf-8")
        # Validate via subprocess (like strategy contract test) for isolation + timeout
        from ez.factor.base import Factor
        stem = safe_name.replace(".py", "")
        module_name = f"{target_dir.name}.{stem}"
        try:
            # Subprocess import test with 10s timeout (code NEVER runs in main process)
            safe_path_repr = repr(str(target))  # Properly escaped for Python string literal
            # Subprocess: import + verify Factor subclass exists + print class names
            test_code = (
                f"import importlib.util, sys\n"
                f"spec = importlib.util.spec_from_file_location('{module_name}', {safe_path_repr})\n"
                f"mod = importlib.util.module_from_spec(spec)\n"
                f"spec.loader.exec_module(mod)\n"
                f"from ez.factor.base import Factor\n"
                f"classes = [k for k, v in Factor._registry.items() if v.__module__ == '{module_name}']\n"
                f"if not classes: raise ValueError('No Factor subclass')\n"
                f"print(','.join(classes))\n"
            )
            import subprocess as _sp_mod
            _py = _get_python_executable()
            _frozen_inprocess = False
            if not _py:
                # Frozen mode fallback: in-process import (exec_module registers real class)
                spec = importlib.util.spec_from_file_location(module_name, str(target))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    registered = [k for k, v in Factor._registry.items() if v.__module__ == module_name]
                    if not registered:
                        raise ValueError("No Factor subclass found")
                    _frozen_inprocess = True  # real class already registered, skip stub
                else:
                    raise ValueError("Cannot create module spec")
            else:
                proc = _sp_mod.run([_py, "-c", test_code], capture_output=True, text=True, timeout=10)
                if proc.returncode != 0:
                    raise ValueError(f"Import failed: {proc.stderr[-200:]}")
                registered = [c.strip() for c in proc.stdout.strip().split(",") if c.strip()]
            if not registered:
                raise ValueError("No Factor subclass found in code")
            # Hot-reload the actual factor implementation into the main process.
            # Before V2.12.1 post-review, this path only registered a stub that raised
            # NotImplementedError on compute() — users saw their factor in the registry
            # but any evaluation crashed until a manual /api/code/refresh or restart.
            # Skip if frozen in-process: exec_module above already registered real classes.
            if not _frozen_inprocess:
                _reload_factor_code(safe_name, target_dir)
                # Verify the reload actually placed real (non-stub) classes in the registry
                live = [k for k, v in Factor._registry.items() if v.__module__ == module_name]
                if not live:
                    raise ValueError(
                        f"Factor hot-reload succeeded but registry has no entries for {module_name}"
                    )

            # V2.19.0: guard framework — run after hot-reload succeeds,
            # before returning success. On block: roll back the file and
            # let _reload_factor_code clean out the now-invalid v2 + load
            # v0 from the backup (or clean registry if there is no backup).
            # Straight-line rollback (not via the `except Exception` block
            # which would mis-categorize the error as "Factor validation failed").
            factor_guard_result = _run_guards(safe_name, "factor", target_dir)
            if factor_guard_result is not None and factor_guard_result.blocked:
                # I2: disk write first, then rely on _reload_factor_code
                # to clean the dual-dict registry atomically. Wrap the
                # disk write in try/except so a disk error is surfaced
                # instead of swallowed.
                try:
                    if backup is not None:
                        target.write_text(backup, encoding="utf-8")
                        # _reload_factor_code internally cleans BOTH
                        # _registry and _registry_by_key for this module
                        # (V2.12.2 hot-reload helper), then re-imports
                        # the backup source. One call, atomic semantics.
                        # Codex round-2 P2 #1: surface re-register failure
                        # as a half-state CRITICAL, not a silent log line.
                        try:
                            _reload_factor_code(safe_name, target_dir)
                        except Exception as restore_err:
                            logger.error(
                                "Factor guard rollback re-register failed: %s",
                                restore_err,
                            )
                            return {
                                "success": False,
                                "errors": [
                                    f"Guard failed: {blk.guard_name}: {blk.message}"
                                    for blk in factor_guard_result.blocks
                                ] + [
                                    f"CRITICAL: backup file restored but re-register "
                                    f"failed — live registry may be in a half-state. "
                                    f"Run /api/code/refresh or restart the server. "
                                    f"Details: {type(restore_err).__name__}: {restore_err}"
                                ],
                                "guard_result": factor_guard_result.to_payload(),
                            }
                    else:
                        target.unlink(missing_ok=True)
                        # No backup — manually clean the v2 entries using
                        # the shared helper (I4: one source of truth for
                        # registry cleanup, drift-checked by parity test).
                        with _reload_lock:
                            for reg in _sandbox_registries_for_kind("factor"):
                                for k in [
                                    k for k, v in reg.items()
                                    if v.__module__ == module_name
                                ]:
                                    reg.pop(k, None)
                            if module_name in sys.modules:
                                del sys.modules[module_name]
                except Exception as disk_err:
                    logger.error("Factor guard rollback disk op failed: %s", disk_err)
                    return {
                        "success": False,
                        "errors": [
                            f"Guard blocked save AND rollback failed: {disk_err}",
                        ],
                        "guard_result": factor_guard_result.to_payload(),
                    }
                return {
                    "success": False,
                    "errors": [
                        f"Guard failed: {blk.guard_name}: {blk.message}"
                        for blk in factor_guard_result.blocks
                    ],
                    "guard_result": factor_guard_result.to_payload(),
                }
        except Exception as e:
            # Clean up any dirty registry entries.
            # V2.12.2 codex reviewer: dual-dict registry — clean BOTH dicts
            # or the full-key dict leaks zombies on save rollback. Prior
            # version only popped name-keyed entries.
            dirty = [k for k, v in Factor._registry.items() if v.__module__ == module_name]
            for k in dirty:
                del Factor._registry[k]
            dirty_full = [k for k, v in Factor._registry_by_key.items() if v.__module__ == module_name]
            for k in dirty_full:
                del Factor._registry_by_key[k]
            if module_name in sys.modules:
                del sys.modules[module_name]
            # Rollback file AND re-register the previous version so users
            # don't lose a working factor just because a save failed.
            # V2.12.1 post-review (codex #12): prior version restored the
            # backup file but never re-executed it, so the factor vanished
            # from the live registry until the user manually hit /refresh.
            if backup is not None:
                target.write_text(backup, encoding="utf-8")
                # Best-effort re-register: if the backup itself fails to
                # import (shouldn't happen, but defend against environment
                # drift), leave the registry empty rather than crashing.
                try:
                    _reload_factor_code(safe_name, target_dir)
                except Exception as restore_err:
                    logger.warning(
                        "Factor rollback succeeded but re-register failed: %s",
                        restore_err,
                    )
            else:
                target.unlink(missing_ok=True)
            return {"success": False, "errors": [f"Factor validation failed: {e}"]}
        return {
            "success": True,
            "path": str(target),
            "test_output": f"Factor saved. Registered: {registered}",
            "guard_result": factor_guard_result.to_payload() if factor_guard_result is not None else None,
        }


    safe_name = _safe_filename(filename)
    if not safe_name:
        return {"success": False, "errors": [f"Invalid filename: {filename}"]}

    errors = check_syntax(code)
    if errors:
        return {"success": False, "errors": errors}

    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    if target.exists() and not overwrite:
        return {"success": False, "errors": [f"File already exists: {safe_name}"]}

    original_code = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(code, encoding="utf-8")

    # Contract test: import and validate
    test_result = _run_portfolio_contract_test(safe_name, kind, target_dir)
    if not test_result["passed"]:
        if original_code:
            target.write_text(original_code, encoding="utf-8")
        else:
            target.unlink(missing_ok=True)
        return {"success": False, "errors": [f"Contract test failed: {test_result['output']}"],
                "test_output": test_result["output"]}

    # Hot-reload: register in main process.
    # V2.12.1 post-review (codex): prior version returned success=True even
    # on reload failure, hiding stale-registry state. Now reload failure is
    # reported as success=False (matches strategy save behavior).
    try:
        _reload_portfolio_code(safe_name, kind, target_dir)
    except Exception as e:
        logger.warning("Portfolio code saved but hot-reload failed: %s", e)
        return {
            "success": False,
            "errors": [
                f"File saved but hot-reload failed — live registry still holds "
                f"the previous version. Details: {e}. Please use /api/code/refresh "
                f"or restart the server."
            ],
            "path": f"{target_dir.name}/{safe_name}",
            "test_output": f"Contract test passed. Hot-reload failed: {e}",
        }

    # V2.19.0: guard framework — run after hot-reload succeeds, before
    # returning success. On block: roll back the file and either re-run
    # _reload_portfolio_code (backup path) or manually clean the v2
    # entries via _sandbox_registries_for_kind (no-backup path).
    portfolio_guard_result = _run_guards(safe_name, kind, target_dir)
    if portfolio_guard_result is not None and portfolio_guard_result.blocked:
        stem_pf = safe_name.replace(".py", "")
        module_name_pf = f"{target_dir.name}.{stem_pf}"
        try:
            if original_code:
                target.write_text(original_code, encoding="utf-8")
                # Codex round-2 P2 #1: surface re-register failure as
                # half-state CRITICAL.
                try:
                    _reload_portfolio_code(safe_name, kind, target_dir)
                except Exception as restore_err:
                    logger.error(
                        "Portfolio guard rollback re-register failed: %s",
                        restore_err,
                    )
                    return {
                        "success": False,
                        "errors": [
                            f"Guard failed: {blk.guard_name}: {blk.message}"
                            for blk in portfolio_guard_result.blocks
                        ] + [
                            f"CRITICAL: backup file restored but re-register "
                            f"failed — live registry may be in a half-state. "
                            f"Run /api/code/refresh or restart the server. "
                            f"Details: {type(restore_err).__name__}: {restore_err}"
                        ],
                        "test_output": test_result["output"],
                        "guard_result": portfolio_guard_result.to_payload(),
                    }
            else:
                target.unlink(missing_ok=True)
                with _reload_lock:
                    for reg in _sandbox_registries_for_kind(kind):
                        for k in [
                            k for k, v in reg.items()
                            if v.__module__ == module_name_pf
                        ]:
                            reg.pop(k, None)
                    if module_name_pf in sys.modules:
                        del sys.modules[module_name_pf]
        except Exception as disk_err:
            logger.error("Portfolio guard rollback disk op failed: %s", disk_err)
            return {
                "success": False,
                "errors": [f"Guard blocked save AND rollback failed: {disk_err}"],
                "guard_result": portfolio_guard_result.to_payload(),
            }
        return {
            "success": False,
            "errors": [
                f"Guard failed: {blk.guard_name}: {blk.message}"
                for blk in portfolio_guard_result.blocks
            ],
            "test_output": test_result["output"],
            "guard_result": portfolio_guard_result.to_payload(),
        }

    return {
        "success": True,
        "errors": [],
        "path": f"{target_dir.name}/{safe_name}",
        "test_output": test_result["output"],
        "guard_result": portfolio_guard_result.to_payload() if portfolio_guard_result is not None else None,
    }


def _run_portfolio_contract_test(filename: str, kind: str, target_dir: Path) -> dict:
    """Contract test for portfolio strategies and cross-sectional factors."""
    python_exe = _get_python_executable()
    if not python_exe:
        return _validate_portfolio_inprocess(filename, kind, target_dir)

    safe_path_repr = repr(str(target_dir / filename))
    if kind == "portfolio_strategy":
        test_code = f"""
import importlib.util, sys, numpy as np, pandas as pd
from datetime import datetime
spec = importlib.util.spec_from_file_location('_check', {safe_path_repr})
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
from ez.portfolio.portfolio_strategy import PortfolioStrategy
classes = [v for v in vars(mod).values() if isinstance(v, type) and issubclass(v, PortfolioStrategy) and v is not PortfolioStrategy]
assert classes, 'No PortfolioStrategy subclass found'
cls = classes[0]
inst = cls()
# Mock data: 3 stocks, 50 days
rng = np.random.default_rng(42)
dates = pd.date_range('2024-01-01', periods=50, freq='B')
data = {{}}
for s in ['T001', 'T002', 'T003']:
    p = 10 * np.cumprod(1 + rng.normal(0, 0.02, 50))
    data[s] = pd.DataFrame({{'open': p, 'high': p*1.01, 'low': p*0.99, 'close': p, 'adj_close': p, 'volume': rng.integers(1000, 9999, 50)}}, index=dates)
w = inst.generate_weights(data, datetime(2024, 3, 15), {{}}, {{}})
assert isinstance(w, dict), f'generate_weights must return dict, got {{type(w)}}'
for k, v in w.items():
    assert v >= 0, f'Weight for {{k}} is {{v}} < 0 (long-only)'
assert sum(w.values()) <= 1.001, f'Weights sum {{sum(w.values())}} > 1.0'
print(f'OK: {{cls.__name__}} weights={{w}}')
"""
    elif kind == "ml_alpha":
        # V2.13 Phase 4: MLAlpha contract test. MLAlpha subclasses need
        # sklearn AND do a whitelist probe in __init__, so the subprocess
        # must have sklearn installed. The test validates:
        # 1. File exports an MLAlpha subclass
        # 2. Subclass can be instantiated (triggers whitelist + n_jobs check)
        # 3. compute() returns a pd.Series on mock data
        test_code = f"""
import importlib.util, numpy as np, pandas as pd
from datetime import datetime
spec = importlib.util.spec_from_file_location('_check', {safe_path_repr})
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
from ez.portfolio.ml_alpha import MLAlpha
classes = [v for v in vars(mod).values() if isinstance(v, type) and issubclass(v, MLAlpha) and v is not MLAlpha]
assert classes, 'No MLAlpha subclass found'
cls = classes[0]
inst = cls()  # triggers _assert_supported_estimator (whitelist + n_jobs)
rng = np.random.default_rng(42)
dates = pd.date_range('2024-01-01', periods=200, freq='B')
data = {{}}
for s in ['T001', 'T002', 'T003']:
    p = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 200))
    data[s] = pd.DataFrame({{'open': p, 'high': p*1.01, 'low': p*0.99, 'close': p, 'adj_close': p, 'volume': rng.integers(100000, 999999, 200).astype(float)}}, index=dates)
result = inst.compute(data, datetime(2024, 7, 1))
assert isinstance(result, pd.Series), f'compute must return Series, got {{type(result)}}'
print(f'OK: {{cls.__name__}} warmup={{inst.warmup_period}}')
"""
    else:  # cross_factor
        test_code = f"""
import importlib.util, numpy as np, pandas as pd
from datetime import datetime
spec = importlib.util.spec_from_file_location('_check', {safe_path_repr})
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
from ez.portfolio.cross_factor import CrossSectionalFactor
classes = [v for v in vars(mod).values() if isinstance(v, type) and issubclass(v, CrossSectionalFactor) and v is not CrossSectionalFactor]
assert classes, 'No CrossSectionalFactor subclass found'
cls = classes[0]
inst = cls()
rng = np.random.default_rng(42)
dates = pd.date_range('2024-01-01', periods=50, freq='B')
data = {{}}
for s in ['T001', 'T002', 'T003']:
    p = 10 * np.cumprod(1 + rng.normal(0, 0.02, 50))
    data[s] = pd.DataFrame({{'open': p, 'high': p*1.01, 'low': p*0.99, 'close': p, 'adj_close': p, 'volume': rng.integers(1000, 9999, 50)}}, index=dates)
result = inst.compute(data, datetime(2024, 3, 15))
assert isinstance(result, pd.Series), f'compute must return Series, got {{type(result)}}'
assert not result.isna().all(), 'compute returned all NaN'
print(f'OK: {{cls.__name__}} scores={{result.to_dict()}}')
"""
    try:
        result = subprocess.run(
            [python_exe, "-c", test_code],
            capture_output=True, text=True, timeout=30, cwd=str(_PROJECT_ROOT),
        )
        return {"passed": result.returncode == 0,
                "output": (result.stdout + result.stderr)[-2000:]}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "Contract test timed out (30s)"}
    except Exception as e:
        return {"passed": False, "output": f"Failed: {e}"}


def _reload_portfolio_code(filename: str, kind: str, target_dir: Path) -> None:
    """Hot-reload portfolio strategy or cross-factor in main process."""
    stem = filename.replace(".py", "")
    module_name = f"{target_dir.name}.{stem}"

    with _reload_lock:
        if kind == "portfolio_strategy":
            from ez.portfolio.portfolio_strategy import PortfolioStrategy
            # V2.12.1 post-review: dual-dict registry (_registry_by_key is
            # authoritative, _registry is the name-keyed backward-compat view).
            # Must clean BOTH dicts or the full-key dict leaks zombies.
            old_name_keys = [k for k, v in PortfolioStrategy._registry.items() if v.__module__ == module_name]
            for k in old_name_keys:
                del PortfolioStrategy._registry[k]
            old_full_keys = [k for k, v in PortfolioStrategy._registry_by_key.items() if v.__module__ == module_name]
            for k in old_full_keys:
                del PortfolioStrategy._registry_by_key[k]
        elif kind in ("cross_factor", "ml_alpha"):
            # V2.13 Phase 4: ml_alpha is a CrossSectionalFactor subclass,
            # shares the same dual-dict registry. Same cleanup path.
            from ez.portfolio.cross_factor import CrossSectionalFactor
            # V2.12.2 codex: dual-dict registry — clean BOTH dicts or the
            # full-key dict leaks zombies on hot-reload.
            old_name_keys = [k for k, v in CrossSectionalFactor._registry.items() if v.__module__ == module_name]
            for k in old_name_keys:
                del CrossSectionalFactor._registry[k]
            old_full_keys = [k for k, v in CrossSectionalFactor._registry_by_key.items() if v.__module__ == module_name]
            for k in old_full_keys:
                del CrossSectionalFactor._registry_by_key[k]

        if module_name in sys.modules:
            del sys.modules[module_name]

        pycache = target_dir / "__pycache__"
        if pycache.exists():
            for pyc in pycache.glob(f"{stem}*.pyc"):
                pyc.unlink(missing_ok=True)
        importlib.invalidate_caches()

        py_file = target_dir / filename
        if not py_file.exists():
            return
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                logger.info("Hot-reloaded %s: %s", kind, filename)
        except Exception as e:
            logger.warning("Failed to hot-reload %s %s: %s", kind, filename, e)
            raise


def _reload_factor_code(filename: str, target_dir: Path) -> None:
    """Hot-reload user factor in main process (trigger __init_subclass__)."""
    stem = filename.replace(".py", "")
    module_name = f"{target_dir.name}.{stem}"

    with _reload_lock:
        from ez.factor.base import Factor
        # V2.12.2 codex: dual-dict registry — clean BOTH dicts or the
        # full-key dict leaks zombies on hot-reload.
        old_name_keys = [k for k, v in Factor._registry.items() if v.__module__ == module_name]
        for k in old_name_keys:
            del Factor._registry[k]
        old_full_keys = [k for k, v in Factor._registry_by_key.items() if v.__module__ == module_name]
        for k in old_full_keys:
            del Factor._registry_by_key[k]

        if module_name in sys.modules:
            del sys.modules[module_name]

        # Clear pycache to prevent stale bytecode
        pycache = target_dir / "__pycache__"
        if pycache.exists():
            for pyc in pycache.glob(f"{stem}*.pyc"):
                pyc.unlink(missing_ok=True)
        importlib.invalidate_caches()

        py_file = target_dir / filename
        if not py_file.exists():
            return
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                logger.info("Hot-reloaded factor: %s", filename)
        except Exception as e:
            logger.warning("Failed to hot-reload factor %s: %s", filename, e)
            raise


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


def list_portfolio_files(kind: str = "portfolio_strategy") -> list[dict]:
    """List files in portfolio_strategies/ or cross_factors/."""
    target_dir = _get_dir(kind)
    if not target_dir.exists():
        return []
    # AST-level class detection: matches `class Foo(BaseClass)` where
    # BaseClass is a direct Name or Attribute node. Limitation: aliased
    # imports (`from ... import MLAlpha as MA`), module-qualified bases
    # (`ml_alpha.MLAlpha`), or intermediate subclasses won't match.
    # This is a pre-existing pattern shared with all 4 original kinds.
    base_classes = {"portfolio_strategy": "PortfolioStrategy", "cross_factor": "CrossSectionalFactor", "factor": "Factor", "ml_alpha": "MLAlpha"}
    target_base = base_classes.get(kind, "PortfolioStrategy")
    results = []
    for py_file in sorted(target_dir.glob("*.py")):
        if py_file.name.startswith("_") or py_file.name.startswith("."):
            continue
        code = py_file.read_text(encoding="utf-8")
        class_name = ""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        base_name = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else "")
                        if base_name == target_base:
                            class_name = node.name
                            break
        except SyntaxError:
            pass
        results.append({"filename": py_file.name, "class_name": class_name, "kind": kind})
    return results
