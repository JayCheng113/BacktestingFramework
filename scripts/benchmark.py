#!/usr/bin/env python3
"""Performance benchmark — Python baseline for V2.1 C++ comparison.

Usage: python scripts/benchmark.py [--bars N]
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from ez.core import ts_ops
from ez.core.matcher import SimpleMatcher
from ez.backtest.engine import VectorizedBacktestEngine
from ez.strategy.builtin.ma_cross import MACrossStrategy


def generate_data(n_bars: int) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0005, 0.02, n_bars)
    prices = 100 * np.cumprod(1 + returns)
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="B")
    return pd.DataFrame({
        "open": prices * (1 + rng.normal(0, 0.001, n_bars)),
        "high": prices * (1 + abs(rng.normal(0, 0.005, n_bars))),
        "low": prices * (1 - abs(rng.normal(0, 0.005, n_bars))),
        "close": prices,
        "adj_close": prices,
        "volume": rng.integers(100000, 10000000, n_bars),
    }, index=dates)


def bench_ts_ops(data: pd.DataFrame, n_runs: int = 50) -> dict[str, float]:
    """Benchmark time series operations."""
    s = data["adj_close"]
    results = {}

    for name, fn in [
        ("rolling_mean_20", lambda: ts_ops.rolling_mean(s, 20)),
        ("rolling_std_20", lambda: ts_ops.rolling_std(s, 20)),
        ("ewm_mean_12", lambda: ts_ops.ewm_mean(s, 12)),
        ("diff", lambda: ts_ops.diff(s)),
        ("pct_change", lambda: ts_ops.pct_change(s)),
    ]:
        times = []
        for _ in range(n_runs):
            t0 = time.perf_counter()
            fn()
            times.append(time.perf_counter() - t0)
        results[name] = np.median(times) * 1000  # ms

    return results


def bench_matcher(n_fills: int = 100000) -> dict[str, float]:
    """Benchmark matcher fill operations."""
    matcher = SimpleMatcher(commission_rate=0.0003, min_commission=5.0)
    results = {}

    t0 = time.perf_counter()
    for _ in range(n_fills):
        matcher.fill_buy(price=50.0, amount=10000.0)
    results["fill_buy"] = (time.perf_counter() - t0) / n_fills * 1e6  # us per fill

    t0 = time.perf_counter()
    for _ in range(n_fills):
        matcher.fill_sell(price=50.0, shares=200.0)
    results["fill_sell"] = (time.perf_counter() - t0) / n_fills * 1e6  # us per fill

    return results


def bench_backtest(data: pd.DataFrame, n_runs: int = 10) -> float:
    """Benchmark full backtest run. Returns median time in ms."""
    engine = VectorizedBacktestEngine(commission_rate=0.001, min_commission=0.0)
    strategy = MACrossStrategy(short_period=5, long_period=20)

    times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        engine.run(data, strategy, initial_capital=100000)
        times.append(time.perf_counter() - t0)
    return np.median(times) * 1000  # ms


def main():
    parser = argparse.ArgumentParser(description="ez-trading performance benchmark")
    parser.add_argument("--bars", type=int, default=5000, help="Number of bars (default: 5000)")
    args = parser.parse_args()

    n = args.bars
    print(f"ez-trading benchmark — {n} bars, Python baseline")
    print("=" * 55)

    data = generate_data(n)

    # ts_ops
    print(f"\n[ts_ops] ({n} bars, median of 50 runs)")
    ts_results = bench_ts_ops(data)
    for name, ms in ts_results.items():
        print(f"  {name:20s}  {ms:8.3f} ms")

    # matcher
    print(f"\n[matcher] (100k fills)")
    m_results = bench_matcher()
    for name, us in m_results.items():
        print(f"  {name:20s}  {us:8.3f} us/fill")

    # full backtest
    print(f"\n[backtest] MACross 5/20 on {n} bars (median of 10 runs)")
    bt_ms = bench_backtest(data)
    print(f"  full_run              {bt_ms:8.1f} ms")
    print(f"  throughput            {n / bt_ms * 1000:8.0f} bars/sec")

    print("\n" + "=" * 55)
    print("Save this output. V2.1 C++ should beat these numbers.")


if __name__ == "__main__":
    main()
