"""Regression: accounting invariants must use explicit raise, not assert."""
import ast
from pathlib import Path
import pytest


def test_no_assert_for_accounting_invariants_source():
    """Source-level guard: no assert statements mentioning cash or equity."""
    engine_path = Path("ez/portfolio/engine.py")
    source = engine_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            segment = ast.get_source_segment(source, node)
            if segment and ("cash" in segment or "equity" in segment):
                pytest.fail(
                    f"Line {node.lineno}: accounting invariant uses assert "
                    f"instead of explicit raise. python -O strips assert.")


def test_accounting_error_importable():
    """AccountingError must exist and be a proper exception."""
    from ez.errors import AccountingError
    assert issubclass(AccountingError, Exception)
    assert AccountingError.__name__ == "AccountingError"
