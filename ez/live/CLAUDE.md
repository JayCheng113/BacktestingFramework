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
V2.16.2 -- beta. Known limitations:
- strategy.state not persisted across process restarts
- Data freshness depends on provider update timing
- ~~Stop does not trigger liquidation~~ -- V2.16: `liquidate=True` option added to stop_deployment
- Single-process scheduler (no multi-worker)
- Historical DeploymentSpecs (pre-V2.16.2) on non-CN markets may carry A-share
  rules; `_start_engine` logs a warning so the operator can redeploy from source run

## V2.16.2 — silent-bug fix batch
Post-V2.15 audit found several V2.18.1-class silent bugs. Four commits:
- **spec market rule gating** (`api/routes/live.py::_build_spec_from_run`): prior
  version read cost/limit fields from top-level `config.get(...)` but `/run`
  persists them under nested `config._cost` bucket → every deployment silently
  fell back to hardcoded CN defaults. Non-CN deployment with default config → T+1
  + 0.05% stamp tax + 10% limit + 100-share lot mis-applied. Fixed: read from
  `_cost` bucket first, top-level second, market-gated default last.
- **paper_engine NaN fallback** (`paper_engine.py::_get_latest_prices` and
  `_get_raw_closes`): `float(df["adj_close"].iloc[-1])` inserted NaN into prices
  dict when adj not yet populated, crashing `execute_portfolio_trades._lot_round(NaN)`
  with ValueError. Parity with V2.18.1 backtest: adj→raw fallback + `math.isfinite`.
- **Scheduler future-date guard** (`scheduler.py::tick`): user-supplied future
  business_date would advance `last_processed_date` past wall clock, silently
  blocking subsequent ticks. Refuse with ValueError.
- **Historical spec warning** (`scheduler.py::_start_engine`): pre-fix specs have
  A-share rules on non-CN markets; spec_id is content-hashed so can't silently
  rewrite. Log loud warning advising redeploy.

Regression: 171 → 182 tests (+11). All mutation-verified.

## V2.17 — strategy.state + auto-tick
- **strategy.state 跨重启持久化**: `deployment_snapshots.strategy_state` BLOB 列 + ALTER 迁移. `save_daily_snapshot(..., strategy_state: bytes | None)` 可选传入. `get_latest_strategy_state()` 取最新非空 blob. `Scheduler.tick` 成功后 `_pickle_strategy` pickle + 一次性 warn 失败. `Scheduler._start_engine` `_unpickle_strategy` + class-name guard + fallback to fresh 构造. 9 regression tests.
- **auto-tick loop** (`ez/api/app.py`): asyncio background task, `EZ_LIVE_AUTO_TICK=1` 启用, interval 默认 3600s (`EZ_LIVE_AUTO_TICK_INTERVAL_S` 可调). 三层异常: CancelledError 干净退出; ValueError (future-date guard) warn + continue; 其他 Exception log.exception + continue. Loop 永不自杀. 5 regression tests.
- **Scheduler future-date guard**: `tick(business_date)` 拒绝未来日期防污染 `last_processed_date`.
- Regression: 182 → 196 tests (+14).
