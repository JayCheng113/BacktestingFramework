# ez-trading

Agent-native quantitative trading platform. Human researchers and AI agents use the same
pipeline, gates, and audit trail.

- Stack: Python 3.12+, FastAPI, DuckDB, React 19, ECharts, C++ (`nanobind`)
- Version: `0.3.3`
- Recent verified baseline: `874` live/api tests passed (V3 QMT closure + capital wire-in 2026-04-16); full backend `3048` tests, frontend `96` tests, all green
- Versioning note: `0.3.x` marks the product-level transition into the V3 execution/broker era; roadmap labels such as `V3.3.x` remain the architecture-stage line

## Read First

Read these before major architectural changes:

- `docs/architecture/system-architecture.md`
- `docs/architecture/governance.md`
- `docs/core-changes/v2.3-roadmap.md`

## Architecture Summary

Core dependency flow:

```text
ez/types.py -> ez/data/ -> ez/factor/ -> ez/strategy/ -> ez/backtest/ -> ez/api/ -> web/
                            ↑ ts_ops                      ↑ matcher
                            └────────── ez/core/ ─────────┘

ez/llm/ depends only on config
ez/agent/ consumes backtest/core/llm interfaces
ez/live/ builds on portfolio + deployment infrastructure
ez/research/ is a reusable research workflow layer
```

High-level module map:

- `ez/core/`: computational primitives (`matcher`, `ts_ops`, `market_rules`)
- `ez/data/`: ingestion, validation, caching, parquet/DuckDB store, provider chain
- `ez/factor/`: factor computation, evaluation, built-in technical + fundamental factors
- `ez/strategy/`: single-name strategy framework and auto-registration
- `ez/backtest/`: single-name engine, metrics, walk-forward, significance
- `ez/portfolio/`: universe, cross-sectional factors, portfolio engine, optimizer/risk, attribution, MLAlpha, ensemble
- `ez/research/`: reusable research pipelines and steps
- `ez/llm/`: provider abstraction
- `ez/agent/`: coding/research agent, sandbox, tools, guards, assistant
- `ez/live/`: paper-trading deployment lifecycle, scheduler, monitor
- `ez/api/`: FastAPI routes
- `web/`: React dashboard

Per-module details live in submodule memory files:

- `ez/core/CLAUDE.md`
- `ez/data/CLAUDE.md`
- `ez/factor/CLAUDE.md`
- `ez/strategy/CLAUDE.md`
- `ez/backtest/CLAUDE.md`
- `ez/portfolio/CLAUDE.md`
- `ez/llm/CLAUDE.md`
- `ez/agent/CLAUDE.md`
- `ez/live/CLAUDE.md`
- `ez/api/CLAUDE.md`
- `web/CLAUDE.md`

## Engineering Rules

- Preserve the dependency direction above. `ez/core/` stays leaf-like.
- Prefer extension points over core edits.
- Core semantic files should not be changed casually; if behavior changes materially, document it in `docs/core-changes/`.
- No version tag or push without code review.
- On this macOS environment, prefer `scripts/run_pytest_safe.sh ...` over raw `pytest` because the system `readline` extension segfaults during pytest startup; the script injects a local shim and loads `pytest_asyncio` explicitly.

Core files to treat as high-risk:

- `ez/types.py`, `ez/errors.py`, `ez/config.py`
- `ez/core/matcher.py`, `ez/core/ts_ops.py`, `ez/core/market_rules.py`
- `ez/data/provider.py`, `ez/data/validator.py`, `ez/data/store.py`
- `ez/factor/base.py`, `ez/factor/evaluator.py`
- `ez/strategy/base.py`, `ez/strategy/loader.py`
- `ez/backtest/engine.py`, `ez/backtest/portfolio.py`, `ez/backtest/metrics.py`
- `ez/backtest/walk_forward.py`, `ez/backtest/significance.py`

Preferred extension points:

- Data source: `ez/data/providers/`
- Factor: `factors/` or `ez/factor/builtin/`
- Strategy: `strategies/` or `ez/strategy/builtin/`
- ML Alpha: `ml_alphas/`

## Current Platform Snapshot

This repo already supports:

- Single-name backtesting
- Portfolio / rotation backtesting
- A-share market rules: `T+1`, lot size, limit-up/down, stamp tax
- Walk-forward analysis and significance testing
- Cross-sectional factor research, IC / RankIC / decay / correlation
- Fundamental data layer and built-in fundamental cross factors
- Portfolio optimizer, risk manager, attribution
- LLM coding assistant and autonomous research agent
- Sandboxed user code editing with save-time guards
- Reusable research pipelines (`NestedOOS`, `WalkForward`, `PairedBlockBootstrap`)
- MLAlpha with diagnostics
- StrategyEnsemble
- Paper trading: deploy gate, scheduler, monitor, snapshots, SSE, optional auto-tick, optional webhook alerts
- OMS-lite / PaperBroker / event-ledger recovery
- QMT broker stack: official XtTrader session bridge, process-owned shared session orchestration, explicit process-owner warmup/introspection, consumer supervision/auto-restart observability, callback-preferred incremental execution sync, callback-driven account-state progression, callback freshness/fallback gate for account state, callback-loop state snapshots, owner/runtime lifecycle events, callback-push projection refresh, explicit broker-sync pump, broker-state audit, cancel request path, fail-closed submit/release gates, and small-whitelist real submit
- Mixed-market auto-tick now batches by market-local business date and rejects non-positive intervals
- Historical non-CN deployments built during the old `_build_spec_from_run` bug window now fail closed on start/resume instead of silently running with CN rules
- Local parquet cache path for market data

## Important Semantic Facts

### A-share constraints

These assumptions are foundational:

- Long-only for cash equities
- No stock shorting
- Stamp tax on sells must be modeled
- `T+1`, lot-size, and limit-up/down rules matter for CN equities
- Pair trading / market-neutral equity legs are out of scope until futures infrastructure exists

### Metrics formula change

`V2.12.2` unified portfolio and single-name metric formulas for:

- `sharpe_ratio`
- `sortino_ratio`
- `alpha`
- `beta`
- `profit_factor`

Meaning:

- Runs stored before `V2.12.2` may use old formulas
- Runs stored after `V2.12.2` use the standardized formulas
- Historical and new values for those metrics are not directly comparable

### Adj-close vs raw-close contract

- Factor layers use `adj_close` semantics; built-in factor tests guard this
- Engines were hardened so valuation stays on adjusted units where needed
- `raw_close` is still required for market-rule checks such as limit-up/down
- If `open` is missing, the backtest engine now falls back to raw `close` before applying the adjustment ratio, avoiding dividend-day double adjustment

Important exception:

- The three QMT-parity portfolio strategies in `ez/portfolio/builtin_strategies.py`
  intentionally use `raw_close` for signal construction to match QMT behavior
- This is a design choice, not an accident
- Side effect: dividend dates can create slightly distorted signals versus `adj_close`-based logic

### Cross-engine consistency matters

There are explicit canary tests to keep single-name backtest, portfolio backtest, and
paper-trading behavior aligned. Silent cross-engine drift is treated as a serious bug class.

## Data Layer Facts

- Primary source is Tushare
- AKShare is fallback for some ETF / raw data cases
- Local parquet cache is supported and preferred when present
- Cache/data rebuilds can change results; portfolio runs record a diagnostic `_data_hash`
- Data hash is for comparability warnings, not a blocker

## Agent / Sandbox Facts

- User code save path is protected by `ez.testing.guards`
- Guard categories include lookahead, NaN/Inf, weight-sum, non-negative weights, and determinism
- Sandbox security blocks dangerous imports and access paths into sensitive internals
- Autonomous research is intentionally serialized; one research task at a time is a design choice

## MLAlpha Contract

`MLAlpha` is productionized but intentionally constrained.

- Supported estimators: `Ridge`, `Lasso`, `LinearRegression`, `ElasticNet`,
  `DecisionTreeRegressor`, `RandomForestRegressor`, `GradientBoostingRegressor`,
  `LGBMRegressor`, `XGBRegressor`
- Classifiers are not part of the contract
- New estimator support requires explicit tests and contract review
- `feature_warmup_days` defaults to `0`; users must declare it if feature engineering needs warmup
- Diagnostics include feature-importance stability, IS/OOS IC decay, turnover, and an overfitting verdict

## Paper Trading Facts

Paper trading is no longer experimental. Key properties:

- Deployment is content-addressed through `DeploymentSpec` (spec_id hash now folds `broker_type` / `shadow_broker_type` — V3.2 hardening)
- `DeployGate` is stricter than research gating
- Scheduler is idempotent and currently single-process; per-deployment `asyncio.Lock` serializes `tick / cancel / pause / resume / pump_broker_state`
- `Scheduler.tick()` writes events + snapshot + broker-order-links in one DuckDB transaction via `DeploymentStore.save_snapshot_with_events(...)`; partial failures roll back
- `LiveLedger.replay()` is idempotent: duplicate `event_id` / broker-execution / cancel-requested events are de-duplicated
- Broker order state machine is forward-only across terminal transitions (`PARTIALLY_FILLED → PART_CANCEL / CANCELED` allowed; `FILLED ↛ CANCELED`)
- Strategy state persistence across restart is supported
- Optional background auto-tick is env-gated; mixed-market batching by market-local date
- Optional webhook alert dispatch is env-gated
- OMS-lite, event-first restore, and broker abstraction are in place
- QMT supports both shadow/reconcile and small-whitelist real submit with defense-in-depth gates: scheduler tick invokes `QMTRealBroker.open_submit_gate(projection)` / `close_submit_gate()` around every real submit, so calling `execute_target_weights` without the runtime gate open fail-closes immediately
- `XtQuantShadowClient` sync path is lock-guarded (`_sync_lock`) with callback-freshness threshold (30s) fallback so an empty callback buffer cannot mask fresh query events; reconnect backoff is capped at 60s with ±20% jitter
- `DeploymentStore._lock` is a `threading.RLock` so the cancel-ack upsert path can re-enter `list_broker_order_links_by_broker_order_id` without deadlocking
- `build_qmt_release_gate_decision(..., hard_gate=...)` folds `qmt_reconcile_hard_gate` truth; release never shows `eligible_for_real_submit` while hard gate is blocked
- **Four-way reconcile closure** (V3.3.44): `account + order + position + trade` are independent reconcile events per tick; `reconcile_broker_positions` and `reconcile_broker_trades` are surfaced in `/broker-state` as `latest_position_reconcile` / `latest_trade_reconcile`; `build_qmt_reconcile_hard_gate` fails closed if any of four drifts
- **Callback-driven lifecycle closure** (V3.3.43): when `XtQuantShadowClient` callback consumer is alive and every submit-ack order has seen a fresh `on_stock_order` / `on_stock_trade` / `on_order_error` callback, `list_execution_reports` runs `callback_only` mode without `query_stock_orders` fallback; stale submit-ack → `callback_query_merge`; consumer dead → `query_only`; mode is surfaced as `execution_sync_mode` on `collect_sync_state` / `session_consumer_state`
- **Host-external QMT orchestration** (V3.3.45): `ez.live.qmt_host.QMTHostService` owns long-running session / callback consumer / reconnect supervisor independently of `Scheduler`; `ensure_ready_or_raise()` fail-closes any submit/cancel when host health != `READY`; subscribers (schedulers) can come and go without killing the session
- **Capital expansion framework** (V3.3.46): `ez.live.capital_policy.CapitalPolicyEngine` supports stage-based ladder (`read_only → paper_sim → small_whitelist → expanded → full`) with per-stage `max_capital_per_day / max_position_value_per_symbol / max_total_gross_exposure`; stage transitions gated on `min_days_no_drift + min_order_success_rate`; env var `EZ_LIVE_QMT_KILL_SWITCH` instantly downgrades any real-broker stage to `paper_sim`; `PreTradeRiskEngine` evaluates capital policy before all other rules
- **Capital policy wire-in** (V3.3.58): `PaperOMS.execute_rebalance` now reads `risk_params["capital_policy"]` via `CapitalPolicyConfig.from_params()` and constructs `CapitalPolicyEngine` + propagates `broker_type` from `DeploymentSpec` through to `PreTradeRiskEngine`; spec config `{"capital_policy": {"enabled": true, "stage": "small_whitelist"}}` is the minimal activation; kill-switch integration test covers the end-to-end OMS → risk → reject path

Operational caveats:

- Scheduler is single-process; large deployment counts will not scale indefinitely
- Data timeliness is end-of-day oriented; delayed source updates can affect same-day expectations
- QMT real submit is intentionally constrained: whitelist + capital preflight + runtime hard gate are in place; host-level always-on real-QMT callback ownership, explicit process-owned warmup/introspection, execution-broker cancel routing, callback execution-report consumption, receipt-time callback event persistence, success-ack cancel-pending closure, cursor-based terminal cancel confirm, real-scoped execution-broker reconcile hard gate, runtime projection provenance surfaced through broker-state/dashboard/UI, reconcile recovery triad coverage, callback-aware snapshot execution-state merging with fresher-query fallback, callback-aware broker snapshot/report reads including the direct `since=None` report path, and out-of-order cancel-ack/cancel-error link hardening are now in place; the next remaining gap is fuller real-broker callback order-state closure

## Research Framework Facts

`ez.research` exists to replace ad hoc `validation/` scripts with reusable workflows.

Main abstractions:

- `PipelineContext`
- `ResearchStep`
- `ResearchPipeline`
- reusable steps in `ez/research/steps/`
- optimizer/objective abstractions in `ez/research/optimizers/`

The validation stack now supports:

- block-bootstrap significance
- paired comparison vs baseline
- deflated Sharpe ratio
- minimum backtest length
- annual breakdown
- unified pass/warn/fail verdicts

## Known Limitations

Keep these in mind before over-engineering around them:

- Research tasks do not support crash recovery
- Research execution is intentionally serialized
- Scheduler is single-process
- C++ accelerated path still holds the GIL
- Provider-chain dedup/routing is still fairly flat
- `n_trials` for validation is still user-supplied rather than persisted from search history
- Validation baseline selectors / run pickers still only surface the latest bounded set of runs
- Some API layering is functional but slightly smelly, especially store access reuse

## Quick Commands

```bash
./scripts/start.sh
./scripts/stop.sh
pytest tests/
python scripts/benchmark.py
pip install -e . --no-build-isolation
```
