# ez/backtest -- Backtest Layer

## Responsibility
Run vectorized backtests, compute metrics, validate via Walk-Forward, test statistical significance.

## Public Interfaces
- `VectorizedBacktestEngine` -- [CORE] run(data, strategy, capital) -> BacktestResult
- `MetricsCalculator` -- [CORE] compute(equity, benchmark) -> dict
- `WalkForwardValidator` -- [CORE] validate(data, strategy, n_splits) -> WalkForwardResult
- `compute_significance()` -- [CORE] Bootstrap CI + Monte Carlo permutation test

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| engine.py | VectorizedBacktestEngine | CORE |
| portfolio.py | PortfolioState (V2, reserved) | CORE |
| metrics.py | MetricsCalculator | CORE |
| walk_forward.py | WalkForwardValidator | CORE |
| significance.py | Statistical significance | CORE |

## Critical Notes
- The engine shifts signals by 1 bar (T+1 execution) to prevent look-ahead bias
- Minimum commission: a floor is applied per trade so that very small trades still incur realistic costs

## Status
- Implemented: Full backtest engine, Walk-Forward (fixed-param), significance testing
- V2.0: Matcher extraction — engine delegates to Matcher.fill_buy/fill_sell
- V2.2: SlippageMatcher — user-configurable slippage via API/frontend
- Engine uses fill.fill_price (not exec_price) for entry/exit/PnL — supports slippage
