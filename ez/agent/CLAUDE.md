# ez/agent — Agent Research Loop (experimental, V2.4+)

## Responsibility
Provide a standardized pipeline for automated and human-driven strategy research:
RunSpec → Runner → Gate → Report → Store.
V2.5 adds batch parameter search: CandidateSearch → PreFilter → BatchRunner → Ranking.
V2.7 adds AI assistant: Tools → Assistant → Chat, plus code sandbox and FDR correction.
V2.8 adds autonomous research agent: Hypothesis → CodeGen → BatchExec → Analyzer → LoopController → Report.

## Public Interfaces
- `RunSpec` — Immutable experiment input with content-hash `spec_id` for idempotency
- `Runner` — Executes RunSpec against data: backtest + WFO + significance
- `RunResult` — Structured output from Runner (backtest/WFO results, timing, git SHA)
- `ResearchGate` — Evaluates RunResult against configurable thresholds
- `GateVerdict` — Pass/fail with per-rule reasons
- `ExperimentReport` — Flattened metrics + gate result for storage/API
- `ExperimentStore` — DuckDB persistence (experiment_specs + experiment_runs + completed_specs tables)
- `SearchConfig` / `ParamRange` — Parameter search space definition (V2.5)
- `grid_search` / `random_search` — Generate candidate RunSpecs from search space (V2.5)
- `prefilter` — Quick backtest-only elimination of weak candidates (V2.5)
- `run_batch` — Orchestrate pre-filter → full run → gate → rank → persist (V2.5)
- `tool()` decorator — Register functions as AI assistant tools (V2.7)
- `get_all_tool_schemas()` / `execute_tool()` — Tool dispatch framework (V2.7)
- `chat_sync()` — Agent loop with tool calling, supports `allowed_tools` filter (V2.7+V2.8)
- `chat_stream()` — Sync streaming agent loop (V2.7)
- `achat_stream()` — Async streaming agent loop (V2.7.1, non-blocking)
- `save_and_validate_strategy()` — Code sandbox: save + contract test (V2.7)
- `apply_fdr()` — FDR correction for batch search results (V2.7)
- `generate_hypotheses()` — LLM-powered strategy hypothesis generation (V2.8)
- `generate_strategy_code()` — LLM-powered code generation with sandbox validation (V2.8)
- `analyze_results()` — LLM-powered batch result analysis (V2.8)
- `LoopController` — Budget/convergence/stop condition management (V2.8)
- `ResearchReport` / `build_report()` — Aggregate iterations + LLM summary (V2.8)
- `ResearchStore` — DuckDB persistence for research tasks + iterations (V2.8)
- `run_research_task()` — Async orchestrator: E1→E2→E3→E4→E5→E6 loop (V2.8)

## Files
| File | Role |
|------|------|
| run_spec.py | RunSpec dataclass + spec_id hash |
| runner.py | Runner + RunResult |
| gates.py | ResearchGate + GateConfig + GateVerdict + GateReason |
| report.py | ExperimentReport |
| experiment_store.py | DuckDB persistence (own tables, not modifying core store) |
| candidate_search.py | Grid + random parameter search (V2.5) |
| prefilter.py | Pre-filter rule engine for fast elimination (V2.5) |
| batch_runner.py | Batch execution + ranking pipeline (V2.5) |
| tools.py | Tool registration framework + 9 built-in tools (V2.7) |
| assistant.py | Agent loop: message → LLM → tool_use → execute → respond (V2.7+V2.8: allowed_tools) |
| sandbox.py | Code validation: syntax check, forbidden imports, contract test (V2.7) |
| fdr.py | False Discovery Rate correction: Bonferroni, Benjamini-Hochberg (V2.7) |
| data_access.py | Agent-layer data singletons: chain, experiment_store, research_store (V2.7+V2.8) |
| hypothesis.py | E1: LLM hypothesis generation from research goal (V2.8) |
| code_gen.py | E2: LLM strategy code generation with filtered tools + retry (V2.8) |
| analyzer.py | E4: LLM result analysis and direction suggestions (V2.8) |
| loop_controller.py | E5: Budget tracking, convergence detection, 4 stop conditions (V2.8) |
| research_report.py | E6: Aggregate iterations into final report + Top 5 strategies (V2.8) |
| research_store.py | DuckDB persistence: research_tasks + research_iterations (V2.8) |
| research_runner.py | Main orchestrator: async loop with SSE events + cancel + budget (V2.8) |

## Dependencies
- Upstream: ez/backtest/ (Engine, WFO, significance), ez/core/ (Matcher), ez/strategy/ (Strategy registry), ez/llm/ (LLMProvider, V2.7+)
- Downstream: ez/api/routes/experiments.py, ez/api/routes/candidates.py, ez/api/routes/code.py, ez/api/routes/chat.py, ez/api/routes/research.py, web/

## Agent Tools (V2.7+V2.9+V2.17)
| Tool | Capability | Permission |
|------|-----------|------------|
| list_strategies | List registered strategies + params | Read-only |
| list_factors | List available factors | Read-only |
| read_source | Read strategy/factor source code | Read-only (strategies/, ez/strategy/builtin/, ez/factor/builtin/) |
| create_strategy | Create file + contract test | Write (strategies/ only) |
| update_strategy | Update file + contract test | Write (strategies/ only) |
| run_backtest | Single backtest execution | Execute (no data modification) |
| run_experiment | Full experiment pipeline | Execute (persists to ExperimentStore) |
| list_experiments | Recent experiment list | Read-only |
| explain_metrics | Experiment detail + gate reasons | Read-only |
| list_portfolio_strategies | List portfolio strategies + schemas | Read-only |
| create_portfolio_strategy | Create portfolio strategy file | Write (portfolio_strategies/ only) |
| create_cross_factor | Create cross-sectional factor file | Write (cross_factors/ only) |
| run_portfolio_backtest | Portfolio backtest execution | Execute |
| create_ml_alpha | Create ML Alpha factor file (V2.17) | Write (ml_alphas/ only) |

## V2.8 Research Agent Pipeline
```
用户目标 → E1 生成假设 → E2 代码生成 (filtered tools: create_strategy/read_source/list_factors)
         → E3 run_batch() → E4 分析结果 → E5 循环控制 (预算/收敛/取消)
         → E6 报告 (Top 5 策略 + LLM 总结)
```

### Budget Control (LoopConfig)
| Parameter | Default | Description |
|-----------|---------|-------------|
| max_iterations | 10 | Maximum loop rounds |
| max_specs | 500 | Total backtest count limit |
| max_llm_calls | 100 | LLM API call limit (approximate: E2 counts ~2 per hypothesis, actual may vary with tool rounds) |
| no_improve_limit | 3 | Stop after N consecutive rounds with 0 new gate-passed |

### Task State Machine
```
pending → running → completed
                  → cancelled (user cancel)
                  → failed (exception)
```

### Concurrency
- Task-level serial: `asyncio.Lock` via `get_start_lock()` ensures only 1 task runs at a time (V2.8.1: public accessor)
- Init failure safe: try/finally guarantees `done=True` + `finished_at` timestamp even on early exception
- `register_task()` pre-registers in memory with `created_at` timestamp before background work (prevents SSE 404)
- Lazy lock init (V2.12.1): `get_start_lock()` creates `asyncio.Lock` on first call — avoids `RuntimeError: no running event loop` on Windows `WindowsSelectorEventLoopPolicy` + module import order

### Persistence
- research_tasks: task_id(PK), goal, config, status, stop_reason, summary
- research_iterations: (task_id, iteration)(PK), hypotheses, tried/passed, best_sharpe, analysis, spec_ids

### Research Isolation (V2.8 post-release)
- Research strategies use `research_` filename prefix + `Research` class name prefix
- Frontend filters: BacktestPanel/ExperimentPanel/CandidateSearch use `key.includes('research_')`
- CodeEditor sidebar hides `research_` files
- Promote workflow: POST /api/code/promote copies research_ file → removes prefix → renames class → contract test → registers globally

## V2.12.2 post-release
- **Sandbox dual-dict hot-reload cleanup**: `_reload_factor_code` 和 `_reload_portfolio_code` 的 `cross_factor` 分支清理 `_registry` + `_registry_by_key` 两个 dict, 避免 hot-reload 后全键 dict 留 zombie 类指针.
- **Sandbox factor save rollback 同步清理**: `_save_factor_code` 的 except 分支之前只清名字键 dict, 现在同时清全键 dict. 与 V2.12.2 Factor dual-dict 注册表匹配.

## Sandbox Security (V2.7+V2.10+V2.12.1)
- **Forbidden imports**: os, sys, subprocess, socket, shutil, pathlib, importlib, ctypes, multiprocessing, threading, signal, pickle, http, urllib, requests, httpx, duckdb, etc.
- **Dunder access**: AST check for `__attr__` attribute access; only _SAFE_DUNDERS allowed (__init__, __getitem__, __setitem__, __contains__, __call__, etc.)
- **Dict-style dunder access**: Blocks `vars()["__import__"]`, `type.__dict__["__subclasses__"]` and similar string-key dunder subscripts (V2.10)
- **File writes**: Only to whitelisted directories: strategies/, factors/, portfolio_strategies/, cross_factors/, ml_alphas/ (V2.13 Phase 4)
- **Filename validation**: No path traversal, no hidden files, no underscore prefix
- **Contract test**: Runs in subprocess with 30s timeout
- **Failed test cleanup**: File is deleted if contract test fails
- **Windows frozen mode (V2.12.1)**: `_get_python_executable()` searches `_internal/python.exe` under `sys._MEIPASS`; returns `""` (empty) when unavailable to trigger in-process fallback; **never** falls back to `sys.executable` (would be ez-trading.exe, causing subprocess recursion)
- **In-process fallback (V2.12.1)**: `_validate_strategy_inprocess()` / `_validate_portfolio_inprocess()` validate code via `importlib.util.spec_from_file_location` + contract test in-process when no real Python bundled
- **AST class-name dedup (V2.12.1)**: `_reload_user_strategy()` filters to Strategy-subclass ClassDef nodes only (not all ClassDef) via `ast.walk` — prevents accidental deletion of unrelated helper classes
- **Agent tool timeouts (V2.12.1)**: `_run_with_timeout` wraps `run_backtest` (300s) and `run_experiment` (600s) via `ThreadPoolExecutor` with `shutdown(wait=False, cancel_futures=True)` — Python threads can't be force-killed but controller is unblocked on timeout

## Gate Rules (configurable via GateConfig)
| Rule | Default | Description |
|------|---------|-------------|
| min_sharpe | 0.5 | Minimum Sharpe ratio |
| max_drawdown | 30% | Maximum drawdown |
| min_trades | 10 | Minimum trade count |
| max_p_value | 0.05 | Monte Carlo significance |
| max_overfitting | 0.5 | WFO overfitting score |

## Design Decisions
- Runner does NOT fetch data (caller provides DataFrame) — keeps it pure/testable
- ExperimentStore uses same DuckDB file but independent tables (thin-core compliance)
- spec_id is SHA-256 of canonical JSON (excludes metadata like tags/description)
- Same spec_id with completed run → API returns duplicate status (idempotency)
- Pre-filter uses backtest-only mode (no WFO) for speed — full pipeline runs on survivors only
- BatchRunner skips duplicates via ExperimentStore.get_completed_run_id() (V2.5)
- Agent tools use ez/agent/data_access.py for singletons, NOT ez/api/deps.py (layer discipline)
- Tool framework uses decorator pattern — `@tool(name, description, params)` auto-registers
- E2 code_gen uses `allowed_tools` to restrict LLM to create_strategy/read_source/list_factors only (V2.8)
- Research runner pre-checks budget before batch execution (V2.8)

## Status
- experimental (V2.4+) — interfaces may change
- V2.17: create_ml_alpha tool (14 total), enhanced research prompts (E1/E2/E4), system prompt operation boundaries, analyzer MagicMock tolerance, research input validation (empty goal/invalid dates)
