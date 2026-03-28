# ez/core -- Core Computational Primitives

## Responsibility
Provide the low-level building blocks (matching, time-series ops) that both factor and backtest layers depend on. Python implementations now, C++ replaceable in V2.1.

## Public Interfaces
- `Matcher(ABC)` — [CORE] order matching interface: `fill_buy()`, `fill_sell()` -> `FillResult`
- `SimpleMatcher` — [CORE] instant fill with proportional commission (V1 default)
- `ts_ops` — [CORE] time series functions: `rolling_mean`, `rolling_std`, `ewm_mean`, `diff`, `pct_change`

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| matcher.py | Matcher ABC + SimpleMatcher | CORE |
| ts_ops.py | Time series primitives (pandas wrappers) | CORE |

## Dependencies
- Upstream: none (leaf package)
- Downstream: `ez/factor/`, `ez/backtest/`

## V2.1 C++ Replacement Plan
1. Each ts_ops function gets a C++ implementation via nanobind
2. SimpleMatcher gets a C++ counterpart (SlippageMatcher)
3. Python implementations stay as fallback when C++ extension not compiled
4. Same interface — callers don't change

## Critical: ewm_mean adjust=True
pandas `ewm(adjust=True)` (default) uses a divisor-corrected formula, NOT simple
recursive `EMA = alpha*x + (1-alpha)*prev`. C++ implementation MUST match:
  weighted_avg = sum(w_i * x_i) / sum(w_i), where w_i = (1-alpha)^i
Failure to match will cause EMA, MACD, and MACD signal values to diverge.

## Benchmark
Run `python scripts/benchmark.py` to capture Python baseline. V2.1 C++ must beat these numbers.
