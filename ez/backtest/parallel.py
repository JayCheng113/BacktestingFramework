"""Parallel batch backtest execution.

Uses ProcessPoolExecutor so each worker has its own GIL + DuckDB connection.
C++ simulate_loop releases GIL, but factor/signal generation still holds it,
so process-level parallelism is the right choice.
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from typing import Any

import pandas as pd


def _run_chunk(args: tuple) -> list[dict[str, Any]]:
    """Worker: run a batch of symbols in one process (amortize spawn cost)."""
    symbols_chunk, market, period, start, end, db_path, strategy_cls, strategy_params, engine_kwargs, skip_sig = args
    from ez.data.store import DuckDBStore
    from ez.backtest.engine import VectorizedBacktestEngine

    store = DuckDBStore(db_path, read_only=True)
    engine = VectorizedBacktestEngine(**engine_kwargs)
    strategy = strategy_cls(**strategy_params)
    results = []

    for symbol in symbols_chunk:
        try:
            if hasattr(store, "query_kline_df"):
                df = store.query_kline_df(symbol, market, period, start, end)
                if df is None or df.empty:
                    results.append({"symbol": symbol, "status": "no_data"})
                    continue
                df = df.set_index("time")
            else:
                bars = store.query_kline(symbol, market, period, start, end)
                if not bars:
                    results.append({"symbol": symbol, "status": "no_data"})
                    continue
                rows = [{"date": b.time, "open": b.open, "high": b.high, "low": b.low,
                         "close": b.close, "adj_close": b.adj_close, "volume": b.volume}
                        for b in bars]
                df = pd.DataFrame(rows).set_index("date")

            if len(df) < 30:
                results.append({"symbol": symbol, "status": "insufficient_data", "bars": len(df)})
                continue

            result = engine.run(df, strategy, skip_significance=skip_sig)
            metrics = result.metrics if hasattr(result, "metrics") else result
            results.append({
                "symbol": symbol, "status": "ok", "bars": len(df),
                "metrics": dict(metrics) if hasattr(metrics, "items") else {},
            })
        except Exception as e:
            results.append({"symbol": symbol, "status": "error", "error": str(e)})

    return results


def parallel_backtest(
    symbols: list[str],
    strategy_cls: type,
    strategy_params: dict | None = None,
    *,
    market: str = "cn_stock",
    period: str = "daily",
    start_date: date | str = "2020-01-01",
    end_date: date | str = "2025-12-31",
    db_path: str = "data/ez_trading.db",
    engine_kwargs: dict | None = None,
    skip_significance: bool = True,
    n_workers: int | None = None,
) -> list[dict[str, Any]]:
    """Run backtests for multiple symbols in parallel using ProcessPoolExecutor.

    Args:
        symbols: list of symbol codes (e.g. ["600000.SH", "000001.SZ"])
        strategy_cls: Strategy subclass (must be picklable — defined at module level)
        strategy_params: kwargs for strategy_cls()
        n_workers: number of parallel workers (default: min(cpu_count, len(symbols)))

    Returns:
        List of result dicts, one per symbol, with keys: symbol, status, bars, metrics.
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)
    if n_workers is None:
        n_workers = min(os.cpu_count() or 4, len(symbols), 8)
    if strategy_params is None:
        strategy_params = {}
    if engine_kwargs is None:
        engine_kwargs = {"commission_rate": 0.0003}

    chunk_size = max(1, len(symbols) // n_workers)
    chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]

    args_list = [
        (chunk, market, period, start_date, end_date, db_path,
         strategy_cls, strategy_params, engine_kwargs, skip_significance)
        for chunk in chunks
    ]

    results = []
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_run_chunk, a): i for i, a in enumerate(args_list)}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as e:
                idx = futures[future]
                for sym in chunks[idx]:
                    results.append({"symbol": sym, "status": "error", "error": str(e)})

    results.sort(key=lambda r: r["symbol"])
    return results
