"""Shared API dependencies — singleton store and provider chain."""
from __future__ import annotations

from ez.config import load_config
from ez.data.provider import DataProviderChain
from ez.data.providers.tencent_provider import TencentDataProvider
from ez.data.store import DuckDBStore

_store: DuckDBStore | None = None
_chain: DataProviderChain | None = None


def get_store() -> DuckDBStore:
    """Return module-level singleton DuckDBStore."""
    global _store
    if _store is None:
        config = load_config()
        _store = DuckDBStore(config.database.path)
    return _store


def get_chain() -> DataProviderChain:
    """Return singleton DataProviderChain (reuses store and providers)."""
    global _chain
    if _chain is None:
        store = get_store()
        providers = [TencentDataProvider()]
        _chain = DataProviderChain(providers=providers, store=store)
    return _chain


def close_store() -> None:
    """Close the singleton store (called at app shutdown)."""
    global _store, _chain
    _chain = None
    if _store is not None:
        _store.close()
        _store = None
