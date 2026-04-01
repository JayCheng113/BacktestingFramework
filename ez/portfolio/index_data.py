"""V2.12.1 F5: Index constituent + weight data provider.

Fetches CSI300/500/1000 constituents via AKShare (free).
24-hour in-memory cache to avoid repeated API calls.
Falls back to equal weight if actual weights unavailable.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class IndexDataProvider:
    """Index constituent + weight provider with AKShare fallback chain."""

    SUPPORTED_INDICES = {
        "000300": "沪深300",
        "000905": "中证500",
        "000852": "中证1000",
    }

    _cache: dict[str, tuple[float, Any]] = {}
    _CACHE_TTL = 86400  # 24 hours

    def get_constituents(self, index_code: str) -> list[str]:
        """Return constituent symbol list for an index. Cached 24h."""
        key = f"cons_{index_code}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        syms = self._fetch_constituents(index_code)
        if syms:
            self._set_cached(key, syms)
        return syms

    def get_weights(self, index_code: str) -> dict[str, float]:
        """Return {symbol: weight}. Equal weight if actual weights unavailable."""
        key = f"weights_{index_code}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        constituents = self.get_constituents(index_code)
        if not constituents:
            return {}
        weights = self._build_weights(constituents)
        self._set_cached(key, weights)
        return weights

    def _fetch_constituents(self, index_code: str) -> list[str]:
        """Try multiple AKShare APIs with fallback."""
        try:
            import akshare as ak
        except ImportError:
            logger.warning("akshare not installed, cannot fetch index constituents")
            return []

        attempts = [
            ("index_stock_cons_csindex_p", {"symbol": index_code}),
            ("index_stock_cons", {"symbol": index_code}),
        ]
        for func_name, kwargs in attempts:
            try:
                func = getattr(ak, func_name, None)
                if func is None:
                    continue
                df = func(**kwargs)
                if df is None or df.empty:
                    continue
                # Try common column names for stock codes
                for col in ["成分券代码", "品种代码", "stock_code", "证券代码"]:
                    if col in df.columns:
                        codes = df[col].astype(str).tolist()
                        result = [self._normalize_code(c) for c in codes if c.strip()]
                        if result:
                            logger.info("Fetched %d constituents for %s via %s",
                                        len(result), index_code, func_name)
                            return result
                # Fallback: use first column
                codes = df.iloc[:, 0].astype(str).tolist()
                result = [self._normalize_code(c) for c in codes if c.strip() and c.strip() != "nan"]
                if result:
                    return result
            except Exception as e:
                logger.debug("AKShare %s failed for %s: %s", func_name, index_code, e)

        logger.warning("All AKShare APIs failed for index %s constituents", index_code)
        return []

    def _build_weights(self, constituents: list[str]) -> dict[str, float]:
        """Equal weight (AKShare free tier rarely has actual weights)."""
        n = len(constituents)
        return {s: 1.0 / n for s in constituents} if n > 0 else {}

    @staticmethod
    def _normalize_code(code: str) -> str:
        """Convert bare code to standard format: 600519 → 600519.SH."""
        code = code.strip()
        if "." in code:
            return code  # already has suffix
        bare = code.split(".")[0]
        if bare.startswith("6") or bare.startswith("9"):
            return f"{bare}.SH"
        return f"{bare}.SZ"

    def _get_cached(self, key: str):
        entry = self._cache.get(key)
        if entry and time.monotonic() - entry[0] < self._CACHE_TTL:
            return entry[1]
        return None

    def _set_cached(self, key: str, value):
        self._cache[key] = (time.monotonic(), value)
