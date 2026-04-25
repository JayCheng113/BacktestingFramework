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
    ``factors.foo`` / ``strategies.bar`` etc.), so ``__init_subclass__``
    full-key collision is avoided. The probe module + its registry
    entries are cleaned up after the suite finishes via
    ``drop_probe_module``.

    **Thread safety (codex round-2 S4)**: the entire probe import →
    suite run → drop_probe sequence is wrapped in ``_get_reload_lock()`` so
    that a concurrent thread iterating ``Factor._registry`` (e.g.
    ``/api/factors`` listing, ``ResearchRunner`` background task) cannot
    observe the transient probe-class displacement of the name-keyed
    dict between import and drop. Without the lock there is a ~3-8 ms
    window where the probe class is visible — under multi-thread access
    this is a real (low-impact) UX bug.

    Graceful degradation: if ``ez.testing.guards`` cannot be imported,
    return ``None``. Callers treat ``None`` as "no guards ran".
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
    with _get_reload_lock():
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
            # Always drop the probe module so its registry entries don't
            # accumulate in memory.
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

# Full module paths user code MUST NOT import (codex round-3 P1-1).
# The above _FORBIDDEN_MODULES check only looks at the ROOT segment
# (`name.split(".")[0]`), so `from ez.agent.sandbox import _get_reload_lock`
# slips through because root="ez" is not forbidden. These are checked
# as either exact matches or prefix matches with a trailing dot.
# KNOWN LIMITATION (codex round-6): the binding table + attribute chain
# check covers explicit imports, aliases (`import ez as z`), simple
# rebindings (`z = ez`), and walrus (`(z := ez)`). However, it CANNOT
# track dynamic rebindings like `z = [ez][0]`, `z = ident(ez)`,
# `for z in [ez]: pass`, or `z = getattr(ez, 'agent')`. These bypass
# the static check and let user code reach the lock.
#
# V2.21 fix: lock moved into closure (_get_reload_lock), no longer a
# module-level attribute. Dynamic alias bypass is now moot — there is
# nothing to reach even if the AST check is bypassed.
# The only attack outcome is a DoS on the save flow, not silent state
# corruption or data theft.
#
# The definitive fix is to move `_reload_lock` out of the module
# namespace entirely (e.g., into a closure-captured variable or
# separate process-level synchronization), so user code cannot access
# it regardless of binding tricks. This is a V2.21+ architectural task.
_FORBIDDEN_FULL_MODULES = frozenset({
    # V2.24 round-2 Critical 2: forbid ALL ez.agent.* and ez.api.routes.*
    # as default-deny. Previous enumeration missed ez.agent.tools
    # (create_portfolio_strategy file-write), ez.api.routes.portfolio
    # (_get_store DuckDB handle), ez.api.routes.validation
    # (_load_run_returns). Direct suffix match covers all internal
    # modules + future additions without re-enumeration.
    "ez.agent",               # blocks ez.agent.tools, .sandbox, .chat, .runner, ...
    "ez.api.routes",          # blocks all REST route internals
    "ez.api.deps",            # global store/provider singletons
    # Guard framework internals — user code in a guard probe must not
    # bypass the guard suite or pollute its mock data.
    "ez.testing.guards",
    # Backwards-compat: specific entries retained for clarity
    "ez.agent.sandbox",
    "ez.api.routes.code",
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
        # V2.13 Phase 4: use ML_ALPHA_TEMPLATE from ez.portfolio.ml.alpha
        from ez.portfolio.ml.alpha import ML_ALPHA_TEMPLATE
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

    def _is_forbidden(name: str) -> bool:
        """Return True if the module path matches a forbidden root or
        forbidden full path. Codex round-3 P1-1 — the root-only check
        missed `ez.agent.sandbox` because root="ez" is fine."""
        if not name:
            return False
        root = name.split(".")[0]
        if root in _FORBIDDEN_MODULES:
            return True
        for forbidden_full in _FORBIDDEN_FULL_MODULES:
            if name == forbidden_full or name.startswith(forbidden_full + "."):
                return True
        return False

    def _dynamic_chain_reaches_forbidden(suffix: str) -> bool:
        """V2.23.2 Critical 1: For dynamic-rooted chains (user did
        something like `z = [ez][0]`), check if the attribute-suffix
        could reach a forbidden full module by treating the root as
        any permissive prefix.

        We check: does ``<any_prefix>.<suffix>`` ever match a forbidden
        full module? Practically: the suffix ends with a segment that
        is a suffix of some forbidden full path.

        Examples (with _FORBIDDEN_FULL_MODULES = {"ez.agent.sandbox", ...}):
            suffix="agent.sandbox"   → matches (could be ez.agent.sandbox)
            suffix="agent.sandbox.X" → matches
            suffix="sandbox"         → matches (could be ez.agent.sandbox)
            suffix="foo.bar"         → does not match
        """
        if not suffix:
            return False
        for forbidden_full in _FORBIDDEN_FULL_MODULES:
            forbidden_parts = forbidden_full.split(".")
            suffix_parts = suffix.split(".")
            # Check if suffix is any suffix of forbidden_full, OR
            # starts with any suffix of forbidden_full (i.e., traverses
            # into / past the forbidden module).
            for i in range(len(forbidden_parts)):
                fb_suffix = forbidden_parts[i:]
                # Check if suffix_parts starts with fb_suffix
                if len(suffix_parts) >= len(fb_suffix) and \
                        suffix_parts[:len(fb_suffix)] == fb_suffix:
                    return True
        return False

    def _reconstruct_attribute_chain(node: ast.AST) -> str | None:
        """Walk up an Attribute chain to reconstruct the dotted name.

        Codex round-4 P1-A — `import ez` is legal, but
        `ez.agent.sandbox._reload_lock` (attribute traversal from a
        legal root import) reaches into a forbidden module via the
        attribute syntax. We must detect such chains at AST level.

        Codex round-5 P1 follow-up — also unwrap NamedExpr (walrus
        operator) so `(z := ez).agent.sandbox` reconstructs as
        ``ez.agent.sandbox`` instead of returning None.

        Returns the dotted string (e.g. "ez.agent.sandbox._get_reload_lock")
        if the chain is rooted at a Name (or NamedExpr wrapping a Name).
        Returns None for chains rooted at a Call result, Subscript, etc.
        — those are too dynamic to analyze statically.
        """
        parts: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        # Unwrap walrus: `(z := ez).agent` has NamedExpr at the root.
        # Use the .value of the NamedExpr (the right-hand side of := )
        # which is the actual module reference.
        if isinstance(cur, ast.NamedExpr):
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
        return None

    # ─── Codex round-5 P1 + P2-1: build a name binding table ───────
    #
    # Pre-pass to track which names refer to which module paths. This
    # lets the attribute-chain check resolve aliases and rebindings:
    #
    #   import ez as z; z.agent.sandbox._get_reload_lock
    #     → z bound to "ez", chain "z.agent.sandbox._get_reload_lock"
    #       resolved to "ez.agent.sandbox._get_reload_lock" → forbidden
    #
    #   sys = MyClass(); sys.mean()
    #     → sys bound to None (locally rebound to non-module value)
    #       chain "sys.mean" skipped (not a module access)
    #
    # Mapping value semantics:
    #   str = the module path the name refers to (e.g. "ez.agent.sandbox")
    #   None = locally rebound to a known non-module value (skip the check)
    #   _DYNAMIC_UNSAFE sentinel = bound via dynamic expression (Call,
    #       Subscript, ForFor, comprehension, etc.) whose value cannot be
    #       statically resolved. V2.23.2 Critical 1 fix: previously we
    #       marked these as None (safe) which let attackers bypass the
    #       check via `z = [ez][0]; z.agent.sandbox.X`. Now we mark them
    #       as "dynamic" and treat chains rooted at such names as
    #       potentially reaching any forbidden module.
    #   missing from dict = unbound (treat root as-is for check)
    name_bindings: dict[str, "str | None | object"] = {}

    _UNKNOWN = object()
    _DYNAMIC_UNSAFE = object()  # V2.23.2 Critical 1: dynamic binding, could be forbidden

    def _resolve_value_to_module(value: ast.AST) -> str | None | object:
        """Try to resolve the right-hand side of an assignment to a
        module path string. Returns:
          - a str module path if resolvable
          - None if it's a simple literal (Constant) — safe local
          - _DYNAMIC_UNSAFE if it's a Call/Subscript/container/
            comprehension/etc. whose value we can't statically determine
            and which COULD reference a forbidden module
          - _UNKNOWN if we can't tell (treat as pass-through for now)
        """
        if isinstance(value, ast.Name):
            src = value.id
            if src in name_bindings:
                return name_bindings[src]
            return _UNKNOWN
        if isinstance(value, ast.Attribute):
            chain = _reconstruct_attribute_chain(value)
            if chain is None:
                return _DYNAMIC_UNSAFE  # chain root itself is dynamic
            parts = chain.split(".")
            if parts[0] in name_bindings:
                base = name_bindings[parts[0]]
                if base is None:
                    return None
                if base is _DYNAMIC_UNSAFE:
                    return _DYNAMIC_UNSAFE
                if isinstance(base, str) and len(parts) > 1:
                    return base + "." + ".".join(parts[1:])
                return base
            return _UNKNOWN
        if isinstance(value, ast.Constant):
            # Literal value — safe non-module
            return None
        # V2.23.2 Critical 1: Call / Subscript / List / Dict / Tuple /
        # GeneratorExp / ListComp / SetComp / DictComp / IfExp /
        # BoolOp / BinOp / UnaryOp etc. → dynamic, could wrap forbidden
        # module. Previously these all returned None (safe), which was
        # the bypass vector (`[ez][0]`, `ident(ez)`, etc.).
        return _DYNAMIC_UNSAFE

    # Pass 1: collect bindings from Import / ImportFrom / Assign / NamedExpr.
    # We walk in document order so later assignments can shadow earlier ones.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    # `import ez.agent as a` → a bound to "ez.agent"
                    name_bindings[alias.asname] = alias.name
                else:
                    # `import ez.agent` binds the TOP-LEVEL name "ez"
                    # (Python semantics — the parent package is what's
                    # bound in scope, not the dotted child).
                    top = alias.name.split(".")[0]
                    name_bindings[top] = top
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    name_bindings[local] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Assign):
            # `target = source` — track simple Name = Name | Attribute
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
                resolved = _resolve_value_to_module(node.value)
                if resolved is _UNKNOWN:
                    if target_name in name_bindings:
                        name_bindings[target_name] = None
                else:
                    name_bindings[target_name] = resolved
        elif isinstance(node, ast.NamedExpr):
            # `(z := value)` walrus operator binds z too.
            if isinstance(node.target, ast.Name):
                target_name = node.target.id
                resolved = _resolve_value_to_module(node.value)
                if resolved is _UNKNOWN:
                    if target_name in name_bindings:
                        name_bindings[target_name] = None
                else:
                    name_bindings[target_name] = resolved
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            # `for z in [ez]: ...` / `async for z in stream: ...` binds
            # z to iterable element — statically unknowable, mark dynamic.
            if isinstance(node.target, ast.Name):
                name_bindings[node.target.id] = _DYNAMIC_UNSAFE
        elif isinstance(node, ast.withitem):
            # V2.24 round-2 Critical 1: `with X() as z` can evaluate X()
            # to the live sandbox module if a custom __enter__ returns it
            # (codex reproduced). Mark dynamic-unsafe, not None.
            if node.optional_vars is not None and isinstance(node.optional_vars, ast.Name):
                name_bindings[node.optional_vars.id] = _DYNAMIC_UNSAFE
        elif isinstance(node, ast.comprehension):
            # List/set/dict/generator comprehension target. Conservative.
            if isinstance(node.target, ast.Name):
                name_bindings[node.target.id] = _DYNAMIC_UNSAFE
        elif isinstance(node, ast.ExceptHandler):
            # V2.24 round-2 Critical 1: `except E as z: z.agent.sandbox...`.
            # Exception instances don't usually hold module refs, but a
            # carefully-crafted custom exception class could. Mark dynamic
            # to be safe.
            if node.name:
                name_bindings[node.name] = _DYNAMIC_UNSAFE
        elif hasattr(ast, "MatchAs") and isinstance(node, ast.MatchAs):
            # V2.24 round-2 Critical 1: `match x: case _ as z: ...` or
            # `case [z]: ...` binds z from the matched subject. The subject
            # can be `ez` or any alias. Mark dynamic.
            if node.name:
                name_bindings[node.name] = _DYNAMIC_UNSAFE
        elif hasattr(ast, "MatchStar") and isinstance(node, ast.MatchStar):
            if node.name:
                name_bindings[node.name] = _DYNAMIC_UNSAFE
        elif hasattr(ast, "MatchMapping") and isinstance(node, ast.MatchMapping):
            if node.rest:
                name_bindings[node.rest] = _DYNAMIC_UNSAFE
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # V2.24 round-2 Important: function args SHADOW outer bindings
            # within the function body. We approximate by marking every
            # positional/kw/vararg/kwarg name as None (locally rebound,
            # safe). This fixes false-positives like
            # `def f(sys): return sys.mean()` which was wrongly blocked
            # because the global `sys` binding polluted the check.
            # Limitation: this is module-flat, not truly scoped, so if
            # the function arg `sys` is used AFTER the function's scope
            # (syntactically impossible but AST-flat), the shadow persists
            # for the rest of the file. Worst case: user can't use `sys`
            # as a name outside of a function after declaring a function
            # that has `sys` as an arg — acceptable degradation.
            for arg_list in (node.args.args, node.args.posonlyargs, node.args.kwonlyargs):
                for a in arg_list:
                    name_bindings[a.arg] = None
            if node.args.vararg:
                name_bindings[node.args.vararg.arg] = None
            if node.args.kwarg:
                name_bindings[node.args.kwarg.arg] = None
        elif isinstance(node, ast.Lambda):
            # `lambda sys: sys.mean()` — same logic as function args.
            for arg_list in (node.args.args, node.args.posonlyargs, node.args.kwonlyargs):
                for a in arg_list:
                    name_bindings[a.arg] = None
            if node.args.vararg:
                name_bindings[node.args.vararg.arg] = None
            if node.args.kwarg:
                name_bindings[node.args.kwarg.arg] = None

    _DYNAMIC_CHAIN_MARKER = "__DYNAMIC_UNSAFE__"

    def _resolve_chain_with_bindings(chain: str) -> str | None:
        """Substitute the chain root with its binding if present.

        Returns:
          - resolved module path string if the chain root maps to a module
          - None if the chain root is locally rebound to a non-module
            (i.e., the chain is not a real module access — skip the check)
          - _DYNAMIC_CHAIN_MARKER + ".<suffix>" if the root is bound
            via a dynamic expression (V2.23.2 Critical 1). Downstream
            code MUST treat this as potentially forbidden.
          - the original chain if the root is unbound
        """
        if not chain:
            return chain
        parts = chain.split(".")
        root = parts[0]
        if root in name_bindings:
            bound = name_bindings[root]
            if bound is None:
                return None  # local rebinding to non-module
            if bound is _DYNAMIC_UNSAFE:
                # Dynamic root — preserve suffix for sensitive-suffix check
                return _DYNAMIC_CHAIN_MARKER + (
                    "." + ".".join(parts[1:]) if len(parts) > 1 else ""
                )
            if len(parts) > 1:
                return bound + "." + ".".join(parts[1:])
            return bound
        return chain

    for node in ast.walk(tree):
        # Forbidden import statements
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    errors.append(f"Forbidden import: {alias.name} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if _is_forbidden(node.module):
                    errors.append(f"Forbidden import: {node.module} (line {node.lineno})")
                else:
                    # `from ez.agent import sandbox` has node.module="ez.agent"
                    # which is NOT forbidden on its own; we must also check
                    # each imported name as if it were a submodule of node.module.
                    # Codex round-3 P1-1 follow-up.
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        full = f"{node.module}.{alias.name}"
                        if _is_forbidden(full):
                            errors.append(
                                f"Forbidden import: {full} (line {node.lineno})"
                            )

        # Forbidden builtin calls: __import__(), eval(), exec(), open(), etc.
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            attr_call_skip = False
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
                # Codex round-5 P2-1 follow-up: don't flag `.run()` /
                # `.system()` etc. when the receiver is locally rebound
                # to a non-module value. Without this, `subprocess =
                # MyClass(); subprocess.run()` is wrongly blocked even
                # though the user clearly meant their own class.
                if name in _FORBIDDEN_ATTR_CALLS:
                    chain = _reconstruct_attribute_chain(func)
                    if chain:
                        resolved = _resolve_chain_with_bindings(chain)
                        if resolved is None:
                            # Receiver locally rebound — skip the check.
                            attr_call_skip = True
                        elif resolved.startswith(_DYNAMIC_CHAIN_MARKER):
                            # V2.23.2: receiver dynamically bound (e.g.
                            # `subprocess = MyClass(); subprocess.run()`).
                            # The Attribute-chain visitor catches
                            # module-path traversal; here we only need
                            # to block direct forbidden-method calls
                            # when the receiver is provably a forbidden
                            # module. Dynamic = not provable → skip.
                            attr_call_skip = True
            if name in _FORBIDDEN_BUILTINS:
                errors.append(f"Forbidden call: {name}() (line {node.lineno})")
            if name in _FORBIDDEN_ATTR_CALLS and not attr_call_skip:
                errors.append(f"Forbidden call: .{name}() (line {node.lineno})")

        # Block dangerous dunder attribute access (e.g. __subclasses__, __bases__)
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr.endswith("__") and attr not in _SAFE_DUNDERS:
                errors.append(f"Forbidden dunder access: .{attr} (line {node.lineno})")
            # Codex round-4 P1-A: block attribute chains that traverse into
            # a forbidden full module.
            #
            # Codex round-5 P1: resolve the chain root through the binding
            # table so `import ez as z; z.agent.sandbox._get_reload_lock`
            # gets caught (the "z" root is bound to "ez", so the resolved
            # chain is "ez.agent.sandbox._get_reload_lock" which is forbidden).
            #
            # Codex round-5 P2-1: skip the check entirely if the root is
            # locally rebound to a non-module value (e.g. `sys = MyClass();
            # sys.mean()` should NOT be flagged as accessing the sys module).
            chain = _reconstruct_attribute_chain(node)
            if chain:
                resolved = _resolve_chain_with_bindings(chain)
                if resolved is None:
                    # Locally rebound — not a module access at all.
                    pass
                elif resolved.startswith(_DYNAMIC_CHAIN_MARKER):
                    # V2.23.2 Critical 1: root bound via dynamic expression
                    # (Call/Subscript/loop target etc.). If the suffix matches
                    # a forbidden-module path pattern, reject conservatively.
                    suffix = resolved[len(_DYNAMIC_CHAIN_MARKER):].lstrip(".")
                    # Any suffix that ends with or traverses a forbidden
                    # full module path is rejected. Example: suffix
                    # "agent.sandbox.X" with `z = [ez][0]; z.agent.sandbox.X`
                    # → suffix traverses `ez.agent.sandbox`.
                    if _dynamic_chain_reaches_forbidden(suffix):
                        errors.append(
                            f"Forbidden attribute chain: {chain} (line {node.lineno}). "
                            f"Root is bound via a dynamic expression; suffix "
                            f"'{suffix}' could reach a forbidden module."
                        )
                elif _is_forbidden(resolved):
                    if resolved != chain:
                        errors.append(
                            f"Forbidden attribute chain: {chain} → {resolved} "
                            f"(line {node.lineno}). Cannot reach forbidden "
                            f"modules via attribute traversal (alias resolved)."
                        )
                    else:
                        errors.append(
                            f"Forbidden attribute chain: {chain} (line {node.lineno}). "
                            f"Cannot reach forbidden modules via attribute traversal."
                        )

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
        # dirty). Hook 1 runs guards BEFORE _reload_code, so
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
    # when _reload_code raised, hiding the fact that the main
    # process registry still held the old implementation. Editor showed
    # "保存成功" but subsequent backtests ran the previous version.
    # Now reload failure surfaces as success=False with a clear reason.
    try:
        _reload_code(safe_name, "strategy", _STRATEGIES_DIR, _strategy_bases, dual_registry=False)
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


# Reverted from RLock to Lock in codex round-4 (P1-A finding).
#
# History:
#   V2.19.0 round-3 (S4): wrapped _run_guards in _reload_lock for
#       thread-safety against concurrent registry access.
#   V2.19.0 round-3 P1-1 (codex round-3): user code can `from ez.agent
#       import sandbox` and acquire _reload_lock from inside its module
#       body — fixed by adding _FORBIDDEN_FULL_MODULES.
#   V2.19.0 round-3 P1-1 follow-up: switched to RLock as defense-in-depth
#       in case the import check missed something.
#   V2.19.0 round-4 P1-A (this commit): the RLock change was MORE harmful
#       than helpful. Lock would immediately deadlock the current save
#       (loud, easy to detect via worker hang). RLock allows user code
#       in the same thread to silently bump the count 1→2; on `with`
#       exit the count goes 2→1 and the lock remains permanently held.
#       Subsequent saves from other threads then deadlock with no
#       traceback pointing at the attacker. Reverting to Lock makes the
#       attack symptom IMMEDIATELY VISIBLE again, and the new attribute-
#       chain check below now catches the previously-overlooked attack
#       vectors at AST time so the lock should never actually be reached
#       by user code.
#
#   V2.21 (this commit): definitive fix — move lock into a closure so
#       it is not accessible as a module attribute at all. User code
#       cannot `sandbox._reload_lock` because the name no longer exists
#       in the module namespace. The lock is captured by `_get_reload_lock`
#       via closure, invisible to `dir(sandbox)` / `getattr(sandbox, ...)`.
def _make_lock_accessor():
    """Create a closure that captures the reload lock.

    V2.21: eliminates the dynamic-alias bypass vulnerability. Previously
    ``_reload_lock`` was a module-level variable that user code could
    potentially reach via ``[ez][0].agent.sandbox._reload_lock`` or
    similar dynamic-binding tricks that bypass the static AST check.

    Now the lock lives inside this closure — it does not appear in
    ``sandbox.__dict__`` and cannot be accessed via
    ``getattr(sandbox, '_reload_lock')`` or any attribute traversal.
    The only way to get the lock is to call ``_get_reload_lock()``,
    which is itself a module-level function. Its ``__closure__``
    cells are technically accessible from Python via
    ``_get_reload_lock.__closure__[0].cell_contents``, but this
    requires ``__closure__`` dunder access which is independently
    blocked by the AST forbidden-dunder check. Three defense layers:
    (1) ``ez.agent.sandbox`` is a forbidden module import,
    (2) ``__closure__`` dunder access is blocked by AST,
    (3) the lock is not a module-level attribute.
    """
    lock = threading.Lock()

    def _accessor() -> threading.Lock:
        return lock

    return _accessor

_get_reload_lock = _make_lock_accessor()


def _strategy_bases():
    """Deferred import: return Strategy base class list for registry cleanup."""
    from ez.strategy.base import Strategy
    return [Strategy]


def _portfolio_bases(kind: str):
    """Deferred import: return PortfolioStrategy or CrossSectionalFactor base class list."""
    if kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy
        return [PortfolioStrategy]
    else:
        from ez.portfolio.cross_factor import CrossSectionalFactor
        return [CrossSectionalFactor]


def _factor_bases():
    """Deferred import: return Factor base class list for registry cleanup."""
    from ez.factor.base import Factor
    return [Factor]


def _reload_code(
    filename: str,
    kind: str,
    target_dir: Path,
    get_base_classes: callable,
    dual_registry: bool = True,
) -> None:
    """Hot-reload user code: strategy, portfolio strategy, cross factor, ml_alpha, or factor.

    Parameters
    ----------
    filename : .py file name
    kind : human label for logging
    target_dir : directory containing the file
    get_base_classes : zero-arg callable returning list of base classes whose registries to clean.
                       Deferred to avoid import at definition time.
    dual_registry : if True, clean both _registry and _registry_by_key
    """
    stem = filename.replace(".py", "")
    module_name = f"{target_dir.name}.{stem}"

    with _get_reload_lock():
        for base_cls in get_base_classes():
            old_keys = [k for k, v in base_cls._registry.items() if v.__module__ == module_name]
            for k in old_keys:
                base_cls._registry.pop(k, None)
            if dual_registry and hasattr(base_cls, "_registry_by_key"):
                old_full_keys = [k for k, v in base_cls._registry_by_key.items() if v.__module__ == module_name]
                for k in old_full_keys:
                    base_cls._registry_by_key.pop(k, None)

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


def _validate_inprocess(filename: str, kind: str, target_dir: Path, get_base_classes: callable) -> dict:
    """In-process validation for frozen mode (no subprocess Python).

    Parameters
    ----------
    filename : .py file name
    kind : human label (unused but kept for symmetry with _reload_code)
    target_dir : directory containing the file
    get_base_classes : zero-arg callable returning list of base classes to check against
    """
    try:
        target = target_dir / filename
        module_name = f"_check_{filename.replace('.py', '')}"
        spec = importlib.util.spec_from_file_location(module_name, str(target))
        if not spec or not spec.loader:
            return {"passed": False, "output": f"Cannot create module spec for {filename}"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        base_classes = get_base_classes()
        classes = [v for v in vars(mod).values()
                   if isinstance(v, type) and any(issubclass(v, bc) and v is not bc for bc in base_classes)]
        if not classes:
            class_names = " or ".join(c.__name__ for c in base_classes)
            return {"passed": False, "output": f"No {class_names} subclass found"}
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
        return _validate_inprocess(filename, "strategy", _STRATEGIES_DIR, _strategy_bases)

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
                _reload_code(safe_name, "factor", target_dir, _factor_bases)
                # Verify the reload actually placed real (non-stub) classes in the registry
                live = [k for k, v in Factor._registry.items() if v.__module__ == module_name]
                if not live:
                    raise ValueError(
                        f"Factor hot-reload succeeded but registry has no entries for {module_name}"
                    )

            # V2.19.0: guard framework — run after hot-reload succeeds,
            # before returning success. On block: roll back the file and
            # let _reload_code clean out the now-invalid v2 + load
            # v0 from the backup (or clean registry if there is no backup).
            # Straight-line rollback (not via the `except Exception` block
            # which would mis-categorize the error as "Factor validation failed").
            factor_guard_result = _run_guards(safe_name, "factor", target_dir)
            if factor_guard_result is not None and factor_guard_result.blocked:
                # I2: disk write first, then rely on _reload_code
                # to clean the dual-dict registry atomically. Wrap the
                # disk write in try/except so a disk error is surfaced
                # instead of swallowed.
                try:
                    if backup is not None:
                        target.write_text(backup, encoding="utf-8")
                        # _reload_code internally cleans BOTH
                        # _registry and _registry_by_key for this module
                        # (V2.12.2 hot-reload helper), then re-imports
                        # the backup source. One call, atomic semantics.
                        # Codex round-2 P2 #1: surface re-register failure
                        # as a half-state CRITICAL, not a silent log line.
                        try:
                            _reload_code(safe_name, "factor", target_dir, _factor_bases)
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
                        with _get_reload_lock():
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
                    _reload_code(safe_name, "factor", target_dir, _factor_bases)
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
        _reload_code(safe_name, kind, target_dir, lambda: _portfolio_bases(kind))
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
    # _reload_code (backup path) or manually clean the v2
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
                    _reload_code(safe_name, kind, target_dir, lambda: _portfolio_bases(kind))
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
                with _get_reload_lock():
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
        return _validate_inprocess(filename, kind, target_dir, lambda: _portfolio_bases(kind))

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
from ez.portfolio.ml.alpha import MLAlpha
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
