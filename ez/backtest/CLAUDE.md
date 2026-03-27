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
| portfolio.py | PortfolioState | CORE |
| metrics.py | MetricsCalculator | CORE |
| walk_forward.py | WalkForwardValidator | CORE |
| significance.py | Statistical significance | CORE |

## Status
- Implemented: Full backtest engine, Walk-Forward (fixed-param), significance testing
- V2: Parameter optimization in Walk-Forward (WFO)
