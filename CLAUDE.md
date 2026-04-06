# ez-trading

Agent-Native quantitative trading platform. Human researchers and AI agents are both
first-class citizens — same pipeline, same gates, same audit trail.
Python 3.12+ / FastAPI / DuckDB / React 19 / ECharts / C++ (nanobind).
Version: 0.2.13.2 | Tests: 2024 with sklearn / 1813 without sklearn (ml tests skip gracefully) | C++ acceleration: up to 7.9x

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
- `ez/live/` — Deploy Gate, OMS, Broker (V2.6+, planned)
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
- AKShare raw fetch 失败时 close=adj_close (同Tencent问题, 有warning log)
- 约束风险平价近似 (行业约束在SLSQP内处理, 但约束可能导致偏离纯等风险贡献, inverse-vol fallback兜底)
- AI助手SSE流式输出无法前端强制中断 (需后端cancel机制, 当前只能等输出完成)
- **MLAlpha V1 whitelist 仅覆盖 7 个 sklearn estimator** — 其中 Ridge/RF/GBR 有 `run_portfolio_backtest` + `portfolio_walk_forward` 端到端集成测试, Lasso/LinearRegression/ElasticNet/DecisionTreeRegressor 只有单元层 whitelist acceptance 测试 (没有单独的 portfolio 集成测试). 添加新 estimator 需要 (a) deepcopy regression test (b) sandbox smoke test (c) plan-file task.
- **MLAlpha `feature_warmup_days` 默认 0** — 用户的 `feature_fn` 若含 `rolling(N)` / `pct_change(N)` 等有 NaN head 的操作, 必须显式设置 `feature_warmup_days=N`, 否则训练 panel 的有效行数可能 < `train_window`. Runtime shortfall detection 在行数 < `train_window × 0.9` 时发一次性 warning, 但不 raise. `TopNRotation.lookback_days` 有 +20 buffer 作为未声明用户的 safety net, 但大 N (>20) 仍会 shortfall.
- **MLAlpha V1 不支持 LightGBM/XGBoost** — defer to V2.13.1. V1 限制于 sklearn 内置 estimator 以保持 pickle/deepcopy/n_jobs 问题可控.

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
- **#7 候选搜索不支持 bool/enum 参数** — web/src/components/CandidateSearch.tsx 的 ParamRangeState 只支持 int/float, generateValues/countValues 也是数值逻辑, 布尔/枚举参数的搜索需要前端 UX 重设计 + 后端 ParamRangeRequest 支持 list[str]/list[bool]. 当前策略作者可以绕过: 用整数编码枚举. 不影响数据正确性.
- **#18 alpha_combiner 训练窗口固定 365 天** — ez/api/routes/portfolio.py::_compute_alpha_weights 用 start-timedelta(days=365) 作训练区间. 长 warmup 的自定义因子会被喂不足历史. #9 修复后 lookback 已动态, 但训练窗口长度本身还是固定的. 动态化需要更多设计 (训练窗口大小 vs 因子 warmup 的权衡). 暂时把默认值留在 365, 用户可以覆盖 forward_days 间接调整.
- **#20 multi_select 参数搜索 UX 语义** — PortfolioPanel.tsx 的 paramGrid[key] = vals.map(v => [v]) 让每个候选值独立成一个 combo. 用户输入 "EP,BP,SP,DP" 得到 4 次单因子运行, 而不是多因子组合. 这是产品设计折衷 (单因子搜索 vs 多因子组合空间爆炸), 真正的多因子子集搜索需要 power-set UX. 和上面 "multi_select 参数搜索只搜单因子组合" 同根.
- ~~**Portfolio 引擎 lookback 硬校验只 warn 不 raise**~~ — ✅ **已关闭** (V2.13.2 G1.4 `strict_lookback: bool = False` option, True 时 raise ValueError, 通过 PortfolioCommonConfig 暴露到 /run + /walk-forward + /search 三条 API 路由)
- **WalkForward deepcopy 对 unpicklable state 敏感** — ez/backtest/walk_forward.py 每折 copy.deepcopy(strategy) 防 IS/OOS 状态污染. 对 hold DuckDB 连接 / 文件句柄 / httpx client 的 strategy 会 raise TypeError. 当前 single-stock builtin strategies 都没有这类字段, 但用户自定义策略需要避免 (或者改用 strategy_factory 模式, 组合 WF 已经这样做). 文档提醒: Strategy 子类尽量只 hold 纯数据字段, 避免 DB/file/network refs.
- ~~**API 层 stamp_tax_rate 默认 0.0005 不按 market gate**~~ — ✅ **已关闭** (V2.13.2 G1.2 `model_fields_set` gate: 非 CN market 未显式传 stamp_tax_rate 时自动归零)
- ~~**部分前端异步 handler 还没有 race token 保护**~~ — ✅ **已关闭** (V2.13.2 G3.7 + A2: handleEvaluateFactors/handleFetchFundamental/handleCompare 加 evalTokenRef/fundaTokenRef/compareTokenRef, CodeEditor.loadFile 加 loadFileTokenRef. 4/4 handlers 全覆盖)
