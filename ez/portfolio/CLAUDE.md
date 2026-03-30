# ez/portfolio — Portfolio Backtesting Module (V2.9+V2.10)

## Responsibility
Multi-stock portfolio backtesting: universe management, cross-sectional factors, portfolio strategies, weight allocation, discrete-share engine with accounting invariant. Factor research: cross-sectional IC evaluation, IC decay, quintile returns, factor correlation, walk-forward validation, significance testing.

## Public Interfaces
- `TradingCalendar` — Trading day calendar, rebalance date computation (no weekday hardcoding)
- `Universe` — PIT security pool with delist/IPO filtering
- `CrossSectionalFactor` — ABC: `compute(universe_data, date) → Series[symbol → score]`
- `PortfolioStrategy` — ABC: `generate_weights(data, date, prev_w, prev_r) → dict[str, float]`
- `Allocator` — ABC: `allocate(raw_weights) → dict[str, float]` (EqualWeight/MaxWeight/RiskParity)
- `run_portfolio_backtest()` — Main engine function
- `CrossSectionalEvaluator` — Cross-sectional IC/RankIC/ICIR/IC decay/quintile returns evaluation
- `FactorCorrelationMatrix` — Pairwise Spearman rank correlation between factors
- `PortfolioWalkForward` — Walk-forward validation for portfolio strategies
- `PortfolioSignificance` — Bootstrap CI + Monte Carlo significance testing
- `PortfolioStore` — DuckDB persistence for portfolio runs
- `resample()` — Daily → weekly/monthly/quarterly resampling utility

## Files
| File | Role |
|------|------|
| calendar.py | TradingCalendar: rebalance dates, date alignment |
| universe.py | PIT Universe: dynamic constituents, delist/IPO, data slicing |
| cross_factor.py | CrossSectionalFactor ABC + MomentumRank/VolumeRank/ReverseVolatilityRank |
| portfolio_strategy.py | PortfolioStrategy ABC (stateful, _registry) + TopNRotation/MultiFactorRotation |
| builtin_strategies.py | EtfMacdRotation/EtfSectorSwitch/EtfStockEnhance (QMT ports) |
| allocator.py | EqualWeight/MaxWeight/RiskParity allocators |
| engine.py | PortfolioEngine: discrete shares, accounting invariant, limit prices, benchmark |
| metrics.py | resample() utility |
| portfolio_store.py | DuckDB persistence |
| cross_evaluator.py | CrossSectionalEvaluator: IC/RankIC/ICIR/IC decay/quintile + FactorCorrelationMatrix (V2.10) |
| walk_forward.py | PortfolioWalkForward + PortfolioSignificance: Bootstrap CI + Monte Carlo (V2.10) |
| loader.py | Startup scanner for portfolio_strategies/ and cross_factors/ |

## Key Design Decisions
- Anti-lookahead: engine slices data to [date-lookback, date-1] before calling strategy
- Accounting invariant: `cash + Σ(shares × price) == equity` checked every day
- Discrete shares: weight → amount → shares (lot-size rounded) → remainder to cash
- Sell-before-buy: two-pass trade execution (sells first to free cash)
- Has-bar-today: only trade symbols with actual data on current day
- Buy/sell separate commission rates
- Benchmark: optional symbol for comparison curve + alpha/beta

## A-share Rules (built into engine)
- Lot size: 100 shares (configurable)
- Stamp tax: sell-side 0.05% (configurable)
- Limit up/down: 10% (configurable, 20% for ChiNext/STAR)
- Min commission: 5 yuan

## Status
- V2.9: Full implementation, 5 built-in strategies, 70+ tests
- V2.9.1: Bisect pre-indexing (10x speedup), regression tests (19 new), TopNRotation/MultiFactorRotation schema + description
- V2.10: CrossSectionalEvaluator (IC/RankIC/ICIR/IC decay/quintile returns), FactorCorrelationMatrix (pairwise Spearman), PortfolioWalkForward, PortfolioSignificance (Bootstrap CI + Monte Carlo), 24 new tests (14 evaluator + 10 WF)
