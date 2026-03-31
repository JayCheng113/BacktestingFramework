# ez-trading

Agent-Native quantitative trading platform. Human researchers and AI agents are both
first-class citizens — same pipeline, same gates, same audit trail.
Python 3.12+ / FastAPI / DuckDB / React 19 / ECharts / C++ (nanobind).
Version: 0.2.10 | Tests: 1207 (1217 collected, 10 skip) | C++ acceleration: up to 7.9x

## Architecture Docs (MUST READ before major changes)
- [System Architecture](docs/architecture/system-architecture.md) — 7-layer design, gates (Research/Deploy/Runtime + PreTradeRisk), dual state machine
- [Engineering Governance](docs/architecture/governance.md) — thin core, lifecycle labels, version discipline
- [V2.3+ Roadmap](docs/core-changes/v2.3-roadmap.md) — detailed per-version plan with exit gates

**Note**: ResearchGate implemented in V2.4 (ez/agent/gates.py). MarketRules in V2.6 (ez/core/market_rules.py). LLM + Web Coding Assistant in V2.7 (ez/llm/, ez/agent/assistant.py). Autonomous Research Agent in V2.8 (ez/agent/research_runner.py). Deploy/Runtime gates planned for V2.9+.

## Module Map
- `ez/core/` — Computational primitives: matcher, ts_ops (C++ accelerated) [CLAUDE.md](ez/core/CLAUDE.md)
- `ez/data/` — Data ingestion, validation, caching [CLAUDE.md](ez/data/CLAUDE.md)
- `ez/factor/` — Factor computation + IC evaluation [CLAUDE.md](ez/factor/CLAUDE.md)
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
pytest tests/                # Full test suite (1217 collected, 1207 pass, 10 skip). 停掉后端再跑: ./scripts/stop.sh
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
- **Next: V2.10.1** — Stability → V2.11 基本面数据层 → V2.11.1 Alpha组合+中性化 → V2.12 优化器+归因+风控 → V2.13 ML Alpha+多策略 → V3.0 Paper OMS

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
- 回测未强平期末持仓 (trade_count 略低于真实)
- LLM 调用计数近似 (chat_sync 内部多轮不精确计入，已文档化为估计值)
