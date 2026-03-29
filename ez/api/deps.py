"""Shared API dependencies — singleton store, provider chain, Tushare provider."""
from __future__ import annotations

import logging
import os

from ez.config import load_config
from ez.data.provider import DataProvider, DataProviderChain
from ez.data.store import DuckDBStore

logger = logging.getLogger(__name__)

_store: DuckDBStore | None = None
_chain: DataProviderChain | None = None
_tushare_provider = None


def get_store() -> DuckDBStore:
    global _store
    if _store is None:
        config = load_config()
        _store = DuckDBStore(config.database.path)
    return _store


def get_tushare_provider():
    global _tushare_provider
    if _tushare_provider is not None:
        return _tushare_provider
    if not os.environ.get("TUSHARE_TOKEN"):
        return None
    from ez.data.providers.tushare_provider import TushareDataProvider
    _tushare_provider = TushareDataProvider(store=get_store())
    logger.info("TushareDataProvider singleton created")
    return _tushare_provider


# Provider registry keyed by config name
def _build_provider(name: str) -> DataProvider | None:
    """Instantiate a provider by its config name. Returns None if unavailable."""
    if name == "tushare":
        return get_tushare_provider()
    if name == "fmp":
        if os.environ.get("FMP_API_KEY"):
            from ez.data.providers.fmp_provider import FMPDataProvider
            return FMPDataProvider()
        return None
    if name == "tencent":
        from ez.data.providers.tencent_provider import TencentDataProvider
        return TencentDataProvider()
    logger.warning("Unknown provider name in config: %s", name)
    return None


def get_chain() -> DataProviderChain:
    """Build provider chain from config data_sources (primary + backups per market).

    All unique providers across all markets are added in priority order.
    This ensures a single chain handles any market, with config-driven failover.
    """
    global _chain
    if _chain is None:
        store = get_store()
        config = load_config()

        # Collect providers in config priority order, deduplicate
        seen: set[str] = set()
        providers: list[DataProvider] = []

        for market_cfg in [config.data_sources.cn_stock, config.data_sources.us_stock,
                           config.data_sources.hk_stock]:
            for name in [market_cfg.primary] + market_cfg.backup:
                if name and name not in seen:
                    p = _build_provider(name)
                    if p:
                        providers.append(p)
                        seen.add(name)

        # Fallback: ensure at least Tencent is present
        if "tencent" not in seen:
            from ez.data.providers.tencent_provider import TencentDataProvider
            providers.append(TencentDataProvider())

        logger.info("DataProviderChain built: %s", [p.name for p in providers])
        _chain = DataProviderChain(providers=providers, store=store)
    return _chain


def _rebuild_chain() -> None:
    """Force rebuild of the data provider chain (e.g., after Tushare token change)."""
    global _chain, _tushare_provider
    _chain = None
    if _tushare_provider is not None:
        _tushare_provider.close()
        _tushare_provider = None
    get_chain()  # rebuild


def close_resources() -> None:
    global _store, _chain, _tushare_provider
    _chain = None
    if _tushare_provider is not None:
        _tushare_provider.close()
        _tushare_provider = None
    if _store is not None:
        _store.close()
        _store = None
    # Close shared ExperimentStore (V2.7.1: single source in data_access)
    from ez.agent.data_access import reset_data_access
    reset_data_access()
    # Close LLM provider's persistent connections (V2.7.1)
    from ez.llm.factory import reset_provider_cache
    reset_provider_cache()
