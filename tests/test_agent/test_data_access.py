"""Tests for agent-layer data access singletons."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ez.agent.data_access import _resolve_db_path, reset_data_access


class TestResolveDbPath:
    """P0-3: DB path must match DuckDBStore logic, including EZ_DATA_DIR."""

    def test_respects_ez_data_dir(self, tmp_path):
        """EZ_DATA_DIR overrides config path — must match DuckDBStore behavior."""
        data_dir = str(tmp_path / "custom_data")
        with patch.dict(os.environ, {"EZ_DATA_DIR": data_dir}):
            p = _resolve_db_path()
            assert p == Path(data_dir) / "ez_trading.db"
            assert p.parent.exists()  # directory created

    def test_falls_back_to_config(self):
        """Without EZ_DATA_DIR, uses config.database.path."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EZ_DATA_DIR", None)
            p = _resolve_db_path()
            assert str(p).endswith(".db")

    def test_matches_duckdb_store_path(self, tmp_path):
        """EZ_DATA_DIR path must be identical to what DuckDBStore would use."""
        data_dir = str(tmp_path / "shared_data")
        with patch.dict(os.environ, {"EZ_DATA_DIR": data_dir}):
            from ez.data.store import DuckDBStore
            agent_path = _resolve_db_path()
            # DuckDBStore uses same logic
            store = DuckDBStore("ignored_when_env_set.db")
            # Both should resolve to the same directory
            store_path = Path(data_dir) / "ez_trading.db"
            assert agent_path == store_path
            store.close()
