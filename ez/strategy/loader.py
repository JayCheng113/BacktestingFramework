"""Strategy auto-discovery from configured directories.

[CORE] — scans paths from config, does not hardcode directories.
Resolves relative paths against the project root (where pyproject.toml lives).
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import pkgutil
import sys
from pathlib import Path

from ez.config import load_config

logger = logging.getLogger(__name__)

# Project root: use get_project_root() for frozen-mode compatibility
from ez.config import get_project_root
_PROJECT_ROOT = get_project_root()


def _load_py_files(directory: Path, module_prefix: str) -> None:
    """Load all .py files from a directory via importlib (works in frozen mode)."""
    if not directory.exists():
        return
    for py_file in sorted(directory.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"{module_prefix}.{py_file.stem}"
        if module_name in sys.modules:
            continue
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                logger.debug("Loaded user module: %s", py_file.name)
        except Exception as e:
            logger.warning("Failed to load %s: %s", py_file, e)


def load_all_strategies() -> None:
    """Import all strategy modules from configured scan directories."""
    if getattr(sys, 'frozen', False):
        # PyInstaller frozen mode: load builtins + user strategies
        try:
            import ez.strategy.builtin.ma_cross  # noqa: F401
            import ez.strategy.builtin.momentum  # noqa: F401
            import ez.strategy.builtin.boll_reversion  # noqa: F401
        except ImportError as e:
            logger.warning("Failed to load frozen builtin strategy: %s", e)
        # Also load user strategies from strategies/ dir next to exe
        exe_dir = Path(os.environ.get("EZ_DATA_DIR", "")).parent if os.environ.get("EZ_DATA_DIR") else _PROJECT_ROOT
        _load_py_files(exe_dir / "strategies", "strategies")
        return

    config = load_config()
    for scan_dir in config.strategy.scan_dirs:
        # Resolve relative paths against project root, not CWD
        path = Path(scan_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path

        if not path.exists():
            logger.debug("Strategy scan dir does not exist: %s", path)
            continue

        module_base = scan_dir.replace("/", ".").replace("\\", ".")
        try:
            pkg = importlib.import_module(module_base)
            for _importer, modname, _ispkg in pkgutil.iter_modules(pkg.__path__):
                full_name = f"{module_base}.{modname}"
                try:
                    importlib.import_module(full_name)
                    logger.debug("Loaded strategy module: %s", full_name)
                except Exception as e:
                    logger.warning("Failed to load strategy module %s: %s", full_name, e)
        except ModuleNotFoundError:
            # Fallback: load .py files directly (for user strategies dir)
            for py_file in path.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                module_name = f"{module_base}.{py_file.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(module_name, str(py_file))
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = mod  # register before exec to prevent double-import
                        spec.loader.exec_module(mod)
                        logger.debug("Loaded user strategy: %s", py_file.name)
                except Exception as e:
                    logger.warning("Failed to load user strategy %s: %s", py_file, e)


def load_user_factors() -> None:
    """Import all factor modules from factors/ directory (user-created)."""
    # In frozen mode, user factors are next to exe
    if getattr(sys, "frozen", False) and os.environ.get("EZ_DATA_DIR"):
        factors_dir = Path(os.environ["EZ_DATA_DIR"]).parent / "factors"
    else:
        factors_dir = _PROJECT_ROOT / "factors"
    if not factors_dir.exists():
        return
    for py_file in sorted(factors_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"factors.{py_file.stem}"
        if module_name in sys.modules:
            continue
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                logger.debug("Loaded user factor: %s", py_file.name)
        except Exception as e:
            logger.warning("Failed to load user factor %s: %s", py_file, e)
