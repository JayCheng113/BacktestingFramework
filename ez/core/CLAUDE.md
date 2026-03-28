# ez/core -- Core Computational Primitives

## Responsibility
Provide the low-level building blocks (matching, time-series ops) that both factor and backtest layers depend on. Python implementations now, C++ replaceable in V2.1.

## Public Interfaces
- `Matcher(ABC)` — [CORE] order matching interface: `fill_buy()`, `fill_sell()` -> `FillResult`
- `SimpleMatcher` — [CORE] instant fill with proportional commission (V1 default)
- `SlippageMatcher` — [CORE] fill with configurable slippage + commission (V2.2)
- `ts_ops` — [CORE] time series functions: `rolling_mean`, `rolling_std`, `ewm_mean`, `diff`, `pct_change`

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| matcher.py | Matcher ABC + SimpleMatcher | CORE |
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
- Commission applied on slipped price (realistic)
- `slippage_rate=0` is equivalent to SimpleMatcher

## Critical: ewm_mean adjust=True
pandas `ewm(adjust=True)` (default) uses a divisor-corrected formula, NOT simple
recursive `EMA = alpha*x + (1-alpha)*prev`. C++ implementation MUST match:
  weighted_avg = sum(w_i * x_i) / sum(w_i), where w_i = (1-alpha)^i
Failure to match will cause EMA, MACD, and MACD signal values to diverge.

## Benchmark
Run `python scripts/benchmark.py` to measure performance.

V2.1 C++ results (5000 bars, Apple M-series):
| Function | Python | C++ | Speedup |
|----------|--------|-----|---------|
| rolling_mean | 0.044ms | 0.010ms | 4.4x |
| ewm_mean | 0.035ms | 0.012ms | 2.9x |
| pct_change | 0.071ms | 0.009ms | 7.9x |
| rolling_std | 0.078ms | 0.086ms | ~1x (TODO: Welford online) |
