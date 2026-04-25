#!/usr/bin/env python3
"""重负载并行回测基准测试（含显著性检验）。

与 bench_parallel.py 相同结构，但每个回测启用
Bootstrap + Monte Carlo 显著性检验，模拟真实工作负载。

用法: python scripts/bench_parallel_heavy.py
"""
import time, sys
sys.path.insert(0, ".")
from ez.backtest.parallel import parallel_backtest
from ez.strategy.base import Strategy

class BenchMA(Strategy):
    name = "bench_heavy"
    params = {}
    def required_factors(self): return []
    def generate_signals(self, data):
        c = data["adj_close"]
        return (c.rolling(10).mean() > c.rolling(50).mean()).astype(float)

if __name__ == "__main__":
    syms = [
        "600000.SH","601318.SH","000858.SZ","000651.SZ","601166.SH",
        "000006.SZ","000020.SZ","600051.SH","300071.SZ","688038.SH",
    ] * 5  # 50 symbols, each ~6ms with significance

    print(f"Running {len(syms)} backtests WITH significance (heavier per task)...")

    t0 = time.perf_counter()
    r1 = parallel_backtest(syms, BenchMA, n_workers=1,
                           start_date="2025-03-01", end_date="2025-12-31",
                           skip_significance=False)
    t_serial = time.perf_counter() - t0
    ok1 = sum(1 for r in r1 if r["status"] == "ok")

    t0 = time.perf_counter()
    r2 = parallel_backtest(syms, BenchMA, n_workers=8,
                           start_date="2025-03-01", end_date="2025-12-31",
                           skip_significance=False)
    t_para = time.perf_counter() - t0
    ok2 = sum(1 for r in r2 if r["status"] == "ok")

    print(f"\n=== Parallel WITH Significance ({len(syms)} symbols) ===")
    print(f"  Serial:   {t_serial*1000:>7.0f}ms  ({ok1} OK, {t_serial/max(ok1,1)*1000:.1f}ms/stock)")
    print(f"  Parallel: {t_para*1000:>7.0f}ms  ({ok2} OK)")
    print(f"  Speedup:  {t_serial / max(t_para, 0.001):.1f}x")
    print(f"  Projected 3000 stocks parallel: {3000/len(syms)*t_para:.1f}s")
