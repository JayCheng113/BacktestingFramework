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
| ml_alpha.py | MLAlpha(CrossSectionalFactor): walk-forward ML factor framework + V1 whitelist + n_jobs runtime enforcement + positional purge/embargo (trading days) + ML_ALPHA_TEMPLATE + UnsupportedEstimatorError (V2.13 Phase 1) |

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
- V2.12: PortfolioOptimizer(MeanVariance/MinVariance/RiskParity, Ledoit-Wolf), RiskManager(drawdown+turnover), Brinson attribution(Carino几何链接), engine每日回撤+紧急减仓+期末强平, PortfolioStore归因数据持久化(rebalance_weights+trades)
- V2.12.1: Gram-Schmidt因子正交化(orthogonalization.py), IndexDataProvider(AKShare成分+24h cache), Optimizer TE约束(benchmark_weights+max_tracking_error), batch kline query, weights完整历史端点, TypeScript types(0 as any)
- V2.12.1 post-release (codex 6 轮 + Claude reviewer 8 轮迭代):
  - **engine.py 指标公式统一**: sharpe/sortino/alpha/beta 全部匹配 ez/backtest/metrics.py 的标准公式 (excess returns + ddof=1), 之前组合 vs 单票公式不同 (差 0.77 sortino / 5.72pp alpha)
  - **清仓 equity_curve 写回**: 期末强平后 append(cash, liq_date, {}) 到 equity_curve/dates/weights_history, 之前 metrics 基于清仓前曲线系统性高估
  - **归因覆盖最后持仓区间**: effective_dates 追加 result.dates[-1], 之前 Brinson 漏最后段导致 total_excess != total_return
  - **t_plus_1 gate**: run_portfolio_backtest 加 t_plus_1 参数, sold_today 检查按 market gate (非 cn_stock 允许同日 sell→buy)
  - **成交层 turnover 复核**: RiskManager.check_turnover 权重层通过后, _lot_round 可能放大卖侧换手, 引擎 post-loop 重算实际 turnover, 超限 emit risk_event
  - **lookback 动态化**: run_portfolio_backtest 启动时 warn 若 strategy.lookback_days < max(factor.warmup_period)
  - **PortfolioStrategy dual-dict registry**: _registry_by_key (module.class 唯一) + _registry (name-keyed 向后兼容) + resolve_class() 三阶段解析, 消除同名覆盖
  - **PortfolioOptimizer fallback_events**: 记录每次降级 (total_alpha<=0 / cov 失败 / _optimize 异常), API 层 surface
  - **walk_forward deepcopy**: 每折 copy.deepcopy(strategy) 防 IS→OOS 状态污染
  - **walk_forward optimizer/risk_manager factory**: 每折 fresh 实例 + aggregate optimizer_fallback_events / risk_events
  - **oos_metrics 拼接重算**: 用 MetricsCalculator 基于拼接 oos_equity_curve 算, 不是每折 sharpe 平均
- V2.12.2 post-release:
  - **walk_forward.py 尾部丢弃**: 原 `window_size = n_days // n_splits` 静默丢弃 `n_days % n_splits` 尾部交易日. 改整数区间 `i*n_days//n_splits .. (i+1)*n_days//n_splits`, 最后一折吸收余数, 所有交易日都纳入 IS/OOS.
  - **CrossSectionalFactor dual-dict registry**: 补齐 `_registry_by_key` + `_registry` + `resolve_class()` + 冲突 warning (对齐 PortfolioStrategy V2.12.1). `AlphaCombiner` pop 同步清理 `_registry_by_key` (reviewer sibling miss fix).
  - **portfolio_store 上下文完整**: 新增 `config` / `warnings` / `dates` 三列 + ALTER 迁移, `/run` 打包 market/optimizer/risk/index/cost 到 `config`, 历史对比图表用真实交易日 time axis 对齐 (legacy 空 dates 行降级 index 轴 + 警告 banner).
  - **alpha_combiner 训练 lookback 正确传递**: `_compute_alpha_weights` 的 `dynamic_lb` 之前只传 fetch, 现在也传 `evaluate_cross_sectional_factor()`, 避免长 warmup 因子训练窗被默认 252 截断.
- **V2.13 Phase 1 — MLAlpha Core** (`ml_alpha.py`, plan `docs/superpowers/plans/2026-04-06-v213-ml-alpha.md`): 走完 F8 第 1 阶段 (14 tasks + code review round). 见根 CLAUDE.md V2.13 条目. 关键点摘要:
  - **MLAlpha(CrossSectionalFactor)** + lazy retrain + purge/embargo + anti-lookahead 两层防御
  - **V1 safety 层**: 7 类 sklearn whitelist (Ridge/Lasso/LinearRegression/ElasticNet/DecisionTreeRegressor/RandomForestRegressor/GradientBoostingRegressor), n_jobs=1 运行时强制, type 身份比对阻止 monkey-patch 子类绕过. `_assert_supported_estimator` 在 `__init__` 和每次 `_retrain()` 运行.
  - **⚠️ Purge/embargo 是 trading days (positional `iloc[:-N]`), 不是 calendar days** — 原始用 `timedelta(days=N)` 会让 label 穿过周末指向预测窗口, reviewer 发现这个 C1 bug 后改为 positional trim 匹配 `shift(-k)` 的行单位.
  - **portfolio walk-forward 隔离走 `strategy_factory()` 每折 fresh instance** (非 `copy.deepcopy`). 单票 WalkForwardValidator 走 deepcopy — MLAlpha 作为 Python value 也 deepcopy-safe. `test_walk_forward_fresh_instances_have_no_cross_fold_state_bleed` 3 splits × (IS+OOS) 断言 6 个独立 id + 初始状态清零.
  - **In-memory 模型**, 无磁盘 I/O (sandbox 禁 pickle). `retrain_freq` 窗口内 compute 调用走 cache.
  - **容错**: feature_fn/target_fn 异常 skip, non-DataFrame/Series 返回一次性 warning + skip, inf 特征 `np.isfinite` 过滤, `model.fit()` 异常保留上一次模型不 crash.
  - **75 tests** (`test_ml_alpha.py` 61 + `test_ml_alpha_sklearn.py` 14) 覆盖 whitelist enforcement / anti-lookahead outlier regression / deepcopy round-trip / end-to-end factory-freshness / cache / error handling / edge cases / template render+exec.
  - **Dependency**: `scikit-learn>=1.5` 作为 `[ml]` optional group (numpy>=2.0 ABI 兼容).
