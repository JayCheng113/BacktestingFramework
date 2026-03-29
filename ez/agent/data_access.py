"""Agent-layer data access singletons.

Provides get_chain() and get_experiment_store() for agent tools
without importing from ez/api/ (which would violate layer dependencies).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ez.agent.experiment_store import ExperimentStore
from ez.config import load_config
from ez.data.provider import DataProvider, DataProviderChain
from ez.data.store import DuckDBStore

logger = logging.getLogger(__name__)

_store: DuckDBStore | None = None
_chain: DataProviderChain | None = None
_exp_store: ExperimentStore | None = None


def _get_store() -> DuckDBStore:
    global _store
    if _store is None:
        config = load_config()
        _store = DuckDBStore(config.database.path)
    return _store


def _build_provider(name: str, store: DuckDBStore) -> DataProvider | None:
    if name == "tushare":
        if not os.environ.get("TUSHARE_TOKEN"):
            return None
        from ez.data.providers.tushare_provider import TushareDataProvider
        return TushareDataProvider(store=store)
    if name == "fmp":
        if os.environ.get("FMP_API_KEY"):
            from ez.data.providers.fmp_provider import FMPDataProvider
            return FMPDataProvider()
        return None
    if name == "tencent":
        from ez.data.providers.tencent_provider import TencentDataProvider
        return TencentDataProvider()
    return None


def get_chain() -> DataProviderChain:
    """Build data provider chain from config."""
    global _chain
    if _chain is None:
        store = _get_store()
        config = load_config()
        seen: set[str] = set()
        providers: list[DataProvider] = []
        for market_cfg in [config.data_sources.cn_stock, config.data_sources.us_stock,
                           config.data_sources.hk_stock]:
            for name in [market_cfg.primary] + market_cfg.backup:
                if name and name not in seen:
                    p = _build_provider(name, store)
                    if p:
                        providers.append(p)
                        seen.add(name)
        if "tencent" not in seen:
            from ez.data.providers.tencent_provider import TencentDataProvider
            providers.append(TencentDataProvider())
        _chain = DataProviderChain(providers=providers, store=store)
    return _chain


def get_experiment_store() -> ExperimentStore:
    """Get or create ExperimentStore."""
    global _exp_store
    if _exp_store is None:
        import duckdb
        config = load_config()
        p = Path(config.database.path)
        if not p.is_absolute():
            project_root = Path(__file__).resolve().parent.parent.parent
            p = project_root / p
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(p))
        _exp_store = ExperimentStore(conn)
    return _exp_store


def reset_data_access() -> None:
    """Reset cached singletons (for testing)."""
    global _store, _chain, _exp_store
    _chain = None
    _exp_store = None
    if _store is not None:
        _store.close()
        _store = None
