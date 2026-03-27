"""Architecture fitness tests -- enforce Core/Extension boundaries."""
import ast
from pathlib import Path

CORE_FILES = [
    Path("ez/types.py"), Path("ez/errors.py"), Path("ez/config.py"),
    Path("ez/data/provider.py"), Path("ez/data/validator.py"), Path("ez/data/store.py"),
    Path("ez/factor/base.py"), Path("ez/factor/evaluator.py"),
    Path("ez/strategy/base.py"), Path("ez/strategy/loader.py"),
    Path("ez/backtest/engine.py"), Path("ez/backtest/portfolio.py"),
    Path("ez/backtest/metrics.py"), Path("ez/backtest/walk_forward.py"),
    Path("ez/backtest/significance.py"),
]

EXTENSION_MODULES = ["ez.data.providers", "ez.factor.builtin", "ez.strategy.builtin", "ez.api.routes"]


def test_core_does_not_import_extension():
    for core_file in CORE_FILES:
        if not core_file.exists():
            continue
        tree = ast.parse(core_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for ext in EXTENSION_MODULES:
                    assert ext not in node.module, (
                        f"Core file {core_file} imports extension module {node.module}"
                    )
