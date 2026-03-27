"""Data provider and store abstract base classes.

[CORE] — interface signatures frozen after V1.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date

from ez.errors import ProviderError
from ez.types import Bar

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependency (validator imports Bar from types)
_DataValidator = None


def _get_validator():
    global _DataValidator
    if _DataValidator is None:
        from ez.data.validator import DataValidator
        _DataValidator = DataValidator
    return _DataValidator


class DataProvider(ABC):
    """Abstract data source. All providers (Tushare, Tencent, FMP) implement this."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]: ...

    @abstractmethod
    def search_symbols(self, keyword: str, market: str = "") -> list[dict]: ...


class DataStore(ABC):
    """Abstract data storage. V1 = DuckDB, V2 may switch to ArcticDB."""

    @abstractmethod
    def query_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]: ...

    @abstractmethod
    def save_kline(self, bars: list[Bar], period: str) -> int: ...

    @abstractmethod
    def has_data(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> bool: ...


class DataProviderChain:
    """Try providers in priority order with failover."""

    def __init__(self, providers: list[DataProvider], store: DataStore):
        self._providers = providers
        self._store = store

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        # 1. Check cache — only use if it covers the requested range
        cached = self._store.query_kline(symbol, market, period, start_date, end_date)
        if cached:
            cached_start = cached[0].time.date()
            cached_end = cached[-1].time.date()
            # Allow 3-day tolerance for weekends/holidays at range boundaries
            start_covered = (cached_start - start_date).days <= 3
            end_covered = (end_date - cached_end).days <= 3
            if start_covered and end_covered:
                logger.info("Cache hit for %s/%s/%s", symbol, market, period)
                return cached
            logger.info("Cache partial for %s (have %s~%s, need %s~%s), fetching fresh",
                        symbol, cached_start, cached_end, start_date, end_date)

        # 2. Try providers in order
        last_error: Exception | None = None
        for provider in self._providers:
            try:
                logger.info("Fetching %s from %s", symbol, provider.name)
                bars = provider.get_kline(symbol, market, period, start_date, end_date)
                if bars:
                    validator = _get_validator()
                    result = validator.validate_bars(bars)
                    if result.invalid_count > 0:
                        logger.warning("%d invalid bars filtered for %s: %s",
                                       result.invalid_count, symbol, result.errors[:3])
                    if result.valid_bars:
                        self._store.save_kline(result.valid_bars, period)
                    return result.valid_bars
            except Exception as e:
                logger.warning("Provider %s failed for %s: %s", provider.name, symbol, e)
                last_error = e
                continue

        # 3. All failed
        if last_error:
            raise ProviderError(f"All providers failed for {symbol}: {last_error}") from last_error
        return []

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        for provider in self._providers:
            try:
                return provider.search_symbols(keyword, market)
            except Exception:
                continue
        return []
