# ez/portfolio — Portfolio Backtesting Module

## Responsibility
Multi-stock portfolio backtesting: universe management, cross-sectional factors, portfolio strategies, weight allocation, discrete-share engine with accounting invariant. Factor research: cross-sectional IC evaluation, IC decay, quintile returns, factor correlation, walk-forward validation, significance testing. Alpha combination: industry neutralization, multi-factor composite, parameter search.

## Public Interfaces
- `TradingCalendar` — Trading day calendar, rebalance date computation (no weekday hardcoding)
- `Universe` — PIT security pool with delist/IPO filtering
- `CrossSectionalFactor` — ABC: `compute(universe_data, date) → Series[rank]`, `compute_raw(universe_data, date) → Series[raw_value]` (V2.11.1)
- `PortfolioStrategy` — ABC: `generate_weights(data, date, prev_w, prev_r) → dict[str, float]`
- `Allocator` — ABC: `allocate(raw_weights) → dict[str, float]` (EqualWeight/MaxWeight/RiskParity)
- `PortfolioOptimizer` — ABC: `set_context(date, data)` + `optimize(alpha_weights) → dict` (MeanVariance/MinVariance/RiskParity) (V2.12)
- `RiskManager` — `check_drawdown(equity)` + `check_turnover(new, old)` — 每日回撤熔断 + 换手率限制 (V2.12)
- `compute_attribution()` — Brinson 归因: 配置/选股/交互效应 + 行业维度 (V2.12)
- `run_portfolio_backtest()` — Main engine function (V2.12: +optimizer +risk_manager 可选参数)
- `CrossSectionalEvaluator` — Cross-sectional IC/RankIC/ICIR/IC decay/quintile returns (nanmean/nanstd)
- `FactorCorrelationMatrix` — Pairwise Spearman rank correlation between factors
- `PortfolioWalkForward` — Walk-forward validation for portfolio strategies
- `PortfolioSignificance` — Bootstrap CI + Monte Carlo significance testing
- `PortfolioStore` — DuckDB persistence for portfolio runs
- `neutralize_by_industry()` — Industry neutralization with coverage threshold (V2.11.1)
- `AlphaCombiner` — Multi-factor composite: z-score + weighted sum (V2.11.1)
- `resample()` — Daily → weekly/monthly/quarterly resampling utility

## Files
| File | Role |
|------|------|
| calendar.py | TradingCalendar: rebalance dates, date alignment |
| universe.py | PIT Universe: dynamic constituents, delist/IPO, data slicing |
| cross_factor.py | CrossSectionalFactor ABC (compute + compute_raw) + MomentumRank/VolumeRank/ReverseVolatilityRank |
| portfolio_strategy.py | PortfolioStrategy ABC (stateful, _registry) + TopNRotation/MultiFactorRotation |
| builtin_strategies.py | EtfMacdRotation/EtfSectorSwitch/EtfStockEnhance (QMT ports) |
| allocator.py | EqualWeight/MaxWeight/RiskParity allocators |
| engine.py | PortfolioEngine: discrete shares, accounting invariant, limit prices, benchmark |
| metrics.py | resample() utility |
| portfolio_store.py | DuckDB persistence |
| cross_evaluator.py | CrossSectionalEvaluator: IC/RankIC/ICIR/IC decay/quintile + FactorCorrelationMatrix |
| walk_forward.py | PortfolioWalkForward + PortfolioSignificance: Bootstrap CI + Monte Carlo |
| neutralization.py | neutralize_by_industry(): coverage threshold, single-stock drop, no-industry fallback (V2.11.1) |
| alpha_combiner.py | AlphaCombiner: z-score + weighted sum, equal/IC/ICIR, not auto-registered (V2.11.1) |
| optimizer.py | PortfolioOptimizer ABC + MeanVariance/MinVariance/RiskParity + Ledoit-Wolf (V2.12) |
| risk_manager.py | RiskConfig + RiskManager: drawdown state machine + turnover limiter (V2.12) |
| attribution.py | BrinsonAttribution + compute_attribution(): Brinson decomposition (V2.12) |
| loader.py | Startup scanner for portfolio_strategies/ and cross_factors/ |

## Key Design Decisions
- Anti-lookahead: engine slices data to [date-lookback, date-1] before calling strategy
- Accounting invariant: `cash >= -0.01` + `equity > 0` checked every day (V2.11.1 post-release: 替换原同义反复 assert)
- Discrete shares: weight → amount → shares (lot-size rounded) → remainder to cash
- Sell-before-buy: two-pass trade execution (sells first to free cash)
- Has-bar-today: only trade symbols with actual data on current day
- Buy/sell separate commission rates
- Benchmark: optional symbol for comparison curve + alpha/beta
- compute_raw(): raw values for neutralization and combination; compute(): percentile rank (V2.11.1)
- IC weights sign-preserving: negative IC = factor direction wrong → negative weight (V2.11.1)
- FundamentalCrossFactor.compute_raw() includes dropna() to filter NaN from any data source (V2.11.1)

## A-share Rules (built into engine)
- T+1: sold_today tracking — cannot buy a symbol that was sold on the same day
- Lot size: 100 shares (configurable)
- Stamp tax: sell-side 0.05% (configurable)
- Limit up/down: 10% (configurable, 20% for ChiNext/STAR)
- Min commission: 5 yuan
- Directional slippage: buy price = base * (1 + slippage_rate), sell price = base * (1 - slippage_rate)

## Status
- V2.9: Full implementation, 5 built-in strategies, 70+ tests
- V2.9.1: Bisect pre-indexing (10x speedup), regression tests (19 new)
- V2.10: CrossSectionalEvaluator, FactorCorrelation, WalkForward, Significance, 24 new tests
- V2.10 post-release: T+1, directional slippage, __init_subclass__ auto-registration
- V2.11.1: compute_raw() interface, neutralization, AlphaCombiner, parameter search, IC nanmean/nanstd, EP/BP/SP negative exclusion, PIT restatement fix, ann_date INDEX
- V2.11.1 post-release: 会计assert改有意义(cash>=0+equity>0), WF不可达代码移除(test_end_idx>n_days), Bootstrap CI升级BCa(z0 clamp防±inf, jackknife加速)
- V2.12: PortfolioOptimizer(MeanVariance/MinVariance/RiskParity, Ledoit-Wolf), RiskManager(drawdown+turnover), Brinson attribution, engine每日回撤+紧急减仓
