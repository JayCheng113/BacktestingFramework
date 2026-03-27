"""Shared API dependencies — singleton store, provider chain, Tushare provider."""
from __future__ import annotations

import logging
import os

from ez.config import load_config
from ez.data.provider import DataProvider, DataProviderChain
from ez.data.providers.tencent_provider import TencentDataProvider
from ez.data.store import DuckDBStore

logger = logging.getLogger(__name__)

_store: DuckDBStore | None = None
_chain: DataProviderChain | None = None
_tushare_provider = None  # TushareDataProvider singleton (shared by chain + routes)


def get_store() -> DuckDBStore:
    """Return module-level singleton DuckDBStore."""
    global _store
    if _store is None:
        config = load_config()
        _store = DuckDBStore(config.database.path)
    return _store


def get_tushare_provider():
    """Return singleton TushareDataProvider if token is configured, else None."""
    global _tushare_provider
    if _tushare_provider is not None:
        return _tushare_provider
    if not os.environ.get("TUSHARE_TOKEN"):
        return None
    from ez.data.providers.tushare_provider import TushareDataProvider
    _tushare_provider = TushareDataProvider(store=get_store())
    logger.info("TushareDataProvider singleton created (TUSHARE_TOKEN found)")
    return _tushare_provider


def get_chain() -> DataProviderChain:
    """Return singleton DataProviderChain (reuses store and providers)."""
    global _chain
    if _chain is None:
        store = get_store()
        providers: list[DataProvider] = []

        tushare = get_tushare_provider()
        if tushare:
            providers.append(tushare)

        providers.append(TencentDataProvider())
        _chain = DataProviderChain(providers=providers, store=store)
    return _chain


def close_resources() -> None:
    """Close all singleton resources (called at app shutdown)."""
    global _store, _chain, _tushare_provider
    _chain = None
    if _tushare_provider is not None:
        _tushare_provider.close()
        _tushare_provider = None
    if _store is not None:
        _store.close()
        _store = None
