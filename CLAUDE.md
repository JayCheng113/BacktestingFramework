# ez-trading

Agent-Native quantitative trading platform. Human researchers and AI agents are both
first-class citizens — same pipeline, same gates, same audit trail.
Python 3.12+ / FastAPI / DuckDB / React 19 / ECharts / C++ (nanobind).
Version: 0.2.27 | Tests: 2685 passed + 10 skipped with sklearn+lgbm+xgb | C++ acceleration: up to 7.9x

## Architecture Docs (MUST READ before major changes)
- [System Architecture](docs/architecture/system-architecture.md) — 7-layer design, gates (Research/Deploy/Runtime + PreTradeRisk), dual state machine
- [Engineering Governance](docs/architecture/governance.md) — thin core, lifecycle labels, version discipline
- [V2.3+ Roadmap](docs/core-changes/v2.3-roadmap.md) — detailed per-version plan with exit gates

**Note**: ResearchGate implemented in V2.4 (ez/agent/gates.py). MarketRules in V2.6 (ez/core/market_rules.py). LLM + Web Coding Assistant in V2.7 (ez/llm/, ez/agent/assistant.py). Autonomous Research Agent in V2.8 (ez/agent/research_runner.py). Deploy/Runtime gates planned for V2.9+.

## Module Map
- `ez/core/` — Computational primitives: matcher, ts_ops (C++ accelerated) [CLAUDE.md](ez/core/CLAUDE.md)
- `ez/data/` — Data ingestion, validation, caching, fundamental data store (V2.11) [CLAUDE.md](ez/data/CLAUDE.md)
- `ez/factor/` — Factor computation + IC evaluation + fundamental factors (V2.11) [CLAUDE.md](ez/factor/CLAUDE.md)
- `ez/strategy/` — Strategy framework, auto-registration [CLAUDE.md](ez/strategy/CLAUDE.md)
- `ez/backtest/` — Backtest engine, Walk-Forward, significance [CLAUDE.md](ez/backtest/CLAUDE.md)
- `ez/api/` — FastAPI REST endpoints [CLAUDE.md](ez/api/CLAUDE.md)
- `web/` — React frontend dashboard [CLAUDE.md](web/CLAUDE.md)
- `ez/llm/` — LLM provider abstraction: DeepSeek/Qwen/Local/OpenAI (V2.7) [CLAUDE.md](ez/llm/CLAUDE.md)
- `ez/agent/` — Agent loop: RunSpec, Runner, Gates, Report, ExperimentStore, CandidateSearch, BatchRunner, Prefilter, Tools, Assistant, Sandbox, FDR + V2.8 Research Agent [CLAUDE.md](ez/agent/CLAUDE.md)
- `ez/portfolio/` — Portfolio backtesting: Universe, CrossSectionalFactor, PortfolioStrategy, Allocator, Engine, 5 built-in strategies, CrossSectionalEvaluator, PortfolioWalkForward (V2.9+V2.10) [CLAUDE.md](ez/portfolio/CLAUDE.md)
- `ez/live/` — Paper Trading Bridge: DeploymentSpec, DeployGate, PaperTradingEngine, Scheduler, Monitor, DeploymentStore (V2.15) [CLAUDE.md](ez/live/CLAUDE.md)
- `ez/ops/` — Scheduling, monitoring, audit (V3.2+, planned)

## Dependency Flow
```
ez/types.py -> ez/data/ -> ez/factor/ -> ez/strategy/ -> ez/backtest/ -> ez/api/ -> web/
                            ↑ ts_ops                      ↑ matcher
                            └────────── ez/core/ ─────────┘  (leaf, no deps)

ez/llm/ (V2.7) — LLM provider abstraction, only depends on ez/config
ez/agent/ consumes backtest/core/llm interfaces (V2.4+V2.7). Future: ez/live/, ez/ops/ — never modify core.
```

## Core Files (DO NOT MODIFY without proposal in docs/core-changes/)
ez/types.py, ez/errors.py, ez/config.py, ez/core/matcher.py, ez/core/ts_ops.py,
ez/core/market_rules.py, ez/data/provider.py, ez/data/validator.py, ez/data/store.py,
ez/factor/base.py, ez/factor/evaluator.py, ez/strategy/base.py, ez/strategy/loader.py,
ez/backtest/engine.py, ez/backtest/portfolio.py, ez/backtest/metrics.py,
ez/backtest/walk_forward.py, ez/backtest/significance.py

## Adding Extensions (no Core changes needed)
| Type | Directory | Base Class | Test |
|------|-----------|------------|------|
| Data source | ez/data/providers/ | DataProvider | pytest tests/test_data/test_provider_contract.py |
| Factor | factors/ or ez/factor/builtin/ | Factor | pytest tests/test_factor/test_factor_contract.py |
| Strategy | strategies/ or ez/strategy/builtin/ | Strategy | pytest tests/test_strategy/ |
| Matcher | ez/core/matcher.py | Matcher | pytest tests/test_core/test_matcher_contract.py |

## Quick Commands
```bash
./scripts/start.sh          # Start backend (8000) + frontend (3000)
./scripts/stop.sh            # Stop all
pytest tests/                # Full test suite (1308 collected, 1298 pass, 10 skip). 停掉后端再跑: ./scripts/stop.sh
python scripts/benchmark.py  # Performance baseline
pip install -e . --no-build-isolation  # Rebuild C++ extension
```

## Mandatory: Code Review After Every Version/Feature
Use `/superpowers:requesting-code-review` or send to Codex for review.
No version tag without review pass. No push without critical issues resolved.

## Current Version Progress
- V1.0-V1.1: Full Python pipeline (data → factor → strategy → backtest → API → web)
- V2.0: Matcher + ts_ops extraction into ez/core/
- V2.1: C++ nanobind acceleration (up to 7.9x)
- V2.2: SlippageMatcher + user-configurable trading costs
- **V2.3**: Correctness hardening — accounting invariants (173 tests), C++/Python dual-path parity (109 tests), rolling_std Welford O(n) (5.2x vs Python), architecture gate tests (47 tests), ewm_mean span=1 NaN fix
- **V2.4**: Agent Loop — RunSpec, Runner, ResearchGate (DD sign fix), Report, ExperimentStore, /experiments API, Experiments UI
- **V2.4.1**: Stability — PK-based idempotency (completed_specs), gate DD sign fix, NaN sanitization, JSON double-encoding fix, concurrent regression tests, 725 total tests
- **V2.5**: Scale — param grid/random search (144 specs in 4.7s), pre-filter engine, batch runner + ranking, new factors (VWAP/OBV/ATR), multi-period frontend, experiment delete/cleanup, DatePicker + Min/Max/Step UI, 779 total tests
- **V2.5 post-release fixes**: factor adj_close split-adjustment, delete FK consistency, int truncation rejection, timezone off-by-one, render perf (O(1) combos), Sortino formula, NaN price guard, pnl_pct cost_basis, significance constant signal, pct_change deprecation
- **V2.6**: MarketRules — T+1, 涨跌停 (10%/20%), 整手 (100股), engine on_bar 钩子 (+3 行), fill-retry 修复, raw close 涨跌停判定, DB 落库审计, 800 total tests
- **V2.6.1**: Stability — CORE_FILES 注册, DateBtn 共享组件, lot-size 佣金重算, DB 迁移缩窄, countValues 精度对齐, 801 tests
- **V2.7**: LLM + Web Coding Assistant — Monaco Editor, AI Chat (DeepSeek/Qwen/Local), Tool框架 (9 tools), 代码沙箱 (AST禁危险import/builtins/dunders), FDR 多重检验 (Bonferroni/BH), 全站中文化, 开发文档 (11章1497行), 设置面板 (LLM/Tushare), 多会话 Chat (localStorage持久化), 897 tests
- **V2.7 post-release fixes**: 整手买入超预算修复, read_source 前缀校验, FDR None 容错, WF 参数校验, 热重载 pyc 清理, SPA API 404, bool 字符串解析, 凭证清空接口, 因子列名修正 (macd_line/boll_upper_20), 设置 YAML 持久化
- **V2.7.1**: Stability — Chat async 化, Provider 连接池, ExperimentStore 合并, 多列因子评估, 路径穿越修复, 921 tests
- **V2.8**: Autonomous Research Agent — 全自治策略探索 (E1假设生成+E2代码生成+E3批量回测+E4结果分析+E5循环控制+E6报告), ResearchStore持久化 (2张新表), SSE进度流+中文格式化, 研究助手前端 (目标表单+日期快捷+进度面板+报告), asyncio.Lock串行保护, cancel→cancelled状态机, 预算预检查, allowed_tools工具过滤, research_前缀隔离+注册到全局, 代码编辑器(一键新建+文件绑定对话+分组侧栏), 实验列表回测区间列, 开发文档大更新(12章1658行), 1050 tests
- **V2.8 post-release fixes**: 任务卡死(try/finally全包裹), 串行竞态(asyncio.Lock原子), 取消语义(cancelled≠completed), store连接泄漏(close实现), 预算硬约束(批前检查), LLM计数(保守估计), code_gen异常重试, best_strategies查询Top5, SSE预注册, E2工具最小权限, TS构建修复(删未用变量), 默认策略取过滤后数组, promote文件名校验+422错误码, 隔离改用key.includes('research_'), promote测试4条, 开发文档+161行
- **V2.8.1**: Stability — get_start_lock()封装(消除私有名跨模块导入), SSE heartbeat(15s keepalive防代理断开), LLM计数文档化(近似值注释), 因子面板动态化(API获取9因子+中文标签), cleanup_finished_tasks时间戳排序, promote regex精确化(Research+大写), 参数面板bool/str支持, 1039 tests
- **V2.8.1 post-release fixes**: 任务落库保障(save_task提前), promote失败前端alert, 策略列表严格任务隔离(移除全局fallback), LLM计数注释强化, 空因子禁用评估按钮
- **V2.9**: Portfolio / Rotation — 多股组合回测 (TradingCalendar+PIT Universe+CrossSectionalFactor+PortfolioStrategy有状态+Allocator+PortfolioEngine离散股数记账+会计不变量+涨跌停+基准对比+Sortino/Alpha/Beta+组合API+DuckDB持久化+Agent工具4个+前端组合tab 6-tab架构), 1119 tests
- **V2.9.1**: Stability — 引擎价格预索引(bisect O(log n), 10x加速), 单股回测接入MarketRules(stamp_tax+lot_size+limit_pct), 策略参数动态渲染(schema驱动), CodeEditor组合代码类型(4组侧栏+新建组合策略/截面因子), TopNRotation/MultiFactorRotation description+schema补全, 23项回归测试(C1 raw close涨跌停+C2 NaN carry-forward+内置策略行为+引擎确定性+印花税+容差+排序), 1156 tests
- **V2.10**: Factor Research + Research Efficiency — CrossSectionalEvaluator(截面IC/RankIC/ICIR/IC衰减/分位数收益), FactorCorrelationMatrix(Spearman秩相关热力图), PortfolioWalkForward(组合WF验证), PortfolioSignificance(Bootstrap CI+Monte Carlo), 多回测对比(checkbox选择+叠加曲线+指标表), CSV导出(净值曲线+交易记录), 预设标的池(宽基ETF/ETF轮动池/行业+宽基22只), 持仓饼图(latest_weights), 因子研究sub-tab(IC表+时序+衰减+分位数+相关性热力图), 3个新API端点, 1172 tests
- **V2.10 post-release fixes**: Sandbox安全加固(dict-style dunder access拦截: `vars()["__import__"]`), RSI修正(flat=50/uptrend=100/downtrend=0), VWAP/ATR adj_ratio缩放(split-adjusted一致性), 组合引擎T+1(sold_today集合+当日卖出股禁买), 方向性滑点(买推高/卖推低), DateRangePicker共享组件(react-datepicker), ExperimentPanel 3 sub-tabs(单次运行/参数搜索/组合实验), Factor/CrossSectionalFactor __init_subclass__自动注册, factors/用户因子目录, WalkForward参数校验(n_splits>=2, 0<train_ratio<1), fetch_kline_df共享到deps.py, sandbox根本加固(factor主进程不exec_module+gc禁用+stub注册), 1207 tests
- **V2.11**: 基本面数据层 — FundamentalStore(DuckDB fundamental_daily+fina_indicator表, PIT ann_date对齐, preload内存缓存), TushareProvider扩展(get_fina_indicator+dv_ratio), 18个FundamentalCrossFactor(Value: EP/BP/SP/DP, Quality: ROE/ROA/GrossMargin/NetProfitMargin, Growth: RevenueGrowthYoY/ProfitGrowthYoY/ROEChange, Size: LnMarketCap/LnCircMV反转, Liquidity: TurnoverRate/AmihudIlliquidity, Leverage: DebtToAssets反转/CurrentRatio, Industry: IndustryMomentum), 行业分类(复用symbols.industry), Fundamental API(fetch/quality/factors 3端点), 前端因子分类(optgroup+按类别分组+付费标注), 数据质量仪表板(覆盖率+财报期数), Tushare权限分层降级(daily_basic免费/fina_indicator付费), 1273 tests
- **V2.11.1**: Alpha组合+研究工具+post-release稳定化 — compute_raw()接口, 行业中性化, AlphaCombiner(等权/IC/ICIR), 组合参数搜索(schema驱动+截断提示+seed可复现), IC nanmean修正, EP/BP/SP负值排除, PIT重报修正, Tushare ETF fund_daily, AKShare免费fallback(双fetch qfq+raw+线程安全节流), DataProviderChain覆盖率检查(bar数量), 基准曲线修复(_ensure_benchmark+idx clamp+warning链路闭环), 策略因子管理(registry侧栏+删除原子性+refresh全量重载+zombie清理), 研究助手取消(AbortController), Navbar状态指示灯, 会计assert改有意义(cash>=0+equity>0), WF不可达代码清理, Bootstrap CI升级BCa(z0 clamp), FundamentalStore LRU缓存(统一units+protect+ghost清理), 1304 tests
- **V2.12**: 组合优化+归因+风控 — PortfolioOptimizer(MeanVariance/MinVariance/RiskParity, Ledoit-Wolf协方差SLSQP约束优化), RiskManager(每日回撤熔断状态机+紧急减仓+换手率混合), Brinson归因(配置/选股/交互效应+Carino几何链接+行业维度), 期末强平(完整round-trip), 归因数据持久化(rebalance_weights+trades存DB), /run扩展优化器/风控参数+内联归因, 前端折叠面板(优化器+风控+归因+事件日志), 1339 tests
- **V2.12.1**: Stability — batch kline(单SQL批量查询), Gram-Schmidt因子正交化(AlphaCombiner orthogonalize), 指数增强F5(AKShare成分+TE约束+指数归因+主动权重), weights完整历史端点, TypeScript types补全(0处as any), 边缘测试(16个), Windows冻结兼容(frozen mode Python探测+进程内fallback+Strategy子类AST去重+agent工具300s/600s超时), ChatPanel AI创建策略竞态修复(aiCreatedFileRef+onCodeUpdate+localStorage清理), V2.9+契约测试补全(CrossSectionalFactor/PortfolioStrategy/Allocator/PortfolioOptimizer共136个), 1500 tests
- **V2.12.2**: 大规模修复 — V2.13 前置深度审查触发的 **codex 六轮外部审查 + Claude reviewer 八轮迭代** 共 55+ bug 修复. 绝大多数修复是跨层级的数据正确性问题, 加上 3 个架构级防御 (PortfolioCommonConfig mixin + 共享 helper + drift-catcher test).
  - **指标公式统一** (CORE 语义变更 ⚠️): 组合引擎 sharpe/sortino/alpha/beta 全部对齐 ez/backtest/metrics.py 标准公式 (原 ann_ret/vol 公式差 30%); profit_factor 改标准 gross_profit/gross_loss (原 avg_win_pct/avg_loss_pct 忽略 position sizing); _sharpe helpers 3 处统一 ddof=1 (原 ddof=0 短 OOS 偏 2.7%); evaluator `or True` 永真 bug + 完整 NaN guard (ic_mean/rank_ic_mean/turnover); walk_forward oos_metrics 用 MetricsCalculator 拼接重算 (原每折 sharpe 平均)
  - **WF 状态隔离**: walk_forward 每折 deepcopy strategy; Runner 分阶段 fresh _resolve_strategy 防 backtest→WFO 跨阶段污染; walk_forward 加 optimizer_factory / risk_manager_factory 每折 fresh 实例
  - **数据层完整性**: DataProviderChain 缓存两阶段 check (boundary 3日容差 + density 75%), 防中间漏 bar 的缓存被当完整; _known_sparse_symbols session 记忆避免 niche ETF 无限 refetch; evaluate-factors / factor-correlation lookback propagation (_max_factor_warmup 传给 evaluator 不止 fetch)
  - **组合引擎**: 清仓写回 equity_curve + dates + weights_history (metrics 不再高估期末持仓); 归因覆盖最后持仓区间 (total_excess = total_return); latest_weights 取最后非空 entry (绕过清仓后 append({})); 成交层 turnover re-check (_lot_round 放大卖侧换手 → risk_event)
  - **市场规则一致性**: 非 cn_stock 不再误用 T+1 (lot_size>1 才包 MarketRulesMatcher, t_plus_1 按 market gate); portfolio engine + portfolio_walk_forward + /search 全部传 t_plus_1; AI tool run_portfolio_backtest_tool market/period/cost_model 完整 gate (含 stamp_tax + min_commission)
  - **注册表唯一性**: Strategy.resolve_class() classmethod 三阶段解析 (exact key → unique name → AmbiguousStrategyName); API 和 Runner 共用, 前端 option value 改 s.key 防 promote 撞名; PortfolioStrategy dual-dict registry (_registry_by_key + _registry) + resolve_class 同逻辑
  - **Agent runtime**: factor 保存后真正 hot-reload (之前仅 stub + NotImplementedError); _run_with_timeout try/finally 清理 executor (之前仅 TimeoutError 分支, 成功路径泄漏线程池); generate_strategy_code allowed_tools 收紧到 strategy-only; research 流水线 batch_timeout + cancel 中间检查 + 跳过 E6 LLM summary; run_experiment save_spec 用 spec.to_dict() 不是 __dict__ (spec_id 是 @property 会 KeyError)
  - **架构级防御** (reviewer round 6-8 核心价值): **PortfolioCommonConfig mixin** 让 PortfolioRunRequest/PortfolioWFRequest/PortfolioSearchRequest 共享 20+ 字段的单一默认值源, 防止 default/constraint drift; **_build_optimizer_risk_factories() 共享 helper** 让 3 个 endpoint 统一构造 optimizer/risk/index + helper_warnings; **test_portfolio_request_config_parity** drift-catcher 比较 defaults + Field metadata 阻止未来 drift; **test_portfolio_walk_forward_surfaces_fallback_events** spy optimizer runtime test 强制验证聚合路径执行
  - **UX 一致性**: ChatPanel fileKey 加 kind 前缀 (防不同类型同名撞会话); BacktestPanel 切 market 重置 benchmark; PortfolioPanel 切策略清空 searchGrid; fullWeights 随 run_id 清空; hot-reload 失败 success=False (不伪装成功); _reload_user_strategy 仅清自己 module (不误删同 stem builtin 或同名跨模块); factor 保存失败 rollback 后重注册旧实现
  - **参数搜索一致性**: SearchConfig 传市场规则字段; PortfolioSearchRequest 继承 mixin 支持 optimizer/risk; /search 聚合 optimizer fallback_events + risk_events (和 /run /walk-forward 对等)
  - **/walk-forward 完整性**: 继承 mixin 支持 optimizer/risk/index; aggregate fallback_events + risk_events 到 response
  - **候选搜索门控**: require_wfo 跟随 run_wfo (原 run_wfo=False + require_wfo=True 全部系统性判失败)
  - **归因 + attribution**: Brinson 归因覆盖最后持仓区间; optimizer fallback 事件记录 + API surface (日期 + reason)
  - **测试强化**: 基本面因子真参数化契约测试 (18 因子 × 12 invariants = 216 独立 cases); evaluator 边界测试 (常数/NaN/单值); walk_forward 参数化边界 + 数据隔离测试; optimizer 奇异协方差 edge case; portfolio 引擎 metrics 和 single-stock byte-identical integration test
  - **⚠️ 非 backward compat**: 5 个指标 (sharpe/sortino/alpha/beta/profit_factor) 公式变更, V2.12.2 之前存入 DB 的历史 run 用旧公式, 之后新 run 用新公式, 无迁移脚本
  - 1500 → 1781 tests (+281)
- **V2.12.2 post-release**: 再一轮 codex + reviewer 挖出 13 bug 并修复 (1781 → 1789 tests, +8 回归测试):
  - **数据正确性**: walk-forward `window_size = n // n_splits` 尾部丢弃 (backtest + portfolio 两处, 改整数区间 `i*n//n_splits`); alpha_combiner `_compute_alpha_weights` 的 `dynamic_lb` 未传给 `evaluate_cross_sectional_factor()` (长 warmup 因子训练窗被 252 默认截断)
  - **注册表 dual-dict 贯彻**: Factor 和 CrossSectionalFactor 补齐 `_registry_by_key` + `_registry` 双字典 + `resolve_class()` + 冲突 warning (对齐 PortfolioStrategy V2.12.1 模式); sandbox 热重载两处 (Factor/CrossFactor) 清理两个字典; reviewer round 发现 3 个 sibling miss 并修复: `alpha_combiner.py` AlphaCombiner pop, `sandbox.py` 因子保存失败 rollback 路径, `code.py` `/refresh` + delete 路由 (新增 `_get_all_registries_for_kind()` 返回 dict list 统一清理)
  - **Portfolio store 上下文完整**: 新增 `config` + `warnings` + `dates` 三列 (ALTER 迁移), `/run` 打包 market/optimizer/risk/index/cost config 持久化; 历史对比图表用真实交易日 time axis 对齐 (legacy 空 dates 行降级 index 轴 + 警告 banner)
  - **前端 market 状态贯通**: PortfolioPanel 原本无 `market` state, 所有 7 个 API 调用点 (run/walk-forward/search/evaluate-factors/factor-correlation/fundamental-fetch/quality) 默认 backend cn_stock; 新增 state + 两个子组件选择器 UI, A 股 T+1/印花税/涨跌停规则不再误加到美股港股
  - **UI 陈旧状态清理**: BacktestPanel + FactorPanel `useEffect` 按 symbol/market/dates/factor 变化清 result/wfResult/trades, 避免用户切标的后看到上一次的指标
  - **ChatPanel 原子文件绑定**: `activeId` 闭包改 `targetId` 捕获防流式中途切换会话串线; AI 创建文件失败 fetch 路径从 "更新 filename 但留旧代码" 改为 "atomic: 要么三项都更新, 要么仅刷新侧栏 + 用户警告" (CodeEditor handler 配套允许 undefined 三元组); fileKey 统一 `${kind}:${filename}` 格式消除重复会话
  - **CodeEditor 删除 kind 校验**: 删除文件时需要 filename **和** kind 都匹配才清编辑器, 避免同名跨类型误清
  - 1789 tests (+8 回归: walk-forward tail drop×2, factor collision×2, portfolio store config/dates×4)
- **V2.12.2 post-release rounds 3-8** (1789 → 1813 tests, +24): 7 轮追加修复 + reviewer 验证 共 36 个新 bug 修复 + 全部通过 `superpowers:code-reviewer` 独立审查:
  - **round 3** (12 bug, `1217705`): 单票 sell_commission_rate 独立字段, portfolio /walk-forward & /search 补齐 optimizer/risk/index 传播, /run trades 不再截断 100, portfolio_store 新增 weights_history 列, PortfolioPanel market 完整 reset, 历史列表 config_summary + warning_count, ChatPanel aiCreatedFileRef 生命周期, 输入变化清 result, trackingError 市场 reset, latest_weights terminal_liquidated flag, multi_select `|` subset 分隔符, 搜索失败 combos surface
  - **round 4** (4 bug, `dbf8d29`): handleLoadFullWeights 改调 /holdings (语义对齐), 历史 run 保留期末清仓 terminal 标记, BacktestPanel mode toggle 清交易标记, portfolio WF OOS sharpe 用拼接曲线 MetricsCalculator 重算 (原折平均 sibling of 单票 WF round 1)
  - **round 5** (6 bug, `8d52236`): 单票回测 terminal liquidation 虚拟 TradeRecord (held-to-end 策略不再 trade_count=0), MetricsCalculator 短样本 NaN guard (n_days>=2), portfolio weights_history 日频 drift 重算 (非调仓日 `holdings × prices / equity` 反映真实漂移), MarketRulesMatcher 整手佣金 min_commission floor 保底, PortfolioPanel 全字段 invalidation (strategyParams/settings/optimizer/risk/index 任何变化都清 result), BacktestPanel params/costSettings/nSplits 变化 invalidation
  - **round 6** (4 bug, `a634a0d` + `a7eacd2`): CodeEditor `committedFilename` state (半成品文件名不驱动 ChatPanel), newFile 跨 kind 允许同名, 单票引擎 prev_weight 按实际成交回算 (整手 lot rounding 后不再误判"已到目标"), pnl_pct `cycle_peak_invested` cost basis (加仓/减仓循环内真实投入资本), reviewer round: terminal liquidation sibling miss + `1e-6`→`1e-3` 阈值防 phantom trade + 测试强度加强
  - **round 7** (3 bug, `1e7944b` + `5744a6c`): compute_significance 常量信号 NaN → 1.0 (前端 isFinite guard), PortfolioRunContent 交易记录 "100+" 过时提示删除, latest_weights 期末清仓标签语义对齐 round 5 daily drift
  - **round 8** (3 bug, `fed9a2f` + `2d58b76`): 回测/WF/搜索异步响应 `runTokenRef` 版本保护 (输入变化时失效在途请求), handleLoadFullWeights `currentRunIdRef` run_id 匹配检查, CodeEditor.deleteFile 改用 `committedFilename` 稳定身份; reviewer follow-up: mode onChange 补 token bump + sidebar 高亮 normalize + 4 个未覆盖 handler 记入遗留项
  - **V2.13 基础设施 shape-level 契约测试** (+8 tests, `tests/test_portfolio/test_v213_readiness.py`): 用 **纯 Python mock** (`_MLShapedFactor` 内部用累积收益模拟"模型") 验证 5 个基础设施契约 — 自重训 stateful CrossSectionalFactor 走通 engine, 严格 anti-lookahead (slice 用 `< target_date`), walk-forward factory 每折 fresh instance, 纯 Python 有状态策略 deepcopy 独立, dual-dict registry 解析动态定义的 ML-shaped factor. **这些不是** sklearn/lightgbm/xgboost 真实模型的 pickle/deepcopy 验证 (那是 V2.13 实施任务). 测试本身是 regression canaries, 防止后续 refactor 破坏 V2.13 依赖的基础设施.
- **V2.13 Phase 1 — MLAlpha Core** (`ez/portfolio/ml_alpha.py`, plan `docs/superpowers/plans/2026-04-06-v213-ml-alpha.md`): 走完 F8 第 1 阶段 (14 原始 tasks + **6 轮独立审查**: AI reviewer ×3 + codex ×3, 共 60+ bug fixes / polish). 实现 walk-forward ML 因子框架 `MLAlpha(CrossSectionalFactor)`.
  - **构造器安全**: callable 校验 (model_factory / feature_fn / target_fn) + `model_factory()` 双次 probe (whitelist + singleton 检测: 比对 `id()` 防止工厂缓存单例破坏 fold 隔离) + probe 异常包装为 `TypeError` (配置错立即失败).
  - **V1 estimator whitelist**: 严格 7 类 (Ridge/Lasso/LinearRegression/ElasticNet/DecisionTreeRegressor/RandomForestRegressor/GradientBoostingRegressor), `n_jobs=1` via `getattr(instance, 'n_jobs')` 运行时检查, `type(instance)` 身份比对 (非 isinstance) 阻止子类绕过. `_assert_supported_estimator` 在 `__init__` (probe1 + probe2) 和每次 `_retrain()` 都执行.
  - **反 lookahead (两层)**: (1) `slice_universe_data` 上游严格 `<` 切片; (2) `_build_training_panel()` **positional purge (交易日单位 `iloc[:-purge_bars]`)** — round 1 reviewer 发现原 `timedelta(days=N)` 日历日让 label 穿过周末指向预测窗口 (C1 bug), 改为行单位匹配 `shift(-k)`.
  - **warmup 传播链 (round 5 codex-H)**: `TopNRotation` / `MultiFactorRotation` 加 `factor` / `factors` 公开 property + 覆盖 `lookback_days = max(252, factor.warmup_period + 20)`, 让 engine 既存 warmup 检查开始工作. `MLAlpha.__init__` 新增 `feature_warmup_days` 参数 (round 6 reviewer-I1) — 用户的 feature_fn 若有 `rolling(N)` warmup 应声明, 否则 runtime shortfall detection 在实际行数 < `train_window × 0.9` 时发一次性 warning.
  - **状态隔离**: portfolio walk-forward 走 `strategy_factory()` 每折 fresh instance (NOT `copy.deepcopy`); 单票 WF 走 deepcopy — MLAlpha 两条路径都支持. factory singleton detection 防止工厂缓存同一 estimator.
  - **feature schema 防御 (round 5 codex-H)**: `_retrain` 成功后记录 `_trained_feature_cols`, `_predict` 里比对列名 + 顺序 — 同集合不同顺序自动 reorder + warn; 不同集合 skip + warn. 同时验证 feature_fn / target_fn 输出的 index 是 DatetimeIndex + 单调递增 (乱序自动 sort + warn, 非 DatetimeIndex skip + warn).
  - **dtype 防御 (round 3-4)**: `_NUMERIC_DTYPE_KINDS = {'f','i','u','b'}` 主动白名单 dtype.kind — datetime64/timedelta64 会被 `np.asarray(dtype=float)` 静默 coerce 到 ~1.65e18 nanosecond-epoch, 先拒再 try/except 兜底. 非数值 (object/string) dtype 也在 asarray 之前被主动白名单拒.
  - **容错 + 可诊断性**: 15 个一次性 warning flag 覆盖: type mismatch (训练+预测各 2), fn exception (训练+预测各 2), model.predict exception, 空 panel, 空 predict, 非数值 dtype, feature index 非法, 乱序 index, feature schema drift, train shortfall. `model_factory()` 和 `model.fit()` 在 retrain 时都 wrap + log + 保留上一次模型. `inf` 特征在 fit 前 `np.isfinite` 过滤.
  - **end-to-end 验证**: **Ridge + RF + GBR** 三个 estimator 都有 `run_portfolio_backtest` + `portfolio_walk_forward` 两条路径集成测试 (含 factory-freshness 断言). Deepcopy round-trip 独立验证. Lasso/LR/EN/DT 4 个 estimator 只有单元层 whitelist acceptance 测试 (没有单独的 portfolio 集成测试, 依赖 sklearn API 一致性).
  - **CI 兼容**: `pytest.importorskip("sklearn")` 让没装 `[ml]` 的环境 graceful skip 整个 ml_alpha 测试模块 (1813 passed / 12 skipped / exit 0).
  - **用户 API**: `ML_ALPHA_TEMPLATE` 字符串 (含 `feature_warmup_days` 参数 + 白名单限制文档), `UnsupportedEstimatorError` 公开异常, 三个符号从 `ez.portfolio` 顶层 export. MLAlpha 基类不自动注册 (dual-dict pop).
  - **测试覆盖**: **123 个新测试** (`tests/test_portfolio/test_ml_alpha.py` 106 + `test_ml_alpha_sklearn.py` 17). 覆盖: 构造器 callable/validation/whitelist (28) + lazy retrain/purge+embargo (10) + anti-lookahead outlier+boundary (5) + determinism (3) + cache (2) + feature error handling+mixed partial failure (6) + fit exception/inf/non-numeric/datetime64 (11) + contract edge cases (17) + template (3) + package exports (3) + sklearn deepcopy Ridge/RF/GBR (7) + cross-instance determinism (2) + end-to-end backtest+walk_forward Ridge/RF/GBR (10) + warmup propagation (3) + factory freshness (2).
  - **依赖**: `scikit-learn>=1.5` (新增 `[ml]` optional group, 与 numpy>=2.0 ABI 兼容). 1813 → 1936 tests (+123).
- **V2.13 Phase 2 — MLDiagnostics** (`ez/portfolio/ml_diagnostics.py`, plan `docs/superpowers/plans/2026-04-06-v213-phase2-ml-diagnostics.md`): F9 overfitting 检测. 用 Option C (fresh instance + polling via `config_dict()` + `diagnostics_snapshot()`) 驱动. 4 个诊断指标:
  - **Feature importance stability**: 每次 retrain 采集 `coef_` / `feature_importances_`, 计算 per-feature CV. CV > 2.0 警告不稳定特征.
  - **IS/OOS IC decay**: IS IC 用 `_build_training_panel` + `model.predict` + spearmanr; OOS IC 用后续 `min(max(retrain_freq, 21), 42)` 自适应窗口内的 factor scores vs forward returns. `overfitting_score = max(0, (IS - OOS) / |IS|)`.
  - **Turnover analysis**: top-N symbols retention rate 跨 rebalance. `avg_turnover = 1 - mean(retention)`.
  - **Verdict**: `DiagnosticsConfig` 参数化阈值 (severe_overfit=0.5, mild_overfit=0.2, high_turnover=0.6), 生成 human-readable warnings.
  - `DiagnosticsResult.to_dict()` JSON-serializable (numpy 标量自动转换).
  - **21 tests**, 覆盖 skeleton/cadence/importance/IC/turnover/verdict/config/e2e-JSON. 1942 → 1963 (+21).
- **V2.13 Phase 3 — StrategyEnsemble** (`ez/portfolio/ensemble.py`, plan `docs/superpowers/plans/2026-04-06-v213-phase3-strategy-ensemble.md`): D5 multi-strategy composition-layer heuristic orchestrator (**NOT** a statistical meta-optimizer). 4 modes: `equal` (exact) / `manual` (exact) / `return_weighted` (proxy, not IC) / `inverse_vol` (proxy, not risk parity). Sub-strategy `copy.deepcopy` at construction (ownership isolation). Hypothetical-return ledger in `self.state` (pure dict/list/float). Combination Formula: cash intent preserved, exception ≠ no-signal, per-sub one-shot warning. `correlation_warnings` (warn-only, Pearson, structured payload `{sub_i, sub_j, correlation, n_samples}`). Nested ensembles: "only leaf adds buffer" lookback rule, inner state isolated. Registry popped (Python-only, no dropdown). **38 tests**, 覆盖 skeleton/validation/deepcopy/equal/manual/ledger/return_weighted/inverse_vol/warmup-dual-gate/correlation/nesting/e2e-backtest/e2e-walk-forward/all-empty-cash/exception-vs-no-signal/nan-inf-weights/defensive-copy/corr-threshold-range/sub-output-nan-filter. 1964 → 2002 (+38).
- **V2.13 Phase 4 — Sandbox `ml_alpha` kind** (`ez/agent/sandbox.py` + `ez/api/routes/code.py`): F7 最小可用入口. `_KIND_DIR_MAP` 新增 `"ml_alpha" → ml_alphas/` 目录. `get_template("ml_alpha")` 用 `ML_ALPHA_TEMPLATE` 生成含 Ridge + feature_fn + target_fn 的完整文件. `_reload_portfolio_code` 和 `_run_portfolio_contract_test` 路由 `ml_alpha → cross_factor` 分支 (MLAlpha IS CrossSectionalFactor). `_get_all_registries_for_kind("ml_alpha")` 返回 CrossSectionalFactor 双字典. 7 sandbox tests. 1964 → 2009 (+7 sandbox + Phase 3 的 38).
- **V2.13.1 Phase 5 — API endpoints** (`ez/api/routes/portfolio.py` + `ez/portfolio/loader.py` + `ez/api/routes/code.py`): 补全 REST API 层. `load_ml_alphas()` startup scan 让 `ml_alphas/` 在服务重启后自动注册. `/refresh` 完整清理+重载 ml_alpha 条目. `/template` + `/files` 支持 ml_alpha kind. 新增 `POST /api/portfolio/ml-alpha/diagnostics` endpoint (resolve class → instantiate → fetch data → MLDiagnostics.run → to_dict). 错误处理: sklearn 缺失→422, class 未找到→404, 非 MLAlpha→422, instantiation 失败→422, data fetch 失败→502. 6 tests (含 mocked happy path). `/evaluate-factors` 天然兼容 MLAlpha (无代码修改, 只需 loader scan).
- **V2.13.2 — Frontend Phase 6 + Backend Polish** (核心交付完成, legacy G4.1/G4.2 + 扩展 G5 明确延期到独立 PR/V2.14): CodeEditor `+ ML Alpha` 按钮 + sidebar group + TS types (`DiagnosticsResult`/`MLDiagnosticsRequest`) + typed API client. PortfolioPanel ML Alpha 因子分类 (backend 自动 categorize via `issubclass(MLAlpha)`). PortfolioFactorContent ML 诊断面板 (verdict badge + IC chart + importance table + warnings). /registry 5th category. stamp_tax market gate (`model_fields_set`). alpha_combiner dynamic window. strict_lookback option. Race token 全覆盖 (3 handlers). Strategy deepcopy doc. Dead code cleanup. _predict None warning. 4 test additions. G4.1 (bool/enum search) + G4.2 (power-set cap) deferred. G5 (LightGBM/XGBoost) deferred to V2.14.

- **V2.14**: 搜索增强 + ML 扩展 + Ensemble UI — 实现 V2.13.2 延期的 G4.1/G4.2/G5 + 新增 B3 Ensemble:
  - **B1 Bool/Enum 参数搜索** (G4.1): `CandidateSearch.tsx` 重构为 discriminated union (NumericParamRange/BoolParamRange/EnumParamRange), bool 参数显示 checkbox pair, enum/select 显示按钮组. 后端 `ParamRangeRequest.values` 和 `ParamRange.values` 放宽为 `list[int|float|str|bool]`. 后端 grid/random search 已经是类型无关的, 零后端逻辑改动.
  - **B2 multi_select 组合搜索** (G4.2): PortfolioPanel "组合搜索" checkbox → bitmask 生成所有 2^N-1 非空子集. 64 上限硬限 (>6 因子禁用按钮 + handleSearch defense). 原 `|` 分隔模式保留. searchGrid/comboSearch 变化自动清搜索结果.
  - **B3 StrategyEnsemble UI**: 新 `EnsembleBuilder.tsx` 组件 (4 mode radio + 子策略卡片 + 手动权重 + 高级设置). 后端 `_create_strategy` 新增 Ensemble 分支 (列表格式 `sub_strategies: [{name, params}, ...]` 避免同名 key 冲突). `/strategies` endpoint 追加 Ensemble 元信息 (`is_ensemble: true`). 搜索按钮在 Ensemble 模式下隐藏. 6 个 API 测试.
  - **B4 LightGBM/XGBoost 白名单** (G5): `_build_supported_estimator_set` 可选加载 `LGBMRegressor` + `XGBRegressor` (仅 regressor, classifier 待定义分类契约). GPU 拦截 (tree_method + device + device_type). 白名单不缓存 (每次 rebuild, 支持运行时安装库). `pyproject.toml` 新增 `[ml-extra]` group. 补齐 V1 sklearn 测试缺口 (Lasso/LR/EN/DT deepcopy + GBR cross-instance). 12 个新测试.
  - **异步 loading 状态机统一**: 7 个 PortfolioPanel handler + PortfolioFactorContent diagnostics 全部统一为 "token-driven loading lifecycle" 模式: finally 条件清 (`if token === myToken`) + invalidation effect 同步清 loading. 消除 superseded 请求覆盖新请求 loading 和 loading 卡死两类 bug.
  - **陈旧结果清理**: run-input 变化清 compareData, market 变化清 searchMeta, searchGrid/comboSearch 变化清 searchResults.
  - 2028 → 2054 tests (+26: 6 ensemble API + 12 ML sklearn gap-fill/lgbm/xgb + 2 GPU rejection + 6 existing).

- **V2.15**: Paper Trading Bridge — 模拟盘部署生命周期 (research → deploy → paper trade → monitor):
  - **DeploymentSpec** (不可变, content-hash): 策略配置快照, SHA-256[:16] content-addressed ID, sorted symbols/params 保证幂等, 完整交易成本+市场规则字段, optimizer/risk 可选
  - **DeploymentRecord**: 可变运行时记录, 6 状态状态机 (pending→approved→running⇄paused→stopped/error), 门控结果/时间戳/错误计数
  - **DeploymentStore**: DuckDB 持久化 (deployment_specs+deployment_records+deployment_snapshots 3 张表), 每日快照 (equity/holdings/trades/risk_events)
  - **DeployGate**: 不可跳过的 10 项硬检查 (4 阶段: 来源存在→研究指标→WF 指标→部署专属), 比 ResearchGate 更严格 (sharpe>=0.5, dd<=25%, trades>=20, p<=0.05, overfit<=0.3, days>=504, symbols>=5, concentration<=40%, require_wfo)
  - **PaperTradingEngine**: 日线驱动仿真执行, 复用 PortfolioStrategy.generate_weights + execute_portfolio_trades + CostModel + MarketRules, 从 DataProviderChain 获取实时数据
  - **Scheduler**: 单进程幂等调度器, asyncio.Lock 串行, per-deployment last_processed_date 防重复, per-market TradingCalendar 非交易日跳过, 连续 3 错误→error 状态自动升级, resume_all() 进程重启恢复
  - **Monitor**: 健康仪表板 (cumulative_return/max_drawdown/sharpe/today_pnl/risk_events/consecutive_loss_days) + 预警规则 (回撤>20%/连续亏损>5天/执行停滞/错误累积)
  - **API** (13 端点): POST /deploy, GET /deployments, GET /deployments/{id}, POST /approve, POST /start, POST /stop, POST /pause, POST /resume, POST /tick, GET /dashboard, GET /snapshots, GET /trades, GET /stream (SSE)
  - **前端**: PaperTradingPage (部署列表+权益曲线+指标+交易记录+控制面板), Navbar "模拟盘" tab, PortfolioRunContent "部署到模拟盘" 按钮
  - **execution 模块抽取**: execute_portfolio_trades() 从 PortfolioEngine 提取到 ez/portfolio/execution.py, 回测+模拟盘共享
  - **RiskManager.replay_equity()**: 从历史权益曲线恢复风控状态 (peak/drawdown), 支持 resume 场景
  - **TradingCalendar.from_market()**: 类方法, 按 market 字符串构造日历实例
  - **DocsPage Ch15 模拟盘**: 部署流程/门控/调度/监控/已知限制
  - 2054 → 2226 passed tests (+172), 2236 collected

- **V2.15.1**: Stability — 模拟盘从 experimental 推向 beta:
  - **S1 server-side WF metrics**: `portfolio_runs` 新增 `wf_metrics` 列. `/walk-forward` 端点接收 `source_run_id` → 自动将 WF 指标 (p_value/overfitting_score/oos_sharpe) 写回 source run. DeployGate 从 DB 读 WF 指标, 不再信任客户端传入. 消除 V2.15 的 wf_metrics 信任边界.
  - **S2 恢复回归测试**: 3 个 restart recovery 测试 — error 快照不污染恢复, _last_prices 从 weights+equity+holdings 重建, risk_manager.replay_equity 恢复回撤状态机.
  - **S3 DeploymentStore 独立连接**: 不再共享 `get_store()._conn`, 自建 DuckDB 连接到同一 DB 文件.
  - **S4 resume_all 加锁**: `asyncio.Lock` 覆盖 resume_all + start/pause/resume/stop/tick 全部 6 个入口.
  - **S5 _is_rebalance_day 缓存**: 首次计算后缓存 rebalance date set, 后续 O(1) 查找.
  - 2226 → 2229 tests (+3 recovery regression)
- **V2.16**: Platform Polish — 可靠性 + 集成测试 + 文档:
  - **AKShare raw fetch 重试**: 原始价格 fetch 失败后自动重试 1 次再降级到 adj_close fallback (减少因瞬时网络错误导致的数据质量降级)
  - **stop_deployment liquidate 选项**: Scheduler.stop_deployment 新增 `liquidate: bool = False` 参数, True 时先用空权重触发 execute_portfolio_trades 平仓所有持仓, 保存清仓快照后再停止; API 层 `/stop` 端点接受 `?liquidate=true` query param
  - **DocsPage API 参考**: 新增 13 个模拟盘 API 端点到 Ch8 API 参考
  - **集成测试 5 个**: test_factor_contract_all_builtins (所有注册因子计算契约), test_backtest_with_market_rules (T+1/整手/印花税), test_walk_forward_determinism (WF 确定性), test_portfolio_backtest_with_optimizer (MeanVariance 优化器), test_full_pipeline_research_gate (回测→WF→门控→裁定)
  - 2229 → 2234 tests (+5 integration)

- **V2.16.1**: Stability — 前端深度打磨:
  - **Toast 通知系统**: 新建 `ToastProvider` + `useToast` hook, 34 处 `alert()` 替换为非阻塞 toast (success/error/warning/info), 4s/8s 自动消失, fadeIn 动画
  - **TypeScript `any` 清零**: components/ + pages/ 全部 `.tsx` 文件 0 处 `any` (从 65+ 降至 0). 新增 20+ 接口定义 (EvalFactorResult, CorrResponse, WalkForwardResult, SearchResultRow, FactorCategory, RegistryEntry, ChartMarker 等)
  - **silent catch 清零**: 所有 `.catch(() => {})` 替换为 toast 错误反馈, 包括策略列表/历史记录/因子列表/设置加载等初始化路径
  - **表单验证**: 日期范围 (start < end) 校验, WF nSplits (2-20) 校验, ResearchPanel 表单 streaming 时 disable, ExperimentPanel 清理天数输入校验
  - **状态一致性**: ResearchPanel 文件加载失败清空旧任务数据, SettingsModal 保存/刷新分离 (刷新失败不覆盖保存成功)
  - **UI 视觉**: 表格斑马纹, 视觉层次 (标题 text-base semibold), 图表高度统一, 响应式侧边栏, SettingsModal boxSizing, 研究事件左侧蓝色边线
  - **Navbar 版本号动态化**: 从 /api/health 读取版本 (不再硬编码)
  - **Windows 兼容**: 所有 `read_text()` 加 `encoding="utf-8"`, LightGBM/XGBoost 测试 skip 用 `except Exception` (捕获 OSError)
  - **CI 修复**: macOS `brew install libomp` for LightGBM
  - **chartTheme 统一**: 新建 `shared/chartTheme.ts`, 所有 ECharts 组件 (KlineChart/BacktestPanel/FactorPanel/PaperTradingPage/PortfolioRunContent/PortfolioFactorContent/PortfolioHistoryContent) 的 ~100 处硬编码颜色迁移到 `CHART.*` 常量. MA60 从绿色改为青色 (避免与跌色混淆)
  - **全局样式增强**: global.css 新增暗色滚动条 + focus-visible 蓝色焦点环 + ::selection 蓝底白字 + disabled cursor + button hover brightness
  - **组件视觉**: EnsembleBuilder radio→styled buttons, PortfolioHistoryContent 选中行 accent 左边框, 模态框背景→var(--bg-secondary), 斑马纹 opacity 0.02→0.04, BacktestSettings padding 对齐, DocsPage h3 颜色变量化

- **V2.17**: QMT 策略严格移植 + 引擎增强 + AI Agent 能力 + UX 打磨:
  - **QMT 策略严格 1:1 移植** (3 个生产策略从 QMT 5分钟回测源码完整移植):
    - `EtfMacdRotation` V1.2 — ETF动量轮动+周线MACD过滤, QMT `calc_rotate_signal` 完整还原: 2日均线+20日ratio+二次归一化+exp加权+>75%负收益跳过exp+周线MACD(iloc[:-1]排除当周)+top_n等权+0.987现金保留+ordered list比较(同选股同排序=不交易)
    - `EtfSectorSwitch` — ETF加权行业宽基切换, QMT `calc_com_signal` 完整还原: 多窗口斜率(5/7/21d)+`_remove_outliers_and_refit()`加权线性回归(QMT特殊weights[-2]=weights[-3]=weights[-1])+MSE归一化+动态alpha(0.15→2.5→3.5→6.5→1.5→25)+累积投票cW(最近2期加权)+惩罚机制penaltyW[0.5,1.0]+宽基/行业切换
    - `EtfRotateCombo` — 轮动加多组合V1.2, QMT双日程: 周四轮动(10只ETF MACD排名, top_n select)+周五加权(21只ETF行业/宽基切换, sector/broad scoring), `DEFAULT_BROAD_ETFS`(10)+`DEFAULT_SECTOR_ETFS`(11)+`DEFAULT_COM_SYMBOLS`(22)+`DEFAULT_ROTATE_SYMBOLS`(10)
  - **引擎增强 (engine.py)**:
    - `generate_weights` 返回 `None` → 跳过调仓但仍记录日权益/日期/权重 (critical: 修复前 None 导致 equity_curve 缺失, 年化收益从 18% 膨胀到 1043%)
    - `use_open_price` 参数: 交易用 open, 权益跟踪用 close (QMT 5分钟兼容)
    - `skip_terminal_liquidation` 参数: 跳过期末强平 (QMT 无期末清仓)
    - `rebal_weekday` 参数: 按星期几调仓, 4级fallback (exact > next-after > last-before > last-of-week)
    - `_sym_data` 5元组: (dates, adj_close, raw_close, open, date_set) — 支持 raw close 和 open price
    - **critical fix**: 价格carry-forward bug — `elif` 误接 `use_open_price` 条件, 导致 `use_open_price=False` 时每天 `prices[sym]` 被 `prev_prices[sym]` 覆盖, 权益曲线恒定. 改为 `if sym not in prices` 仅在 adj/raw 均 NaN 时 fallback
    - `exec_prices` 与 `prices` 分离: 交易执行用 open (或 close), 权益追踪始终用 close
  - **一键预设 (Live Presets)**:
    - PortfolioRunContent 3张预设卡片 (MACD轮动/行业切换/组合), 点击加载策略+参数+标的池+日期
    - 策略类属性 `broad_etfs`/`sector_etfs`/`rotate_etfs` 自动注入 API symbols
    - `pendingPresetParams` useEffect-based deferred hydration (替换 setTimeout)
  - **AI Agent 能力增强**:
    - `create_ml_alpha` 专用工具 (14 tools total): LLM 可通过 tool calling 创建 ML Alpha 因子
    - 系统 prompt "操作边界" 规则: 严禁 AI 自动执行删除/回测/部署等操作, 必须用户确认
    - ML Alpha 创建指南: 含模板 + 白名单限制 + 完整 feature_fn/target_fn 示例
    - 研究管线质量增强: E1/E2/E4 prompt 加入高表现策略模式 (连续信号/多因子/趋势过滤/波动率自适应) + 常见错误 + 逐策略诊断, best sharpe 从 0.36 提升到 0.72
    - 研究输入校验: goal min_length=1 + start_date >= end_date 422 拒绝
    - analyzer MagicMock 容错: `float()` cast + try/except 防测试中 format error
  - **UX 打磨**:
    - 搜索结果可排序: 点击表头排序 (asc/desc/原始), max_drawdown abs 比较, null 方向感知
    - WF 指标颜色提示: sharpe/p_value/overfitting_score 按阈值着色 + 中文提示
    - 组合回测指标颜色: rateMetric/rateWfSharpe 等 6 个分级函数 + 中文一句话建议
    - WF 折数/比例输入: tooltip + 实时摘要 ("5折, 训练比例 0.80 → 80%训练/20%测试")
    - IC/ICIR 颜色评级: 共享 `metricRatings.ts` (rateIc/rateIcir), 因子面板+组合面板统一
    - 搜索排序提示: 表头显示当前排序状态
  - **因子研究修复**:
    - ETF 基本面数据警告: ETF 无基本面数据时前端 toast 提示
    - 4 个验证 guard: 日期选择器空值/无因子/无标的/ETF 基本面不可用
  - **模拟盘修复**:
    - PaperTradingPage 审批黑屏: DeployGate 返回 400 时 `detail` 为 object `{message, verdict}`, `showToast` 收到 object → React 崩溃. 修复: typeof 检查 + 提取 message 字符串
    - 部署列表加载优化: DeploymentStore 连接改独立 + thread lock
    - rebal_weekday 持久化: run_config + DeploymentSpec 新增字段 + calendar fallback 修复
  - **代码质量**:
    - `_get_raw_close()` helper: QMT 策略使用 raw close (非 adj_close)
    - `_weekly_macd_signal()`: `weekly.iloc[:-1]` 排除当前/不完整周
    - shared `metricRatings.ts`: rateIc/rateIcir 单一真相源
    - ChatPanel 14 tools 完整中文标签
    - tool registry test 收紧: `>= 8` → `>= 14`
  - 2234 → 2252 tests (+18)

- **V2.18**: Parquet Data Cache — 预构建本地数据仓库, 回测零 API 调用:
  - **Parquet 优先查询**: `DuckDBStore.query_kline()` 和 `query_kline_batch()` 先查 `data/cache/{market}_{period}.parquet`, 命中后不触发 DuckDB 和 API. C4 日期守卫防过期循环 (manifest end + 7天 grace)
  - **批量下载脚本**: `scripts/build_data_cache.py` — Tushare 按 trade_date 批量拉股票 + `fund_daily` + `fund_adj` 拉 ETF (正确前复权) + `index_daily` 拉指数 (market="cn_stock" 匹配 benchmark 查询)
  - **交叉验证门控 (C1)**: 比较日收益率 vs AKShare, >1pp = ERROR 中止构建, 不写 parquet. ETF-only 也验证
  - **覆盖完整性门控**: 所有 required ETF + 指数必须全部存在, 缺失即 `sys.exit(1)`
  - **ETF 正确前复权**: `fund_adj` API 获取 ETF 复权因子, `adj_close = close × factor / latest_factor` (非 close=adj_close)
  - **周月线推导**: 从日线 resample (W-FRI/ME), 不单独调 API, 保证三频率一致
  - **Release 打包**: CI 自动生成种子 ETF 数据 (25 ETF + 5 指数, ~1.4MB), 随发布包. 全 A 股用户跑脚本 (~10 分钟)
  - **数据源**: Tushare (股票+ETF+指数, 主) → AKShare (ETF fallback). BaoStock 不支持 ETF K 线数据 (已验证)
  - 2252 → 2259 tests (+7 parquet cache tests)

- **V2.18.1**: 引擎分红处理 + Tushare fund_adj 数据质量修复 (2 个相关问题):
  - **引擎 bug: use_open_price=True 用 raw_close 估值导致长期持有策略低估** (`ez/portfolio/engine.py`):
    - 问题: V2.17 引入 `use_open_price=True` (QMT 5 分钟兼容模式) 用 raw_close 做每日估值, 但引擎没有分红账务处理. ETF/股票分红日 raw_close 跳变 -50% 时被当成亏损, 实际分红以现金形式入账, 总资产不变. 长期持有策略被系统性低估 (StaticLowVol 年化收益 **-0.5% vs +13.9%**, MDD **-58% vs -15.8%**)
    - 修复方案 A (统一 adj 单位系统, `docs/core-changes/2026-04-10-engine-dividend-fix.md`):
      - 估值**永远**用 `adj_close` (不管 use_open_price 参数)
      - use_open_price=True 时交易价格用 `adj_open = open × (adj_close / close)` (复权 open)
      - benchmark 曲线已经优先 adj_close (无需修改)
      - raw_close 保留用于涨跌停判定 (市场规则必须用真实价格)
    - 影响: 默认行为 (use_open_price=False) 完全不变. use_open_price=True 的高换手 QMT 策略非分红日结果不变, 分红日才会更准 (不是更差). 长期持有策略从此正确处理分红
    - **测试**: 新建 `tests/test_portfolio/test_engine_dividend_handling.py` (6 个测试: equity 连续性/use_open 一致性/adj_open 计算/benchmark 追踪/长期持有/双模式对比). 合成数据验证修复正确
  - **数据 bug: Tushare fund_adj API 返回的 adj_factor 在某些日期异常**:
    - 问题: 57 个 ETF 的 131 个日期的 adj_close 有数据异常, 两类 pattern:
      - **Type A — 单日 factor spike**: 2020-09-18 等日期 Tushare 对多个 ETF 批量返回 `adj_factor=1.0` (应为 0.488), 导致 adj_close 突然跳 +100% 再跳 -50% (512890/510030/510150/510170/510210/510580 等)
      - **Type A — 历史初期截断**: 159901.SZ 2018-01-04 之前 factor=1.0 (错), 2018-01-05 降到 0.4882 (对). Tushare 对某些早期数据的复权因子返回了 raw 值
      - **Type B — raw 异常**: 512100 等 ETF 在某个日期 raw_close 突然 scale 变化 (+176% 不是真实市场事件). Tushare 对 raw 数据本身的 bug
    - 修复工具: `validation/fix_adj_close_anomalies.py` 和 `validation/fix_adj_close_v2.py`
      - 规则 C: 历史初期 factor≈1.0 连续段 → 用后续稳定 factor 反向重算
      - 规则 B-adj: adj_close daily return > 15% 但 raw_close < 10% 变动 (严格 Type A) → 用 raw ret 修复 adj_close. **不做双日 spike fallback, 避免误修真实涨停+跌回**
      - 规则 B-raw (Tencent 重建): 检测到 raw_close > 50% 单日变动持续, 用 Tencent qfq API (`ak.stock_zh_a_hist_tx`) 作为 ground truth 重建整个序列
    - **持久化**: `scripts/build_data_cache.py` 加 `sanitize_adj_close()` 函数集成到 build 流程, 未来 rebuild parquet 会自动处理 Type A anomalies (Type B 需要离线跑 v2 脚本)
    - **多源独立验证**: `validation/verify_parquet_multi_source.py` 用 Tencent qfq + Sina raw 双数据源交叉验证修复后的 parquet
  - 2259 → 2265 tests (+6 dividend handling tests)

- **V2.20.0**: ez.research — 研究工作流编排框架 (P1-A MVP)
  - **动机**: validation/ 目录有 30+ phase scripts (phase_o_nested_oos, phase_p_walk_forward, phase_q_paired_bootstrap...), 每个都重新实现 data load, runner wiring, metrics, report. 痛点是重复造轮子, 无法跨研究复用. ez.research 把这些抽象成 first-class pipeline + step components.
  - **新模块 `ez/research/`**: PipelineContext (config + artifacts + audit history), ResearchStep ABC, ResearchPipeline orchestrator, StepRecord audit, StepError wrap. Pipeline 顺序执行, 任一 step 抛异常 → 包装为 StepError + 立即停止, history 完整记录 success/failed
  - **3 个 V2.20.0 MVP steps** (`ez/research/steps/`):
    - **DataLoadStep**: 复用 `ez.api.deps.fetch_kline_df`, 多 symbol 批量加载, 单股失败 skip 不中断, 全失败 raise. 支持 str/date/datetime 自动 coerce + config 默认值 fallback. 写 `artifacts['universe_data']` (dict[symbol → DataFrame])
    - **RunStrategiesStep**: 接受 `{label: Strategy_instance}` (label 是 universe_data 的 symbol key), 复用 `ez.backtest.engine.VectorizedBacktestEngine` 跑每个 strategy. 单策略 crash skip 不中断, 全失败 raise. 写 `artifacts['returns']` (DataFrame[date × label]), `metrics`, `equity_curves`, `run_strategies_skipped`
    - **ReportStep**: 纯 Python f-string + dict 渲染 (零 jinja 依赖), `default_template` 输出 markdown (title + config + metrics table + returns sample + audit log + warnings sections), 支持 custom `template_fn` callable, 可选 `output_path` 落盘
  - **Lazy import**: DataLoadStep 和 RunStrategiesStep 内部 lazy import `ez.backtest.engine` / `ez.api.deps`, 让 `ez.research` 在轻量环境可被 import
  - **Pipeline audit**: `_run_guards` 自动 snapshot artifact diff 计算 written_keys, StepRecord 包含 duration_ms / status / error / written_keys
  - **测试**: 48 unit + integration tests (13 pipeline + 12 data_load + 8 run_strategies + 11 report + 4 e2e). 所有 test strategy/factor 用 **duck typing** (不继承 `Strategy`/`Factor`), 避免污染 global registry — 否则 `test_strategy_contract.py` 会自动 discover 测试用的 `_RaisingStrategy` 并 crash. Engine 接受的是 duck-typed object, 不做 isinstance check.
  - 2343 → 2391 tests (+48: 13+12+8+11+4)

- **V2.20.1**: ez.research — Optimizer ABC + NestedOOSStep (P1-A 延伸)
  - **动机**: V2.20.0 只是框架骨架,没有真正的 research step。V2.20.1 加了 optimizer 层 + NestedOOSStep,替代 validation/phase_o_nested_oos.py 的 411 行 (~25 行 pipeline declaration)
  - **新模块 `ez/research/optimizers/`**: Optimizer ABC, Objective ABC, OptimalWeights frozen dataclass
    - **5 内置 objectives**: `MaxSharpe` / `MaxCalmar` / `MaxSortino` / `MinCVaR(alpha)` / `EpsilonConstraint(objective, metric, op, value)`
    - `EpsilonConstraint` 支持 string DSL (`"0.9*baseline_ret"`) 和 callable (`lambda b: 0.9 * b["ret"]`), 底层用 custom AST visitor safe-eval (只允许 float/int/baseline_* + * / 运算)
    - **`SimplexMultiObjectiveOptimizer`**: scipy.differential_evolution wrapper, **stick-breaking (Dirichlet) 参数化** — 在 `[0,1]^N` 无约束空间优化映射回 simplex (`w_k = x_k * (1-sum(w_1..w_{k-1}))`), 100% feasible 初始化对任意 N. Cash = 1-sum(w). 优化 N 个 objectives, 返回 `list[OptimalWeights]`
    - `res.success` 三态: converged / max_iter / infeasible. `is_feasible` 接受 converged + max_iter
  - **新 `ez/research/_metrics.py`**: `compute_basic_metrics(returns)` + `compute_cvar(returns, alpha)` — thin wrapper around `ez.backtest.metrics.MetricsCalculator`, 返回 short-key dict (`ret`/`sharpe`/`sortino`/`vol`/`dd`/`mdd_abs`/`calmar`)
  - **新 `ez/research/steps/nested_oos.py`**: `NestedOOSStep` — IS slice → optimize → OOS validate → baseline compare. 接受 optimizer + `baseline_weights` + `baseline_label`. anti-overlap 验证, stale artifact 自动清理. 输出 `artifacts['nested_oos_results']` (candidates + baseline_is + baseline_oos)
  - **`RunStrategiesStep` 增强**: `label_map` 参数解耦 label 和 symbol (`{"A": "EtfRotateCombo_proxy"}`)
  - **`ReportStep` 增强**: 渲染 `nested_oos_results` candidates table (Objective / Status / IS Sharpe / IS Ret / OOS Sharpe / OOS Ret / OOS MDD + baseline row)
  - **`ResearchPipeline` 增强**: `run(reset=True)` 清理复用 context; failure 路径 `StepRecord.written_keys` 记录 partial mutation; `StepError.context` 携带 partial state (只 backfill 不 clobber nested)
  - **9 轮 review 加固** (Claude reviewer ×5 + codex external ×4):
    - V2.19.0 guard framework: C1 in-place mutation 两层防御, LookaheadGuard 3-run + 7-probe + 5-run preflight, drop_probe_module dual-dict last-write-wins restore, _run_guards _reload_lock thread safety
    - Sandbox security: `_FORBIDDEN_FULL_MODULES` + AST `ImportFrom` submodule check + AST attribute chain reconstruction + **name binding table** (Import/ImportFrom/Assign/NamedExpr 跟踪 alias/rebinding/walrus). **Known limitation**: dynamic binding (`[ez][0]`, `ident(ez)`, `for z in [ez]`) 不可 static 覆盖, Lock (not RLock) 让攻击 loud, V2.21 architectural fix (closure-capture lock) 待做
    - DataLoadStep: `None` sentinel (不是 `"cn_stock"` 默认值), `isinstance(symbols, (str, bytes, bytearray))` 拒绝, `is not None` 统一 5 参数
    - Pipeline: `isinstance(returned, PipelineContext)` 严格验证, `prev_ctx` fallback on bad return, `partial_written` in failure path
    - Report: `_md_escape()` 对 `|`/`\n`/backtick 统一 escape (tables + warnings + config + audit)
    - Optimizer: stick-breaking 替代 N-dim direct (1/N! feasibility → 100%), `res.success` 三态, callable EpsilonConstraint + non-zero stub validation
  - **后续 V2.20.x 计划**:
    - ~~RunPortfolioStep~~ ✅ (V2.20.2)
    - ~~WalkForwardStep~~ ✅ (V2.20.3)
    - ~~PairedBlockBootstrapStep~~ ✅ (V2.20.4)
    - jinja2-based ReportStep (升级模板能力, 低优先级)
    - ~~V2.21: sandbox `_reload_lock` 移出 module scope~~ ✅
  - 2391 → 2554 tests (+163: 20 _metrics + 22 objectives + 38 epsilon + 18 simplex + 2 label_map + 18 nested_oos + 12 round-5 + 10 round-4 + 23 round-3)

- **V2.20.2**: ez.research — RunPortfolioStep (P1-A 延伸)
  - **新 `ez/research/steps/run_portfolio.py`**: `RunPortfolioStep` — 单 portfolio strategy per step, 包装 `run_portfolio_backtest()`, 输出 daily returns (equity_curve.pct_change) 到 `artifacts['returns']`
  - **Market-derived defaults**: cn_stock 自动设 T+1/整手 100/印花税 0.05%/涨跌停 10%, 非 CN market 全部归零
  - **Merge 语义**: outer join 追加列到已有 returns DataFrame, 支持和 RunStrategiesStep 任意顺序组合. RunStrategiesStep 同步添加 merge 逻辑
  - **Infrastructure wiring**: 内部构造 TradingCalendar (从 universe_data 日期索引) + Universe + CostModel (market defaults + user overrides)
  - **Guards**: C1 start>=end guard (short data + large lookback buffer), C2 非重叠日期范围 guard, I3 重复 label warning + drop-before-join
  - **关键 e2e**: DataLoad → RunPortfolio → RunStrategies → NestedOOS → Report 五步 pipeline 验证 (alpha sleeve + bond/gold → 权重优化)
  - 2554 → 2583 tests (+29: 27 run_portfolio + 2 e2e pipeline)

- **V2.20.3**: ez.research — WalkForwardStep (P1-A 延伸)
  - **新 `ez/research/steps/walk_forward.py`**: `WalkForwardStep` — 滚动 N-fold walk-forward 权重优化. NestedOOSStep 的滚动版: 把 returns 按整数区间分成 N 个 non-overlapping folds, 每折 IS 优化权重 + OOS 验证
  - **Fold splitting**: 整数区间算术 `i*n//n_splits .. (i+1)*n//n_splits` (同 `ez/backtest/walk_forward.py`, 无尾部丢弃)
  - **聚合**: 拼接所有 OOS returns 重算 metrics (不是折平均 Sharpe, V2.12.2 教训), 计算 degradation = (IS_sharpe - OOS_sharpe) / |IS_sharpe|
  - **容错**: optimizer 异常跳过该折 + warning (不中断), all-NaN 行自动 drop (outer join gap), 最小数据量 guard
  - **Baseline**: per-fold baseline IS/OOS metrics + aggregate baseline_avg_oos_sharpe
  - **ReportStep 增强**: 渲染 walk_forward_results 折表 (Fold/IS Window/OOS Window/Best OOS Sharpe/Ret) + 聚合指标 (OOS Sharpe/Return/MDD/Degradation/Baseline)
  - 2583 → 2607 tests (+24: 4 constructor + 4 fold boundaries + 4 happy path + 2 real optimizer + 2 baseline + 6 edge cases + 2 degradation)

- **V2.20.4**: ez.research — PairedBlockBootstrapStep (P1-A 完结)
  - **新 `ez/research/steps/paired_bootstrap.py`**: `PairedBlockBootstrapStep` + `paired_block_bootstrap()` 核心函数
  - **配对块 bootstrap**: block_size=21 (1 month) 保留自相关, **同 block 索引**采样两条 series 保留横截面配对, Sharpe 差值作为统计量
  - **输出**: sharpe_diff, 95% percentile CI, two-sided p-value (centered under H0), is_significant (p<0.05), treatment/control 各自 metrics
  - **Window slicing**: 可选 `window=(start, end)` 限定 bootstrap 区间
  - **ReportStep 增强**: 渲染 bootstrap 显著性表 (Sharpe diff/CI/p-value/standalone metrics)
  - **P1-A 完结**: ez.research 7 个 step 全部实现 (DataLoad/RunStrategies/RunPortfolio/NestedOOS/WalkForward/PairedBootstrap/Report), 可替代 validation/ 全部 phase scripts
  - 2607 → 2631 tests (+24: 3 sharpe helper + 7 core bootstrap + 5 constructor + 3 step happy + 4 edge cases + 2 statistical properties)

- **V2.21**: sandbox `_reload_lock` closure-capture (安全修复)
  - **问题**: `_reload_lock` 作为 module-level 变量, 用户代码通过 dynamic alias (`[ez][0].agent.sandbox._reload_lock`) 可绕过 AST 检查直接访问
  - **修复**: `_make_lock_accessor()` 闭包捕获 `threading.Lock()`, 返回 `_get_reload_lock` 函数. Lock 不再出现在 `sandbox.__dict__`, `getattr(sandbox, '_reload_lock')` 返回 `AttributeError`
  - **攻击面消除**: 即��� dynamic alias bypass 绕过 AST 检查到达 sandbox 模块, 也无法获��� lock (闭包 cell 需要 `inspect`/`types` 模块访问, 均在 forbidden imports 列表)
  - **向后兼容**: 所有 6 处 `with _reload_lock:` 改为 `with _get_reload_lock():`, 行为不变
  - **CLAUDE.md Known limitation 关闭**: "dynamic binding bypass" 不再适用
  - 2631 → 2632 tests (+1: `test_reload_lock_not_module_attr` 防 regression)

- **V2.22 Phase 1 (done)**: 统一 OOS 验证 — 后端 + API
  - **新 `ez/research/_metrics.py` 扩展**: `deflated_sharpe_ratio()` (de Prado DSR, 带 skew/excess kurt 调整 + Bonferroni 多重检验), `minimum_backtest_length()` (MinBTL 年数公式), `annual_breakdown()` (每年 Sharpe/Return/MDD + best/worst/profitable_ratio)
  - **新 `ez/research/verdict.py`**: `VerdictThresholds` 可配置阈值 + `compute_verdict()` 规则引擎. 7 项检验 (WF degradation / overfit / CI 不含零 / p-value / DSR / MinBTL / 年度稳定性) → pass/warn/fail 综合裁决 + 中文 summary
  - **新 `ez/api/routes/validation.py`**: `POST /api/validation/validate` 端点, 输入 `{run_id, baseline_run_id?, n_bootstrap, block_size, n_trials, seed}`. 从 portfolio_runs 读 equity_curve → 转 daily returns → 运行: 单序列块 bootstrap (CI + Monte Carlo p-value), DSR, MinBTL, annual breakdown, 可选配对 bootstrap (vs baseline_run_id), 综合裁决. 返回 JSON-safe 结构 (7 个顶层 panel)
  - **Review round 修复**:
    - C1: DSR 峰度公式修正 (`kurt - 1` → `kurt - 3`, 非 excess 转 excess)
    - I2: run_id 加 `Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")` 输入校验
    - I4: DSR 病态 denom 返回 `warning` 字段 + `excess_kurt` 诊断字段
    - I5: Sharpe ≤ 0 时跳过 MinBTL 检查 (已被其他检验覆盖, 避免冗余 failed 计数)
    - S1: URL prefix `/api/research/` → `/api/validation/` (避免和自治研究 agent 命名冲突)
    - C3: Monte Carlo p-value two-sided 行为文档化
  - **测试**: 53 tests (16 _metrics + 12 verdict + 14 API mock + 11 e2e+前端模拟)
    - e2e: 真实 PortfolioStore (in-memory DuckDB) + save_run → validate 完整链路
    - 前端模拟: 7 个 contract tests 编码前端将发送的 request/response 格式, 防止 Phase 2 UI 对接时 schema drift
  - 2632 → 2685 tests (+53), 零 regression

- **V2.22 Phase 2 (done)**: 前端 ValidationPanel
  - **位置**: 嵌入 `PortfolioRunContent` 结果展示区 (wfResult 块之后), `result && result.run_id` 时自动渲染
  - **组件** (`web/src/components/ValidationPanel.tsx`, ~600 LOC):
    - VerdictBanner: pass/warn/fail 徽章 + 中文 summary + per-check ✓/⚠/✗ hover 徽章
    - WalkForwardSection: 4 指标卡 (IS Sharpe/OOS Sharpe/降解率/过拟合分数) + IS vs OOS 柱状图 (ECharts)
    - SignificanceSection: 4 指标卡 (观察 Sharpe/p-value/DSR/MinBTL) + 自定义 div-based CI 区间条 (带 0 线 + 观察值标记, 绿色=不含零/琥珀=含零) + DSR warning 显示
    - AnnualSection: per-year Sharpe 柱状图 (正绿/弱黄/负红) + 盈利年份比例/最好最差年份摘要
  - **新类型** (`web/src/types/index.ts`): 11 个新 interface — ValidationRequest/ValidationResult/VerdictCheck/VerdictResult/SignificanceResult/DeflatedResult/MinBtlResult/AnnualResult/AnnualYear/WalkForwardAggregate/ComparisonResult
  - **新 API client**: `runValidation(data) → POST /validation/validate` 已 typed
  - **共享 rating helpers** (`shared/metricRatings.ts`): rateDegradation/rateOverfit/ratePValue/rateDsr/rateMinBtl 带 ⚠ SOURCE OF TRUTH 注释指向 `ez/research/verdict.py`, 防止前后端 threshold drift
  - **Review round 修复**:
    - C1: CIBar 显示下限 0.1→0.5 + CI 宽度分量, 避免窄 CI 塌缩到 1% 宽度
    - C2: 盈利年份数本地 `filter(ret>0)` 计算, 替代后端 `round(ratio*n)` 不一致
    - I1+S6: rating helpers 移到 shared, 文档化 threshold 来源
    - I2: `useEffect([runId])` runId 变化清 result + invalidate token
    - I3: AxiosError.response.data.detail 提取, 404/422 显示后端 message
    - I4: 提交前 input precheck (n_bootstrap / block_size)
    - I5: 动态 loading 提示 "预计 ~N 秒" 按 n_bootstrap 缩放
  - **Phase 2.1 延后**: 配对对比 (基线 run dropdown + 双组表), 报告导出 (前端 markdown 拼接)
  - **构建**: tsc -b + vite build 零错误, bundle 2006 KB (gzip 606 KB)

- **V2.22 遗留 (Phase 1 review 延后项)**:
  - C2: DSR 的 `(1 - γ_euler) * 2·log N` expected-max 公式是近似, 更精确应用 scipy.stats.norm.ppf + Gumbel 分布 (当前是标准 de Prado 近似, 可接受)
  - I1: `validation.py` 每次请求 `PortfolioStore()` 新建连接, 未来应 reuse `ez.api.deps` 的 singleton
  - I3: `annual_breakdown` min_days=5 阈值硬编码 (可参数化)
  - I6: `n_trials` 由 client 传入, 存在信任边界问题 (类似 V2.15 `wf_metrics` bug). V2.14 `/search` 应把 n_trials 存进 portfolio_runs 让 `/validate` 读回
  - S2-S5: 小优化 (block bootstrap 向量化, verdict emoji 抽字段, profitable_ratio vs consistency_score 语义区分)

- **V2.19.0**: ez.testing.guards — 代码守卫框架 (save-time 验证层)
  - **动机 (第一性原理)**: 量化代码错误是静默致命的 — v1 Dynamic EF lookahead bug (Sharpe 虚高 ~0.4), MLAlpha `timedelta(days=N)` purge 跨周末泄漏, Block Bootstrap 循环包裹不可达, 都是靠 codex 多轮 review 才发现. 每类都是可以用小型专属测试自动检测的 bug class
  - **新模块 `ez/testing/guards/`**: Guard ABC + GuardContext/GuardResult/GuardSeverity + GuardSuite 编排器 + `load_user_class` (in-process 导入用户文件) + `default_guards()`
  - **mock 数据**: `build_mock_panel()` 200 B-day × 5 符号, deterministic GBM (seed 42), `@lru_cache` 缓存. `build_shuffled_panel(cutoff_idx)` 保留 [0..cutoff_idx], 打乱 [cutoff_idx+1..] 行值 (index 不变)
  - **5 guards**:
    - **LookaheadGuard (Tier 1 Block, `applies_to=("factor","strategy")`)**: **3-run shuffle-future test** — clean₁ vs clean₂ (侦测 non-determinism → WARN, 不 block), clean₁ vs shuffled (侦测 lookahead → BLOCK). tolerance 1e-9, cutoff_idx=150. **不 apply 到 cross_factor/portfolio_strategy/ml_alpha**: engine 会先 slice `[date-lookback, date-1]`, 用户代码合法使用 `iloc[-1]` 假设 pre-slice, guard 传 full panel 会把 `iloc[-1]=row[199]=shuffled` 误判为 lookahead. V2 再考虑针对 MLAlpha `feature_fn`/`target_fn` 的专用 guard
    - **NaNInfGuard (Tier 1 Block, 5 kinds)**: factor 扫 DataFrame **新列** (非整个 df, 否则 OHLCV 的合法 NaN 会误报), cross_factor/ml_alpha 扫 Series values, portfolio_strategy 扫 dict values, 遵守 `warmup_period` 跳过前 N 行. error message 提示 "warmup_period 声明错误 vs. 真实 rolling window"
    - **WeightSumGuard (Tier 1 Block, portfolio_strategy)**: 在 5 个 target_date (`[50, 100, 150, 175, 199]`) 调 `generate_weights`, 检查 sum 在 `[-0.001, 1.001]`. 比现有 sandbox contract test 的**单日**检查更强, 捕获日期相关的 over-leverage bug
    - **NonNegativeWeightsGuard (Tier 2 Warn, portfolio_strategy)**: 同 5 日扫描, 任一 symbol 权重 < -1e-9 即 warn. 不 block 因为有些 workflow 合法返回未归一化 raw alpha
    - **DeterminismGuard (Tier 2 Warn, 5 kinds)**: 双 run 相同输入, canonical string 比较 (dict sorted, Series to_json, float `.15e` 格式). BLAS 线程 non-determinism 常见, warn-only
  - **Sandbox 集成**:
    - `_sandbox_registries_for_kind(kind)` helper: mirrors `ez/api/routes/code._get_all_registries_for_kind` 但在 agent 层避免 layer violation (agent 不能 import api). parametrized parity test 验证两个 helper 返回**同一个**字典对象 (identity)
    - `_run_guards(filename, kind, target_dir)` helper: lazy import `ez.testing.guards` (module 保持 optional), 构造 `GuardContext`, 跑 `GuardSuite().run(ctx)`, 返回 `SuiteResult`
    - **Hook 1** (`save_and_validate_strategy`): 合约测试过后 / 热加载前调 guards. Guard block → 清 `Strategy._registry` + `sys.modules` → rollback 文件 → 重注册 backup → 返回失败 + `guard_result` payload
    - **Hook 2** (factor branch 内联): hot-reload 成功后, 返回 success 前调 guards. Block → 清 dual-dict Factor registry + rollback. **直线 rollback** (不用 exception), 避免和现有 `except Exception` cleanup 块的顺序冲突
    - **Hook 3** (portfolio/cross_factor/ml_alpha branch): `_reload_portfolio_code` 成功后调 guards. Block → `_sandbox_registries_for_kind(kind)` 清双 dict + rollback + 重注册 backup
  - **前端**: `web/src/components/CodeEditor.tsx` **零新组件**. 新增 `GuardReport`/`GuardReportEntry` TS 类型 (零 `any`), `guardReport` state, save handler 在 success/failure 两个分支都捕获 `guard_result`, 文件加载/新建路径 reset state. **状态栏**扩展: 每个 guard 一个 badge (✓/⚠/✗ + name), 颜色反映最坏严重度 (红 block / 琥珀 warn / 绿 pass), total_runtime_ms 后缀. **测试输出面板**扩展: 非 pass 的 guards 在合约测试输出下面渲染 `[SEVERITY] name (ms) + full message`, 共享 ✕ 按钮清两者
  - **Golden bug regression tests** (`tests/test_guards/golden_bugs/`): v1 Dynamic EF (`iloc[t+1]` 作为 factor 值) + MLAlpha 日历购货 (`timedelta(days=5)` 跨周末) — 都编码为 Factor kind (LookaheadGuard V1 scope), 保证**未来任何 LookaheadGuard 回退都会让这两个测试失败** (canary)
  - **性能预算**: 默认 suite < 500ms (runtime budget test 常驻), 单 guard < 150ms
  - **零 breaking changes**: GuardSuite 捕获 guard 异常作为 block (guard bug ≠ sandbox crash), 返回 dict 新增 `guard_result` key 被旧客户端忽略. 既有 51 个 sandbox tests 仍然通过
  - **测试**: 2265 → 2333 tests (+68: guard_base 6 + mock_data 5 + suite 8 + lookahead 10 + nan_inf 6 + weight_sum 5 + non_negative 4 + determinism 5 + sandbox_helpers 7 + sandbox_integration 10 + golden bugs 2)

- **V2.18.1 策略研究发现: A + 国债 + 黄金 降回撤组合** (仅研究结论, 不是代码改动):
  - **背景**: 8 次直接修改策略 C 的尝试全部失败 (factor / chop filter / vol scaling 等), 元诊断确认策略 C 是本地最优. 改变方向, 从"策略层之外"寻找降回撤方案
  - **研究方法**: 三层证据链验证, 避免单一时期过拟合
    - 层 1 — QMT 同期窗口 (2025-01 → 2026-03, 15 个月): 对比 QMT 真实回测 (Sharpe 3.28, MDD -11.08%), 确认 ez 复刻 EtfRotateCombo 核心逻辑对 (Sharpe 3.22 一致, β 细节有差异)
    - 层 2 — 2020-2025 长期 (5 年, QMT 成本参数): 单策略 Sharpe 1.98 → A 70%+D+F 方案 2.10, MDD -16.8% → -11.4%
    - 层 3 — **2015-2025 10 年 Walk-Forward** (20 个独立 6 月 folds, 覆盖 2015 股灾/2018 熊市/2022 俄乌/2024 踩踏): 这是决定性证据
  - **关键发现** (用 511010 5Y 国债替代 10Y 511260 因其 2017 才上市):
    - **100% folds MDD 改善** (20/20, 所有 6 月窗口都降回撤, 无反向)
    - **80% folds Sharpe 改善** (16/20, 方案 3 = A 50% + 5Y国债 50%)
    - **每一个市场周期都全面改善**: 2015-2017 牛熊, 2018 熊市, 2019-2020 复苏, 2021-2022 核心资产破灭, 2023-2024 踩踏 — 没有反向
    - **2018 熊市**: A 单独 Sharpe **-0.28** → 方案 3 **+0.02** (转正)
    - **2021-2022 弱期**: A Sharpe **+0.46** → 方案 3 **+0.65** (+0.19)
    - **最差 fold MDD**: A -19.0% → 方案 3 **-9.5%** (降 50%)
  - **核心 insight**: 国债和策略 C 跨 10 年相关性 -0.06 (长期) 到 -0.16 (2025 样本), 是唯一真正负相关的 A 股可投资产类别. 黄金 0.18 正相关但 2025 暴涨 50% 是特例 (地缘政治)
  - **DGU 1/N 原则再次验证**: A 50% + 国债 50% (两资产) 比 A + 国债 + 黄金 (三资产) 改善率更高 (80% vs 70%). RiskParityAllWeather 5 ETF 反而被其中的沪深 300/纳指 污染, corr(A) 被拉到 +0.39
  - **三个候选方案** (按收益保留率):
    - A 70% + 5Y国债 15% + 黄金 15%: 保留 74% 收益, MDD 降 30%, 70% folds 改善
    - A 60% + 5Y国债 20% + 黄金 20%: 保留 66% 收益, MDD 降 40%, 70% folds 改善
    - **A 50% + 5Y国债 50%** ★: 保留 51% 收益, MDD 降 50%, **80% folds 改善** (最稳, 最简)
  - **已知限制/警告**:
    - 债股相关性时变 — 2022 年 Q1 曾出现过债股同向下跌 (美联储加息预期), 未来利率上升周期需警惕
    - 样本起点 2015-01-05 受限于最早 ETF 上市日, 无法覆盖 2008 金融危机等更早极端事件
    - 10Y 国债 (511260) 数据只从 2017-08 开始, 10 年 WF 用 5Y 国债替代
    - 黄金 2025 暴涨 50% 是特殊事件, 长期平均 Sharpe 0.93 才是合理预期
  - **研究文件** (validation/ gitignored, 仅本地):
    - `validation/qmt_compare_2025.py` — QMT 同期对比
    - `validation/qmt_compare_2025_extended.py` — 扩展组合 (A + 各种 sleeve)
    - `validation/long_term_extended.py` — 2020-2025 长期扩展
    - `validation/wf_a_bond_gold.py` — 5 年 WF 验证 (10 folds)
    - `validation/wf_a_bond_gold_2015.py` — 10 年 WF 验证 (20 folds)
  - **下一步**: 研究国债对冲在未来市场的稳定性 (债股相关性滚动分析, 利率周期风险, fail-safe 机制)

## A 股约束 (贯穿所有版本)
- **不能做空个股**：信号 ∈ [0, 1]，组合优化 w >= 0 (long-only)
- T+1 / 涨跌停 / 整手 — V2.6 已实现
- 印花税卖出 0.05% 需计入交易成本
- 配对交易空腿、市场中性 — A 股不可行，推迟到 V3.x 有期货基础设施后考虑

## Known Limitations (后续版本跟进)
- 研究任务不支持进程恢复 (crash recovery)
- 研究任务串行 (同时只跑 1 个)
- 数据源链扁平去重而非按市场独立路由
- C++ 加速路径持 GIL (并发场景受限)
- LLM 调用计数近似 (chat_sync 内部多轮不精确计入，已文档化为估计值)
- Tencent provider close=adj_close (qfq当raw, 涨跌停判断不精确; Tushare/AKShare优先所以影响低)
- multi_select 参数搜索只搜单因子组合 (设计限制, 不是多因子组合空间)
- AKShare raw fetch 失败时 close=adj_close (同Tencent问题, 有warning log; V2.16 加 1 次重试降低瞬时错误概率)
- 约束风险平价近似 (行业约束在SLSQP内处理, 但约束可能导致偏离纯等风险贡献, inverse-vol fallback兜底)
- AI助手SSE流式输出无法前端强制中断 (需后端cancel机制, 当前只能等输出完成)
- **MLAlpha whitelist 覆盖 9 个 estimator** (V2.14 扩展) — 7 sklearn (Ridge/Lasso/LR/EN/DT/RF/GBR) + LGBMRegressor + XGBRegressor. 其中 Ridge/RF/GBR/LGBM/XGB 有 deepcopy + e2e backtest 测试, Lasso/LR/EN/DT 有 deepcopy 测试. **Classifier (LGBMClassifier/XGBClassifier) 未列入** — 需要先定义分类契约 (predict 输出语义 + TopNRotation 兼容性). 添加新 estimator 需要 (a) deepcopy regression test (b) sandbox smoke test (c) plan-file task.
- **MLAlpha `feature_warmup_days` 默认 0** — 用户的 `feature_fn` 若含 `rolling(N)` / `pct_change(N)` 等有 NaN head 的操作, 必须显式设置 `feature_warmup_days=N`, 否则训练 panel 的有效行数可能 < `train_window`. Runtime shortfall detection 在行数 < `train_window × 0.9` 时发一次性 warning, 但不 raise. `TopNRotation.lookback_days` 有 +20 buffer 作为未声明用户的 safety net, 但大 N (>20) 仍会 shortfall.
- ~~**MLAlpha V1 不支持 LightGBM/XGBoost**~~ — ✅ **已关闭** (V2.14 B4: LGBMRegressor + XGBRegressor 加入白名单, GPU 拦截, `[ml-extra]` optional group. Classifier 待定义分类契约)
- **模拟盘 strategy.state 不持久化** — PaperTradingEngine 进程重启后策略内部状态 (如 MLAlpha 已训练的模型) 丢失. resume_all() 从 DB 恢复 holdings/equity 但不恢复 strategy.state. 有状态策略首个 rebalance 从零训练. 需要 strategy state serialization 协议 (V3.x).
- **模拟盘数据时效性** — tick 执行时依赖 DataProviderChain 返回当日数据. 如果数据源尚未更新 (收盘后延迟), 可能获取到前一日数据导致信号错误. 建议收盘后 1-2 小时再触发 tick.
- **模拟盘停止不清仓** — 停止部署不执行自动清仓操作. 对模拟盘无实际影响 (不产生真实委托), 但权益曲线末端的持仓不会被平掉, 期末收益指标可能高估或低估.
- **Scheduler 单进程** — 无多 worker 支持, tick() 串行处理所有部署. 大量部署 (>50) 时可能在交易日窗口内处理不完.

## V2.12.2 指标公式语义变更 (⚠ 非 backward compat)
V2.12.2 修复 codex 发现的"同名指标不同公式"问题, 跨 `ez/backtest/engine.py`
和 `ez/portfolio/engine.py` 统一以下 5 个指标为标准公式:
- **sharpe_ratio**: `excess.mean() / excess.std(ddof=1) × √252` (原 portfolio 是 `ann_ret/vol` 无 rf)
- **sortino_ratio**: `excess.mean() / sqrt(mean(min(excess,0)²)) × √252` (原 portfolio 用 total return+ddof=0, 差约 30%)
- **alpha**: `(mean(excess_s) - beta × mean(excess_b)) × 252` (原 portfolio 没减 rf, 差约 5pp)
- **beta**: `cov(excess_s, excess_b, ddof=1) / var(excess_b, ddof=1)` (原 portfolio 用 ddof=0)
- **profit_factor**: `gross_profit / gross_loss` (原单票用 `avg_win_pct/avg_loss_pct`, 忽略 position sizing)
另外 `ez/portfolio/walk_forward.py` 和 `ez/backtest/significance.py` 的 `_sharpe` helpers 也统一用 ddof=1, 和 engine sharpe 一致 (之前默认 ddof=0, 短 OOS 窗口下 CI 和显示 sharpe 偏差最大 2.7%).

**影响**: V2.12.2 之前存入 `experiment_runs` 和 `portfolio_runs` 的指标使用旧公式, V2.12.2 之后新建 run 使用新公式. 历史 run 和新 run 的这 5 个指标不可直接比较. 没有迁移脚本 — 历史值保留为记录 (old-formula), 新运行以新公式为准. UI 不区分新旧.

## V2.12.2 遗留项 (V2.13+ 处理)
- ~~**#7 候选搜索不支持 bool/enum 参数**~~ — ✅ **已关闭** (V2.14 B1: CandidateSearch discriminated union + ParamRangeRequest 放宽 `list[int|float|str|bool]`)
- **#18 alpha_combiner 训练窗口固定 365 天** — ez/api/routes/portfolio.py::_compute_alpha_weights 用 start-timedelta(days=365) 作训练区间. 长 warmup 的自定义因子会被喂不足历史. #9 修复后 lookback 已动态, 但训练窗口长度本身还是固定的. 动态化需要更多设计 (训练窗口大小 vs 因子 warmup 的权衡). 暂时把默认值留在 365, 用户可以覆盖 forward_days 间接调整.
- ~~**#20 multi_select 参数搜索 UX 语义**~~ — ✅ **已关闭** (V2.14 B2: "组合搜索" checkbox → power-set 2^N-1 子集, 64 上限, 原 `|` 模式保留)
- ~~**Portfolio 引擎 lookback 硬校验只 warn 不 raise**~~ — ✅ **已关闭** (V2.13.2 G1.4 `strict_lookback: bool = False` option, True 时 raise ValueError, 通过 PortfolioCommonConfig 暴露到 /run + /walk-forward + /search 三条 API 路由)
- **WalkForward deepcopy 对 unpicklable state 敏感** — ez/backtest/walk_forward.py 每折 copy.deepcopy(strategy) 防 IS/OOS 状态污染. 对 hold DuckDB 连接 / 文件句柄 / httpx client 的 strategy 会 raise TypeError. 当前 single-stock builtin strategies 都没有这类字段, 但用户自定义策略需要避免 (或者改用 strategy_factory 模式, 组合 WF 已经这样做). 文档提醒: Strategy 子类尽量只 hold 纯数据字段, 避免 DB/file/network refs.
- ~~**API 层 stamp_tax_rate 默认 0.0005 不按 market gate**~~ — ✅ **已关闭** (V2.13.2 G1.2 `model_fields_set` gate: 非 CN market 未显式传 stamp_tax_rate 时自动归零)
- ~~**部分前端异步 handler 还没有 race token 保护**~~ — ✅ **已关闭** (V2.13.2 G3.7 + A2: handleEvaluateFactors/handleFetchFundamental/handleCompare 加 evalTokenRef/fundaTokenRef/compareTokenRef, CodeEditor.loadFile 加 loadFileTokenRef. 4/4 handlers 全覆盖)
