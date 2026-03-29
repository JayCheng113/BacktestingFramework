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


class TestListUserStrategies:
    def test_returns_list(self):
        result = list_user_strategies()
        assert isinstance(result, list)
