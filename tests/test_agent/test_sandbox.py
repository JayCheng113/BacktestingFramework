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
        assert len(errors) == 1
        assert "Forbidden import: os" in errors[0]

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


class TestListUserStrategies:
    def test_returns_list(self):
        result = list_user_strategies()
        assert isinstance(result, list)
