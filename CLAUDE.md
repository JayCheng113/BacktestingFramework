# ez-trading

Agent-Native quantitative trading platform. Human researchers and AI agents are both
first-class citizens — same pipeline, same gates, same audit trail.
Python 3.12+ / FastAPI / DuckDB / React 19 / ECharts / C++ (nanobind).
Version: 0.2.12.1 | Tests: 1781 (1791 collected, 10 skip) | C++ acceleration: up to 7.9x

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
- **V2.12.1**: Stability — batch kline(单SQL批量查询), Gram-Schmidt因子正交化(AlphaCombiner orthogonalize), 指数增强F5(AKShare成分+TE约束+指数归因+主动权重), weights完整历史端点, TypeScript types补全(0处as any), 边缘测试(16个), Windows冻结兼容(frozen mode Python探测+进程内fallback+Strategy子类AST去重+agent工具300s/600s超时), ChatPanel AI创建策略竞态修复(aiCreatedFileRef+onCodeUpdate+localStorage清理), V2.9+契约测试补全(CrossSectionalFactor/PortfolioStrategy/Allocator/PortfolioOptimizer共136个), **V2.13前置深度审查+code review迭代** — evaluator `or True`永真bug修复+完整NaN guard(ic_mean/rank_ic_mean/turnover, 之前仅修ic_decay, reviewer发现的漏网之鱼), _rolling_corr浮点容差常数检测(1e-12), np.errstate窄化divide/invalid抑制, 真正测试decay行为(ic_decay[1]>ic_decay[5]断言, 前一版假装测lag但实际只isfinite), walk_forward参数校验(n_splits/train_ratio, +参数化), optimizer奇异协方差/小样本edge case(+5), **基本面因子真参数化契约测试**(18因子×12 invariants=216独立测试用例, scope=module+batch insert, 暴露fixture只覆盖1月的真实盲区已修), AKShare iterrows→zip向量化, **codex外部审查3修复**: factor保存后真正hot-reload(之前仅注册stub, compute()抛NotImplementedError, 必须手动refresh) + _run_with_timeout try/finally清理executor(之前仅TimeoutError分支shutdown, 每次成功调用泄漏一个线程池) + generate_strategy_code allowed_tools收紧到strategy-only(之前含create_portfolio_strategy/create_cross_factor, LLM可能选错工具浪费retry预算), **codex第二轮审查4业务逻辑修复**: DataProviderChain缓存完整性(两阶段check: 首尾3日容差+密度75%阈值, 之前只看首尾导致中间漏bar的缓存被当完整数据返回→回测悄悄偏) + 组合回测清仓写回equity_curve(之前清仓只更新cash和trades, metrics基于清仓前曲线导致期末持仓收益系统性偏高, 不扣除最终close-out的commission+stamp+slippage) + 研究流水线batch timeout保护(asyncio.wait_for+60s/strategy min120s, 之前坏策略可卡死整个research task) + cancel中间检查+跳过E6 LLM summary(之前仅循环顶部检查cancel, 且取消后无条件跑build_report又要等LLM), **codex第三轮审查3修复**: 策略key撞名严格化(_get_strategy优先module.class精确匹配, 同名类多个→409, 前端option value改用s.key防止promote后的ResearchFoo→Foo撞名builtin) + prefilter用dataclasses.replace保全RunSpec所有字段(之前丢失use_market_rules/t_plus_1/lot_size/price_limit_pct, 预筛和正式实验环境不一致→门控不可信) + BacktestPanel walk-forward分支清空trades(之前切tab后KlineChart继续绘上次单次回测的旧trades), **Claude reviewer 第三轮反馈2修复**: 共享Strategy.resolve_class() helper(I1: R3.E只修了API路由, ez/agent/runner.py::_resolve_strategy仍有first-match漏洞, 研究流水线/experiment工具/chat backtest都受影响, 修复: 在ez/strategy/base.py新增AmbiguousStrategyName + classmethod resolve_class()三阶段解析, API和Runner都调用) + 稀疏symbol记忆(I2: 75%密度阈值对新上市/niche ETF造成无限refetch回归, 修复: _known_sparse_symbols session集合, provider成功返回<75%时标记, 后续cache check skip_density), **codex第四轮审查P1批次10修复(绩效口径+WF隔离+规则一致性)**: run_experiment工具save_spec用to_dict不用__dict__(spec_id是@property漏掉会KeyError崩溃) + walk_forward每折deepcopy strategy防IS→OOS状态污染 + Runner分阶段fresh resolve防backtest→WFO跨阶段污染 + oos_metrics从拼接oos_equity_curve重算(之前是每折sharpe平均,折长不一时偏) + 组合引擎sharpe改用标准公式(excess/std×√252 匹配metrics.py,之前是ann_ret/vol含义不同) + 非cn_stock不再误用T+1(lot_size>1才包MarketRulesMatcher,t_plus_1按market gate) + 候选搜索require_wfo跟随run_wfo(之前run_wfo=False+require_wfo=True全部系统性判失败) + PortfolioStrategy dual-dict registry(module.class唯一key+name向后兼容+冲突告警+resolve_class三阶段) + Brinson归因覆盖最后持仓区间(rebalance_dates + result.dates[-1],之前漏最后段导致total_excess和total_return对不上) + SearchConfig传市场规则字段(prefilter已修,candidate_search同样需要) + AI tools/research_runner构造RunSpec按market自动设use_market_rules(之前chat/research和web ExperimentPanel执行环境不一致), 1764 tests
- **Next: V2.13** — ML Alpha+多策略 → V3.0 Paper OMS

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

## V2.12.1 中期指标公式语义变更 (⚠ 非 backward compat)
V2.12.1 修复 codex 发现的"同名指标不同公式"问题, 跨 `ez/backtest/engine.py`
和 `ez/portfolio/engine.py` 统一以下 5 个指标为标准公式:
- **sharpe_ratio**: `excess.mean() / excess.std(ddof=1) × √252` (原 portfolio 是 `ann_ret/vol` 无 rf)
- **sortino_ratio**: `excess.mean() / sqrt(mean(min(excess,0)²)) × √252` (原 portfolio 用 total return+ddof=0, 差约 30%)
- **alpha**: `(mean(excess_s) - beta × mean(excess_b)) × 252` (原 portfolio 没减 rf, 差约 5pp)
- **beta**: `cov(excess_s, excess_b, ddof=1) / var(excess_b, ddof=1)` (原 portfolio 用 ddof=0)
- **profit_factor**: `gross_profit / gross_loss` (原单票用 `avg_win_pct/avg_loss_pct`, 忽略 position sizing)
另外 `ez/portfolio/walk_forward.py` 和 `ez/backtest/significance.py` 的 `_sharpe` helpers 也统一用 ddof=1, 和 engine sharpe 一致 (之前默认 ddof=0, 短 OOS 窗口下 CI 和显示 sharpe 偏差最大 2.7%).

**影响**: V2.12.1 之前存入 `experiment_runs` 和 `portfolio_runs` 的指标使用旧公式, V2.12.1 之后新建 run 使用新公式. 历史 run 和新 run 的这 5 个指标不可直接比较. 没有迁移脚本 — 历史值保留为记录 (old-formula), 新运行以新公式为准. UI 不区分新旧.

## V2.12.1 codex 六轮累计遗留项 (V2.13+ 处理)
- **#7 候选搜索不支持 bool/enum 参数** — web/src/components/CandidateSearch.tsx 的 ParamRangeState 只支持 int/float, generateValues/countValues 也是数值逻辑, 布尔/枚举参数的搜索需要前端 UX 重设计 + 后端 ParamRangeRequest 支持 list[str]/list[bool]. 当前策略作者可以绕过: 用整数编码枚举. 不影响数据正确性.
- **#18 alpha_combiner 训练窗口固定 365 天** — ez/api/routes/portfolio.py::_compute_alpha_weights 用 start-timedelta(days=365) 作训练区间. 长 warmup 的自定义因子会被喂不足历史. #9 修复后 lookback 已动态, 但训练窗口长度本身还是固定的. 动态化需要更多设计 (训练窗口大小 vs 因子 warmup 的权衡). 暂时把默认值留在 365, 用户可以覆盖 forward_days 间接调整.
- **#20 multi_select 参数搜索 UX 语义** — PortfolioPanel.tsx 的 paramGrid[key] = vals.map(v => [v]) 让每个候选值独立成一个 combo. 用户输入 "EP,BP,SP,DP" 得到 4 次单因子运行, 而不是多因子组合. 这是产品设计折衷 (单因子搜索 vs 多因子组合空间爆炸), 真正的多因子子集搜索需要 power-set UX. 和上面 "multi_select 参数搜索只搜单因子组合" 同根.
- **Portfolio 引擎 lookback 硬校验只 warn 不 raise** — #22 修复加了 warning log 但不 raise, 保留向后兼容. 有误差但可诊断. 硬 raise 需要确认所有 builtin 策略的 lookback_days 声明正确.
- **WalkForward deepcopy 对 unpicklable state 敏感** — ez/backtest/walk_forward.py 每折 copy.deepcopy(strategy) 防 IS/OOS 状态污染. 对 hold DuckDB 连接 / 文件句柄 / httpx client 的 strategy 会 raise TypeError. 当前 single-stock builtin strategies 都没有这类字段, 但用户自定义策略需要避免 (或者改用 strategy_factory 模式, 组合 WF 已经这样做). 文档提醒: Strategy 子类尽量只 hold 纯数据字段, 避免 DB/file/network refs.
- **API 层 stamp_tax_rate 默认 0.0005 不按 market gate** — PortfolioRunRequest/PortfolioWFRequest/PortfolioSearchRequest 的 Pydantic 默认是 A 股. 前端 getDefaultSettings 按 market 传 0, 所以 UI 路径 OK; 但非 UI 客户端 (测试, 外部脚本, 直接 HTTP 不传该字段) 会给 US/HK 错加中国印花税. 真正的 defense-in-depth 需要 model_validator 或 endpoint 层 coerce. 风险低 (几乎所有客户端都是 UI).
