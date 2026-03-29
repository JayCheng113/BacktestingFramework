# ez/agent — Agent Research Loop (experimental, V2.4+)

## Responsibility
Provide a standardized pipeline for automated and human-driven strategy research:
RunSpec → Runner → Gate → Report → Store.
V2.5 adds batch parameter search: CandidateSearch → PreFilter → BatchRunner → Ranking.

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

## Dependencies
- Upstream: ez/backtest/ (Engine, WFO, significance), ez/core/ (Matcher), ez/strategy/ (Strategy registry)
- Downstream: ez/api/routes/experiments.py, ez/api/routes/candidates.py, web/ExperimentPanel, web/CandidateSearch

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

## Status
- experimental (V2.4+) — interfaces may change
