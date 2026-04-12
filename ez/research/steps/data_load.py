"""DataLoadStep: fetch market data via the data provider chain.

Reads:
  - context.config['symbols']: list[str] of symbols to fetch
  - context.config['start_date']: str/date — inclusive
  - context.config['end_date']: str/date — inclusive
  - context.config['market'] (default 'cn_stock')
  - context.config['period'] (default 'daily')
  OR explicit constructor args (override config).

Writes:
  - artifacts['universe_data']: dict[symbol → DataFrame] with OHLCV+adj_close

The DataFrame layout matches ``ez.api.deps.fetch_kline_df``:
  - index: pd.DatetimeIndex (trade dates)
  - columns: open, high, low, close, adj_close, volume
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Iterable

import pandas as pd

from ..pipeline import ResearchStep
from ..context import PipelineContext


class DataLoadStep(ResearchStep):
    name = "data_load"
    writes = ("universe_data",)

    def __init__(
        self,
        symbols: Iterable[str] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        market: str | None = None,
        period: str | None = None,
    ):
        # Codex round-3 P2-4 + round-4 P2-A: reject single string OR
        # bytes/bytearray symbols. `list("AAA")` → ['A','A','A'] and
        # `list(b"AAA")` → [65, 65, 65]. Both are silent footguns.
        # Force the user to wrap in a list explicitly.
        if isinstance(symbols, (str, bytes, bytearray)):
            raise TypeError(
                f"DataLoadStep symbols must be a list of str, not a single "
                f"{type(symbols).__name__}. Did you mean [{symbols!r}]?"
            )
        self.symbols = list(symbols) if symbols is not None else None
        self.start_date = start_date
        self.end_date = end_date
        # Codex round-3 P2-3: use None as the "not explicitly set" sentinel.
        # The previous defaults `market="cn_stock"` and `period="daily"`
        # collided with users who explicitly want to pin those values
        # against a context.config that holds something else — the explicit
        # constructor arg was silently overridden because the value matched
        # the default.
        self.market = market
        self.period = period

    def _resolve(self, context: PipelineContext) -> tuple[list[str], date, date, str, str]:
        """Resolve constructor args + context.config defaults.

        Constructor args win when explicitly set (i.e. not None) — falls
        back to context.config when the constructor arg is None.
        Codex round-4 P2-B: ALL four parameters (symbols/start/end/market/
        period) now use the same `is not None` sentinel pattern, so an
        explicit empty string or other falsy value is preserved instead
        of being silently replaced.
        """
        symbols = self.symbols if self.symbols is not None else context.config.get("symbols")
        if not symbols:
            raise ValueError(
                "DataLoadStep requires symbols (constructor arg or context.config['symbols'])"
            )
        # Reject string / bytes symbols from context.config too — same
        # pitfall as the constructor check above.
        if isinstance(symbols, (str, bytes, bytearray)):
            raise TypeError(
                f"DataLoadStep symbols must be a list of str, not a single "
                f"{type(symbols).__name__}. context.config['symbols'] = {symbols!r}"
            )
        start = self.start_date if self.start_date is not None else context.config.get("start_date")
        end = self.end_date if self.end_date is not None else context.config.get("end_date")
        if start is None or end is None:
            raise ValueError("DataLoadStep requires start_date and end_date")
        market = self.market if self.market is not None else context.config.get("market", "cn_stock")
        period = self.period if self.period is not None else context.config.get("period", "daily")
        return list(symbols), self._to_date(start), self._to_date(end), market, period

    @staticmethod
    def _to_date(value) -> date:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        raise TypeError(f"Cannot coerce {value!r} to date")

    def _fetch_one(self, symbol: str, market: str, period: str, start: date, end: date) -> pd.DataFrame:
        """Fetch a single symbol via the shared data chain.

        Lazy import keeps ez.research importable in test environments
        without a populated data store.
        """
        from ez.api.deps import fetch_kline_df
        return fetch_kline_df(symbol, market, period, start, end)

    def run(self, context: PipelineContext) -> PipelineContext:
        # Codex round-3 P2-5: clear ANY stale skipped artifact from a
        # previous run BEFORE doing work. Otherwise a clean rerun would
        # leave the previous run's warning in the context, and ReportStep
        # would render it as if it belonged to the current run.
        context.artifacts.pop("data_load_skipped", None)

        symbols, start, end, market, period = self._resolve(context)
        universe_data: dict[str, pd.DataFrame] = {}
        skipped: list[tuple[str, str]] = []
        for sym in symbols:
            try:
                df = self._fetch_one(sym, market, period, start, end)
            except Exception as e:
                skipped.append((sym, f"{type(e).__name__}: {e}"))
                continue
            if df is None or len(df) == 0:
                skipped.append((sym, "empty dataframe"))
                continue
            universe_data[sym] = df

        if not universe_data:
            raise RuntimeError(
                f"DataLoadStep: no symbols loaded successfully. "
                f"Requested: {symbols}, skipped: {skipped}"
            )

        context.artifacts["universe_data"] = universe_data
        if skipped:
            context.artifacts["data_load_skipped"] = skipped
        return context
