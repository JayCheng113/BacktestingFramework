# ez/factor — Factor Layer

## Responsibility
Compute technical indicators and evaluate their predictive power via IC analysis.

## Public Interfaces
- `Factor(ABC)` — [CORE] base class with `__init_subclass__` auto-registration. Properties: `name`, `warmup_period`. Method: `compute(df) -> df`. Access registry via `Factor.get_registry()`.
- `FactorEvaluator` — [CORE] computes IC, ICIR, IC decay, turnover for a factor
- `MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR` — [EXTENSION] built-in technical indicators

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| base.py | Factor ABC + __init_subclass__ auto-registration + get_registry() | CORE |
| evaluator.py | FactorEvaluator | CORE |
| builtin/technical.py | MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR | EXTENSION |

## Dependencies
- Upstream: `ez/types.py`
- Downstream: `ez/strategy/`, `ez/backtest/`, `ez/api/`

## Adding a New Factor
1. Create file in `factors/your_factor.py` (user directory) or `ez/factor/builtin/your_factor.py` (built-in)
2. Inherit from `Factor`, implement `name`, `warmup_period`, `compute()`
3. Auto-registered via `__init_subclass__` — no manual registration needed
4. Run `pytest tests/test_factor/test_factor_contract.py` — auto-validates

## Factor Correctness (V2.10 fixes)
- **RSI**: flat period = 50, pure uptrend = 100, pure downtrend = 0 (edge cases handled)
- **VWAP**: adj_ratio scaling (`adj_close / close`) applied to high/low for split-adjusted consistency
- **ATR**: adj_ratio scaling applied to OHLC for split-adjusted consistency

## Files (V2.11 additions)
| File | Role | Core/Extension |
|------|------|---------------|
| builtin/fundamental.py | 18 FundamentalCrossFactor subclasses: Value(EP/BP/SP/DP), Quality(ROE/ROA/GrossMargin/NetProfitMargin), Growth(RevenueGrowthYoY/ProfitGrowthYoY/ROEChange), Size(LnMarketCap/LnCircMV), Liquidity(TurnoverRate/AmihudIlliquidity), Leverage(DebtToAssets/CurrentRatio), Industry(IndustryMomentum) | EXTENSION |

## Status
- Implemented: MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR, FactorEvaluator (time-series IC)
- V2.10: Factor __init_subclass__ auto-registration, factors/ user directory for custom factors, RSI/VWAP/ATR correctness fixes
- V2.11: 18 fundamental CrossSectionalFactors (FundamentalCrossFactor base), data from FundamentalStore (daily_basic + fina_indicator), all output percentile rank with "高分=好" convention, PIT-aligned via ann_date
- V2.11.1: compute_raw() refactor (raw values for neutralization/combination), EP/BP排除PE<0/PB<0, SP排除PS<0/NaN, compute_raw() 统一dropna过滤NaN, FundamentalCrossFactor从registry pop
- Known limitation: V1 IC is time-series (single stock); cross-sectional IC available via ez/portfolio/CrossSectionalEvaluator (V2.10)
