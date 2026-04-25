# ez/live

## Responsibility
Deployment lifecycle and paper-trading execution for strategies that graduate from research.
This module now sits between two eras:

- current: engine-driven paper trading
- next: OMS-driven execution stack

## Public Interfaces
- `DeploymentSpec` — immutable, content-addressed deployment config
- `BrokerAdapter` — execution contract for paper and future real brokers
- `DeploymentRecord` — mutable lifecycle record (`pending -> approved -> running/paused -> stopped/error`)
- `DeploymentStore` — DuckDB persistence for specs, records, snapshots, and append-only events
- `DeployGate` — non-bypassable deployment gate
- `PaperTradingEngine` — daily execution engine reusing portfolio strategy/optimizer/risk logic
- `PaperOMS` — V3.0-lite Phase 1 minimal OMS wrapper around live execution
- `PreTradeRiskEngine` — order-intent hard reject gate before execution
- `RuntimeAllocator` — pre-order target-weight allocator / runtime gate
- `Monitor` — health summary + alert generation
- `Scheduler` — single-process orchestrator for deployment lifecycle and daily ticks

## Files
- `broker.py` — broker adapter ABC + shared execution-report models
- `deployment_spec.py` — spec + record models
- `deployment_store.py` — specs / records / snapshots / `deployment_events`
- `deploy_gate.py` — deploy-time checks
- `paper_engine.py` — daily live execution path
- `oms.py` — minimal order/fill/event orchestration + replay
- `paper_broker.py` — paper execution boundary returning broker fill reports
- `ledger.py` — shared event reducer for restore / replay / checkpoint catch-up
- `allocation.py` — runtime target-weight allocation policies
- `optimizer_allocator.py` — deterministic constrained projection for target-weight live rebalancing
- `risk.py` — pre-trade order-level rejection rules
- `events.py` — order, fill, event models
- `scheduler.py` — start/pause/resume/stop/tick/recovery (orchestration core)
- `_snapshot_collectors.py` — snapshot serialization and reconcile helpers (extracted from scheduler)
- `_broker_pump.py` — broker state pump helpers (extracted from scheduler)
- `_utils.py` — shared private utilities (utc_now, coerce_timestamp, get_field, etc.)
- `_broker_order_links.py` — BrokerOrderLinkRepository (extracted from deployment_store)
- `monitor.py` — dashboard + alerts
- `alert_dispatcher.py` — webhook dispatch

### qmt/ — QMT 券商接入子包
- `qmt/broker.py` — QMT read-only/shadow broker, official xtquant session bridge, report normalization, and gate builders
- `qmt/session_owner.py` — resident QMT session owner, `QMTSessionManager`, `XtQuantShadowClient`, reconnect logic
- `qmt/callback_bridge.py` — XtQuant trader callback bridge + consumer thread
- `qmt/host.py` — `QMTHostService`: long-running host-external QMT orchestration (V3.3.45)
- `qmt/_projection.py` — QMT runtime projection builder (stateless helpers extracted from scheduler)
- `qmt/reconcile.py` — broker-state reconciliation: account, orders, positions, trades

## Key Design Facts
- Reuse over rewrite: fill semantics still come from `ez.portfolio.execution.execute_portfolio_trades()`
- `DeploymentSpec` now declares `broker_type` (default `paper`) so broker selection is explicit, hashed, and audit-safe
- Event log is append-only and idempotent by deterministic `event_id`
- `client_order_id` is deterministic per deployment/date/symbol/side
- Scheduler now persists OMS events + daily snapshot atomically in one store transaction
- `SNAPSHOT_SAVED` events now carry restore checkpoint fields (`cash` / `holdings` / `weights` / `prev_returns`) alongside the persisted snapshot row
- Scheduler now appends day-level `RISK_RECORDED` events and a terminal `TICK_COMPLETED` event into the same atomic event transaction
- Scheduler now also appends a day-level `MARKET_SNAPSHOT` event capturing the price map and bar coverage seen by the live engine
- Scheduler now also appends per-symbol day bars as `MARKET_BAR_RECORDED`, and timestamps runtime events so `market_bar -> market_snapshot -> order/fill -> risk/snapshot/tick` replays in logical order
- Recovery is now event-first when the log contains `SNAPSHOT_SAVED` checkpoints; snapshot rows remain compatibility fallback and drift detectors for legacy deployments
- OMS replay and scheduler recovery now share the same `LiveLedger` reducer, so fills / statuses / checkpoint events are interpreted once
- Snapshot schema now persists `liquidation=True` on stop-with-liquidate final snapshots
- OMS no longer calls `execute_portfolio_trades()` directly; paper execution now flows through `PaperBroker`
- `PaperBroker` is now an implementation of the formal `BrokerAdapter` contract; `PaperOMS`, `PaperTradingEngine`, and `Scheduler` all resolve brokers through that contract instead of hard-coding paper execution
- `QMTShadowBroker` now owns the read-only/shadow path: account snapshots, execution/runtime normalization, reconcile, broker-state, and cancel/audit plumbing without changing paper execution semantics
- `QMTRealBroker` now owns the explicit small-whitelist real-submit path: submit/release gates stay fail-closed, actual fills still arrive through callback/query sync, and submit-time ack identity is preserved separately from later broker-order identity
- `QMTShadowBroker` is now aligned with official xtquant semantics: lazy `XtQuantTrader(path, session_id)` bootstrap, `StockAccount(account_id[, account_type])`, `register_callback -> start -> connect -> subscribe`, official order-status normalization, and optional cancel path via `cancel_order_stock`
- Scheduler now supports `shadow_broker_type`; shadow brokers can feed broker-state reconciliation and standardized broker execution reports into the append-only event log without changing paper execution semantics
- Broker-order identity is now persisted via `deployment_broker_order_links`, so `client_order_id <-> broker_order_id(order_sysid) <-> latest_report_id/status` survives across ticks and restart boundaries
- Live API / Scheduler now expose a minimal cancel-request path that resolves `client_order_id` or `broker_order_id` through `deployment_broker_order_links` before calling the broker adapter; final `canceled` state still depends on later broker execution reports/callbacks
- QMT shadow report ingestion now merges official callback-buffer events with `query_stock_orders/query_stock_trades`, dedupes repeated callback payloads, and dedupes normalized reports by `report_id` before they enter scheduler/store
- `deployment_broker_order_links` now treat `latest_status/latest_report_id` as forward-only state: newer broker reports can advance a link to `canceled`, but older/staler reports no longer regress it back to `partially_filled`
- QMT shadow now also normalizes official runtime callbacks (`on_account_status`, `on_order_stock_async_response`, `on_disconnected`) into broker runtime events, and scheduler persists them as `BROKER_RUNTIME_RECORDED`
- QMT shadow now also normalizes the official `on_cancel_order_stock_async_response` callback into broker runtime events, so cancel acks enter the same append-only audit stream as account/session callbacks
- Live API now exposes `/deployments/{id}/broker-orders`, so shadow/real broker link state is queryable without opening DuckDB directly
- Scheduler now persists raw shadow broker account snapshots as `BROKER_ACCOUNT_RECORDED`, and reuses the same snapshot read for both raw audit state and broker-reconcile drift computation
- Scheduler now also computes `broker_order_reconcile` from the same broker snapshot plus current broker-order links/current reports, so account drift and order drift are checked together without extra broker reads
- Live API now exposes `/deployments/{id}/broker-state`, returning the latest broker account event, recent runtime events, the latest broker-reconcile summary, and the latest broker-order-reconcile summary
- There is now a real API E2E covering `qmt shadow partial -> cancel request -> canceled confirm -> broker-orders API`
- Monitor/dashboard now surfaces broker health from persisted state only: latest `broker_reconcile`, latest `broker_order_reconcile`, and latest broker runtime disconnect/account status, with alert types for broker drift and disconnected sessions
- `PaperBroker` now emits per-order execution reports (`requested/fill/remaining/status`) so OMS can distinguish `FILLED` vs `PARTIALLY_FILLED` vs execution reject
- Unsupported `broker_type` values now fail closed at engine start; this keeps future QMT/PTrade rollout explicit instead of silently falling back to paper
- Pre-trade risk now emits structured reject payloads (`reason` / `message` / `details`) into OMS events and daily `risk_events`
- Pre-trade risk currently supports kill-switch, max order notional, max position weight, max daily turnover, max concentration, and max gross exposure
- Runtime allocator now supports `pro_rata_cap` / `equal_weight_cap` / `risk_budget_cap` / `constrained_opt`, optional `max_names`, `runtime_allocation_cap`, `max_position_weight`, `max_daily_turnover`, `covariance_risk_aversion`, `risk_budget_strength`, and `target_portfolio_vol`
- `constrained_opt` only applies to the current target-weight rebalance path (`strategy -> allocator -> OMS -> execution`), not to future broker-native/manual/split-order flows
- `constrained_opt` is now covariance-aware and can blend toward a risk-budget target using price-history covariance from the live engine; industry constraints are still out of scope until industry context is wired into live allocation
- Live API now has a real scheduler-backed E2E that covers deploy -> approve -> start -> tick -> pause/resume -> restart/restore -> stop/liquidate
- The runtime backbone now records `market/order/fill/risk/snapshot/tick` events in `deployment_events`; `risk_events` still remain in snapshot JSON for dashboard compatibility
- Paper trading is beta-grade, not yet a true OMS architecture

## Current Status
Completed:

- deployment lifecycle
- deploy gate
- paper engine
- scheduler + auto-tick
- monitor + webhook alerts
- strategy-state persistence across restart
- cross-engine correctness canaries
- minimal OMS Phase 1: order/fill/event models, append-only event log, replay support
- PaperBroker boundary: OMS and stop-liquidation execution now consume broker fill reports instead of calling execution helpers directly
- Phase 3 recovery: event-first restore from snapshot checkpoints + legacy snapshot-baseline fallback for old deployments
- Shared event-ledger reducer: `LiveLedger` now powers OMS replay, event-first restore, legacy drift checks, and checkpoint-based fallback
- Minimal runtime backbone: `RISK_RECORDED` and `TICK_COMPLETED` now complement OMS + snapshot events in the append-only log
- Day-level market-data backbone: `MARKET_SNAPSHOT` now captures the live price map/bar coverage per tick
- Per-symbol day-bar backbone: `MARKET_BAR_RECORDED` now captures each symbol's latest OHLCV/adj_close before the aggregate market snapshot
- Partial-fill semantics: OMS and `LiveLedger` now understand `ORDER_PARTIALLY_FILLED` and preserve remaining-share information in event payloads
- V3.1 Phase 1: pre-trade hard reject gate wired into OMS and surfaced in daily `risk_events`
- V3.1 Phase 2: structured reject reasons + concentration/gross-exposure checks + runtime allocation gate
- V3.1 Phase 3: runtime allocator for pre-order target-weight reshaping
- V3.1 Phase 4: volatility-aware advanced allocator (`risk_budget_cap` + `target_portfolio_vol`)
- V3.1 Phase 5: covariance-aware constrained optimizer allocator for live target-weight rebalancing
- live API E2E: real scheduler lifecycle coverage, including restart recovery and liquidation snapshot persistence
- pre-QMT broker abstraction: `BrokerAdapter` + `broker_type` + scheduler/engine/OMS broker resolution are now in place for future real-broker adapters
- QMT first stage: shadow/read-only broker skeleton + standardized broker execution reports are now available without importing xtquant at module import time
- shadow broker plumbing: scheduler can now reconcile broker snapshots and ingest standardized broker execution reports into `deployment_events` with deterministic `report_id -> event_id` idempotency
- shadow broker order identity: scheduler now atomically upserts broker-order links alongside broker-report events, including a stable synthetic `client_order_id` fallback when broker callbacks do not carry `order_remark`
- official QMT session bridge is now in place for future real-broker rollout: lazy xtquant import, startup sequencing, callback buffering, and optional cancel capability are implemented without changing paper execution semantics
- QMT shadow brokers with the same `account_id/account_type/install_path/session_id` now reuse one in-process shadow session owner, so repeated scheduler/broker construction does not repeatedly bootstrap xtquant in the same process
- QMT shadow session lifecycle is now normalized into broker runtime events (`session_bootstrap_started` / `session_started` / `session_connected` / `session_subscribed` / `*_failed`), so broker-state/monitor can observe session progress before real order callbacks arrive
- QMT shared session owner itself is now observable: managed shadow brokers expose `session_owner_created` / `session_owner_reused` / `session_owner_create_failed`, and `QMTSessionState` tracks acquisition count, last access, and last error without opening real submit
- Scheduler now syncs shadow-broker runtime events at `start/resume` time, so QMT session/account runtime state no longer waits for the first daily tick to enter `deployment_events`
- Live API `broker-state` now returns `latest_session_runtime` and `latest_session_owner_runtime` summaries in addition to recent runtime events
- Scheduler now exposes `pump_broker_state()` and the live API exposes `POST /api/live/deployments/{id}/broker-sync`, so QMT shadow runtime/account/execution state can be synchronized without waiting for the next daily tick
- QMT shadow brokers now support an efficient sync bundle path (`collect_sync_state`) that shares one client/session fetch across account snapshot, execution reports, and runtime events; scheduler `tick()` and `broker-sync` both reuse it
- `broker-state` now reads the latest broker/order reconcile summaries from `RISK_RECORDED` events first, instead of only depending on the latest snapshot payload
- `XtQuantShadowClient` now supports a process-local `run_forever()` consumer skeleton: first owner attach starts a daemon callback consumer, and the last owner close can stop/join it while keeping `session_consumer_*` lifecycle events observable
- `QMTSessionManager` now harvests `session_consumer_*` teardown events before evicting the last shared shadow client, so consumer stop/failure events survive client teardown and can still enter `deployment_events`
- `QMTSessionManager` now also supervises managed callback consumers: if a deployment-owned QMT session still has owners but its `run_forever()` consumer is no longer alive, scheduler sync paths can trigger `session_consumer_restarted` / `session_consumer_restart_failed` and expose that state through `broker-state`
- `XtQuantShadowClient` execution sync is now callback-preferred for incremental paths: when the callback consumer is alive, `list_execution_reports()` / `collect_sync_state()` consume callback execution deltas first and only fall back to `query_stock_orders/query_stock_trades` for cold-start or no-consumer cases
- broker-order reconcile now overlays the latest callback/query execution reports onto broker `open_orders` before drift detection, so terminal callback states suppress stale open-order rows and fresh callback statuses can close the order-state gap without waiting for a later broker snapshot refresh
- `XtQuantShadowClient` now emits `session_consumer_state` runtime snapshots once the process-owned callback loop has started, exposing `consumer_alive / consumer_status / latest_callback_at / buffered_event_count` through the normal runtime-event path
- `XtQuantShadowClient` now also accepts official `on_connected` / `on_stock_asset` / `on_stock_position` callbacks; `collect_sync_state()` will prefer callback-driven asset state when the consumer is alive instead of always querying `query_stock_asset()`
- `XtQuantShadowClient` now applies a formal callback freshness/fallback gate for account state: fresh asset callbacks keep `account_sync_mode=callback_preferred`, while stale or missing asset callbacks force `query_fallback` and surface `asset_callback_freshness` through `session_consumer_state`
- QMT shadow broker state now also emits a unified readiness summary for future real-submit gating: `qmt_readiness` folds session health, callback freshness, and account/order reconcile statuses into one read-only conclusion exposed by `broker-state` and `broker-sync`
- QMT gate stack now supports explicit small-whitelist real submit: `qmt_submit_gate` can remain `shadow_only` for shadow-QMT deployments, become `blocked` for degraded real-QMT runtime, or open to `real_submit` when callback/readiness/preflight all pass
- Deployment-level `qmt_real_submit_policy` now drives the real-QMT submit path end-to-end: whitelist + small-capital preflight are evaluated into `qmt_submit_gate`, and `broker-state` / `/broker-submit-gate` distinguish real-QMT fail-closed runtime blockers from structural shadow-only mode
- QMT release workflow now has a first-class `qmt_release_gate`: it folds `DeployGate` verdict, deployment lifecycle status, and `qmt_submit_gate` into a single release decision surfaced by approval responses, deployment detail, `broker-state`, and `/release-gate`; `shadow_only` / `unavailable` / `blocked` submit gates now keep release blocked instead of being mislabeled as candidates
- `qmt_submit_gate` / `qmt_release_gate` now carry explicit `source=preview|runtime`, so approval/deployment-detail previews are distinguishable from runtime-backed broker-state decisions
- Monitor/dashboard now rebuilds QMT release state from persisted `risk_recorded` events first, falling back to snapshot-embedded `risk_events` only when needed; `qmt_release_gate_blocked` alerts are suppressed for the structural `qmt_submit_gate_shadow_only` case and only fire when additional actionable blockers remain
- QMT broker-state / monitor / scheduler gate paths now all use precise latest-event lookup instead of scanning a small recent window; callback freshness comes from the nested runtime payload, main session health only uses true session lifecycle events (`session_connected` / `*_failed` / `disconnected`), and broker-sync gate summaries now fall back to persisted runtime/reconcile state so an incremental sync cannot regress `qmt_readiness` just because the current bundle omitted older lifecycle events
- Real-QMT now has an end-to-end small-whitelist submit path: first tick preflight pumps broker-state, `qmt_submit_gate/qmt_release_gate` open only when runtime truth is healthy, and the real-QMT API E2E verifies `start -> first tick -> real submit -> shadow callback sync`
- Submit-time QMT ack identity is now explicit: `BrokerOrderReport` carries `broker_submit_id` and optional `broker_order_id`, async submit `seq` is no longer confused with broker order identity, and submission-only reports with a real broker order id are persisted immediately as `BROKER_EXECUTION_RECORDED`
- `deployment_broker_order_links` now also accept submit-time execution events and official `order_stock_async_response` runtime callbacks, so real-submit order links can exist before later query/callback execution reports arrive
- `deployment_broker_order_links` now also persist internal `account_id` scope for QMT identity hardening: scheduler only reuses an existing canonical link for `broker_order_id`-only reports when account, symbol, status, and report timing all stay compatible; public `/broker-orders` keeps the old surface shape and does not expose this internal discriminator
- `broker-state` / monitor now guard against stale persisted runtime projections: if newer broker account/runtime/risk events exist, surfaces rebuild from the latest append-only truth instead of blindly trusting old projections
- Scheduler now syncs shadow runtime events again on stop/error teardown, so `session_owner_closed` / `session_consumer_stopped` can enter `deployment_events` instead of staying only in memory
- QMT shadow sessions are now process-owned by running deployments: `attach_deployment` happens on start/resume, `detach_deployment` happens on stop/error teardown, and the last owner can trigger optional client `close/stop/shutdown` with corresponding owner lifecycle events
- Scheduler now treats real/shadow QMT owners symmetrically at deployment scope: both execution and shadow brokers can attach/detach/supervise their resident owner, runtime projection selects by target `account_id`, and cross-account shadow truth no longer backfills real-QMT projection
- Resident QMT session ownership has now been split into `qmt/session_owner.py`: `QMTSessionManager`, callback bridge, and `XtQuantShadowClient` live there; `qmt/broker.py` remains the broker adapter / gate surface and continues to re-export the owner symbols through `ez.live.qmt` for compatibility
- Broker callback/order-state closure is now callback-first across `events.py` / `ledger.py` / `reconcile.py`: broker lifecycle statuses are normalized once, `LiveLedgerState.broker_order_states` only moves forward, and terminal callback reports suppress stale broker `open_orders`
- Scheduler/runtime contracts now carry an explicit fail-closed `qmt_reconcile_hard_gate`: `tick()` / `broker-sync` persist it as a risk event, `broker-state`/monitor surface it as runtime truth, and the live frontend renders it separately from preview release-gate state
- Scheduler auto-tick now groups active deployments by market-local business date via `Scheduler.get_auto_tick_batches()`, so mixed-market unattended loops no longer feed one host-local date into every deployment
- Historical non-CN specs carrying CN market rules now fail closed in `_start_engine()` instead of warning-only startup
- QMT callback arrival now also push-refreshes persisted runtime projection: the resident callback bridge marks attached deployments dirty, scheduler coalesces refreshes onto the event loop, and `broker-state` / monitor no longer need to wait for the next tick or manual broker-sync to surface fresh runtime truth
- Real-QMT session ownership now supports host-pinned resident mode: `QMTRealBroker` defaults `always_on_owner=True`, last deployment detach leaves the shared client in `resident` state instead of closing it, and supervision/reconnect continue to work through `QMTSessionManager`
- QMT owner orchestration now also has an explicit process-owned slice: `QMTSessionManager` supports `process_owner` pin/unpin + session-state introspection, `QMTShadowBroker/QMTRealBroker` expose thin warmup/release wrappers, and `Scheduler.warmup_qmt_process_owner()` can prewarm/supervise a resident QMT session without any deployment attach
- Scheduler callback refresh registration now survives `QMTSessionManager.clear()`: listener tokens are re-registered when the manager table was reset, so callback-push projection refresh does not silently stop after a manager recycle
- Real-QMT cancel closure is now callback-first through terminal confirm: successful `cancel_order_stock_async_response` acks can advance links into cancel-pending directly, scheduler `/cancel` eagerly harvests real owner runtime/execution events before recomputing projection, and real-owner callback cursors now prevent same/older-timestamp terminal cancel confirms from being missed
- `XtQuantShadowClient.collect_sync_state()` is now callback-aware for snapshot execution state too: callback terminal/open-order truth can suppress stale `query_stock_orders()` rows, callback-only open orders/trades can still surface in snapshot state when query returns nothing, but a clearly fresher query snapshot still wins over an older callback row
- `QMTShadowBroker.snapshot_account_state()` and `list_execution_reports()` now both prefer the client's callback-aware `collect_sync_state()` view when that path exists, so direct broker snapshot/report reads no longer bypass the callback-first open-order/trade merge; legacy/query-only clients still fall back to raw `query_stock_*`
- `XtQuantShadowClient.list_execution_reports(since=None)` now also uses a callback-aware merged order/trade view when the consumer is alive: stale query open orders no longer leak through the full-snapshot path, terminal callback orders are preserved, and direct no-arg report reads stay aligned with `collect_sync_state()`
- Out-of-order cancel lifecycle now closes more safely in the store: `cancel_order_stock_async_response` can bootstrap a `reported_cancel_pending` broker-order link when the callback already knows `client_order_id`, later order reports backfill that seeded row's metadata without dropping cancel-pending semantics, and a newer `cancel_error` will block an older `cancel_requested` from re-opening pending state
- Scheduler now allows terminal broker-order links to anchor compatible follow-up reports more precisely: same-timestamp/older stale reports and later terminal confirms can still reuse the canonical `client_order_id`, while later non-terminal reports still fall back instead of silently reattaching to a potentially new lifecycle
- Scheduler now also reuses an already-persisted compatible broker-order link even when a later report finally arrives with a real `client_order_id`: callback-first synthetic terminal/order-error rows no longer fragment into a second canonical row just because later query convergence surfaces `order_remark`, while ambiguous multi-link matches still refuse reuse
- `collect_sync_state()` fast-path execution reports now also go through the same scheduler-side canonical `client_order_id` normalization as direct broker reads, so both shadow and real-QMT bundles preserve callback-first synthetic terminal/order-error rows when later reports finally carry a real `order_remark`
- Scheduler now persists QMT callback events at receipt time too: when the resident callback bridge marks a deployment dirty, scheduler first normalizes and writes the single callback `order` / `trade` / `order_error` (or runtime event) into the append-only store before kicking off the async broker-state refresh, so callback-only terminal/error truth no longer depends on the later pump succeeding to become durable

Still missing before true V3 OMS:

- full real-broker callback order-state closure beyond the current execution-report consumption path
- full real-broker reconcile closure beyond the current gate/surface slice; recovery triad is covered, but account/order/position/trade closure is not yet complete
- intraday / multi-bar market-data events beyond the current per-symbol day-bar + aggregate snapshot backbone
- split-order execution algorithms (TWAP/VWAP/iceberg)
- industry-aware constrained optimization allocator / fuller risk-budget solver
- richer policy composition beyond the current minimal gate
- append-only ledger as primary truth source

## V3 QMT Closure Sweep (2026-04-16, package `0.3.3`)
A four-agent follow-up sweep (QMT-A/B/C/D) closed the remaining V3.3 real-broker items from the roadmap. Base test count: 805 → **871 live/api passed** (+66).

- **QMT-A — Full reconcile closure (V3.3.44)**: `reconcile.py` now has independent `reconcile_broker_positions` (holdings-only, T+1 / rights-in-transit aware) + `reconcile_broker_trades` (aggregated by `(symbol, side)` with volume + price tolerance). `build_qmt_reconcile_hard_gate(..., position_reconcile=..., trade_reconcile=...)` folds four reconciles fail-closed. Scheduler persists `position_reconcile` / `trade_reconcile` / `real_position_reconcile` / `real_trade_reconcile` as `RISK_RECORDED` events. `/broker-state` API exposes `latest_position_reconcile` / `latest_trade_reconcile`.
- **QMT-B — Full callback order-state closure**: `_XtQuantTraderCallbackBridge._lifecycle_closures` tracks `submit_ack_received / last_order_callback_ts / terminal_callback_ts / order_error_received / trade_callback_count` per order, aliased by `order_remark / client_order_id / order_sysid / order_id / seq`. `list_execution_reports` + `collect_sync_state` pick `execution_sync_mode ∈ {callback_only, callback_query_merge, query_only, unknown}` based on closure freshness. `on_order_error` is confirmed terminal (ORDER_JUNK=57). `session_consumer_state` runtime event surfaces the sync mode.
- **QMT-C — Host-external orchestration (V3.3.45)**: `ez/live/qmt/host.py` provides `QMTHostService`. Owns session lifecycle / callback consumer supervisor / reconnect loop / health state (`UNINITIALIZED → CONNECTING → READY ↔ DEGRADED → DISCONNECTED → STOPPED`). Fail-closed `ensure_ready_or_raise()` gates all proxied `submit_order / cancel_order / collect_sync_state / list_execution_reports`. Subscription is set-idempotent, last-unsub does **not** auto-stop. Scheduler integration (deployment-lifecycle wiring) is intentionally deferred — the host is a standalone contract with 12 tests; the scheduler is unchanged in this sweep.
- **QMT-D — Capital policy + kill-switch (V3.3.46)**: new `ez/live/capital_policy.py` with `CapitalStage` ladder (`read_only → paper_sim → small_whitelist → expanded → full`), `StageLimits` (`max_capital_per_day / max_position_value_per_symbol / max_total_gross_exposure`), `entry_gates` (`min_days_no_drift / min_order_success_rate`), env-var `EZ_LIVE_QMT_KILL_SWITCH` that instantly downgrades any real-broker stage to `paper_sim`. `PreTradeRiskEngine.__init__` accepts `capital_policy=` and runs its check **before** every other rule; default is `None` (fully backward compatible).
- **QMT-E — Capital policy wire-in (V3.3.58)**: `CapitalPolicyConfig.from_params()` reads `risk_params["capital_policy"]` dict; `PaperOMS.execute_rebalance` accepts `broker_type: str = "paper"` and builds `CapitalPolicyEngine` + passes `broker_type` into `PreTradeRiskEngine`. `PaperTradingEngine.execute_day` forwards `spec.broker_type` automatically. End-to-end OMS integration tests confirm the kill-switch rejects real-broker orders but leaves paper-broker paths alive, and that missing/disabled `capital_policy` preserves legacy behavior.

## V3 Hardening Sweep (2026-04-16, package `0.3.2`)
A six-agent fix sweep (Fix-A/B/C/D/E/F) closed the Critical/Important findings from the V3 review:

- `LiveLedger.replay()` now dedupes by `event_id`; `LiveLedgerState.seen_event_count` exposes the running dedupe metric
- Broker order status rank table split into non-terminal (10–29) and terminal (30); `broker_order_status_can_transition(current, incoming)` exposes forward-only semantics — allows `PARTIALLY_FILLED → PART_CANCEL / CANCELED` but blocks `FILLED ↛ CANCELED`
- `make_shadow_broker_client_order_id` fallback is a deterministic hash over `(broker_type, broker_order_id, event_ts, symbol, side)`; the old `"unknown"` literal is gone
- `DeploymentStore.save_snapshot_with_events(...)` writes events + snapshot + broker-order-link upserts in one DuckDB transaction; rollback-safe
- `DeploymentStore._lock` is `threading.RLock` so the cancel-ack upsert path can re-enter `list_broker_order_links_by_broker_order_id`
- `DeploymentSpec._spec_id` now folds `broker_type` / `shadow_broker_type`; `is_legacy_spec_id()` / `compute_content_hash()` let callers surface legacy specs
- Scheduler per-deployment `asyncio.Lock` guards `start/pause/resume/stop/tick/cancel/pump_broker_state`
- `QMTRealBroker.open_submit_gate(decision)` / `close_submit_gate()` provide defense-in-depth; scheduler `tick()` pulls `qmt_submit_gate` from the persisted broker-state projection and brackets `engine.execute_day()` with them, so a missed runtime gate fail-closes inside the broker
- `XtQuantShadowClient._sync_lock` serializes cursor read / bridge snapshot / new-cursor compute; callback freshness threshold (30s) drives callback-only-vs-merged fallback; `register_refresh_listener` is persistent across `QMTSessionManager.clear()`; reconnect backoff capped at 60s with ±20% jitter
- `build_qmt_release_gate_decision(..., hard_gate=...)` folds `qmt_reconcile_hard_gate`; API `/release-gate`, `/broker-state`, deployment detail, and approval all pass it through
- `/api/live/deployments/{id}/broker-orders` now returns `{deployment_id, target_account_id, orders: [...]}` and each link carries `account_id`; `/cancel?if_not_already=true` returns `{status: "already_canceling"}` for idempotent re-clicks; `_build_spec_from_run` raises `422 conflicting_spec_config` when legacy + new optimizer/risk params disagree
- Frontend `SyncErrorBar` surfaces per-endpoint sync failures; `resolveReleaseSummary` requires `deployment.status === "running"` before accepting a `source=runtime` gate; a green/gray badge distinguishes runtime vs preview
- `PreTradeRiskEngine` now evaluates `max_concentration` / `kill_switch` / `max_daily_turnover` on sell-side orders (A-share long-only still means `max_position_weight` / `max_gross_exposure` stay buy-only — sells only free exposure)
- `RuntimeAllocator._optimizer` does eigenvalue-based PSD rescue + `covariance_degenerate=True` fallback to pro-rata; `feasibility_clamped=True` when `Σ(caps) < budget`; vol-scale re-projects to per-symbol cap; new decision details: `feasibility_clamped / feasibility_original_budget / covariance_degenerate / underfill_ratio / underfill_reason / risk_budget_fallback / vol_target_reproject_cap_hit`
- Test baseline: `643` live/api tests passed (was `376`); `TestLiveApiRealE2E` + `test_v3_e2e.py` + `test_v3_concurrency.py` replace the MagicMock-based scheduler E2E; 11 concurrency / idempotency regressions guard the Fix-A~F boundary

## Review Focus
When editing this module, review these first:

- idempotency (`client_order_id`, `event_id`, `last_processed_date`, ledger dedupe)
- replay correctness (fills -> holdings/cash)
- execution-path layering (OMS -> PaperBroker -> shared execution semantics, no direct OMS bypass)
- broker resolution safety (`broker_type` hashing, unsupported adapters fail closed, no silent paper fallback, `open_submit_gate` wiring preserved)
- event-first restore vs legacy snapshot fallback boundaries
- `save_snapshot_with_events` atomicity (never append events without the snapshot sibling when both are available)
- allocator and risk rule ordering (allocator before order generation; hard rejects before execution; sell-side concentration check)
- non-CN market rule gating
- scheduler error escalation and restart recovery
- QMT callback-freshness fallback + `_sync_lock` discipline
