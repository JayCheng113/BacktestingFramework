"""Deterministic mock data fixtures for guard tests.

All randomness is from `np.random.default_rng(seed)` — no global state.
Data is cached at module-import time to avoid rebuild on each guard call.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime
from functools import lru_cache

MOCK_SEED = 42
SHUFFLE_SEED = 7
MOCK_N_DAYS = 200
MOCK_START_DATE = "2024-01-01"
MOCK_SYMBOLS = ("T001", "T002", "T003", "T004", "T005")


@lru_cache(maxsize=1)
def _mock_date_index() -> pd.DatetimeIndex:
    return pd.date_range(MOCK_START_DATE, periods=MOCK_N_DAYS, freq="B")


@lru_cache(maxsize=1)
def _cached_mock_seed() -> dict[str, pd.DataFrame]:
    """Internal: build the seed panel once per process.

    This is the authoritative source. `build_mock_panel()` wraps it to hand
    out defensive copies so that user code inside a guard can NEVER pollute
    the cached seed (which would cause cross-test bleed and silently defeat
    the lookahead test — see C1 in V2.19.0 post-review).
    """
    rng = np.random.default_rng(MOCK_SEED)
    dates = _mock_date_index()
    panel: dict[str, pd.DataFrame] = {}
    for sym in MOCK_SYMBOLS:
        r = rng.normal(0.0005, 0.015, MOCK_N_DAYS)
        price = 100 * np.cumprod(1 + r)
        high = price * (1 + np.abs(rng.normal(0, 0.005, MOCK_N_DAYS)))
        low = price * (1 - np.abs(rng.normal(0, 0.005, MOCK_N_DAYS)))
        open_ = price * (1 + rng.normal(0, 0.003, MOCK_N_DAYS))
        volume = rng.integers(100_000, 1_000_000, MOCK_N_DAYS).astype(float)
        panel[sym] = pd.DataFrame({
            "open": open_,
            "high": high,
            "low": low,
            "close": price,
            "adj_close": price,
            "volume": volume,
        }, index=dates)
    return panel


def build_mock_panel() -> dict[str, pd.DataFrame]:
    """Return a FRESH dict[symbol → DataFrame] with OHLCV + adj_close.

    200 B-day bars × 5 symbols, deterministic GBM from seed 42. A new dict
    containing fresh ``.copy()`` DataFrames is returned on every call —
    callers may mutate the result safely without polluting subsequent
    guard runs. Cost: ~1-2 ms per call (acceptable vs the 500 ms budget).
    """
    seed = _cached_mock_seed()
    return {sym: df.copy() for sym, df in seed.items()}


def build_shuffled_panel(cutoff_idx: int) -> dict[str, pd.DataFrame]:
    """Return a FRESH dict with rows strictly after cutoff_idx shuffled.

    Row `cutoff_idx` itself stays in place. Rows `[cutoff_idx + 1, N)` are
    permuted by values (the DatetimeIndex is preserved — only values move).
    Each call returns fresh DataFrames — never cached — so that downstream
    mutation cannot leak across guard invocations.
    """
    rng = np.random.default_rng(SHUFFLE_SEED)
    base = build_mock_panel()  # already fresh copies
    shuffled: dict[str, pd.DataFrame] = {}
    for sym, df in base.items():
        head = df.iloc[: cutoff_idx + 1].copy()
        tail = df.iloc[cutoff_idx + 1:].copy()
        if len(tail) > 0:
            perm = rng.permutation(len(tail))
            tail_vals = tail.values[perm]
            tail = pd.DataFrame(tail_vals, index=tail.index, columns=tail.columns)
        shuffled[sym] = pd.concat([head, tail])
    return shuffled


def target_date_at(idx: int) -> datetime:
    """Return the date at position `idx` in the mock panel."""
    return _mock_date_index()[idx].to_pydatetime()
