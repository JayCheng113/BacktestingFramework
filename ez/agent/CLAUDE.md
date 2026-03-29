# ez/agent — Agent Research Loop (experimental, V2.4+)

## Responsibility
Provide a standardized pipeline for automated and human-driven strategy research:
RunSpec → Runner → Gate → Report → Store.
V2.5 adds batch parameter search: CandidateSearch → PreFilter → BatchRunner → Ranking.
V2.7 adds AI assistant: Tools → Assistant → Chat, plus code sandbox and FDR correction.

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
- `chat_sync()` / `chat_stream()` — Agent loop with tool calling (V2.7, sync)
- `achat_stream()` — Async streaming agent loop (V2.7.1, non-blocking)
- `save_and_validate_strategy()` — Code sandbox: save + contract test (V2.7)
- `apply_fdr()` — FDR correction for batch search results (V2.7)

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
| assistant.py | Agent loop: message → LLM → tool_use → execute → respond (V2.7) |
| sandbox.py | Code validation: syntax check, forbidden imports, contract test (V2.7) |
| fdr.py | False Discovery Rate correction: Bonferroni, Benjamini-Hochberg (V2.7) |
| data_access.py | Agent-layer data singletons (avoids L5→L6 import) (V2.7) |

## Dependencies
- Upstream: ez/backtest/ (Engine, WFO, significance), ez/core/ (Matcher), ez/strategy/ (Strategy registry), ez/llm/ (LLMProvider, V2.7)
- Downstream: ez/api/routes/experiments.py, ez/api/routes/candidates.py, ez/api/routes/code.py, ez/api/routes/chat.py, web/

## Agent Tools (V2.7)
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

## Sandbox Security (V2.7)
- **Forbidden imports**: os, sys, subprocess, socket, shutil, pathlib, importlib, ctypes, multiprocessing, threading, signal, pickle, http, urllib, requests, httpx, duckdb, etc.
- **File writes**: Only to strategies/ directory
- **Filename validation**: No path traversal, no hidden files, no underscore prefix
- **Contract test**: Runs in subprocess with 30s timeout
- **Failed test cleanup**: File is deleted if contract test fails

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

## V2.8 Autonomous Research Agent
| File | Role |
|------|------|
| hypothesis.py | E1: LLM hypothesis generation from research goal |
| code_gen.py | E2: LLM strategy code generation with sandbox validation |
| analyzer.py | E4: LLM result analysis and direction suggestions |
| loop_controller.py | E5: Budget tracking, convergence detection, stop conditions |
| research_report.py | E6: Aggregate iterations into final report |
| research_store.py | DuckDB persistence: research_tasks + research_iterations |
| research_runner.py | Main orchestrator: coordinate E1-E6 in async loop with SSE events |

## Status
- experimental (V2.4+) — interfaces may change
