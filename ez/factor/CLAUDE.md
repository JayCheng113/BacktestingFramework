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

## adj_close contract (V2.17)
**Every built-in Factor / CrossSectionalFactor computes signals on `adj_close`, NOT raw `close`.** Raw close jumps ~50% on dividend days for ETFs — a factor reading it would produce phantom negative signals. V2.18.1 research measured 14pp/year impact on StaticLowVol.

Contract canary: `tests/test_factor/test_adj_close_contract.py` runs every registered factor on synthetic dividend-day data; any factor with >40% phantom jump fails on CI. The only intentional raw-close use in the codebase is `ez/portfolio/builtin_strategies.py` (QMT-ported strategies) — NOT factors; documented as design choice in root CLAUDE.md.

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
- V2.12.2 post-release: **dual-dict 注册表** — `Factor` 和 `CrossSectionalFactor` 补齐 `_registry_by_key` (module.class 唯一) + `_registry` (名字键向后兼容) + `resolve_class()` 三阶段解析 + 名字冲突 warning (对齐 PortfolioStrategy V2.12.1 模式). 相关 pop 点 (`fundamental.py`, `alpha_combiner.py`, `portfolio.py` `_NeutralizedWrapper`) 全部同步 pop 两个 dict. Sandbox 热重载 (`_reload_factor_code`) 和因子保存失败 rollback 路径同样清理两个 dict. `/api/code/refresh` + delete 路由通过新 helper `_get_all_registries_for_kind()` 统一批量清理.
- Known limitation: V1 IC is time-series (single stock); cross-sectional IC available via ez/portfolio/CrossSectionalEvaluator (V2.10)
