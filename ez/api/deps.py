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
_fundamental_store = None


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
    if name == "akshare":
        try:
            from ez.data.providers.akshare_provider import AKShareDataProvider
            return AKShareDataProvider()
        except ImportError:
            logger.warning("akshare package not installed, skipping")
            return None
    if name == "tencent":
        from ez.data.providers.tencent_provider import TencentDataProvider
        return TencentDataProvider()
    if name == "jqdata":
        if os.environ.get("JQDATA_USERNAME") and os.environ.get("JQDATA_PASSWORD"):
            try:
                from ez.data.providers.jqdata_provider import JQDataProvider
                return JQDataProvider()
            except ImportError:
                logger.debug("jqdatasdk not installed, skipping jqdata provider")
                return None
        return None
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

        # Fallback: ensure AKShare + Tencent are present as safety net
        if "akshare" not in seen:
            try:
                from ez.data.providers.akshare_provider import AKShareDataProvider
                providers.append(AKShareDataProvider())
                seen.add("akshare")
            except ImportError:
                pass
        if "tencent" not in seen:
            from ez.data.providers.tencent_provider import TencentDataProvider
            providers.append(TencentDataProvider())

        # JQData as lowest-priority fallback (cross-verification source)
        if "jqdata" not in seen:
            jq_provider = _build_provider("jqdata")
            if jq_provider:
                providers.append(jq_provider)
                seen.add("jqdata")

        logger.info("DataProviderChain built: %s", [p.name for p in providers])
        _chain = DataProviderChain(providers=providers, store=store)
    return _chain


def _close_chain_providers() -> None:
    """Close all providers in the current chain (tushare, fmp, tencent httpx clients)."""
    global _chain, _tushare_provider
    if _chain is not None:
        for p in _chain._providers:
            try:
                p.close()
            except Exception:
                pass
        _chain = None
    # Tushare singleton is also a chain provider, but clear our reference too
    _tushare_provider = None


def _rebuild_chain() -> None:
    """Force rebuild of the data provider chain (e.g., after Tushare token change)."""
    _close_chain_providers()
    get_chain()  # rebuild


def get_fundamental_store():
    """Lazy singleton for FundamentalStore. Shares DuckDB connection with DuckDBStore."""
    global _fundamental_store
    if _fundamental_store is None:
        from ez.data.fundamental import FundamentalStore
        _fundamental_store = FundamentalStore(get_store()._conn)
        logger.info("FundamentalStore singleton created")
    return _fundamental_store


def fetch_kline_df(symbol: str, market: str, period: str, start, end):
    """Shared single-stock kline fetch → DataFrame. Used by backtest + experiments."""
    import pandas as pd
    from fastapi import HTTPException
    chain = get_chain()
    bars = chain.get_kline(symbol, market, period, start, end)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")
    return pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")


def close_resources() -> None:
    global _store, _chain, _tushare_provider, _fundamental_store
    _fundamental_store = None
    _close_chain_providers()
    if _store is not None:
        _store.close()
        _store = None
    # Close shared ExperimentStore (V2.7.1: single source in data_access)
    from ez.agent.data_access import reset_data_access
    reset_data_access()
    # Close LLM provider's persistent connections (V2.7.1)
    from ez.llm.factory import reset_provider_cache
    reset_provider_cache()
    # Close PortfolioStore (V2.9)
    from ez.api.routes.portfolio import reset_portfolio_store
    reset_portfolio_store()
    # Close Live singletons (V2.15)
    from ez.api.routes.live import reset_live_singletons
    reset_live_singletons()
