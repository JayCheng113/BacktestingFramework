"""Strategy auto-discovery from configured directories.

[CORE] — scans paths from config, does not hardcode directories.
Resolves relative paths against the project root (where pyproject.toml lives).
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from ez.config import load_config

logger = logging.getLogger(__name__)

# Project root = parent of ez/ package
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def load_all_strategies() -> None:
    """Import all strategy modules from configured scan directories."""
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
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        logger.debug("Loaded user strategy: %s", py_file.name)
                except Exception as e:
                    logger.warning("Failed to load user strategy %s: %s", py_file, e)
