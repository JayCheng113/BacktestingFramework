# ez/factor — Factor Layer

## Responsibility
Compute technical indicators and evaluate their predictive power via IC analysis.

## Public Interfaces
- `Factor(ABC)` — [CORE] base class. Properties: `name`, `warmup_period`. Method: `compute(df) -> df`
- `FactorEvaluator` — [CORE] computes IC, ICIR, IC decay, turnover for a factor
- `MA, EMA, RSI, MACD, BOLL, Momentum` — [EXTENSION] built-in technical indicators

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| base.py | Factor ABC | CORE |
| evaluator.py | FactorEvaluator | CORE |
| builtin/technical.py | MA, EMA, RSI, MACD, BOLL | EXTENSION |

## Dependencies
- Upstream: `ez/types.py`
- Downstream: `ez/strategy/`, `ez/backtest/`, `ez/api/`

## Adding a New Factor
1. Create file in `ez/factor/builtin/your_factor.py`
2. Inherit from `Factor`, implement `name`, `warmup_period`, `compute()`
3. Run `pytest tests/test_factor/test_factor_contract.py` — auto-validates
4. Edit `ez/api/routes/factors.py` `_FACTOR_MAP` to expose the factor via API

## Status
- Implemented: MA, EMA, RSI, MACD, BOLL, Momentum, FactorEvaluator (time-series IC)
- Known limitation: V1 IC is time-series (single stock), not cross-sectional
