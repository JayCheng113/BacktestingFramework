# ez-trading

Agent-Native quantitative trading platform. Human researchers and AI agents are both
first-class citizens — same pipeline, same gates, same audit trail.
Python 3.12+ / FastAPI / DuckDB / React 19 / ECharts / C++ (nanobind).
Version: 0.2.8 | Tests: 980 | C++ acceleration: up to 7.9x

## Architecture Docs (MUST READ before major changes)
- [System Architecture](docs/architecture/system-architecture.md) — 7-layer design, gates (Research/Deploy/Runtime + PreTradeRisk), dual state machine
- [Engineering Governance](docs/architecture/governance.md) — thin core, lifecycle labels, version discipline
- [V2.3+ Roadmap](docs/core-changes/v2.3-roadmap.md) — detailed per-version plan with exit gates

**Note**: ResearchGate implemented in V2.4 (ez/agent/gates.py). MarketRules in V2.6 (ez/core/market_rules.py). LLM + Web Coding Assistant in V2.7 (ez/llm/, ez/agent/assistant.py). Deploy/Runtime gates planned for V2.8+.

## Module Map
- `ez/core/` — Computational primitives: matcher, ts_ops (C++ accelerated) [CLAUDE.md](ez/core/CLAUDE.md)
- `ez/data/` — Data ingestion, validation, caching [CLAUDE.md](ez/data/CLAUDE.md)
- `ez/factor/` — Factor computation + IC evaluation [CLAUDE.md](ez/factor/CLAUDE.md)
- `ez/strategy/` — Strategy framework, auto-registration [CLAUDE.md](ez/strategy/CLAUDE.md)
- `ez/backtest/` — Backtest engine, Walk-Forward, significance [CLAUDE.md](ez/backtest/CLAUDE.md)
- `ez/api/` — FastAPI REST endpoints [CLAUDE.md](ez/api/CLAUDE.md)
- `web/` — React frontend dashboard [CLAUDE.md](web/CLAUDE.md)
- `ez/llm/` — LLM provider abstraction: DeepSeek/Qwen/Local/OpenAI (V2.7) [CLAUDE.md](ez/llm/CLAUDE.md)
- `ez/agent/` — Agent loop: RunSpec, Runner, Gates, Report, ExperimentStore, CandidateSearch, BatchRunner, Prefilter, Tools, Assistant, Sandbox, FDR [CLAUDE.md](ez/agent/CLAUDE.md)
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
| Factor | ez/factor/builtin/ | Factor | pytest tests/test_factor/test_factor_contract.py |
| Strategy | strategies/ or ez/strategy/builtin/ | Strategy | pytest tests/test_strategy/ |
| Matcher | ez/core/matcher.py | Matcher | pytest tests/test_core/test_matcher_contract.py |

## Quick Commands
```bash
./scripts/start.sh          # Start backend (8000) + frontend (3000)
./scripts/stop.sh            # Stop all
pytest tests/                # Full test suite (1060 collected, 1050 pass, 10 skip). 停掉后端再跑: ./scripts/stop.sh
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
- **V2.8**: Autonomous Research Agent — 全自治策略探索 (E1假设生成+E2代码生成+E3批量回测+E4结果分析+E5循环控制+E6报告), ResearchStore持久化 (2张新表), SSE进度流, 研究助手前端, asyncio.Lock串行保护, cancel→cancelled状态机, 预算预检查, allowed_tools工具过滤, 1026 tests
- **V2.8 post-release fixes**: 任务卡死(try/finally全包裹), 串行竞态(asyncio.Lock), 取消语义(cancelled≠completed), store连接泄漏(close实现), 预算硬约束(批前检查), LLM计数(保守估计), code_gen异常重试, best_strategies查询, SSE预注册, E2工具最小权限
- **Next: V2.8.1** — Stability

## Known Limitations (V2.8.1 跟进)
- 因子评估面板不支持自定义因子 (下拉框硬编码 _FACTOR_MAP，自定义因子只能在策略代码中引用)
- _start_lock 私有名跨模块导入 (应封装为函数)
- LLM 调用计数近似 (chat_sync 内部多轮不精确计入)
- SSE 流无 heartbeat (长迭代时代理可能断开)
- 前端 409 串行拒绝无用户提示
- 研究任务不支持进程恢复 (crash recovery)
- 研究任务串行 (同时只跑 1 个)
- 数据源链扁平去重而非按市场独立路由
- C++ 加速路径持 GIL (并发场景受限)
- 回测未强平期末持仓 (trade_count 略低于真实)
- 前端参数面板仅支持数值型 (bool/str 参数不可用)
