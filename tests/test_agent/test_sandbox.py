"""Tests for the code validation sandbox."""
from __future__ import annotations

import pytest

from ez.agent.sandbox import (
    _safe_filename,
    check_syntax,
    get_template,
    list_user_strategies,
)


class TestSyntaxCheck:
    def test_valid_code(self):
        code = "x = 1\nprint(x)"
        assert check_syntax(code) == []

    def test_syntax_error(self):
        code = "def foo(\n  x = 1"
        errors = check_syntax(code)
        assert len(errors) > 0
        assert "Syntax error" in errors[0]

    def test_forbidden_import_os(self):
        code = "import os\nprint(os.getcwd())"
        errors = check_syntax(code)
        # V2.19.0 round-4 P1-A: stricter — both `import os` AND
        # `os.getcwd` (attribute chain) are flagged. Previously only
        # the import line produced an error.
        assert len(errors) >= 1
        assert any("Forbidden import: os" in e for e in errors)
        assert any("os.getcwd" in e for e in errors)

    def test_forbidden_import_subprocess(self):
        code = "import subprocess\nsubprocess.run(['ls'])"
        errors = check_syntax(code)
        assert "Forbidden import: subprocess" in errors[0]

    def test_forbidden_from_import(self):
        code = "from os.path import join"
        errors = check_syntax(code)
        assert "Forbidden import: os.path" in errors[0]

    def test_allowed_imports(self):
        code = "import pandas as pd\nimport numpy as np\nfrom ez.factor.base import Factor"
        assert check_syntax(code) == []

    def test_forbidden_socket(self):
        code = "import socket"
        errors = check_syntax(code)
        assert len(errors) == 1

    def test_forbidden_requests(self):
        code = "import requests"
        errors = check_syntax(code)
        assert len(errors) == 1

    def test_forbidden_duckdb(self):
        code = "import duckdb"
        errors = check_syntax(code)
        assert len(errors) == 1

    def test_forbidden_dunder_import(self):
        code = '__import__("os").system("id")'
        errors = check_syntax(code)
        assert any("__import__" in e for e in errors)

    def test_forbidden_eval(self):
        code = 'eval("1+1")'
        errors = check_syntax(code)
        assert any("eval" in e for e in errors)

    def test_forbidden_exec(self):
        code = 'exec("print(1)")'
        errors = check_syntax(code)
        assert any("exec" in e for e in errors)

    def test_forbidden_open(self):
        code = 'open("/etc/passwd")'
        errors = check_syntax(code)
        assert any("open" in e for e in errors)

    def test_forbidden_getattr(self):
        code = 'getattr(__builtins__, "__import__")'
        errors = check_syntax(code)
        assert any("getattr" in e for e in errors)

    def test_forbidden_system_call(self):
        code = 'import os\nos.system("id")'
        errors = check_syntax(code)
        assert len(errors) >= 1  # at least the import

    def test_forbidden_subclasses(self):
        code = '().__class__.__bases__[0].__subclasses__()'
        errors = check_syntax(code)
        assert any("__subclasses__" in e for e in errors)

    def test_forbidden_builtins_dict(self):
        code = '__builtins__["open"]'
        errors = check_syntax(code)
        assert any("__builtins__" in e for e in errors)

    def test_forbidden_globals_attr(self):
        code = 'x.__globals__'
        errors = check_syntax(code)
        assert any("__globals__" in e for e in errors)

    def test_allowed_init_dunder(self):
        code = 'class Foo:\n  def __init__(self): pass'
        assert check_syntax(code) == []

    def test_allowed_future_import(self):
        code = 'from __future__ import annotations'
        assert check_syntax(code) == []

    def test_forbidden_help(self):
        code = 'help()'
        errors = check_syntax(code)
        assert any("help" in e for e in errors)


class TestSafeFilename:
    def test_valid(self):
        assert _safe_filename("my_strategy.py") == "my_strategy.py"

    def test_camel_case(self):
        assert _safe_filename("MyStrategy.py") == "MyStrategy.py"

    def test_no_extension(self):
        assert _safe_filename("my_strategy") is None

    def test_hidden_file(self):
        assert _safe_filename(".hidden.py") is None

    def test_underscore_prefix(self):
        assert _safe_filename("_private.py") is None

    def test_directory_traversal(self):
        assert _safe_filename("../../../etc/passwd.py") is None

    def test_path_with_slash_rejected(self):
        assert _safe_filename("foo/bar/valid.py") is None


class TestTemplate:
    def test_strategy_template(self):
        code = get_template("strategy", "MyStrat", "Test strategy")
        assert "class MyStrat(Strategy)" in code
        assert "required_factors" in code
        assert "generate_signals" in code
        errors = check_syntax(code)
        assert errors == []

    def test_factor_template(self):
        code = get_template("factor", "MyFactor", "Test factor")
        assert "class MyFactor(Factor)" in code
        assert "compute" in code
        assert "warmup_period" in code
        errors = check_syntax(code)
        assert errors == []

    def test_default_names(self):
        code = get_template("strategy")
        assert "MyStrategy" in code
        code2 = get_template("factor")
        assert "MyFactor" in code2


class TestReloadUserStrategyDedup:
    """Test AST-based class name dedup in _reload_user_strategy."""

    def test_strategy_subclass_detected(self):
        """Only classes inheriting Strategy should be matched."""
        import ast
        code = '''
from ez.strategy.base import Strategy

class MyHelper:
    pass

class MyStrat(Strategy):
    pass
'''
        tree = ast.parse(code)
        class_names = {node.name for node in ast.walk(tree)
                       if isinstance(node, ast.ClassDef)
                       and any((isinstance(b, ast.Name) and b.id == "Strategy")
                               or (isinstance(b, ast.Attribute) and b.attr == "Strategy")
                               for b in node.bases)}
        assert "MyStrat" in class_names
        assert "MyHelper" not in class_names

    def test_attribute_base_detected(self):
        """class Foo(ez.strategy.base.Strategy) should match."""
        import ast
        code = 'class Foo(ez.strategy.base.Strategy):\n    pass'
        tree = ast.parse(code)
        class_names = {node.name for node in ast.walk(tree)
                       if isinstance(node, ast.ClassDef)
                       and any((isinstance(b, ast.Name) and b.id == "Strategy")
                               or (isinstance(b, ast.Attribute) and b.attr == "Strategy")
                               for b in node.bases)}
        assert "Foo" in class_names

    def test_no_strategy_base_empty(self):
        """File with no Strategy subclass should return empty set."""
        import ast
        code = 'class NotAStrategy:\n    pass'
        tree = ast.parse(code)
        class_names = {node.name for node in ast.walk(tree)
                       if isinstance(node, ast.ClassDef)
                       and any((isinstance(b, ast.Name) and b.id == "Strategy")
                               or (isinstance(b, ast.Attribute) and b.attr == "Strategy")
                               for b in node.bases)}
        assert len(class_names) == 0


class TestRunWithTimeout:
    """Test _run_with_timeout utility."""

    def test_fast_function_returns_result(self):
        from ez.agent.tools import _run_with_timeout
        result = _run_with_timeout(lambda: 42, timeout=5)
        assert result == 42

    def test_timeout_returns_error_dict(self):
        import time
        from ez.agent.tools import _run_with_timeout
        result = _run_with_timeout(lambda: time.sleep(10) or "never", timeout=1)
        assert isinstance(result, dict)
        assert "error" in result
        assert "超时" in result["error"]

    def test_exception_propagates(self):
        from ez.agent.tools import _run_with_timeout
        def raise_error():
            raise ValueError("test error")
        # _run_with_timeout re-raises exceptions (not caught internally)
        import pytest
        with pytest.raises(ValueError, match="test error"):
            _run_with_timeout(raise_error, timeout=5)

    def test_success_path_does_not_leak_threads(self):
        """Regression test for codex finding: _run_with_timeout previously only
        called pool.shutdown() on TimeoutError, leaking one ThreadPoolExecutor
        per successful call. Fixed via try/finally.
        """
        import gc
        import threading
        import time as _t
        from ez.agent.tools import _run_with_timeout

        gc.collect()
        _t.sleep(0.05)
        baseline = threading.active_count()

        for i in range(30):
            result = _run_with_timeout(lambda x=i: x * 2, timeout=5)
            assert result == i * 2

        gc.collect()
        _t.sleep(0.1)
        final = threading.active_count()

        # Leaked at most 1 thread (pytest internal or stray timer) — before the fix,
        # this would be +30 threads because each call leaked a ThreadPoolExecutor.
        assert final - baseline <= 1, (
            f"Thread leak: baseline={baseline}, after 30 calls={final} "
            f"(diff={final - baseline})"
        )


class TestFactorHotReload:
    """Regression test for codex finding: factor save previously registered a
    stub with compute() raising NotImplementedError, requiring manual refresh.
    Fixed by calling _reload_factor_code() in the factor save path.
    """

    def test_factor_save_registers_real_implementation(self, tmp_path, monkeypatch):
        """After save_and_validate_code(kind='factor'), Factor._registry should
        contain the real class whose compute() executes successfully — not a
        stub that raises NotImplementedError."""
        from ez.agent.sandbox import save_and_validate_code
        from ez.factor.base import Factor
        import pandas as pd

        code = '''
from ez.factor.base import Factor
import pandas as pd

class CodexRegressionFactor(Factor):
    @property
    def name(self):
        return "codex_regression_factor"
    @property
    def warmup_period(self):
        return 3
    def compute(self, data):
        return data.assign(codex_regression=data["adj_close"].rolling(3).mean())
'''
        result = save_and_validate_code(
            "test_codex_regression_factor.py", code, kind="factor", overwrite=True,
        )
        try:
            assert result["success"], f"Save failed: {result.get('errors')}"
            cls = Factor._registry.get("CodexRegressionFactor")
            assert cls is not None, "Class not registered"
            # The fix: real compute() must work, not raise NotImplementedError
            instance = cls()
            df = pd.DataFrame({"adj_close": [1.0, 2.0, 3.0, 4.0, 5.0]})
            out = instance.compute(df)  # Must NOT raise NotImplementedError
            assert "codex_regression" in out.columns, (
                "compute() is a stub (codex regression): expected real implementation"
            )
            # Verify it produced actual rolling mean values, not NaN-only
            assert not out["codex_regression"].dropna().empty
        finally:
            # Cleanup
            from ez.config import get_project_root
            target = get_project_root() / "factors" / "test_codex_regression_factor.py"
            if target.exists():
                target.unlink()
            Factor._registry.pop("CodexRegressionFactor", None)


class TestCodeGenAllowedTools:
    """Regression test for codex finding: generate_strategy_code() previously
    allowed create_portfolio_strategy/create_cross_factor tools, but only scans
    strategies/ for the result — leading to wasted retries when LLM picked wrong tool.
    """

    def test_allowed_tools_locked_to_strategy_path(self):
        """The allowed_tools list must not include create_portfolio_strategy or
        create_cross_factor, since _find_latest_strategy() only checks strategies/."""
        import inspect
        from ez.agent import code_gen
        source = inspect.getsource(code_gen.generate_strategy_code)
        # Must include the minimal set
        assert '"create_strategy"' in source
        assert '"read_source"' in source
        assert '"list_factors"' in source
        # Must NOT include tools that create files outside strategies/
        assert '"create_portfolio_strategy"' not in source, (
            "create_portfolio_strategy in allowed_tools causes retry waste "
            "(codex regression)"
        )
        assert '"create_cross_factor"' not in source, (
            "create_cross_factor in allowed_tools causes retry waste (codex regression)"
        )


class TestReloadSafety:
    """Regression tests for codex round 4 P2: reload must not delete unrelated
    registry entries just because class names happen to match.
    """

    def test_user_strategy_reload_does_not_delete_builtin_with_same_stem(self, tmp_path, monkeypatch):
        """Codex #11: saving a user file `ma_cross.py` previously triggered
        deletion of `ez.strategy.builtin.ma_cross` entries because the reload
        code also cleaned `alt_module = ez.strategy.builtin.{stem}`. Now only
        the user module path is cleaned.
        """
        from ez.strategy.base import Strategy
        from ez.agent.sandbox import _reload_user_strategy

        # Ensure builtin MACrossStrategy is registered
        import ez.strategy.builtin.ma_cross  # noqa: F401
        builtin_key = "ez.strategy.builtin.ma_cross.MACrossStrategy"
        assert builtin_key in Strategy._registry, "fixture precondition"

        # Patch _STRATEGIES_DIR to tmp_path so we don't touch real strategies/
        monkeypatch.setattr("ez.agent.sandbox._STRATEGIES_DIR", tmp_path)

        # Create a user file with the same stem as the builtin
        user_file = tmp_path / "ma_cross.py"
        user_file.write_text(
            "from ez.strategy.base import Strategy\n"
            "import pandas as pd\n"
            "class UserMaStrat(Strategy):\n"
            "    def required_factors(self): return []\n"
            "    def generate_signals(self, data):\n"
            "        return pd.Series([0.0] * len(data), index=data.index)\n"
        )

        # Trigger the reload
        try:
            _reload_user_strategy("ma_cross.py")
        except Exception:
            pass  # may fail in sandbox, but the registry cleanup happens first

        # Builtin MACrossStrategy must still be registered
        assert builtin_key in Strategy._registry, (
            "Builtin MACrossStrategy was erased by user file reload — "
            "codex #11 regression"
        )

    def test_user_strategy_reload_does_not_delete_cross_module_same_name(self, monkeypatch, tmp_path):
        """Codex #17: reload previously deleted ANY registry entry whose class
        __name__ matched a class defined in the file, globally, across all
        modules. Now only the user module path is cleaned.
        """
        from ez.strategy.base import Strategy
        from ez.agent.sandbox import _reload_user_strategy
        import pandas as pd

        # Create a fake "other module" class with name ResultTestStrat
        class ResultTestStrat(Strategy):
            def required_factors(self):
                return []
            def generate_signals(self, data):
                return pd.Series([0.0] * len(data), index=data.index)
        # Manually register under a synthetic key to simulate "another module"
        other_key = "tests.other_module.ResultTestStrat"
        Strategy._registry[other_key] = ResultTestStrat

        try:
            monkeypatch.setattr("ez.agent.sandbox._STRATEGIES_DIR", tmp_path)
            # User file declares a class with the same __name__
            (tmp_path / "my_strat.py").write_text(
                "from ez.strategy.base import Strategy\n"
                "import pandas as pd\n"
                "class ResultTestStrat(Strategy):\n"
                "    def required_factors(self): return []\n"
                "    def generate_signals(self, data):\n"
                "        return pd.Series([0.0] * len(data), index=data.index)\n"
            )
            try:
                _reload_user_strategy("my_strat.py")
            except Exception:
                pass
            # The other module's ResultTestStrat must still be registered
            assert other_key in Strategy._registry, (
                "Cross-module ResultTestStrat was deleted by user file reload — "
                "codex #17 regression"
            )
        finally:
            Strategy._registry.pop(other_key, None)


class TestHotReloadFailureSignal:
    """Regression tests for codex #16: hot-reload failure must be reported as
    success=False, not success=True with a warning string.
    """

    def test_reload_failure_returns_success_false(self, monkeypatch, tmp_path):
        """save_and_validate_strategy previously returned success=True even
        when _reload_user_strategy raised. Now returns success=False.
        """
        from ez.agent import sandbox

        def _raise_reload(filename):
            raise RuntimeError("simulated reload failure")

        monkeypatch.setattr(sandbox, "_reload_user_strategy", _raise_reload)
        monkeypatch.setattr(sandbox, "_STRATEGIES_DIR", tmp_path)
        # Mock contract test to pass so we reach the reload step
        monkeypatch.setattr(
            sandbox, "_run_contract_test",
            lambda fn: {"passed": True, "output": "ok"},
        )

        code = (
            "from ez.strategy.base import Strategy\n"
            "import pandas as pd\n"
            "class HRFailTest(Strategy):\n"
            "    def required_factors(self): return []\n"
            "    def generate_signals(self, data):\n"
            "        return pd.Series([0.0] * len(data), index=data.index)\n"
        )
        result = sandbox.save_and_validate_strategy("hr_fail_test.py", code)
        assert result["success"] is False, (
            "Hot-reload failure must surface as success=False "
            "(codex #16 regression)"
        )
        assert "hot-reload failed" in (result.get("errors", [""])[0].lower()), (
            f"Error message should mention hot-reload failure: {result}"
        )


class TestListUserStrategies:
    def test_returns_list(self):
        result = list_user_strategies()
        assert isinstance(result, list)
