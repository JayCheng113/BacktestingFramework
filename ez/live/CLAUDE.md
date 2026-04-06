# ez/live -- Paper Trading Bridge (V2.15)

## Responsibility
Deployment lifecycle (spec -> gate -> paper trading -> monitoring) for strategies that pass research gate.
Bridge between offline backtest research and forward-looking paper execution.

## Public Interfaces
- `DeploymentSpec` -- Immutable strategy config with content hash (SHA-256[:16]), sorted symbols/params for idempotent spec_id
- `DeploymentRecord` -- Mutable runtime record (state machine: pending -> approved -> running <-> paused -> stopped / error)
- `DeployGate` -- Non-bypassable 10-check deployment gate (4 phases: source existence, research metrics, WF metrics, deploy-specific)
- `DeployGateConfig` -- Configurable thresholds (min_sharpe=0.5, max_drawdown=0.25, min_trades=20, etc.)
- `PaperTradingEngine` -- Daily bar-driven paper execution, reuses PortfolioStrategy + execute_portfolio_trades + CostModel
- `Scheduler` -- Single-process idempotent scheduler with asyncio.Lock, per-market TradingCalendar, auto-recovery (resume_all)
- `Monitor` -- Health dashboard (DeploymentHealth dataclass) + alert checks (drawdown, loss streak, stale execution, errors)
- `DeploymentHealth` -- Per-deployment health summary dataclass
- `DeploymentStore` -- DuckDB persistence (3 tables: deployment_specs, deployment_records, deployment_snapshots)

## Files
| File | Role |
|------|------|
| `__init__.py` | Package docstring |
| `deployment_spec.py` | DeploymentSpec (immutable, content-hashed) + DeploymentRecord (mutable lifecycle) |
| `deploy_gate.py` | DeployGate (10 checks, 4 phases) + DeployGateConfig (thresholds) |
| `deployment_store.py` | DuckDB persistence: 3 tables, CRUD, snapshot save/query |
| `paper_engine.py` | PaperTradingEngine: daily execute_day(), reuses backtest execution logic |
| `scheduler.py` | Scheduler: tick(), start/stop/pause/resume, resume_all(), error escalation |
| `monitor.py` | Monitor: get_dashboard(), check_alerts(), DeploymentHealth dataclass |

## Dependencies
- Upstream: `ez/portfolio/` (PortfolioStrategy, execute_portfolio_trades, CostModel, TradingCalendar, PortfolioOptimizer, RiskManager), `ez/data/` (DataProviderChain), `ez/agent/gates.py` (GateReason, GateVerdict)
- Downstream: `ez/api/routes/live.py` (13 REST endpoints), `web/` (PaperTradingPage)

## Key Design Decisions
- **Reuse over rewrite**: PaperTradingEngine calls the same `execute_portfolio_trades()` as backtest engine
- **Content-addressed specs**: DeploymentSpec.spec_id is deterministic hash -- same logical config always produces same ID
- **Non-bypassable gate**: DeployGate has no skip/override mechanism; all 10 checks must pass
- **Idempotent scheduler**: last_processed_date per deployment prevents duplicate execution on retry
- **Error escalation**: 3 consecutive errors -> automatic status="error" + engine removal (no infinite retry)

## Status
V2.15 -- experimental. Known limitations:
- strategy.state not persisted across process restarts
- Data freshness depends on provider update timing
- Stop does not trigger liquidation
- Single-process scheduler (no multi-worker)
