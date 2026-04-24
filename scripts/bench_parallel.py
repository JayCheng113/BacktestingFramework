#!/usr/bin/env python3
"""Benchmark: parallel batch backtest using ProcessPoolExecutor."""
import time
import sys
sys.path.insert(0, ".")

from ez.backtest.parallel import parallel_backtest
from ez.strategy.base import Strategy


class BenchMA(Strategy):
    name = "bench_parallel_ma"
    params = {}
    def required_factors(self):
        return []
    def generate_signals(self, data):
        c = data["adj_close"]
        return (c.rolling(10).mean() > c.rolling(50).mean()).astype(float)


if __name__ == "__main__":
    syms = [
        "600000.SH", "601318.SH", "000858.SZ", "000651.SZ", "601166.SH",
        "000006.SZ", "000020.SZ", "600051.SH", "300071.SZ", "688038.SH",
    ] * 10  # 100 symbols

    print(f"Running {len(syms)} backtests...")

    # Serial
    t0 = time.perf_counter()
    r1 = parallel_backtest(syms, BenchMA, n_workers=1,
                           start_date="2025-03-01", end_date="2025-12-31")
    t_serial = time.perf_counter() - t0
    ok1 = sum(1 for r in r1 if r["status"] == "ok")

    # Parallel
    t0 = time.perf_counter()
    r2 = parallel_backtest(syms, BenchMA, n_workers=8,
                           start_date="2025-03-01", end_date="2025-12-31")
    t_para = time.perf_counter() - t0
    ok2 = sum(1 for r in r2 if r["status"] == "ok")

    print(f"\n=== P3 Parallel Benchmark ({len(syms)} symbols) ===")
    print(f"  Serial (1 worker):    {t_serial*1000:>7.0f}ms  ({ok1} OK)")
    print(f"  Parallel (8 workers): {t_para*1000:>7.0f}ms  ({ok2} OK)")
    print(f"  Speedup: {t_serial / max(t_para, 0.001):.1f}x")
    print(f"  Projected 3000 stocks parallel: {3000/len(syms)*t_para:.1f}s")
