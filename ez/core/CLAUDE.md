# ez/core -- Core Computational Primitives

## Responsibility
Provide the low-level building blocks (matching, time-series ops, optional JIT kernels) that both factor and backtest layers depend on.

## Public Interfaces
- `Matcher(ABC)` — [CORE] order matching interface: `fill_buy()`, `fill_sell()` -> `FillResult`
- `SimpleMatcher` — [CORE] instant fill with proportional commission (V1 default)
- `SlippageMatcher` — [CORE] fill with configurable slippage + commission (V2.2)
- `MarketRulesMatcher` — [CORE] A-share rules decorator: T+1, price limits, lot size (V2.6)
- `ts_ops` — [CORE] time series functions: `rolling_mean`, `rolling_std`, `ewm_mean`, `diff`, `pct_change`

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| _jit_fill.py | Optional numba fill kernels + simulation loop fallback | CORE |
| matcher.py | Matcher ABC + SimpleMatcher + SlippageMatcher | CORE |
| market_rules.py | MarketRulesMatcher (V2.6) | CORE |
| ts_ops.py | Time series primitives (pandas wrappers) | CORE |

## Dependencies
- Upstream: none (leaf package)
- Downstream: `ez/factor/`, `ez/backtest/`

## ts_ops Scope
ts_ops covers **rolling/windowed time-series operations on market data** — the hot path
in factor computation. The following are explicitly OUT OF SCOPE (not hot path, not
worth C++ overhead):
- `equity_curve.pct_change()` in metrics.py — aggregate return calculation
- `df["adj_close"].pct_change()` in engine.py — benchmark/significance returns
- `.std()`, `.corr()`, `.cov()`, `.cummax()` in metrics.py/evaluator.py — aggregate stats

These remain direct pandas calls. Only factor-layer time-series math routes through ts_ops.

## V2.1 C++ Status
- All 5 ts_ops functions have C++ implementations via nanobind (up to 7.9x faster)
- Python fallback active when C++ extension not compiled
- Same interface — callers don't change

## SlippageMatcher (V2.2)
- `buy fill_price = price * (1 + slippage_rate)` — buying pushes price up
- `sell fill_price = price * (1 - slippage_rate)` — selling pushes price down
- Commission: buys on input amount, sells on slipped execution value
- `slippage_rate=0` is equivalent to SimpleMatcher
- Negative parameters rejected (ValueError)

## MarketRulesMatcher (V2.6)
- Decorator wrapping inner Matcher (SimpleMatcher/SlippageMatcher)
- T+1: cannot sell shares bought on the same bar (`_buy_bar == _bar`)
- Price limits: 涨停不可买 (`price >= prev_close * 1.1`), 跌停不可卖
- Lot size: round down to `lot_size` multiples, proportional commission adjustment
- `on_bar(bar_index, prev_close)` called by engine via `hasattr` check
- **Note**: 整手佣金用比例缩减 (`ratio = actual_shares / fill.shares`)，不重调 inner matcher

## Critical: ewm_mean adjust=True
pandas `ewm(adjust=True)` (default) uses a divisor-corrected formula, NOT simple
recursive `EMA = alpha*x + (1-alpha)*prev`. C++ implementation MUST match:
  weighted_avg = sum(w_i * x_i) / sum(w_i), where w_i = (1-alpha)^i
Failure to match will cause EMA, MACD, and MACD signal values to diverge.

## Benchmark
Run `python scripts/benchmark.py` to measure performance.

V2.3 C++ results (5000 bars, Apple M-series):
| Function | Python | C++ | Speedup |
|----------|--------|-----|---------|
| rolling_mean | 0.044ms | 0.010ms | 4.4x |
| ewm_mean | 0.035ms | 0.012ms | 2.9x |
| pct_change | 0.071ms | 0.009ms | 7.9x |
| rolling_std | 0.078ms | 0.015ms | 5.2x (V2.3 Welford O(n)) |
