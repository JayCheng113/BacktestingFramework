# ez/agent — Agent Research Loop (experimental, V2.4)

## Responsibility
Provide a standardized pipeline for automated and human-driven strategy research:
RunSpec → Runner → Gate → Report → Store.

## Public Interfaces
- `RunSpec` — Immutable experiment input with content-hash `spec_id` for idempotency
- `Runner` — Executes RunSpec against data: backtest + WFO + significance
- `RunResult` — Structured output from Runner (backtest/WFO results, timing, git SHA)
- `ResearchGate` — Evaluates RunResult against configurable thresholds
- `GateVerdict` — Pass/fail with per-rule reasons
- `ExperimentReport` — Flattened metrics + gate result for storage/API
- `ExperimentStore` — DuckDB persistence (experiment_specs + experiment_runs tables)

## Files
| File | Role |
|------|------|
| run_spec.py | RunSpec dataclass + spec_id hash |
| runner.py | Runner + RunResult |
| gates.py | ResearchGate + GateConfig + GateVerdict + GateReason |
| report.py | ExperimentReport |
| experiment_store.py | DuckDB persistence (own tables, not modifying core store) |

## Dependencies
- Upstream: ez/backtest/ (Engine, WFO, significance), ez/core/ (Matcher), ez/strategy/ (Strategy registry)
- Downstream: ez/api/routes/experiments.py, web/ExperimentPanel

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

## Status
- experimental (V2.4) — interfaces may change
