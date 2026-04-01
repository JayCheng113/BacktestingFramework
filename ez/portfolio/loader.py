"""V2.9: Portfolio strategy loader — scans portfolio_strategies/ at startup.

Mirrors ez/strategy/loader.py: imports all .py files from portfolio_strategies/,
triggering PortfolioStrategy.__init_subclass__ auto-registration.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from ez.config import get_project_root
_PROJECT_ROOT = get_project_root()


def _user_root() -> Path:
    """In frozen mode, user dirs are next to exe (not inside _MEIPASS)."""
    if getattr(sys, "frozen", False) and os.environ.get("EZ_DATA_DIR"):
        return Path(os.environ["EZ_DATA_DIR"]).parent
    return _PROJECT_ROOT


def load_portfolio_strategies() -> None:
    """Import all portfolio strategy modules from portfolio_strategies/."""
    _scan_dir(_user_root() / "portfolio_strategies", "portfolio_strategies")


def load_cross_factors() -> None:
    """Import all cross factor modules from cross_factors/."""
    _scan_dir(_user_root() / "cross_factors", "cross_factors")


def _scan_dir(path: Path, module_base: str) -> None:
    if not path.exists():
        return
    for py_file in sorted(path.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"{module_base}.{py_file.stem}"
        if module_name in sys.modules:
            continue  # already loaded
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                logger.debug("Loaded portfolio module: %s", py_file.name)
        except Exception as e:
            logger.warning("Failed to load portfolio module %s: %s", py_file, e)
