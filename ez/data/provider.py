"""Data provider and store abstract base classes.

[CORE] — interface signatures frozen after V1.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import date

from datetime import datetime as _dt

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

    def query_kline_batch(
        self, symbols: list[str], market: str, period: str,
        start_date: date, end_date: date,
    ) -> dict[str, list[Bar]]:
        """Batch query. Default: falls back to N individual queries."""
        result = {}
        for sym in symbols:
            bars = self.query_kline(sym, market, period, start_date, end_date)
            result[sym] = bars
        return result


class DataProviderChain:
    """Try providers in priority order with failover."""

    # Session-scoped memoization of symbols known to legitimately return fewer
    # bars than the density threshold would expect (new listings, niche ETFs,
    # thinly-traded instruments). Populated on provider refetch when the bar
    # count is < 75% of expected — prevents the cache-check from forcing an
    # infinite refetch loop on every subsequent call for the same symbol.
    # Key: (symbol, market, period) — range-independent so a 1-month query
    # and 6-month query share the same "known sparse" flag.
    _known_sparse_symbols: dict[tuple[str, str, str], float] = {}
    _SPARSE_TTL_SECONDS: float = 86400.0

    def __init__(self, providers: list[DataProvider], store: DataStore):
        self._providers = providers
        self._store = store

    @staticmethod
    def _is_cache_complete(
        cached: list[Bar],
        start_date: date,
        end_date: date,
        *,
        skip_density: bool = False,
    ) -> tuple[bool, str]:
        """Check if cached bars cover the requested range WITHOUT gaps.

        Two-stage check (both must pass):
        1. Boundary: first/last bar within 3-day tolerance of requested range
        2. Density: bar count >= 75% of expected trading days (catches middle gaps)

        Args:
            skip_density: when True, only boundary is checked. Used for symbols
                known to legitimately return sparse data (see
                `_known_sparse_symbols`) to avoid infinite refetch loops.

        Returns (complete, reason). `reason` explains why cache is considered
        incomplete (for logging).
        """
        if not cached:
            return False, "empty"
        cs, ce = cached[0].time.date(), cached[-1].time.date()
        # Boundary check
        if (cs - start_date).days > 3:
            return False, f"start gap: have {cs}, need {start_date}"
        if (end_date - ce).days > 3:
            return False, f"end gap: have {ce}, need {end_date}"
        # Density check — only for ranges long enough to be reliable
        req_days = (end_date - start_date).days
        if req_days <= 14:
            return True, "boundary ok (short range, skip density check)"
        if skip_density:
            return True, "boundary ok (known-sparse symbol, skip density check)"
        # A-share: ~245 trading days/year. 0.75 tolerance covers long holidays
        # (Spring Festival week + October week + minor breaks ≈ 15 days).
        expected_bars = max(1, int(req_days * 245 / 365))
        actual_bars = len(cached)
        if actual_bars < expected_bars * 0.75:
            return False, f"middle gap: {actual_bars} bars, expected ~{expected_bars} (<75%)"
        return True, "ok"

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        # 1. Check cache — only use if it covers the requested range WITHOUT gaps.
        # Skip density check for symbols already known to be sparse (e.g. niche
        # ETFs, new listings) to avoid refetch loops on legitimately thin data.
        sparse_key = (symbol, market, period)
        _ts = self._known_sparse_symbols.get(sparse_key)
        if _ts is not None and (time.monotonic() - _ts) >= self._SPARSE_TTL_SECONDS:
            del self._known_sparse_symbols[sparse_key]
            _ts = None
        skip_density = _ts is not None
        cached = self._store.query_kline(symbol, market, period, start_date, end_date)
        if cached:
            complete, reason = self._is_cache_complete(
                cached, start_date, end_date, skip_density=skip_density,
            )
            if complete:
                logger.info("Cache hit for %s/%s/%s (%s)", symbol, market, period, reason)
                return cached
            logger.info("Cache incomplete for %s (%s), fetching fresh", symbol, reason)

        # 2. Try providers in order
        last_error: Exception | None = None
        for provider in self._providers:
            try:
                logger.info("Fetching %s from %s", symbol, provider.name)
                bars = provider.get_kline(symbol, market, period, start_date, end_date)
                if bars:
                    # Filter out future dates (some APIs return fake future data)
                    today = _dt.now().date()
                    bars = [b for b in bars if b.time.date() <= today]
                    if not bars:
                        continue

                    validator = _get_validator()
                    result = validator.validate_bars(bars)
                    if result.invalid_count > 0:
                        logger.warning("%d invalid bars filtered for %s: %s",
                                       result.invalid_count, symbol, result.errors[:3])
                    if result.valid_bars:
                        self._store.save_kline(result.valid_bars, period)
                        # Check coverage by BAR COUNT (not just date span).
                        # ~245 trading days/year. If provider returns <50% of expected bars
                        # AND there are more providers, try next for better coverage.
                        vb = result.valid_bars
                        req_days = (end_date - start_date).days
                        expected_bars = max(1, int(req_days * 245 / 365))
                        actual_bars = len(vb)
                        is_last = (provider is self._providers[-1])
                        if req_days > 30 and actual_bars < expected_bars * 0.5 and not is_last:
                            logger.info("Provider %s returned partial data for %s (%d/%d bars), trying next",
                                        provider.name, symbol, actual_bars, expected_bars)
                            continue
                        # Mark as known-sparse if legitimately below density threshold:
                        # the (last) provider confirmed this is all the data available,
                        # so future cache checks should skip density to avoid refetch loops.
                        if req_days > 30 and actual_bars < expected_bars * 0.75:
                            self._known_sparse_symbols[sparse_key] = time.monotonic()
                            logger.info("Marked %s as known-sparse (%d/%d bars)",
                                        symbol, actual_bars, expected_bars)
                        return result.valid_bars
                    else:
                        logger.warning("All %d bars invalid from %s for %s, trying next provider",
                                       result.invalid_count, provider.name, symbol)
                        continue
            except Exception as e:
                logger.warning("Provider %s failed for %s: %s", provider.name, symbol, e)
                last_error = e
                continue

        # 3. All failed
        if last_error:
            raise ProviderError(f"All providers failed for {symbol}: {last_error}") from last_error
        return []

    def get_kline_batch(
        self, symbols: list[str], market: str, period: str,
        start_date: date, end_date: date,
    ) -> dict[str, list[Bar]]:
        """Batch: single DB query for cached, individual fetch for missing.

        Uses the same two-stage completeness check as get_kline() — boundary +
        density — to catch middle gaps in cached data.
        """
        cached = self._store.query_kline_batch(symbols, market, period, start_date, end_date)
        result: dict[str, list[Bar]] = {}
        missing: list[str] = []
        for sym in symbols:
            bars = cached.get(sym, [])
            _batch_key = (sym, market, period)
            _batch_ts = self._known_sparse_symbols.get(_batch_key)
            if _batch_ts is not None and (time.monotonic() - _batch_ts) >= self._SPARSE_TTL_SECONDS:
                del self._known_sparse_symbols[_batch_key]
                _batch_ts = None
            skip_density = _batch_ts is not None
            complete, reason = self._is_cache_complete(
                bars, start_date, end_date, skip_density=skip_density,
            ) if bars else (False, "empty")
            if complete:
                result[sym] = bars
                continue
            if bars:
                logger.info("Batch cache incomplete for %s (%s), refetching", sym, reason)
            missing.append(sym)
        for sym in missing:
            try:
                bars = self.get_kline(sym, market, period, start_date, end_date)
                if bars:
                    result[sym] = bars
            except Exception as e:
                logger.warning("Batch fetch failed for %s: %s", sym, e)
        return result

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        for provider in self._providers:
            try:
                results = provider.search_symbols(keyword, market)
                if results:  # Only return if non-empty
                    return results
            except Exception:
                continue
        return []
