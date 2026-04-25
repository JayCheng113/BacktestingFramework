"""V2.15 B1: Scheduler — single-process, idempotent, with pause/resume and auto-recovery.

Single-process scheduler that drives PaperTradingEngine instances daily.
Process restart -> resume_all() from DB. No multi-worker support.

Key invariants:
- tick() is serial (asyncio.Lock covers entire method)
- Idempotent: per-deployment last_processed_date check skips duplicates
- Error escalation: 3 consecutive errors -> status "error" + engine removed
- Success resets error count to 0
"""
from __future__ import annotations

import asyncio
import json
import logging

from ez.live._broker_pump import (
    build_shadow_execution_report_events as _build_shadow_execution_report_events_fn,
    build_shadow_runtime_events as _build_shadow_runtime_events_fn,
    build_shadow_sync_events as _build_shadow_sync_events_fn,
    derive_shadow_business_date as _derive_shadow_business_date_fn,
)
from ez.live._snapshot_collectors import (
    broker_positions_from_snapshot as _broker_positions_from_snapshot_fn,
    broker_trades_from_snapshot as _broker_trades_from_snapshot_fn,
    build_runtime_reconcile_event as _build_runtime_reconcile_event_fn,
    build_shadow_risk_events as _build_shadow_risk_events_fn,
    historical_non_cn_market_rule_mismatches as _historical_non_cn_market_rule_mismatches_fn,
    latest_runtime_event_by_kind as _latest_runtime_event_by_kind_fn,
    latest_runtime_event_by_kinds as _latest_runtime_event_by_kinds_fn,
    latest_runtime_event_by_prefix as _latest_runtime_event_by_prefix_fn,
    local_trades_from_engine as _local_trades_from_engine_fn,
    sequence_runtime_events as _sequence_runtime_events_fn,
    serialize_position_reconcile as _serialize_position_reconcile_fn,
    serialize_trade_reconcile as _serialize_trade_reconcile_fn,
)
from ez.live.qmt._projection import (
    build_qmt_runtime_projection as _build_qmt_runtime_projection_fn,
    extract_account_event_account_id as _extract_account_event_account_id_fn,
    extract_qmt_account_id as _extract_qmt_account_id_fn,
    extract_runtime_event_account_id as _extract_runtime_event_account_id_fn,
    get_latest_account_event_for_account as _get_latest_account_event_for_account_fn,
    get_latest_runtime_event_for_account as _get_latest_runtime_event_for_account_fn,
    parse_gate_verdict as _parse_gate_verdict_fn,
    persist_qmt_runtime_projection as _persist_qmt_runtime_projection_fn,
)
import pickle
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ez.live.broker import (
    BrokerAdapter,
    BrokerAccountSnapshot,
    BrokerCapability,
    BrokerExecutionReport,
    BrokerRuntimeEvent,
    BrokerSyncBundle,
)
from ez.live.events import (
    DeploymentEvent,
    EventType,
    broker_order_status_is_terminal,
    make_broker_account_event,
    make_broker_cancel_requested_event,
    make_broker_execution_event,
    make_event_id,
    make_broker_runtime_event,
    make_market_bar_event,
    make_market_snapshot_event,
    make_risk_event,
    make_shadow_broker_client_order_id,
    make_snapshot_event,
    make_tick_completed_event,
    broker_order_status_rank,
    normalize_broker_order_status,
    utcnow,
)
from ez.live.ledger import LiveLedger
from ez.live.paper_broker import PaperBroker
from ez.live.qmt.broker import (
    build_qmt_release_gate_decision,
    build_qmt_reconcile_hard_gate,
    QMTBrokerConfig,
    QMTRealBroker,
    QMTSessionState,
    QMTShadowBroker,
    build_qmt_real_submit_policy,
    build_qmt_readiness_summary,
    build_qmt_submit_gate_decision,
)
from ez.live.qmt.reconcile import (
    reconcile_broker_orders,
    reconcile_broker_positions,
    reconcile_broker_snapshot,
    reconcile_broker_trades,
)
from ez.live.deployment_spec import DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.paper_engine import PaperTradingEngine
from ez.portfolio.calendar import TradingCalendar

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 3
_SESSION_RUNTIME_KINDS = {
    "session_bootstrap_started",
    "session_started",
    "session_connected",
    "session_subscribed",
    "session_reconnect_started",
    "session_reconnected",
    "session_resubscribed",
    "session_connect_failed",
    "session_subscribe_failed",
    "session_reconnect_failed",
    "session_resubscribe_failed",
    "session_reconnect_deferred",
    "disconnected",
}
_BROKER_CANCEL_BLOCKING_STATUSES = frozenset(
    {
        "reported_cancel_pending",
        "partially_filled_cancel_pending",
        "partially_canceled",
        "filled",
        "canceled",
        "junk",
        "order_error",
    }
)

# V2.17 strategy-state persistence: per-deployment set of dep_ids for
# which pickling has already failed once. Keeps logs quiet — we warn
# on first failure, then silently skip on subsequent ticks.
_pickle_failure_warned: set[str] = set()
_unpickle_failure_warned: set[str] = set()
_MARKET_TIMEZONES = {
    "cn_stock": "Asia/Shanghai",
    "hk_stock": "Asia/Hong_Kong",
    "us_stock": "America/New_York",
}


def _pickle_strategy(strategy, deployment_id: str) -> bytes | None:
    """Serialize a strategy instance via pickle.

    Returns None if the strategy is unpicklable (file handle / DB conn
    / lambda / C extension state). Logs once per deployment, then stays
    quiet. Missing state falls back to the V2.15 behavior: engine
    reconstructs a fresh strategy at restart (acceptable for stateless
    strategies like TopNRotation; lossy for MLAlpha / Ensemble).
    """
    try:
        return pickle.dumps(strategy)
    except Exception as e:
        if deployment_id not in _pickle_failure_warned:
            _pickle_failure_warned.add(deployment_id)
            logger.warning(
                "Deployment %s: strategy (%s) is unpicklable — state will "
                "NOT persist across restart. Error: %s. (Silenced on "
                "subsequent ticks.) Implement __getstate__/__setstate__ "
                "on the strategy to exclude unpicklable attrs.",
                deployment_id, type(strategy).__name__, e,
            )
        return None


def _unpickle_strategy(blob: bytes, deployment_id: str):
    """Deserialize a strategy instance; return None on failure."""
    try:
        return pickle.loads(blob)
    except Exception as e:
        if deployment_id not in _unpickle_failure_warned:
            _unpickle_failure_warned.add(deployment_id)
            logger.warning(
                "Deployment %s: failed to restore strategy from pickle — "
                "falling back to fresh construction. Error: %s. This "
                "happens if the strategy class was renamed/removed, or "
                "pickle format changed between Python versions.",
                deployment_id, e,
            )
        return None


def _strategy_restore_compatible(restored: Any, fresh: Any) -> bool:
    if type(restored) is not type(fresh):
        return False
    restored_version = getattr(restored, "STATE_SCHEMA_VERSION", None)
    fresh_version = getattr(fresh, "STATE_SCHEMA_VERSION", None)
    if restored_version is None and fresh_version is None:
        restored_version = getattr(restored, "__state_schema_version__", None)
        fresh_version = getattr(fresh, "__state_schema_version__", None)
    if restored_version is not None or fresh_version is not None:
        return restored_version == fresh_version
    return True


class Scheduler:
    """Single-process scheduler. No multi-worker support.
    Process restart -> resume_all() from DB."""

    def __init__(
        self,
        store: DeploymentStore,
        data_chain,
        broker_factories: dict[str, Callable[[DeploymentSpec], BrokerAdapter]] | None = None,
    ):
        self.store = store
        self.data_chain = data_chain
        self._engines: dict[str, PaperTradingEngine] = {}
        self._paused: set[str] = set()  # in-memory paused marker
        self._lock = asyncio.Lock()
        # Per-deployment asyncio locks (lazy). Same-deployment paths
        # (`_start_engine`, `_restore_full_state`, `tick`, `cancel_order`,
        # `pump_broker_state`) serialize on the same lock to keep engine
        # state mutations free of recovery / live-update races. Different
        # deployments stay independent. `self._lock` still wraps
        # engine-iteration scopes (`tick`, `resume_all`) so the dict is
        # not mutated underneath the per-deployment critical sections.
        self._deployment_locks: dict[str, asyncio.Lock] = {}
        self._calendars: dict[str, TradingCalendar] = {}
        self._qmt_callback_refresh_loop: asyncio.AbstractEventLoop | None = None
        self._pending_qmt_callback_refreshes: set[str] = set()
        self._qmt_callback_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._qmt_callback_listener_tokens: dict[int, tuple[object, int]] = {}
        self._broker_factories = broker_factories or {
            "paper": lambda spec: PaperBroker(),
            "qmt": self._build_qmt_shadow_broker,
        }
        self._remember_callback_refresh_loop()

    def _build_execution_broker(self, spec: DeploymentSpec) -> BrokerAdapter:
        broker_type = getattr(spec, "broker_type", "paper")
        if broker_type == "qmt":
            return self._build_qmt_real_broker(spec)
        return self._build_broker(
            spec,
            broker_type=broker_type,
            required_capabilities=frozenset({BrokerCapability.TARGET_WEIGHT_EXECUTION}),
        )

    def _build_shadow_broker(self, spec: DeploymentSpec) -> BrokerAdapter | None:
        shadow_type = getattr(spec, "shadow_broker_type", "")
        if not isinstance(shadow_type, str) or not shadow_type:
            if getattr(spec, "broker_type", "") == "qmt":
                return self._build_qmt_shadow_broker(spec, allow_real_config_fallback=True)
            return None
        return self._build_broker(
            spec,
            broker_type=shadow_type,
            required_capabilities=frozenset(
                {BrokerCapability.READ_ACCOUNT_STATE, BrokerCapability.SHADOW_MODE}
            ),
        )

    def _build_broker(
        self,
        spec: DeploymentSpec,
        *,
        broker_type: str,
        required_capabilities: frozenset[BrokerCapability],
    ) -> BrokerAdapter:
        if not isinstance(broker_type, str) or not broker_type:
            broker_type = "paper"
        factory = self._broker_factories.get(broker_type)
        if factory is None:
            raise NotImplementedError(
                f"Unsupported broker_type {broker_type!r} for deployment live execution"
            )
        broker = factory(spec)
        missing = required_capabilities - broker.capabilities
        if missing:
            raise NotImplementedError(
                f"Broker {broker_type!r} does not provide required capabilities: "
                f"{', '.join(cap.value for cap in sorted(missing, key=lambda c: c.value))}"
            )
        return broker

    @staticmethod
    def _is_qmt_owner_broker(candidate: Any) -> bool:
        return str(getattr(candidate, "broker_type", "") or "").lower() == "qmt"

    @classmethod
    def _iter_qmt_brokers(cls, engine) -> list[BrokerAdapter]:
        seen: set[int] = set()
        brokers: list[BrokerAdapter] = []
        for candidate in (getattr(engine, "shadow_broker", None), getattr(engine, "broker", None)):
            if not cls._is_qmt_owner_broker(candidate):
                continue
            marker = id(candidate)
            if marker in seen:
                continue
            seen.add(marker)
            brokers.append(candidate)
        return brokers

    def _remember_callback_refresh_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._qmt_callback_refresh_loop is None or self._qmt_callback_refresh_loop.is_closed():
            self._qmt_callback_refresh_loop = loop

    def _get_deployment_lock(self, deployment_id: str) -> asyncio.Lock:
        """Return (and lazily create) the per-deployment asyncio lock.

        Every lifecycle entry point that mutates engine state for a specific
        deployment must hold this lock for the duration of the critical
        section. This prevents `_restore_full_state` rewriting
        `engine._order_statuses` while a concurrent QMT callback-driven
        `pump_broker_state` or `cancel_order` is already advancing the
        engine — which used to race on legacy code paths.
        """
        lock = self._deployment_locks.get(deployment_id)
        if lock is None:
            lock = asyncio.Lock()
            self._deployment_locks[deployment_id] = lock
        return lock

    def _drop_deployment_lock(self, deployment_id: str) -> None:
        """Best-effort cleanup for terminal states. Only drops an unlocked entry."""
        lock = self._deployment_locks.get(deployment_id)
        if lock is None:
            return
        if lock.locked():
            return
        self._deployment_locks.pop(deployment_id, None)

    @staticmethod
    def _market_today(market: str) -> date:
        tz_name = _MARKET_TIMEZONES.get(str(market or ""))
        if not tz_name:
            return datetime.now(timezone.utc).date()
        try:
            return datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            return datetime.now(timezone.utc).date()

    def _register_qmt_callback_refresh_listener(self, shadow_broker: BrokerAdapter | None) -> None:
        if not isinstance(shadow_broker, QMTShadowBroker):
            return
        manager = getattr(shadow_broker, "_session_manager", None)
        register = getattr(manager, "register_deployment_callback_listener", None)
        if not callable(register):
            return
        manager_key = id(manager)
        existing = self._qmt_callback_listener_tokens.get(manager_key)
        if existing is not None:
            _, token = existing
            is_registered = getattr(manager, "has_deployment_callback_listener", None)
            if not callable(is_registered) or bool(is_registered(token)):
                return
        token = int(register(self._handle_qmt_callback_projection_dirty))
        self._qmt_callback_listener_tokens[manager_key] = (manager, token)

    def _handle_qmt_callback_projection_dirty(
        self,
        *,
        deployment_ids: tuple[str, ...],
        event: dict[str, Any],
        session_key: Any,
    ) -> None:
        loop = self._qmt_callback_refresh_loop
        if loop is None or loop.is_closed():
            return
        for deployment_id in deployment_ids:
            loop.call_soon_threadsafe(
                self._persist_and_schedule_qmt_callback_refresh,
                deployment_id,
                session_key,
                dict(event),
            )

    def _persist_and_schedule_qmt_callback_refresh(
        self,
        deployment_id: str,
        session_key: Any,
        event: dict[str, Any],
    ) -> None:
        self._persist_qmt_callback_event(
            deployment_id=deployment_id,
            session_key=session_key,
            event=event,
        )
        self._schedule_qmt_callback_projection_refresh(deployment_id)

    def _resolve_qmt_broker_for_callback_session(
        self,
        *,
        engine: PaperTradingEngine,
        session_key: Any,
    ) -> BrokerAdapter | None:
        qmt_brokers = self._iter_qmt_brokers(engine)
        if not qmt_brokers:
            return None
        target_account_id = str(getattr(session_key, "account_id", "") or "").strip()
        if not target_account_id:
            return qmt_brokers[0] if len(qmt_brokers) == 1 else None
        matching = [
            broker
            for broker in qmt_brokers
            if str(getattr(getattr(broker, "config", None), "account_id", "") or "").strip()
            == target_account_id
        ]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            execution_broker = self._resolve_engine_broker(engine)
            if execution_broker in matching:
                return execution_broker
            shadow_broker = self._resolve_engine_shadow_broker(engine)
            if shadow_broker in matching:
                return shadow_broker
            return matching[0]
        return None

    def _persist_qmt_callback_event(
        self,
        *,
        deployment_id: str,
        session_key: Any,
        event: dict[str, Any],
    ) -> None:
        if not isinstance(event, dict):
            return
        engine = self._engines.get(deployment_id)
        if engine is None:
            return
        broker = self._resolve_qmt_broker_for_callback_session(
            engine=engine,
            session_key=session_key,
        )
        if not isinstance(broker, QMTShadowBroker):
            return
        report_kind = str(event.get("_report_kind", "") or "").strip()
        try:
            if report_kind in {"order", "trade", "order_error"}:
                normalized_reports = self._normalize_broker_execution_reports(
                    deployment_id=deployment_id,
                    reports=[broker._normalize_execution_report(event)],
                )
                if not normalized_reports:
                    return
                self.store.save_broker_sync_result(
                    deployment_id=deployment_id,
                    events=_build_shadow_execution_report_events_fn(
                        deployment_id,
                        normalized_reports,
                    ),
                    broker_reports=normalized_reports,
                )
                return
            normalized_runtime_event = broker._normalize_runtime_event(event)
            self.store.append_events(
                _build_shadow_runtime_events_fn(
                    deployment_id,
                    [normalized_runtime_event],
                )
            )
        except Exception:
            logger.warning(
                "Deployment %s callback event persistence failed",
                deployment_id,
                exc_info=True,
            )

    def _schedule_qmt_callback_projection_refresh(self, deployment_id: str) -> None:
        if deployment_id not in self._engines:
            return
        self._pending_qmt_callback_refreshes.add(deployment_id)
        current = self._qmt_callback_refresh_tasks.get(deployment_id)
        if current is not None and not current.done():
            return
        self._qmt_callback_refresh_tasks[deployment_id] = asyncio.create_task(
            self._run_qmt_callback_projection_refresh(deployment_id)
        )

    async def _run_qmt_callback_projection_refresh(self, deployment_id: str) -> None:
        try:
            while deployment_id in self._pending_qmt_callback_refreshes:
                self._pending_qmt_callback_refreshes.discard(deployment_id)
                if deployment_id not in self._engines:
                    return
                try:
                    await self.pump_broker_state(deployment_id)
                except ValueError:
                    return
                except Exception:
                    logger.warning(
                        "Deployment %s callback-triggered broker refresh failed",
                        deployment_id,
                        exc_info=True,
                    )
        finally:
            current = self._qmt_callback_refresh_tasks.get(deployment_id)
            if current is asyncio.current_task():
                self._qmt_callback_refresh_tasks.pop(deployment_id, None)
                if (
                    deployment_id in self._pending_qmt_callback_refreshes
                    and deployment_id in self._engines
                ):
                    self._qmt_callback_refresh_tasks[deployment_id] = asyncio.create_task(
                        self._run_qmt_callback_projection_refresh(deployment_id)
                    )

    @staticmethod
    def _resolve_qmt_broker_config(
        spec: DeploymentSpec,
        *,
        prefer_real: bool,
        allow_real_fallback: bool,
    ) -> dict[str, Any]:
        risk_params = spec.risk_params if isinstance(spec.risk_params, dict) else {}
        qmt_real_cfg = risk_params.get("qmt_real_broker_config")
        shadow_cfg = risk_params.get("shadow_broker_config")
        if prefer_real and isinstance(qmt_real_cfg, dict) and qmt_real_cfg.get("account_id"):
            return dict(qmt_real_cfg)
        if isinstance(shadow_cfg, dict) and shadow_cfg.get("account_id"):
            return dict(shadow_cfg)
        if allow_real_fallback and isinstance(qmt_real_cfg, dict) and qmt_real_cfg.get("account_id"):
            return dict(qmt_real_cfg)
        return {}

    def _build_qmt_shadow_broker(
        self,
        spec: DeploymentSpec,
        *,
        allow_real_config_fallback: bool = False,
    ) -> BrokerAdapter:
        shadow_cfg = self._resolve_qmt_broker_config(
            spec,
            prefer_real=False,
            allow_real_fallback=allow_real_config_fallback,
        )
        if not isinstance(shadow_cfg, dict) or not shadow_cfg.get("account_id"):
            raise ValueError(
                "QMT shadow broker requires risk_params.shadow_broker_config.account_id "
                "or qmt_real_broker_config.account_id"
            )
        return QMTShadowBroker(QMTBrokerConfig(**shadow_cfg))

    def _build_qmt_real_broker(self, spec: DeploymentSpec) -> BrokerAdapter:
        policy = build_qmt_real_submit_policy(spec.risk_params)
        if not policy.enabled:
            raise NotImplementedError(
                "QMT real execution is disabled by policy; set qmt_real_submit_policy.enabled=true"
            )
        real_cfg = self._resolve_qmt_broker_config(
            spec,
            prefer_real=True,
            allow_real_fallback=True,
        )
        if not isinstance(real_cfg, dict) or not real_cfg.get("account_id"):
            raise ValueError(
                "QMT real broker requires risk_params.qmt_real_broker_config.account_id "
                "or shadow_broker_config.account_id"
            )
        account_id = str(real_cfg.get("account_id") or "")
        if policy.allowed_account_ids and account_id not in policy.allowed_account_ids:
            raise NotImplementedError(
                f"QMT real execution blocked by policy: account {account_id!r} is not whitelisted"
            )
        max_initial_cash = policy.max_initial_cash
        if max_initial_cash is not None and float(spec.initial_cash) > float(max_initial_cash):
            raise NotImplementedError(
                f"QMT real execution blocked by policy: initial_cash={spec.initial_cash} exceeds cap {max_initial_cash}"
            )
        real_cfg = dict(real_cfg)
        real_cfg.setdefault("always_on_owner", True)
        return QMTRealBroker(QMTBrokerConfig(**real_cfg))

    def warmup_qmt_process_owner(
        self,
        spec: DeploymentSpec,
        *,
        prefer_real: bool = True,
        owner_id: str | None = None,
    ) -> QMTSessionState:
        broker: BrokerAdapter | None = None
        if prefer_real and getattr(spec, "broker_type", "") == "qmt":
            try:
                broker = self._build_qmt_real_broker(spec)
            except NotImplementedError:
                broker = None
        if broker is None:
            broker = self._build_shadow_broker(spec)
        if not isinstance(broker, QMTShadowBroker):
            raise ValueError("QMT process owner warmup requires a QMT real or shadow broker")
        self._register_qmt_callback_refresh_listener(broker)
        resolved_owner_id = str(owner_id or "").strip()
        if not resolved_owner_id:
            resolved_owner_id = (
                f"scheduler:qmt:{broker.config.account_id}:"
                f"{broker.config.session_id or 'auto'}"
            )
        state = broker.pin_process_owner(resolved_owner_id)
        supervised = broker.ensure_session_supervision()
        if supervised is not None:
            return supervised
        if state is not None:
            return state
        current = broker.get_session_state()
        if current is None:  # pragma: no cover - defensive
            raise RuntimeError("QMT process owner warmup did not produce session state")
        return current

    def _resolve_engine_broker(self, engine) -> BrokerAdapter:
        broker = getattr(engine, "broker", None)
        if isinstance(broker, BrokerAdapter):
            return broker
        return self._build_execution_broker(engine.spec)

    def _resolve_engine_shadow_broker(self, engine) -> BrokerAdapter | None:
        broker = getattr(engine, "shadow_broker", None)
        if isinstance(broker, BrokerAdapter):
            return broker
        return self._build_shadow_broker(engine.spec)

    def _resolve_cancel_broker(
        self,
        *,
        spec: DeploymentSpec,
        engine: PaperTradingEngine | None,
    ) -> BrokerAdapter | None:
        if engine is not None:
            execution_broker = self._resolve_engine_broker(engine)
            shadow_broker = self._resolve_engine_shadow_broker(engine)
        else:
            execution_broker = self._build_execution_broker(spec)
            shadow_broker = self._build_shadow_broker(spec)
        prefer_execution = str(getattr(spec, "broker_type", "") or "").lower() == "qmt"
        candidates = (
            (execution_broker, shadow_broker)
            if prefer_execution
            else (shadow_broker, execution_broker)
        )
        for candidate in candidates:
            if (
                candidate is not None
                and BrokerCapability.CANCEL_ORDER in candidate.capabilities
            ):
                return candidate
        return None

    def _collect_shadow_snapshot_context(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        business_date: date,
        equity: float,
        prices: dict[str, float],
        broker_reports: list[BrokerExecutionReport] | None = None,
        snapshot: BrokerAccountSnapshot | None = None,
    ) -> tuple[BrokerAccountSnapshot | None, dict | None, dict | None]:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        if shadow_broker is None:
            return None, None, None
        try:
            shadow_snapshot = snapshot or shadow_broker.snapshot_account_state()
            snapshot = shadow_snapshot
            if snapshot is None:
                unavailable = {
                    "date": str(business_date),
                    "event": "broker_reconcile",
                    "status": "unavailable",
                    "broker_type": shadow_broker.broker_type,
                    "message": "Shadow broker did not return an account snapshot.",
                }
                return None, unavailable, None
            result = reconcile_broker_snapshot(
                local_cash=engine.cash,
                local_holdings=engine.holdings,
                local_equity=equity,
                prices=prices,
                broker_snapshot=snapshot,
            )
            account_reconcile = {
                "date": str(business_date),
                "event": "broker_reconcile",
                "status": result.status,
                "broker_type": result.broker_type,
                "message": result.message,
                "details": {
                    "compared_at": result.compared_at.isoformat(),
                    "cash_delta": result.cash_delta,
                    "total_asset_delta": result.total_asset_delta,
                    "max_notional_drift": result.max_notional_drift,
                    "position_drifts": [
                        {
                            "symbol": drift.symbol,
                            "local_shares": drift.local_shares,
                            "broker_shares": drift.broker_shares,
                            "share_delta": drift.share_delta,
                            "notional_delta": drift.notional_delta,
                        }
                        for drift in result.position_drifts
                    ],
                },
            }
            order_reconcile_result = reconcile_broker_orders(
                broker_snapshot=snapshot,
                local_order_links=self._merge_local_broker_order_links(
                    deployment_id=deployment_id,
                    broker_type=shadow_broker.broker_type,
                    broker_reports=broker_reports or [],
                ),
                broker_reports=broker_reports or [],
            )
            order_reconcile = {
                "date": str(business_date),
                "event": "broker_order_reconcile",
                "status": order_reconcile_result.status,
                "broker_type": order_reconcile_result.broker_type,
                "message": order_reconcile_result.message,
                "details": {
                    "compared_at": order_reconcile_result.compared_at.isoformat(),
                    "local_open_order_count": order_reconcile_result.local_open_order_count,
                    "broker_open_order_count": order_reconcile_result.broker_open_order_count,
                    "missing_local_orders": [
                        {
                            "order_key": drift.order_key,
                            "symbol": drift.symbol,
                            "local_status": drift.local_status,
                            "broker_status": drift.broker_status,
                            "reason": drift.reason,
                        }
                        for drift in order_reconcile_result.missing_local_orders
                    ],
                    "missing_broker_orders": [
                        {
                            "order_key": drift.order_key,
                            "symbol": drift.symbol,
                            "local_status": drift.local_status,
                            "broker_status": drift.broker_status,
                            "reason": drift.reason,
                        }
                        for drift in order_reconcile_result.missing_broker_orders
                    ],
                    "status_drifts": [
                        {
                            "order_key": drift.order_key,
                            "symbol": drift.symbol,
                            "local_status": drift.local_status,
                            "broker_status": drift.broker_status,
                            "reason": drift.reason,
                        }
                        for drift in order_reconcile_result.status_drifts
                    ],
                },
            }
            return snapshot, account_reconcile, order_reconcile
        except Exception as exc:
            logger.warning(
                "Deployment %s: shadow reconciliation failed: %s",
                deployment_id,
                exc,
            )
            return None, {
                "date": str(business_date),
                "event": "broker_reconcile",
                "status": "error",
                "broker_type": shadow_broker.broker_type,
                "message": str(exc),
            }, None

    # ------------------------------------------------------------------
    # V3.3.44 — independent position / trade reconcile
    # ------------------------------------------------------------------

    def _collect_shadow_position_reconcile(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        business_date: date,
        snapshot: BrokerAccountSnapshot | None,
    ) -> dict | None:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        if shadow_broker is None:
            return None
        broker_type = getattr(shadow_broker, "broker_type", "qmt")
        if snapshot is None:
            return {
                "date": str(business_date),
                "event": "position_reconcile",
                "status": "unavailable",
                "broker_type": broker_type,
                "message": "Shadow broker did not return an account snapshot.",
            }
        try:
            result = reconcile_broker_positions(
                local_holdings=dict(engine.holdings),
                broker_positions=_broker_positions_from_snapshot_fn(snapshot),
                broker_type=broker_type,
                compared_at=snapshot.as_of,
            )
        except Exception as exc:
            logger.warning(
                "Deployment %s: shadow position reconcile failed: %s",
                deployment_id,
                exc,
            )
            return {
                "date": str(business_date),
                "event": "position_reconcile",
                "status": "error",
                "broker_type": broker_type,
                "message": str(exc),
            }
        return _serialize_position_reconcile_fn(
            business_date=business_date,
            broker_type=broker_type,
            event_name="position_reconcile",
            result=result,
        )

    def _collect_shadow_trade_reconcile(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        business_date: date,
        snapshot: BrokerAccountSnapshot | None,
    ) -> dict | None:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        if shadow_broker is None:
            return None
        broker_type = getattr(shadow_broker, "broker_type", "qmt")
        if snapshot is None:
            return {
                "date": str(business_date),
                "event": "trade_reconcile",
                "status": "unavailable",
                "broker_type": broker_type,
                "message": "Shadow broker did not return an account snapshot.",
            }
        try:
            result = reconcile_broker_trades(
                local_trades=_local_trades_from_engine_fn(
                    engine,
                    business_date=business_date,
                ),
                broker_trades=_broker_trades_from_snapshot_fn(snapshot),
                business_date=business_date,
                broker_type=broker_type,
                compared_at=snapshot.as_of,
            )
        except Exception as exc:
            logger.warning(
                "Deployment %s: shadow trade reconcile failed: %s",
                deployment_id,
                exc,
            )
            return {
                "date": str(business_date),
                "event": "trade_reconcile",
                "status": "error",
                "broker_type": broker_type,
                "message": str(exc),
            }
        return _serialize_trade_reconcile_fn(
            business_date=business_date,
            broker_type=broker_type,
            event_name="trade_reconcile",
            result=result,
        )

    def _collect_real_qmt_position_reconcile(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        business_date: date,
        snapshot: BrokerAccountSnapshot | None,
    ) -> dict | None:
        execution_broker = self._resolve_engine_broker(engine)
        if not self._is_qmt_owner_broker(execution_broker):
            return None
        broker_type = getattr(execution_broker, "broker_type", "qmt")
        account_id = _extract_qmt_account_id_fn(engine.spec)
        if snapshot is None:
            return {
                "date": str(business_date),
                "event": "real_position_reconcile",
                "status": "unavailable",
                "broker_type": broker_type,
                "account_id": account_id or None,
                "message": "Real QMT broker did not return an account snapshot.",
            }
        effective_account_id = (
            str(getattr(snapshot, "account_id", "") or account_id or "")
            or ""
        )
        try:
            result = reconcile_broker_positions(
                local_holdings=dict(engine.holdings),
                broker_positions=_broker_positions_from_snapshot_fn(snapshot),
                broker_type=broker_type,
                compared_at=snapshot.as_of,
            )
        except Exception as exc:
            logger.warning(
                "Deployment %s: real QMT position reconcile failed: %s",
                deployment_id,
                exc,
            )
            return {
                "date": str(business_date),
                "event": "real_position_reconcile",
                "status": "error",
                "broker_type": broker_type,
                "account_id": effective_account_id or None,
                "message": str(exc),
            }
        return _serialize_position_reconcile_fn(
            business_date=business_date,
            broker_type=broker_type,
            event_name="real_position_reconcile",
            result=result,
            account_id=effective_account_id,
        )

    def _collect_real_qmt_trade_reconcile(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        business_date: date,
        snapshot: BrokerAccountSnapshot | None,
    ) -> dict | None:
        execution_broker = self._resolve_engine_broker(engine)
        if not self._is_qmt_owner_broker(execution_broker):
            return None
        broker_type = getattr(execution_broker, "broker_type", "qmt")
        account_id = _extract_qmt_account_id_fn(engine.spec)
        if snapshot is None:
            return {
                "date": str(business_date),
                "event": "real_trade_reconcile",
                "status": "unavailable",
                "broker_type": broker_type,
                "account_id": account_id or None,
                "message": "Real QMT broker did not return an account snapshot.",
            }
        effective_account_id = (
            str(getattr(snapshot, "account_id", "") or account_id or "")
            or ""
        )
        try:
            result = reconcile_broker_trades(
                local_trades=_local_trades_from_engine_fn(
                    engine,
                    business_date=business_date,
                ),
                broker_trades=_broker_trades_from_snapshot_fn(snapshot),
                business_date=business_date,
                broker_type=broker_type,
                compared_at=snapshot.as_of,
            )
        except Exception as exc:
            logger.warning(
                "Deployment %s: real QMT trade reconcile failed: %s",
                deployment_id,
                exc,
            )
            return {
                "date": str(business_date),
                "event": "real_trade_reconcile",
                "status": "error",
                "broker_type": broker_type,
                "account_id": effective_account_id or None,
                "message": str(exc),
            }
        return _serialize_trade_reconcile_fn(
            business_date=business_date,
            broker_type=broker_type,
            event_name="real_trade_reconcile",
            result=result,
            account_id=effective_account_id,
        )

    def _collect_shadow_sync_bundle(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
    ) -> BrokerSyncBundle:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        if shadow_broker is None:
            return BrokerSyncBundle(
                snapshot=None,
                execution_reports=[],
                runtime_events=[],
            )
        self._ensure_qmt_broker_supervision(engine=engine)
        cursor_state = self._get_shadow_sync_cursor(
            deployment_id=deployment_id,
            engine=engine,
        )
        since_reports = self.store.get_latest_event_ts(
            deployment_id,
            event_type=EventType.BROKER_EXECUTION_RECORDED,
        )
        since_runtime = self.store.get_latest_event_ts(
            deployment_id,
            event_type=EventType.BROKER_RUNTIME_RECORDED,
        )
        try:
            collect_sync_state = getattr(shadow_broker, "collect_sync_state", None)
            if callable(collect_sync_state):
                bundle = collect_sync_state(
                    since_reports=since_reports,
                    since_runtime=since_runtime,
                    cursor_state=cursor_state,
                )
                if isinstance(bundle, BrokerSyncBundle):
                    bundle.execution_reports = self._normalize_broker_execution_reports(
                        deployment_id=deployment_id,
                        reports=bundle.execution_reports,
                    )
                return bundle
            return BrokerSyncBundle(
                snapshot=shadow_broker.snapshot_account_state(),
                execution_reports=self._collect_shadow_execution_reports(
                    deployment_id=deployment_id,
                    engine=engine,
                ),
                runtime_events=self._collect_shadow_runtime_events(
                    deployment_id=deployment_id,
                    engine=engine,
                ),
            )
        except Exception as exc:
            logger.warning(
                "Deployment %s: shadow broker sync bundle failed: %s",
                deployment_id,
                exc,
            )
            return BrokerSyncBundle(
                snapshot=None,
                execution_reports=[],
                runtime_events=[],
            )

    def _collect_real_qmt_owner_sync_bundle(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        since_runtime: datetime | None = None,
        since_reports: datetime | None = None,
    ) -> BrokerSyncBundle:
        execution_broker = self._resolve_engine_broker(engine)
        if not self._is_qmt_owner_broker(execution_broker):
            return BrokerSyncBundle(
                snapshot=None,
                execution_reports=[],
                runtime_events=[],
            )
        cursor_state = self._get_real_qmt_owner_sync_cursor(
            deployment_id=deployment_id,
            engine=engine,
        )
        try:
            collect_sync_state = getattr(execution_broker, "collect_sync_state", None)
            if callable(collect_sync_state):
                bundle = collect_sync_state(
                    since_reports=since_reports,
                    since_runtime=since_runtime,
                    cursor_state=cursor_state,
                )
                if isinstance(bundle, BrokerSyncBundle):
                    bundle.execution_reports = self._normalize_broker_execution_reports(
                        deployment_id=deployment_id,
                        reports=bundle.execution_reports,
                    )
                    return bundle
            snapshot = execution_broker.snapshot_account_state()
            runtime_events = execution_broker.list_runtime_events(since=since_runtime)
        except Exception as exc:
            logger.warning(
                "Deployment %s: real QMT owner sync bundle failed: %s",
                deployment_id,
                exc,
            )
            return BrokerSyncBundle(
                snapshot=None,
                execution_reports=[],
                runtime_events=[],
            )
        return BrokerSyncBundle(
            snapshot=snapshot,
            execution_reports=self._collect_broker_execution_reports(
                deployment_id=deployment_id,
                broker=execution_broker,
                broker_label="real QMT owner",
                since=since_reports,
            ),
            runtime_events=runtime_events,
            cursor_state=dict(cursor_state) if isinstance(cursor_state, dict) else None,
        )

    def _merge_local_broker_order_links(
        self,
        *,
        deployment_id: str,
        broker_type: str,
        broker_reports: list[BrokerExecutionReport],
    ) -> list[dict]:
        links = {
            str(link.get("client_order_id", "") or ""): dict(link)
            for link in self.store.list_broker_order_links(
                deployment_id,
                broker_type=broker_type,
            )
            if str(link.get("client_order_id", "") or "")
        }
        for report in broker_reports:
            client_order_id = str(report.client_order_id or "")
            if not client_order_id:
                continue
            normalized_status = normalize_broker_order_status(
                report.status,
                filled_shares=report.filled_shares,
                remaining_shares=report.remaining_shares,
            )
            existing = links.get(client_order_id)
            should_advance = existing is None
            if not should_advance:
                previous_ts = existing.get("last_report_ts")
                incoming_ts = report.as_of
                should_advance = (
                    previous_ts is None
                    or broker_order_status_rank(normalized_status)
                    > broker_order_status_rank(
                        str(existing.get("latest_status", "") or "")
                    )
                    or (
                        broker_order_status_rank(normalized_status)
                        == broker_order_status_rank(
                            str(existing.get("latest_status", "") or "")
                        )
                        and incoming_ts >= previous_ts
                    )
                )
            if should_advance:
                links[client_order_id] = {
                    "deployment_id": deployment_id,
                    "broker_type": broker_type,
                    "client_order_id": client_order_id,
                    "broker_order_id": report.broker_order_id,
                    "symbol": report.symbol,
                    "latest_report_id": report.report_id,
                    "latest_status": normalized_status,
                    "last_report_ts": report.as_of,
                }
        return list(links.values())

    def _collect_broker_execution_reports(
        self,
        *,
        deployment_id: str,
        broker: BrokerAdapter | None,
        broker_label: str,
        since: datetime | None,
    ) -> list[BrokerExecutionReport]:
        if broker is None:
            return []
        if BrokerCapability.STREAM_EXECUTION_REPORTS not in broker.capabilities:
            return []
        try:
            reports = broker.list_execution_reports(since=since)
        except Exception as exc:
            logger.warning(
                "Deployment %s: %s execution-report sync failed: %s",
                deployment_id,
                broker_label,
                exc,
            )
            return []
        return self._normalize_broker_execution_reports(
            deployment_id=deployment_id,
            reports=reports,
        )

    def _normalize_broker_execution_reports(
        self,
        *,
        deployment_id: str,
        reports: list[BrokerExecutionReport],
    ) -> list[BrokerExecutionReport]:
        normalized_reports = []
        for report in reports:
            client_order_id = self._resolve_broker_report_client_order_id(
                deployment_id=deployment_id,
                report=report,
            )
            normalized_reports.append(
                BrokerExecutionReport(
                    report_id=report.report_id,
                    broker_type=report.broker_type,
                    as_of=report.as_of,
                    client_order_id=client_order_id,
                    broker_order_id=report.broker_order_id,
                    symbol=report.symbol,
                    side=report.side,
                    status=report.status,
                    filled_shares=report.filled_shares,
                    remaining_shares=report.remaining_shares,
                    avg_price=report.avg_price,
                    message=report.message,
                    raw_payload=report.raw_payload,
                    account_id=str(getattr(report, "account_id", "") or ""),
                )
            )
        return normalized_reports

    def _collect_shadow_execution_reports(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
    ) -> list[BrokerExecutionReport]:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        since = self.store.get_latest_event_ts(
            deployment_id,
            event_type=EventType.BROKER_EXECUTION_RECORDED,
        )
        return self._collect_broker_execution_reports(
            deployment_id=deployment_id,
            broker=shadow_broker,
            broker_label="shadow",
            since=since,
        )

    def _resolve_broker_report_client_order_id(
        self,
        *,
        deployment_id: str,
        report: BrokerExecutionReport,
    ) -> str:
        report_status = normalize_broker_order_status(
            report.status,
            filled_shares=report.filled_shares,
            remaining_shares=report.remaining_shares,
        )
        reusable_client_order_id = self._find_reusable_broker_report_client_order_id(
            deployment_id=deployment_id,
            report=report,
            normalized_status=report_status,
        )
        if reusable_client_order_id:
            return reusable_client_order_id
        if report.client_order_id:
            return report.client_order_id
        client_order_id = make_shadow_broker_client_order_id(
            deployment_id,
            broker_type=report.broker_type,
            broker_order_id=report.broker_order_id,
            report_id=report.report_id,
        )
        raw_payload = report.raw_payload if isinstance(report.raw_payload, dict) else {}
        legacy_order_id = str(raw_payload.get("order_id", "") or "").strip()
        if not legacy_order_id or legacy_order_id == str(report.broker_order_id or ""):
            return client_order_id
        legacy_client_order_id = make_shadow_broker_client_order_id(
            deployment_id,
            broker_type=report.broker_type,
            broker_order_id=legacy_order_id,
            report_id=report.report_id,
        )
        existing_legacy_link = self.store.get_broker_order_link(
            deployment_id,
            broker_type=report.broker_type,
            client_order_id=legacy_client_order_id,
        )
        if existing_legacy_link is not None:
            return legacy_client_order_id
        return client_order_id

    def _find_reusable_broker_report_client_order_id(
        self,
        *,
        deployment_id: str,
        report: BrokerExecutionReport,
        normalized_status: str | None = None,
    ) -> str:
        if not report.broker_order_id:
            return ""
        report_account_id = str(getattr(report, "account_id", "") or "").strip()
        report_symbol = str(report.symbol or "").strip()
        report_status = (
            normalized_status
            if normalized_status is not None
            else normalize_broker_order_status(
                report.status,
                filled_shares=report.filled_shares,
                remaining_shares=report.remaining_shares,
            )
        )
        candidate_links = self.store.list_broker_order_links_by_broker_order_id(
            deployment_id,
            broker_type=report.broker_type,
            broker_order_id=report.broker_order_id,
            account_id=report_account_id,
        )
        viable_client_order_ids: list[str] = []
        viable_terminal_client_order_ids: list[str] = []
        for existing_link in candidate_links:
            link_account_id = str(existing_link.get("account_id", "") or "").strip()
            link_symbol = str(existing_link.get("symbol", "") or "").strip()
            link_status = normalize_broker_order_status(
                str(existing_link.get("latest_status", "") or "")
            )
            last_report_ts = existing_link.get("last_report_ts")
            existing_client_order_id = str(
                existing_link.get("client_order_id", "") or ""
            ).strip()
            if not existing_client_order_id:
                continue
            if report_account_id and link_account_id and report_account_id != link_account_id:
                continue
            if link_symbol and report_symbol and link_symbol != report_symbol:
                continue
            if last_report_ts is None:
                continue
            report_gap = report.as_of - last_report_ts
            if not broker_order_status_is_terminal(link_status):
                if report.as_of < last_report_ts:
                    continue
                if report_gap > timedelta(days=7):
                    continue
                viable_client_order_ids.append(existing_client_order_id)
                if len(viable_client_order_ids) > 1:
                    return ""
                continue
            # Keep terminal links as anchors only for duplicate/late terminal
            # confirms or for older/same-timestamp stale reports from the same
            # broker order lifecycle. A newer non-terminal report must not
            # reattach to a terminal link because that can hide broker_order_id
            # reuse as a fresh lifecycle.
            if report.as_of > last_report_ts and not broker_order_status_is_terminal(
                report_status
            ):
                continue
            if abs(report_gap) > timedelta(days=7):
                continue
            viable_terminal_client_order_ids.append(existing_client_order_id)
            if len(viable_terminal_client_order_ids) > 1:
                return ""
        if len(viable_client_order_ids) == 1:
            return viable_client_order_ids[0]
        if not viable_client_order_ids and len(viable_terminal_client_order_ids) == 1:
            return viable_terminal_client_order_ids[0]
        return ""

    def _collect_shadow_runtime_events(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
    ) -> list[BrokerRuntimeEvent]:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        if shadow_broker is None:
            return []
        since = self.store.get_latest_event_ts(
            deployment_id,
            event_type=EventType.BROKER_RUNTIME_RECORDED,
        )
        try:
            return shadow_broker.list_runtime_events(since=since)
        except Exception as exc:
            logger.warning(
                "Deployment %s: shadow runtime-event sync failed: %s",
                deployment_id,
                exc,
            )
            return []

    def _collect_real_qmt_snapshot_context(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        business_date: date,
        equity: float,
        prices: dict[str, float],
        broker_reports: list[BrokerExecutionReport] | None = None,
        snapshot: BrokerAccountSnapshot | None = None,
    ) -> tuple[BrokerAccountSnapshot | None, dict | None, dict | None]:
        execution_broker = self._resolve_engine_broker(engine)
        if not self._is_qmt_owner_broker(execution_broker):
            return None, None, None
        account_id = _extract_qmt_account_id_fn(engine.spec)
        try:
            real_snapshot = snapshot or execution_broker.snapshot_account_state()
            snapshot = real_snapshot
            if snapshot is None:
                unavailable = {
                    "date": str(business_date),
                    "event": "real_broker_reconcile",
                    "status": "unavailable",
                    "broker_type": execution_broker.broker_type,
                    "account_id": account_id or None,
                    "message": "Real QMT broker did not return an account snapshot.",
                }
                return None, unavailable, None
            effective_account_id = str(getattr(snapshot, "account_id", "") or account_id or "")
            result = reconcile_broker_snapshot(
                local_cash=engine.cash,
                local_holdings=engine.holdings,
                local_equity=equity,
                prices=prices,
                broker_snapshot=snapshot,
            )
            account_reconcile = {
                "date": str(business_date),
                "event": "real_broker_reconcile",
                "status": result.status,
                "broker_type": result.broker_type,
                "account_id": effective_account_id or None,
                "message": result.message,
                "details": {
                    "compared_at": result.compared_at.isoformat(),
                    "cash_delta": result.cash_delta,
                    "total_asset_delta": result.total_asset_delta,
                    "max_notional_drift": result.max_notional_drift,
                    "position_drifts": [
                        {
                            "symbol": drift.symbol,
                            "local_shares": drift.local_shares,
                            "broker_shares": drift.broker_shares,
                            "share_delta": drift.share_delta,
                            "notional_delta": drift.notional_delta,
                        }
                        for drift in result.position_drifts
                    ],
                },
            }
            order_reconcile_result = reconcile_broker_orders(
                broker_snapshot=snapshot,
                local_order_links=self._merge_local_broker_order_links(
                    deployment_id=deployment_id,
                    broker_type=execution_broker.broker_type,
                    broker_reports=broker_reports or [],
                ),
                broker_reports=broker_reports or [],
            )
            order_reconcile = {
                "date": str(business_date),
                "event": "real_broker_order_reconcile",
                "status": order_reconcile_result.status,
                "broker_type": order_reconcile_result.broker_type,
                "account_id": effective_account_id or None,
                "message": order_reconcile_result.message,
                "details": {
                    "compared_at": order_reconcile_result.compared_at.isoformat(),
                    "local_open_order_count": order_reconcile_result.local_open_order_count,
                    "broker_open_order_count": order_reconcile_result.broker_open_order_count,
                    "missing_local_orders": [
                        {
                            "order_key": drift.order_key,
                            "symbol": drift.symbol,
                            "local_status": drift.local_status,
                            "broker_status": drift.broker_status,
                            "reason": drift.reason,
                        }
                        for drift in order_reconcile_result.missing_local_orders
                    ],
                    "missing_broker_orders": [
                        {
                            "order_key": drift.order_key,
                            "symbol": drift.symbol,
                            "local_status": drift.local_status,
                            "broker_status": drift.broker_status,
                            "reason": drift.reason,
                        }
                        for drift in order_reconcile_result.missing_broker_orders
                    ],
                    "status_drifts": [
                        {
                            "order_key": drift.order_key,
                            "symbol": drift.symbol,
                            "local_status": drift.local_status,
                            "broker_status": drift.broker_status,
                            "reason": drift.reason,
                        }
                        for drift in order_reconcile_result.status_drifts
                    ],
                },
            }
            return snapshot, account_reconcile, order_reconcile
        except Exception as exc:
            logger.warning(
                "Deployment %s: real QMT reconciliation failed: %s",
                deployment_id,
                exc,
            )
            return None, {
                "date": str(business_date),
                "event": "real_broker_reconcile",
                "status": "error",
                "broker_type": execution_broker.broker_type,
                "account_id": account_id or None,
                "message": str(exc),
            }, None

    def _append_real_qmt_owner_events(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        since_runtime: datetime | None = None,
        since_reports: datetime | None = None,
    ) -> None:
        if not self._is_qmt_owner_broker(self._resolve_engine_broker(engine)):
            return
        bundle = self._collect_real_qmt_owner_sync_bundle(
            deployment_id=deployment_id,
            engine=engine,
            since_runtime=since_runtime,
            since_reports=since_reports,
        )
        append_events: list[DeploymentEvent] = []
        execution_reports = bundle.execution_reports
        if execution_reports:
            append_events.extend(
                _build_shadow_execution_report_events_fn(
                    deployment_id,
                    execution_reports,
                )
            )
        runtime_events = bundle.runtime_events
        if runtime_events:
            append_events.extend(
                _build_shadow_runtime_events_fn(
                    deployment_id,
                    runtime_events,
                )
            )
        snapshot = bundle.snapshot if getattr(engine.spec, "broker_type", "") == "qmt" else None
        if snapshot is not None:
            append_events.append(
                make_broker_account_event(
                    deployment_id=deployment_id,
                    broker_type=snapshot.broker_type,
                    account_ts=snapshot.as_of,
                    account_id=str(getattr(snapshot, "account_id", "") or ""),
                    cash=snapshot.cash,
                    total_asset=snapshot.total_asset,
                    positions=snapshot.positions,
                    open_orders=snapshot.open_orders,
                    fill_count=len(snapshot.fills),
                )
            )
        if str(getattr(engine.spec, "broker_type", "") or "").lower() == "qmt":
            business_date = _derive_shadow_business_date_fn(
                snapshot=bundle.snapshot,
                broker_reports=execution_reports,
                broker_runtime_events=runtime_events,
            )
            prices = dict(getattr(engine, "_last_prices", {}) or {})
            equity = float(engine._mark_to_market(dict(prices))) if hasattr(engine, "_mark_to_market") else float(getattr(engine, "cash", 0.0))
            (
                _real_snapshot,
                real_reconcile,
                real_order_reconcile,
            ) = self._collect_real_qmt_snapshot_context(
                deployment_id=deployment_id,
                engine=engine,
                business_date=business_date,
                equity=equity,
                prices=prices,
                broker_reports=execution_reports,
                snapshot=snapshot,
            )
            real_position_reconcile = self._collect_real_qmt_position_reconcile(
                deployment_id=deployment_id,
                engine=engine,
                business_date=business_date,
                snapshot=snapshot,
            )
            real_trade_reconcile = self._collect_real_qmt_trade_reconcile(
                deployment_id=deployment_id,
                engine=engine,
                business_date=business_date,
                snapshot=snapshot,
            )
            real_qmt_hard_gate = build_qmt_reconcile_hard_gate(
                account_reconcile=real_reconcile,
                order_reconcile=real_order_reconcile,
                position_reconcile=real_position_reconcile,
                trade_reconcile=real_trade_reconcile,
                broker_type="qmt",
            )
            resolved_real_account_id = (
                (real_reconcile or {}).get("account_id")
                or (real_order_reconcile or {}).get("account_id")
                or (real_position_reconcile or {}).get("account_id")
                or (real_trade_reconcile or {}).get("account_id")
                or _extract_qmt_account_id_fn(engine.spec)
                or None
            )
            if isinstance(real_qmt_hard_gate, dict):
                real_qmt_hard_gate["event"] = "real_qmt_reconcile_hard_gate"
                real_qmt_hard_gate["account_id"] = resolved_real_account_id
            for risk_event in (
                real_reconcile,
                real_order_reconcile,
                real_position_reconcile,
                real_trade_reconcile,
                real_qmt_hard_gate,
            ):
                if risk_event is None:
                    continue
                append_events.append(
                    _build_runtime_reconcile_event_fn(
                        deployment_id=deployment_id,
                        business_date=business_date,
                        risk_event=risk_event,
                    )
                )
        if append_events:
            self.store.append_events(append_events)
        self._persist_real_qmt_owner_sync_cursor(
            deployment_id=deployment_id,
            engine=engine,
            cursor_state=bundle.cursor_state,
        )

    def _refresh_real_qmt_cancel_projection(
        self,
        *,
        deployment_id: str,
        record,
        engine: PaperTradingEngine | None,
        spec: DeploymentSpec,
    ) -> None:
        if engine is not None and str(getattr(spec, "broker_type", "") or "").lower() == "qmt":
            since_runtime = self.store.get_latest_event_ts(
                deployment_id,
                event_type=EventType.BROKER_RUNTIME_RECORDED,
            )
            since_reports = self.store.get_latest_event_ts(
                deployment_id,
                event_type=EventType.BROKER_EXECUTION_RECORDED,
            )
            self._append_real_qmt_owner_events(
                deployment_id=deployment_id,
                engine=engine,
                since_runtime=since_runtime,
                since_reports=since_reports,
            )
        _persist_qmt_runtime_projection_fn(
            self.store,
            deployment_id=deployment_id,
            record=record,
            spec=spec,
        )

    # ------------------------------------------------------------------
    # Lifecycle — start / pause / resume / stop
    # ------------------------------------------------------------------

    async def resume_all(self) -> int:
        """Startup: restore all status='running' deployments from DB.
        On engine init failure, rolls back status to 'error' (not phantom running).
        Returns the number of engines successfully restored.
        Locked with same _lock as tick() to prevent concurrent mutation.

        Each `_start_engine` call also grabs the per-deployment lock so
        resident QMT callback bridges cannot race the restore's
        `engine._order_statuses` rewrite.
        """
        self._remember_callback_refresh_loop()
        async with self._lock:
            records = self.store.list_deployments(status="running")
            restored = 0
            for record in records:
                dep_id = record.deployment_id
                dep_lock = self._get_deployment_lock(dep_id)
                async with dep_lock:
                    try:
                        await self._start_engine(dep_id)
                        restored += 1
                        logger.info("Restored deployment %s", dep_id)
                    except Exception:
                        logger.error("Failed to restore deployment %s", dep_id, exc_info=True)
                        # Roll back to error — don't leave phantom "running" in DB
                        self.store.update_status(dep_id, "error", stop_reason="恢复引擎失败")
            return restored

    async def start_deployment(self, deployment_id: str) -> None:
        """Start approved deployment. Checks status=='approved' — hard gate.
        Engine init happens BEFORE status update to prevent phantom running.

        Lock order is fixed: ALWAYS `self._lock` -> `dep_lock`. `self._lock`
        guards the `self._engines` registry so tick iteration stays safe.
        `dep_lock` enforces same-deployment serialization between
        start/restore and concurrent callback-driven `pump_broker_state` or
        `cancel_order`; it is the explicit anchor for the recovery /
        live-update mutual exclusion invariant that used to be only
        indirectly satisfied by `self._lock`.
        """
        self._remember_callback_refresh_loop()
        async with self._lock:
            dep_lock = self._get_deployment_lock(deployment_id)
            async with dep_lock:
                record = self.store.get_record(deployment_id)
                if record is None:
                    raise ValueError(f"Deployment {deployment_id!r} not found")
                if record.status != "approved":
                    raise ValueError(
                        f"Cannot start deployment {deployment_id!r}: "
                        f"status is {record.status!r}, must be 'approved'"
                    )
                # Build engine FIRST — if it fails, status stays "approved"
                await self._start_engine(deployment_id)
                try:
                    # Only update status after engine is successfully running
                    self.store.update_status(deployment_id, "running")
                except Exception:
                    engine = self._engines.get(deployment_id)
                    if engine is not None:
                        self._cleanup_engine_resources(
                            deployment_id=deployment_id,
                            engine=engine,
                        )
                    raise
                logger.info("Started deployment %s", deployment_id)

    async def pause_deployment(self, deployment_id: str) -> None:
        """Pause: engine stays in memory, tick skips. Locked."""
        self._remember_callback_refresh_loop()
        async with self._lock:
            dep_lock = self._get_deployment_lock(deployment_id)
            async with dep_lock:
                if deployment_id not in self._engines:
                    raise ValueError(f"Deployment {deployment_id!r} not running")
                self.store.update_status(deployment_id, "paused")
                self._paused.add(deployment_id)
                logger.info("Paused deployment %s", deployment_id)

    async def resume_deployment(self, deployment_id: str) -> None:
        """Resume: only from 'paused' status. Locked.

        The per-deployment lock guards `_restore_engine_state` /
        `_start_engine` against any callback-driven engine-state mutation
        that might fire on the same deployment while restore is writing
        `engine._order_statuses`.
        """
        self._remember_callback_refresh_loop()
        async with self._lock:
            dep_lock = self._get_deployment_lock(deployment_id)
            async with dep_lock:
                record = self.store.get_record(deployment_id)
                if record is None:
                    raise ValueError(f"Deployment {deployment_id!r} not found")
                if record.status != "paused":
                    raise ValueError(
                        f"Cannot resume deployment {deployment_id!r}: "
                        f"status is {record.status!r}, must be 'paused'"
                    )
                started_engine = False
                if deployment_id not in self._engines:
                    await self._start_engine(deployment_id)
                    started_engine = True
                try:
                    self.store.update_status(deployment_id, "running")
                except Exception:
                    if started_engine:
                        engine = self._engines.get(deployment_id)
                        if engine is not None:
                            self._cleanup_engine_resources(
                                deployment_id=deployment_id,
                                engine=engine,
                            )
                    raise
                self._paused.discard(deployment_id)
                logger.info("Resumed deployment %s", deployment_id)

    async def pump_broker_state(self, deployment_id: str) -> dict:
        """Synchronize shadow-broker runtime/account/execution state without a daily tick.

        Holds the per-deployment lock so an in-flight `tick()` cannot
        concurrently advance engine state while this method is also
        re-reading/writing the same deployment's broker projection.
        """
        self._remember_callback_refresh_loop()
        async with self._lock:
            dep_lock = self._get_deployment_lock(deployment_id)
            async with dep_lock:
                record = self.store.get_record(deployment_id)
                if record is None:
                    raise ValueError(f"Deployment {deployment_id!r} not found")
                engine = self._engines.get(deployment_id)
                if engine is None:
                    raise ValueError(
                        f"Deployment {deployment_id!r} is not loaded; start or resume it before broker sync"
                    )
                shadow_broker = self._resolve_engine_shadow_broker(engine)
                if shadow_broker is None:
                    raise ValueError(
                        f"Deployment {deployment_id!r} has no configured shadow broker"
                    )
                return self._pump_broker_state_locked(
                    deployment_id=deployment_id,
                    record=record,
                    engine=engine,
                )

    def _pump_broker_state_locked(
        self,
        *,
        deployment_id: str,
        record,
        engine: PaperTradingEngine,
    ) -> dict:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        if shadow_broker is None:
            raise ValueError(
                f"Deployment {deployment_id!r} has no configured shadow broker"
            )
        since_runtime = self.store.get_latest_event_ts(
            deployment_id,
            event_type=EventType.BROKER_RUNTIME_RECORDED,
        )
        since_reports = self.store.get_latest_event_ts(
            deployment_id,
            event_type=EventType.BROKER_EXECUTION_RECORDED,
        )

        bundle = self._collect_shadow_sync_bundle(
            deployment_id=deployment_id,
            engine=engine,
        )
        business_date = _derive_shadow_business_date_fn(
            snapshot=bundle.snapshot,
            broker_reports=bundle.execution_reports,
            broker_runtime_events=bundle.runtime_events,
        )
        (
            shadow_snapshot,
            shadow_reconcile,
            shadow_order_reconcile,
        ) = self._collect_shadow_snapshot_context(
            deployment_id=deployment_id,
            engine=engine,
            business_date=business_date,
            equity=float(engine._mark_to_market(dict(engine._last_prices))),
            prices=dict(engine._last_prices),
            broker_reports=bundle.execution_reports,
            snapshot=bundle.snapshot,
        )
        shadow_position_reconcile = self._collect_shadow_position_reconcile(
            deployment_id=deployment_id,
            engine=engine,
            business_date=business_date,
            snapshot=shadow_snapshot,
        )
        shadow_trade_reconcile = self._collect_shadow_trade_reconcile(
            deployment_id=deployment_id,
            engine=engine,
            business_date=business_date,
            snapshot=shadow_snapshot,
        )
        qmt_hard_gate = build_qmt_reconcile_hard_gate(
            account_reconcile=shadow_reconcile,
            order_reconcile=shadow_order_reconcile,
            position_reconcile=shadow_position_reconcile,
            trade_reconcile=shadow_trade_reconcile,
            broker_type=shadow_broker.broker_type,
        )
        events = _build_shadow_sync_events_fn(
            deployment_id=deployment_id,
            business_date=business_date,
            snapshot=shadow_snapshot,
            broker_reports=bundle.execution_reports,
            broker_runtime_events=bundle.runtime_events,
            account_reconcile=shadow_reconcile,
            order_reconcile=shadow_order_reconcile,
            hard_gate=qmt_hard_gate,
            position_reconcile=shadow_position_reconcile,
            trade_reconcile=shadow_trade_reconcile,
        )
        self.store.save_broker_sync_result(
            deployment_id=deployment_id,
            events=events,
            broker_reports=bundle.execution_reports,
        )
        self._append_real_qmt_owner_events(
            deployment_id=deployment_id,
            engine=engine,
            since_runtime=since_runtime,
            since_reports=since_reports,
        )
        self._persist_shadow_sync_cursor(
            deployment_id=deployment_id,
            engine=engine,
            cursor_state=bundle.cursor_state,
        )
        runtime_projection = _persist_qmt_runtime_projection_fn(
            self.store,
            deployment_id=deployment_id,
            record=record,
            spec=engine.spec,
        )
        effective_reconcile_status = (
            runtime_projection.get("broker_reconcile_status")
            if isinstance(runtime_projection, dict)
            and str(getattr(engine.spec, "broker_type", "") or "").lower() == "qmt"
            else (
                shadow_reconcile.get("status") if isinstance(shadow_reconcile, dict) else None
            )
        )
        effective_order_reconcile_status = (
            runtime_projection.get("broker_order_reconcile_status")
            if isinstance(runtime_projection, dict)
            and str(getattr(engine.spec, "broker_type", "") or "").lower() == "qmt"
            else (
                shadow_order_reconcile.get("status")
                if isinstance(shadow_order_reconcile, dict)
                else None
            )
        )
        effective_qmt_hard_gate_status = (
            runtime_projection.get("qmt_hard_gate_status")
            if isinstance(runtime_projection, dict)
            and str(getattr(engine.spec, "broker_type", "") or "").lower() == "qmt"
            else (
                qmt_hard_gate.get("status") if isinstance(qmt_hard_gate, dict) else None
            )
        )
        effective_position_reconcile_status = (
            runtime_projection.get("position_reconcile_status")
            if isinstance(runtime_projection, dict)
            and str(getattr(engine.spec, "broker_type", "") or "").lower() == "qmt"
            else (
                shadow_position_reconcile.get("status")
                if isinstance(shadow_position_reconcile, dict)
                else None
            )
        )
        effective_trade_reconcile_status = (
            runtime_projection.get("trade_reconcile_status")
            if isinstance(runtime_projection, dict)
            and str(getattr(engine.spec, "broker_type", "") or "").lower() == "qmt"
            else (
                shadow_trade_reconcile.get("status")
                if isinstance(shadow_trade_reconcile, dict)
                else None
            )
        )
        return {
            "deployment_id": deployment_id,
            "status": "broker_synced",
            "business_date": str(business_date),
            "broker_type": shadow_broker.broker_type,
            "account_event_count": 1 if shadow_snapshot is not None else 0,
            "runtime_event_count": len(bundle.runtime_events),
            "execution_report_count": len(bundle.execution_reports),
            "reconcile_status": effective_reconcile_status,
            "order_reconcile_status": effective_order_reconcile_status,
            "position_reconcile_status": effective_position_reconcile_status,
            "trade_reconcile_status": effective_trade_reconcile_status,
            "qmt_hard_gate_status": effective_qmt_hard_gate_status,
            "qmt_readiness": (
                runtime_projection.get("qmt_readiness")
                if isinstance(runtime_projection, dict)
                else None
            ),
            "qmt_submit_gate": (
                runtime_projection.get("qmt_submit_gate")
                if isinstance(runtime_projection, dict)
                else None
            ),
            "qmt_release_gate": (
                runtime_projection.get("qmt_release_gate")
                if isinstance(runtime_projection, dict)
                else None
            ),
        }

    async def stop_deployment(self, deployment_id: str, reason: str, liquidate: bool = False) -> None:
        """Stop: release engine, update DB status. Validates record exists and is stoppable.

        Parameters
        ----------
        liquidate : bool
            If True, generate empty target weights to close all positions
            before stopping. The liquidation trades are saved as a final
            snapshot. If the engine is not found (e.g., after a restart),
            liquidation is skipped and a warning is logged.
        """
        async with self._lock:
            dep_lock = self._get_deployment_lock(deployment_id)
            async with dep_lock:
                record = self.store.get_record(deployment_id)
                if record is None:
                    raise ValueError(f"Deployment {deployment_id!r} not found")
                if record.status in ("stopped", "pending"):
                    raise ValueError(
                        f"Cannot stop deployment {deployment_id!r}: "
                        f"status is {record.status!r}")

                liquidation_trades: list[dict] = []
                if liquidate and deployment_id in self._engines:
                    engine = self._engines[deployment_id]
                    if engine.holdings:
                        try:
                            from datetime import date as _date
                            from ez.portfolio.execution import CostModel
                            today = _date.today()
                            prices = dict(engine._last_prices)
                            raw_closes = dict(prices)
                            prev_raw = dict(prices)
                            has_bar = set(engine.holdings.keys())
                            cost_model = CostModel(
                                buy_commission_rate=engine.spec.buy_commission_rate,
                                sell_commission_rate=engine.spec.sell_commission_rate,
                                min_commission=engine.spec.min_commission,
                                stamp_tax_rate=engine.spec.stamp_tax_rate,
                                slippage_rate=engine.spec.slippage_rate,
                            )
                            equity = engine._mark_to_market(prices)
                            broker_result = self._resolve_engine_broker(engine).execute_target_weights(
                                business_date=today,
                                target_weights={},
                                holdings=engine.holdings,
                                equity=equity,
                                cash=engine.cash,
                                prices=prices,
                                raw_close_today=raw_closes,
                                prev_raw_close=prev_raw,
                                has_bar_today=has_bar,
                                cost_model=cost_model,
                                lot_size=engine.spec.lot_size,
                                limit_pct=0.0,  # no limit check on liquidation
                                t_plus_1=False,  # allow selling everything
                            )
                            liquidation_trades = [
                                fill.to_trade_dict()
                                for fill in broker_result.fills
                            ]
                            engine.holdings = broker_result.holdings
                            engine.cash = broker_result.cash
                            # Save liquidation snapshot — use ACTUAL new_holdings (may be
                            # non-empty if some positions couldn't be sold due to missing prices)
                            post_equity = broker_result.cash + sum(
                                broker_result.holdings.get(s, 0) * prices.get(s, 0)
                                for s in broker_result.holdings
                            )
                            # Final liquidation snapshot uses the atomic
                            # snapshot+events path so that future liquidation
                            # event emissions (when the broker path gains
                            # structured liquidation events) don't need a
                            # second migration.
                            self.store.save_snapshot_with_events(
                                deployment_id,
                                today,
                                {
                                    "date": str(today),
                                    "equity": post_equity,
                                    "cash": broker_result.cash,
                                    "holdings": dict(broker_result.holdings),
                                    "weights": {},
                                    "trades": liquidation_trades,
                                    "risk_events": [],
                                    "rebalanced": False,
                                    "liquidation": True,
                                },
                                [],
                            )
                            logger.info("Liquidated %d positions for %s", len(liquidation_trades), deployment_id)
                        except Exception:
                            logger.error("Liquidation failed for %s", deployment_id, exc_info=True)
                    else:
                        logger.info("No holdings to liquidate for %s", deployment_id)
                elif liquidate:
                    logger.warning("Engine not found for %s, skipping liquidation", deployment_id)

                engine = self._engines.get(deployment_id)
                if engine is not None:
                    self._detach_qmt_broker_owners(
                        deployment_id=deployment_id,
                        engine=engine,
                    )
                    self._sync_shadow_runtime_state(
                        deployment_id=deployment_id,
                        engine=engine,
                    )
                try:
                    self.store.update_status(deployment_id, "stopped", stop_reason=reason)
                except Exception:
                    if engine is not None:
                        try:
                            self._attach_qmt_broker_owners(
                                deployment_id=deployment_id,
                                engine=engine,
                            )
                        except Exception:
                            logger.warning(
                                "Deployment %s: failed to reattach QMT owner after stop rollback",
                                deployment_id,
                                exc_info=True,
                            )
                        try:
                            self._sync_shadow_runtime_state(
                                deployment_id=deployment_id,
                                engine=engine,
                            )
                        except Exception:
                            logger.warning(
                                "Deployment %s: failed to persist rollback runtime after stop failure",
                                deployment_id,
                                exc_info=True,
                            )
                    raise
                self._engines.pop(deployment_id, None)
                self._paused.discard(deployment_id)
                logger.info("Stopped deployment %s: %s", deployment_id, reason)
            # Terminal state — attempt to drop the now-idle lock entry so
            # long-lived processes don't accumulate dead deployment locks.
            self._drop_deployment_lock(deployment_id)

    async def cancel_order(
        self,
        deployment_id: str,
        *,
        client_order_id: str = "",
        broker_order_id: str = "",
    ) -> dict:
        """Request broker-side order cancellation via persisted broker-order identity.

        Takes the per-deployment lock for the duration. Ensures this path
        never races with `tick()` advancing engine state for the same
        deployment, nor with `_restore_full_state` rewriting
        `engine._order_statuses` during concurrent start/resume.
        """
        async with self._lock:
            dep_lock = self._get_deployment_lock(deployment_id)
            async with dep_lock:
                record = self.store.get_record(deployment_id)
                if record is None:
                    raise ValueError(f"Deployment {deployment_id!r} not found")
                if not client_order_id and not broker_order_id:
                    raise ValueError("Either client_order_id or broker_order_id must be provided")

                engine = self._engines.get(deployment_id)
                spec = engine.spec if engine is not None else self.store.get_spec(record.spec_id)
                if spec is None:
                    raise ValueError(
                        f"Spec {record.spec_id!r} not found for deployment {deployment_id!r}"
                    )
                cancel_broker = self._resolve_cancel_broker(
                    spec=spec,
                    engine=engine,
                )

                if cancel_broker is None:
                    raise NotImplementedError(
                        f"Deployment {deployment_id!r} has no broker with CANCEL_ORDER capability"
                    )

                broker_type = cancel_broker.broker_type
                link = None
                if client_order_id:
                    link = self.store.get_broker_order_link(
                        deployment_id,
                        broker_type=broker_type,
                        client_order_id=client_order_id,
                    )
                elif broker_order_id:
                    link = self.store.find_broker_order_link(
                        deployment_id,
                        broker_type=broker_type,
                        broker_order_id=broker_order_id,
                        account_id=_extract_qmt_account_id_fn(spec) if broker_type == "qmt" else "",
                    )

                resolved_client_order_id = client_order_id or (link["client_order_id"] if link else "")
                resolved_broker_order_id = broker_order_id or (link["broker_order_id"] if link else "")
                symbol = link["symbol"] if link else ""
                account_id = (
                    str(link.get("account_id", "") or "").strip()
                    if isinstance(link, dict)
                    else ""
                ) or (_extract_qmt_account_id_fn(spec) if broker_type == "qmt" else "")
                cancel_ref = resolved_broker_order_id or resolved_client_order_id
                if not cancel_ref:
                    raise ValueError(
                        f"No broker-order link found for deployment {deployment_id!r} and the provided identifiers"
                    )
                if link is not None:
                    link_status = normalize_broker_order_status(
                        str(link.get("latest_status", "") or "")
                    )
                    if link_status in _BROKER_CANCEL_BLOCKING_STATUSES:
                        raise ValueError(
                            f"Order {cancel_ref!r} for deployment {deployment_id!r} is already in "
                            f"cancel-inflight or terminal state {link_status!r}"
                        )

                request_ts = utcnow()
                broker_order_key = resolved_broker_order_id or cancel_ref
                self.store.append_event(
                    make_broker_cancel_requested_event(
                        deployment_id,
                        broker_type=broker_type,
                        request_ts=request_ts,
                        client_order_id=resolved_client_order_id,
                        broker_order_id=broker_order_key,
                        symbol=symbol,
                        account_id=account_id,
                    )
                )
                try:
                    canceled = cancel_broker.cancel_order(cancel_ref, symbol=symbol)
                    if not canceled:
                        raise RuntimeError(
                            f"Broker {broker_type!r} rejected cancel request for order {cancel_ref!r}"
                        )
                except Exception as exc:
                    self.store.append_event(
                        make_broker_runtime_event(
                            deployment_id,
                            runtime_event_id=(
                                f"cancel_error:{broker_order_key or resolved_client_order_id}:"
                                f"{utcnow().isoformat()}"
                            ),
                            broker_type=broker_type,
                            runtime_kind="cancel_error",
                            event_ts=utcnow(),
                            payload={
                                "client_order_id": resolved_client_order_id,
                                "broker_order_id": broker_order_key,
                                "order_sysid": broker_order_key,
                                "account_id": account_id,
                                "status_msg": str(exc),
                            },
                        )
                    )
                    self._refresh_real_qmt_cancel_projection(
                        deployment_id=deployment_id,
                        record=record,
                        engine=engine,
                        spec=spec,
                    )
                    raise
                self._refresh_real_qmt_cancel_projection(
                    deployment_id=deployment_id,
                    record=record,
                    engine=engine,
                    spec=spec,
                )
                logger.info(
                    "Deployment %s cancel requested via %s for order %s",
                    deployment_id,
                    broker_type,
                    cancel_ref,
                )
                return {
                    "deployment_id": deployment_id,
                    "broker_type": broker_type,
                    "client_order_id": resolved_client_order_id,
                    "broker_order_id": broker_order_key,
                    "symbol": symbol,
                    "status": "cancel_requested",
                }

    # ------------------------------------------------------------------
    # Tick — daily execution
    # ------------------------------------------------------------------

    def get_auto_tick_batches(self) -> list[tuple[date, tuple[str, ...] | None]]:
        """Return market-scoped business dates for unattended auto-tick.

        Deployments trading on the same market-local date are grouped into
        one batch. This avoids feeding a single host-local date into mixed
        markets whose local trading day differs.
        """
        self._remember_callback_refresh_loop()
        if not self._engines:
            return [(datetime.now(timezone.utc).date(), None)]
        grouped: dict[date, set[str]] = {}
        for engine in self._engines.values():
            market = str(getattr(engine.spec, "market", "") or "")
            business_date = self._market_today(market)
            grouped.setdefault(business_date, set()).add(market)
        return [
            (business_date, tuple(sorted(markets)))
            for business_date, markets in sorted(grouped.items(), key=lambda item: item[0])
        ]

    async def tick(
        self,
        business_date: date,
        *,
        markets: tuple[str, ...] | None = None,
    ) -> list[dict]:
        """Daily execution. asyncio.Lock covers entire tick.
        Per-deployment: check paused -> check calendar -> check idempotent -> execute -> save.

        V2.16.2 guard: business_date must not be in the future. A future
        date would (a) fetch no fresh bars (live data unavailable), (b)
        trivially succeed with "no trades", (c) advance `last_processed_date`
        past today — permanently blocking correct ticks until the wall
        clock catches up. Refuse with ValueError so the caller notices.
        """
        self._remember_callback_refresh_loop()
        market_scope = {
            str(market or "")
            for market in (markets or ())
        }
        if not market_scope:
            market_scope = None
        fallback_today = datetime.now(timezone.utc).date()
        future_markets = sorted(
            {
                str(getattr(engine.spec, "market", "") or "")
                for engine in self._engines.values()
                if market_scope is None
                or str(getattr(engine.spec, "market", "") or "") in market_scope
                if business_date > self._market_today(str(getattr(engine.spec, "market", "") or ""))
            }
        )
        if not self._engines and business_date > fallback_today:
            raise ValueError(
                f"business_date {business_date} is in the future "
                f"(today={fallback_today}). Refuse to advance last_processed_date "
                f"past real-world time; this would block subsequent ticks."
            )
        if future_markets:
            raise ValueError(
                f"business_date {business_date} is in the future "
                f"for active market(s) {future_markets}. Refuse to advance "
                f"last_processed_date past market-local time; this would block subsequent ticks."
            )
        results: list[dict] = []
        async with self._lock:
            # Snapshot engine keys to avoid mutation during iteration
            dep_ids = list(self._engines.keys())
            for dep_id in dep_ids:
                engine = self._engines.get(dep_id)
                if engine is None:
                    continue
                if (
                    market_scope is not None
                    and str(getattr(engine.spec, "market", "") or "") not in market_scope
                ):
                    continue
                # Per-deployment lock nested under the registry lock. Holding
                # it here is the explicit anchor of "tick for deployment X is
                # mutually exclusive with broker-sync / cancel_order /
                # restart-restore for the same deployment X." The outer
                # `self._lock` already serializes tick against those paths
                # today, but the per-dep lock keeps the invariant load-bearing
                # even if the outer lock is relaxed later.
                dep_lock = self._get_deployment_lock(dep_id)
                async with dep_lock:
                    record = self.store.get_record(dep_id)
                    if record is None:
                        logger.warning("Skipping %s: deployment record not found", dep_id)
                        continue

                    # 1. Skip paused
                    if dep_id in self._paused:
                        logger.debug("Skipping paused deployment %s", dep_id)
                        continue

                    # 2. Check calendar — per-deployment market
                    calendar = self._get_calendar(engine.spec.market)
                    if not calendar.is_trading_day(business_date):
                        logger.debug(
                            "Skipping %s: %s is not a trading day for %s",
                            dep_id, business_date, engine.spec.market,
                        )
                        continue

                    # 3. Idempotency — skip if already processed
                    last_date = self.store.get_last_processed_date(dep_id)
                    if last_date is not None and last_date >= business_date:
                        logger.debug(
                            "Skipping %s: already processed %s (last=%s)",
                            dep_id, business_date, last_date,
                        )
                        continue

                    # 4. Execute
                    submit_gate_broker = None
                    try:
                        if getattr(engine.spec, "broker_type", "paper") == "qmt":
                            self._pump_broker_state_locked(
                                deployment_id=dep_id,
                                record=record,
                                engine=engine,
                            )
                            qmt_gate_open, qmt_gate_reason = self._qmt_real_submit_gate_open(dep_id)
                            if not qmt_gate_open:
                                raise RuntimeError(
                                    f"QMT real submit gate closed for deployment {dep_id}: {qmt_gate_reason}"
                                )
                            broker_resolved = self._resolve_engine_broker(engine)
                            open_submit_gate = getattr(broker_resolved, "open_submit_gate", None)
                            if callable(open_submit_gate):
                                projection = (
                                    self.store.get_broker_state_projection(
                                        dep_id, broker_type="qmt"
                                    )
                                    or {}
                                )
                                submit_gate_decision = projection.get("qmt_submit_gate") or {
                                    "status": "open",
                                    "can_submit_now": True,
                                    "source": "runtime",
                                }
                                open_submit_gate(submit_gate_decision)
                                submit_gate_broker = broker_resolved
                        # Fix A: pre-fetch today's bars so engine sees fresh data
                        self._prefetch_day_bars(engine, business_date)
                        t0 = time.monotonic()
                        result = engine.execute_day(business_date)
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        result["execution_ms"] = elapsed_ms
                        oms_events = [
                            e if isinstance(e, DeploymentEvent) else DeploymentEvent.from_dict(e)
                            for e in result.pop("_oms_events", [])
                        ]
                        pre_events: list[DeploymentEvent] = []
                        market_bars = list(result.pop("_market_bars", []))
                        for market_bar in market_bars:
                            pre_events.append(
                                make_market_bar_event(
                                    deployment_id=dep_id,
                                    business_date=business_date,
                                    symbol=str(market_bar.get("symbol", "")),
                                    open_price=float(market_bar.get("open", 0.0)),
                                    high_price=float(market_bar.get("high", 0.0)),
                                    low_price=float(market_bar.get("low", 0.0)),
                                    close_price=float(market_bar.get("close", 0.0)),
                                    adj_close=float(market_bar.get("adj_close", 0.0)),
                                    volume=float(market_bar.get("volume", 0.0)),
                                    source=str(market_bar.get("source", "live")),
                                )
                            )
                        market_snapshot = result.pop("_market_snapshot", None)
                        if market_snapshot:
                            pre_events.append(
                                make_market_snapshot_event(
                                    deployment_id=dep_id,
                                    business_date=business_date,
                                    prices=market_snapshot.get("prices", {}),
                                    has_bar_symbols=list(market_snapshot.get("has_bar_symbols", [])),
                                    source=str(market_snapshot.get("source", "live")),
                                )
                            )
                        shadow_bundle = self._collect_shadow_sync_bundle(
                            deployment_id=dep_id,
                            engine=engine,
                        )
                        broker_reports = list(shadow_bundle.execution_reports)
                        broker_runtime_events = list(shadow_bundle.runtime_events)
                        (
                            shadow_snapshot,
                            shadow_reconcile,
                            shadow_order_reconcile,
                        ) = self._collect_shadow_snapshot_context(
                            deployment_id=dep_id,
                            engine=engine,
                            business_date=business_date,
                            equity=float(result.get("equity", engine.cash)),
                            prices=dict((market_snapshot or {}).get("prices", {}) or engine._last_prices),
                            broker_reports=broker_reports,
                            snapshot=shadow_bundle.snapshot,
                        )
                        shadow_position_reconcile = self._collect_shadow_position_reconcile(
                            deployment_id=dep_id,
                            engine=engine,
                            business_date=business_date,
                            snapshot=shadow_snapshot,
                        )
                        shadow_trade_reconcile = self._collect_shadow_trade_reconcile(
                            deployment_id=dep_id,
                            engine=engine,
                            business_date=business_date,
                            snapshot=shadow_snapshot,
                        )
                        qmt_hard_gate = build_qmt_reconcile_hard_gate(
                            account_reconcile=shadow_reconcile,
                            order_reconcile=shadow_order_reconcile,
                            position_reconcile=shadow_position_reconcile,
                            trade_reconcile=shadow_trade_reconcile,
                            broker_type=getattr(
                                self._resolve_engine_shadow_broker(engine),
                                "broker_type",
                                "",
                            ),
                        )
                        risk_events = list(result.get("risk_events", []))
                        if shadow_reconcile is not None:
                            risk_events.append(shadow_reconcile)
                        if shadow_order_reconcile is not None:
                            risk_events.append(shadow_order_reconcile)
                        if shadow_position_reconcile is not None:
                            risk_events.append(shadow_position_reconcile)
                        if shadow_trade_reconcile is not None:
                            risk_events.append(shadow_trade_reconcile)
                        if qmt_hard_gate is not None:
                            risk_events.append(qmt_hard_gate)
                        result["risk_events"] = risk_events
                        shadow_events = _build_shadow_sync_events_fn(
                            deployment_id=dep_id,
                            business_date=business_date,
                            snapshot=shadow_snapshot,
                            broker_reports=broker_reports,
                            broker_runtime_events=broker_runtime_events,
                            account_reconcile=shadow_reconcile,
                            order_reconcile=shadow_order_reconcile,
                            hard_gate=qmt_hard_gate,
                            position_reconcile=shadow_position_reconcile,
                            trade_reconcile=shadow_trade_reconcile,
                        )
                        shadow_risk_event_count = (
                            int(shadow_reconcile is not None)
                            + int(shadow_order_reconcile is not None)
                            + int(shadow_position_reconcile is not None)
                            + int(shadow_trade_reconcile is not None)
                            + int(qmt_hard_gate is not None)
                        )
                        post_events = list(shadow_events) + [
                            make_risk_event(
                                deployment_id=dep_id,
                                business_date=business_date,
                                risk_index=shadow_risk_event_count + index,
                                risk_event=risk_event,
                            )
                            for index, risk_event in enumerate(result.get("risk_events", []))
                        ]
                        post_events.append(
                            make_snapshot_event(
                                deployment_id=dep_id,
                                business_date=business_date,
                                equity=float(result.get("equity", 0.0)),
                                cash=float(result.get("cash", 0.0)),
                                rebalanced=bool(result.get("rebalanced", False)),
                                trade_count=len(result.get("trades", [])),
                                holdings=result.get("holdings"),
                                weights=result.get("weights"),
                                prev_returns=result.get("prev_returns"),
                            )
                        )
                        post_events.append(
                            make_tick_completed_event(
                                deployment_id=dep_id,
                                business_date=business_date,
                                execution_ms=elapsed_ms,
                                rebalanced=bool(result.get("rebalanced", False)),
                                trade_count=len(result.get("trades", [])),
                                risk_event_count=len(risk_events),
                                equity=float(result.get("equity", 0.0)),
                                cash=float(result.get("cash", 0.0)),
                            )
                        )
                        oms_events = _sequence_runtime_events_fn(
                            pre_events=pre_events,
                            oms_events=oms_events,
                            post_events=post_events,
                        )

                        # 5. Save snapshot + strategy pickle (V2.17)
                        # Pickle the strategy so sklearn models / ensemble
                        # ledgers / user state survive restart. Per-strategy
                        # failures (unpicklable attrs) log once and fall back
                        # to NULL — deployment still advances.
                        state_blob = _pickle_strategy(engine.strategy, dep_id)
                        since_runtime = self.store.get_latest_event_ts(
                            dep_id,
                            event_type=EventType.BROKER_RUNTIME_RECORDED,
                        )
                        since_reports = self.store.get_latest_event_ts(
                            dep_id,
                            event_type=EventType.BROKER_EXECUTION_RECORDED,
                        )
                        # Atomic: events + snapshot + broker-order links in one
                        # DuckDB transaction (single BEGIN/COMMIT). Failure to
                        # write the snapshot row rolls back the event log so
                        # recovery never observes orphan events without a
                        # matching snapshot checkpoint.
                        self.store.save_snapshot_with_events(
                            dep_id,
                            business_date,
                            result,
                            oms_events,
                            broker_reports,
                            strategy_state=state_blob,
                        )
                        self._append_real_qmt_owner_events(
                            deployment_id=dep_id,
                            engine=engine,
                            since_runtime=since_runtime,
                            since_reports=since_reports,
                        )
                        self._persist_shadow_sync_cursor(
                            deployment_id=dep_id,
                            engine=engine,
                            cursor_state=shadow_bundle.cursor_state,
                        )
                        _persist_qmt_runtime_projection_fn(
                            self.store,
                            deployment_id=dep_id,
                            record=record,
                            spec=engine.spec,
                        )
                        self.store.reset_error_count(dep_id)

                        result["deployment_id"] = dep_id
                        results.append(result)
                        logger.info(
                            "Deployment %s executed %s (%.1fms, equity=%.2f)",
                            dep_id, business_date, elapsed_ms,
                            result.get("equity", 0),
                        )

                    except Exception as e:
                        logger.error(
                            "Deployment %s failed on %s: %s",
                            dep_id, business_date, e, exc_info=True,
                        )
                        # Save error snapshot
                        self.store.save_error(dep_id, business_date, str(e))
                        error_count = self.store.increment_error_count(dep_id)

                        if error_count >= MAX_CONSECUTIVE_ERRORS:
                            logger.error(
                                "Deployment %s reached %d consecutive errors — setting to error state",
                                dep_id, error_count,
                            )
                            self._detach_qmt_broker_owners(
                                deployment_id=dep_id,
                                engine=engine,
                            )
                            self._sync_shadow_runtime_state(
                                deployment_id=dep_id,
                                engine=engine,
                            )
                            self.store.update_status(
                                dep_id, "error",
                                stop_reason=f"连续 {error_count} 次执行失败: {e}",
                            )
                            self._engines.pop(dep_id, None)
                            self._paused.discard(dep_id)
                    finally:
                        if submit_gate_broker is not None:
                            close_submit_gate = getattr(
                                submit_gate_broker, "close_submit_gate", None
                            )
                            if callable(close_submit_gate):
                                try:
                                    close_submit_gate()
                                except Exception:
                                    logger.exception(
                                        "Deployment %s: close_submit_gate() failed",
                                        dep_id,
                                    )

        return results

    def _qmt_real_submit_gate_open(self, deployment_id: str) -> tuple[bool, str]:
        projection = self.store.get_broker_state_projection(
            deployment_id,
            broker_type="qmt",
        )
        if not isinstance(projection, dict):
            return False, "qmt_runtime_projection_unavailable"
        submit_gate = projection.get("qmt_submit_gate")
        if not isinstance(submit_gate, dict):
            return False, "qmt_submit_gate_unavailable"
        submit_status = str(submit_gate.get("status", "") or "").lower()
        if submit_status != "open":
            return False, f"qmt_submit_gate_{submit_status or 'blocked'}"
        if not bool(submit_gate.get("can_submit_now")):
            return False, "qmt_submit_gate_cannot_submit_now"
        release_gate = projection.get("qmt_release_gate")
        if not isinstance(release_gate, dict):
            return False, "qmt_release_gate_unavailable"
        release_status = str(release_gate.get("status", "") or "").lower()
        if release_status != "candidate":
            return False, f"qmt_release_gate_{release_status or 'blocked'}"
        if not bool(release_gate.get("eligible_for_real_submit")):
            return False, "qmt_release_gate_not_eligible"
        return True, "qmt_submit_gate_open"

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    def _prefetch_day_bars(self, engine, business_date: date) -> int:
        """Pre-fetch today's bars from provider chain and save to DuckDB.

        Ensures engine._fetch_latest sees fresh data instead of stale cache.
        Returns the number of symbols successfully refreshed.
        """
        refreshed = 0
        chain = getattr(engine, "data_chain", None)
        if chain is None:
            return 0
        market = getattr(engine.spec, "market", "cn_stock")
        symbols = getattr(engine.spec, "symbols", [])
        for sym in symbols:
            try:
                for provider in getattr(chain, "_providers", []):
                    bars = provider.get_kline(sym, market, "daily", business_date, business_date)
                    if bars:
                        chain._store.save_kline(bars, "daily")
                        refreshed += 1
                        break
            except Exception:
                pass
        if refreshed > 0:
            logger.info(
                "Pre-fetched %d/%d symbols for %s", refreshed, len(symbols), business_date
            )
        return refreshed

    def _get_calendar(self, market: str) -> TradingCalendar:
        """Per-market calendar (cached)."""
        if market not in self._calendars:
            self._calendars[market] = TradingCalendar.from_market(market)
        return self._calendars[market]

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    async def _start_engine(self, deployment_id: str) -> None:
        """Instantiate strategy + engine + restore full state."""
        record = self.store.get_record(deployment_id)
        if record is None:
            raise ValueError(f"Deployment record {deployment_id!r} not found")

        spec = self.store.get_spec(record.spec_id)
        if spec is None:
            raise ValueError(f"Spec {record.spec_id!r} not found for deployment {deployment_id!r}")

        # V2.16.2: historical specs built by the buggy `_build_spec_from_run`
        # could silently carry CN defaults into non-CN markets. Treat these
        # as invalid and fail closed instead of paper-trading with the wrong
        # execution rules.
        mismatches = _historical_non_cn_market_rule_mismatches_fn(spec)
        if mismatches:
            mismatch_text = ", ".join(mismatches)
            raise ValueError(
                "Deployment "
                f"{deployment_id} (spec_id={record.spec_id}, market={spec.market}) "
                "carries A-share market rules "
                f"({mismatch_text}) on a non-CN market — likely built by "
                "pre-V2.16.2 _build_spec_from_run. Redeploy from the source "
                "run before starting."
            )

        strategy, optimizer, risk_manager = self._instantiate(spec)

        # V2.17: try to restore strategy internals from last pickled
        # snapshot. If successful, the restored strategy replaces the
        # fresh instance — MLAlpha keeps its trained model, Ensemble
        # keeps its ledger. Failure (unpicklable, class rename, format
        # drift) silently falls back to the fresh `strategy` above.
        try:
            pickle_blob = self.store.get_latest_strategy_state(deployment_id)
        except Exception as e:
            logger.warning(
                "Deployment %s: get_latest_strategy_state failed (%s) — "
                "using fresh strategy.", deployment_id, e,
            )
            pickle_blob = None
        if pickle_blob:
            restored = _unpickle_strategy(pickle_blob, deployment_id)
            if restored is not None and _strategy_restore_compatible(restored, strategy):
                # Sanity-check class match before swapping; otherwise a
                # spec.strategy_name change would silently drive the
                # engine with an orphan strategy from a different class
                strategy = restored
                logger.info(
                    "Deployment %s: restored strategy %s from pickle",
                    deployment_id, type(strategy).__name__,
                )

        engine = PaperTradingEngine(
            spec=spec,
            strategy=strategy,
            data_chain=self.data_chain,
            optimizer=optimizer,
            risk_manager=risk_manager,
            deployment_id=deployment_id,
            broker=self._build_execution_broker(spec),
            shadow_broker=self._build_shadow_broker(spec),
        )

        # Restore full state from event ledger / snapshot fallback
        self._restore_full_state(engine, deployment_id)
        try:
            self._attach_qmt_broker_owners(deployment_id=deployment_id, engine=engine)
            self._sync_shadow_runtime_state(deployment_id=deployment_id, engine=engine)
        except Exception:
            self._cleanup_engine_resources(
                deployment_id=deployment_id,
                engine=engine,
            )
            raise
        self._engines[deployment_id] = engine

    def _cleanup_engine_resources(self, *, deployment_id: str, engine: PaperTradingEngine) -> None:
        try:
            self._detach_qmt_broker_owners(
                deployment_id=deployment_id,
                engine=engine,
            )
        except Exception:
            logger.warning(
                "Deployment %s: failed to detach QMT owner during cleanup",
                deployment_id,
                exc_info=True,
            )
        try:
            self._sync_shadow_runtime_state(
                deployment_id=deployment_id,
                engine=engine,
            )
        except Exception:
            logger.warning(
                "Deployment %s: failed to persist QMT runtime during cleanup",
                deployment_id,
                exc_info=True,
            )
        self._engines.pop(deployment_id, None)
        self._paused.discard(deployment_id)

    def _sync_shadow_runtime_state(self, *, deployment_id: str, engine: PaperTradingEngine) -> None:
        """Persist shadow-broker runtime events immediately after engine start/resume.

        This closes the observability gap where a QMT shadow session may already
        be bootstrapped but its runtime/owner events would otherwise stay only in
        process memory until the next daily tick.
        """
        bundle = self._collect_shadow_sync_bundle(
            deployment_id=deployment_id,
            engine=engine,
        )
        since_runtime = self.store.get_latest_event_ts(
            deployment_id,
            event_type=EventType.BROKER_RUNTIME_RECORDED,
        )
        since_reports = self.store.get_latest_event_ts(
            deployment_id,
            event_type=EventType.BROKER_EXECUTION_RECORDED,
        )
        runtime_events = bundle.runtime_events
        if not runtime_events:
            self._persist_shadow_sync_cursor(
                deployment_id=deployment_id,
                engine=engine,
                cursor_state={
                    key: value
                    for key, value in (bundle.cursor_state or {}).items()
                    if key in {"owner_runtime_seq", "callback_runtime_seq"}
                },
            )
            self._append_real_qmt_owner_events(
                deployment_id=deployment_id,
                engine=engine,
                since_runtime=since_runtime,
                since_reports=since_reports,
            )
            record = self.store.get_record(deployment_id)
            if record is not None:
                _persist_qmt_runtime_projection_fn(
                    self.store,
                    deployment_id=deployment_id,
                    record=record,
                    spec=engine.spec,
                )
            return
        self.store.append_events(
            _build_shadow_runtime_events_fn(
                deployment_id,
                runtime_events,
            )
        )
        self._persist_shadow_sync_cursor(
            deployment_id=deployment_id,
            engine=engine,
            cursor_state={
                key: value
                for key, value in (bundle.cursor_state or {}).items()
                if key in {"owner_runtime_seq", "callback_runtime_seq"}
            },
        )
        self._append_real_qmt_owner_events(
            deployment_id=deployment_id,
            engine=engine,
            since_runtime=since_runtime,
            since_reports=since_reports,
        )
        record = self.store.get_record(deployment_id)
        if record is not None:
            _persist_qmt_runtime_projection_fn(
                self.store,
                deployment_id=deployment_id,
                record=record,
                spec=engine.spec,
            )

    def _attach_qmt_broker_owners(self, *, deployment_id: str, engine: PaperTradingEngine) -> None:
        for broker in self._iter_qmt_brokers(engine):
            attach = getattr(broker, "attach_deployment", None)
            self._register_qmt_callback_refresh_listener(broker)
            if callable(attach):
                attach(deployment_id)

    @staticmethod
    def _detach_qmt_broker_owners(*, deployment_id: str, engine: PaperTradingEngine) -> None:
        for broker in Scheduler._iter_qmt_brokers(engine):
            detach = getattr(broker, "detach_deployment", None)
            if callable(detach):
                detach(deployment_id)

    @staticmethod
    def _ensure_qmt_broker_supervision(*, engine: PaperTradingEngine) -> None:
        for broker in Scheduler._iter_qmt_brokers(engine):
            ensure = getattr(broker, "ensure_session_supervision", None)
            if callable(ensure):
                ensure()

    def _get_shadow_sync_cursor(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
    ) -> dict[str, Any] | None:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        if shadow_broker is None:
            return None
        cursor_state = self.store.get_broker_sync_cursor(
            deployment_id,
            broker_type=shadow_broker.broker_type,
        )
        if shadow_broker.broker_type != "qmt":
            return cursor_state
        return self._get_qmt_sync_cursor_scope(cursor_state, role="shadow")

    def _persist_shadow_sync_cursor(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        cursor_state: dict[str, Any] | None,
    ) -> None:
        shadow_broker = self._resolve_engine_shadow_broker(engine)
        if shadow_broker is None or cursor_state is None:
            return
        existing = self.store.get_broker_sync_cursor(
            deployment_id,
            broker_type=shadow_broker.broker_type,
        ) or {}
        if shadow_broker.broker_type == "qmt":
            merged = self._merge_qmt_sync_cursor_scope(
                existing,
                role="shadow",
                cursor_state=cursor_state,
            )
        else:
            merged = dict(existing)
            merged.update(cursor_state)
        self.store.upsert_broker_sync_cursor(
            deployment_id,
            broker_type=shadow_broker.broker_type,
            cursor_state=merged,
        )

    def _get_real_qmt_owner_sync_cursor(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
    ) -> dict[str, Any] | None:
        execution_broker = self._resolve_engine_broker(engine)
        if not self._is_qmt_owner_broker(execution_broker):
            return None
        cursor_state = self.store.get_broker_sync_cursor(
            deployment_id,
            broker_type=execution_broker.broker_type,
        )
        return self._get_qmt_sync_cursor_scope(cursor_state, role="real_owner")

    def _persist_real_qmt_owner_sync_cursor(
        self,
        *,
        deployment_id: str,
        engine: PaperTradingEngine,
        cursor_state: dict[str, Any] | None,
    ) -> None:
        execution_broker = self._resolve_engine_broker(engine)
        if not self._is_qmt_owner_broker(execution_broker) or cursor_state is None:
            return
        existing = self.store.get_broker_sync_cursor(
            deployment_id,
            broker_type=execution_broker.broker_type,
        ) or {}
        merged = self._merge_qmt_sync_cursor_scope(
            existing,
            role="real_owner",
            cursor_state=cursor_state,
        )
        self.store.upsert_broker_sync_cursor(
            deployment_id,
            broker_type=execution_broker.broker_type,
            cursor_state=merged,
        )

    @staticmethod
    def _get_qmt_sync_cursor_scope(
        cursor_state: dict[str, Any] | None,
        *,
        role: str,
    ) -> dict[str, Any] | None:
        if not isinstance(cursor_state, dict):
            return None
        scoped = cursor_state.get(role)
        if isinstance(scoped, dict):
            return dict(scoped)
        if role == "shadow":
            return dict(cursor_state)
        return None

    @staticmethod
    def _merge_qmt_sync_cursor_scope(
        existing: dict[str, Any] | None,
        *,
        role: str,
        cursor_state: dict[str, Any],
    ) -> dict[str, Any]:
        existing_dict = dict(existing) if isinstance(existing, dict) else {}
        scoped_shadow = existing_dict.get("shadow")
        scoped_real_owner = existing_dict.get("real_owner")
        if isinstance(scoped_shadow, dict) or isinstance(scoped_real_owner, dict):
            shadow_cursor = dict(scoped_shadow) if isinstance(scoped_shadow, dict) else {}
            real_owner_cursor = (
                dict(scoped_real_owner) if isinstance(scoped_real_owner, dict) else {}
            )
            extras = {
                key: value
                for key, value in existing_dict.items()
                if key not in {"shadow", "real_owner"}
            }
        else:
            shadow_cursor = dict(existing_dict)
            real_owner_cursor = {}
            extras = {}
        if role == "shadow":
            shadow_cursor.update(cursor_state)
        else:
            real_owner_cursor.update(cursor_state)
        if role == "shadow" and not real_owner_cursor and not (
            isinstance(scoped_shadow, dict) or isinstance(scoped_real_owner, dict)
        ):
            return shadow_cursor
        try:
            cursor_version = int(extras.get("v") or 1)
        except (TypeError, ValueError):
            cursor_version = 1
        merged = {key: value for key, value in extras.items() if key != "v"}
        merged["v"] = cursor_version
        merged["shadow"] = shadow_cursor
        if real_owner_cursor:
            merged["real_owner"] = real_owner_cursor
        return merged

    def _restore_full_state(self, engine: PaperTradingEngine, deployment_id: str):
        """Restore engine state from the event ledger when possible.

        Recovery semantics:
        - event ledger is authoritative when it contains snapshot checkpoints
        - legacy deployments without snapshot checkpoints still fall back to
          snapshot-baseline restore + event catch-up
        - snapshot rows remain a compatibility checkpoint and drift detector
        """
        snapshots = self.store.get_all_snapshots(deployment_id)
        events = self.store.get_events(deployment_id)
        engine._recovery_warnings = []
        engine._order_statuses = {}
        legacy_events: list[DeploymentEvent] = []

        ledger = LiveLedger()
        if events:
            replayed_all = ledger.replay(
                events,
                initial_cash=engine.spec.initial_cash,
            )
            if replayed_all.latest_snapshot_date is not None:
                self._restore_from_event_ledger(
                    engine=engine,
                    deployment_id=deployment_id,
                    replayed=replayed_all,
                    snapshots=snapshots,
                )
                return

            baseline_cash, baseline_holdings, replay_events = self._build_replay_baseline(
                engine=engine,
                snapshots=snapshots,
                events=events,
            )
            replayed_legacy = ledger.replay(
                replay_events,
                initial_cash=baseline_cash,
                initial_holdings=baseline_holdings,
            )
            engine._order_statuses = dict(replayed_legacy.order_statuses)
            if not snapshots:
                self._restore_from_legacy_event_replay(
                    engine=engine,
                    deployment_id=deployment_id,
                    replayed=replayed_legacy,
                )
                return
            legacy_events = events

        if not snapshots:
            return

        # Rebuild equity curve and dates from all snapshots
        equity_curve: list[float] = []
        dates: list[date] = []
        all_trades: list[dict] = []
        all_risk_events: list[dict] = []

        for snap in snapshots:
            equity_curve.append(snap["equity"])
            snap_date = snap["snapshot_date"]
            if isinstance(snap_date, str):
                snap_date = date.fromisoformat(snap_date)
            dates.append(snap_date)
            all_trades.extend(snap.get("trades", []))
            all_risk_events.extend(snap.get("risk_events", []))

        # Restore engine state from the latest snapshot
        latest = snapshots[-1]
        engine.cash = latest["cash"]
        engine.holdings = {
            sym: int(qty) for sym, qty in latest.get("holdings", {}).items()
        }
        engine.prev_weights = dict(latest.get("weights", {}))
        engine.prev_returns = dict(latest.get("prev_returns", {}))
        engine.equity_curve = equity_curve
        engine.dates = dates
        engine.trades = all_trades
        engine.risk_events = all_risk_events

        # Rebuild _last_prices from latest snapshot holdings + weights
        # This prevents mark-to-market from estimating holdings at 0 after restart
        if engine.holdings and latest.get("weights"):
            weights = latest["weights"]
            equity = latest["equity"]
            if equity > 0:
                for sym, shares in engine.holdings.items():
                    w = weights.get(sym, 0)
                    if shares > 0 and w > 0:
                        # Reconstruct price: price = (equity * weight) / shares
                        engine._last_prices[sym] = (equity * w) / shares

        # Replay equity curve into risk manager to restore drawdown state machine
        if engine.risk_manager and equity_curve:
            engine.risk_manager.replay_equity(equity_curve)

        if legacy_events:
            latest_snapshot_date = latest["snapshot_date"]
            if isinstance(latest_snapshot_date, str):
                latest_snapshot_date = date.fromisoformat(latest_snapshot_date)
            events_up_to_snapshot = [
                event for event in legacy_events
                if (event_date := self._event_business_date(event)) is not None
                and event_date <= latest_snapshot_date
            ]
            self._check_legacy_snapshot_event_consistency(
                engine=engine,
                deployment_id=deployment_id,
                snapshots=snapshots,
                latest_snapshot=latest,
                events=events_up_to_snapshot,
            )
            post_snapshot_events = [
                event for event in legacy_events
                if (event_date := self._event_business_date(event)) is not None
                and event_date > latest_snapshot_date
            ]
            self._apply_legacy_post_snapshot_events(
                engine=engine,
                deployment_id=deployment_id,
                post_snapshot_events=post_snapshot_events,
            )

    def _restore_from_event_ledger(
        self,
        *,
        engine: PaperTradingEngine,
        deployment_id: str,
        replayed,
        snapshots: list[dict],
    ) -> None:
        """Restore directly from the append-only event ledger.

        Snapshot rows become compatibility checkpoints and drift detectors,
        not the primary truth source.
        """
        engine.cash = replayed.cash
        engine.holdings = dict(replayed.holdings)
        engine._order_statuses = dict(replayed.order_statuses)
        engine.prev_returns = dict(replayed.latest_prev_returns)
        engine.equity_curve = list(replayed.equity_curve)
        engine.dates = list(replayed.dates)
        engine.trades = list(replayed.trades)
        engine.risk_events = list(replayed.risk_events)
        engine._last_prices = dict(replayed.last_prices)
        if engine._last_prices:
            event_equity = engine._mark_to_market(engine._last_prices)
            engine.prev_weights = engine._compute_weights(engine._last_prices, event_equity)
        else:
            engine.prev_weights = dict(replayed.latest_weights)

        if engine.risk_manager and engine.equity_curve:
            engine.risk_manager.replay_equity(engine.equity_curve)

        if snapshots:
            latest_snapshot = snapshots[-1]
            snap_holdings = {
                sym: int(qty) for sym, qty in latest_snapshot.get("holdings", {}).items()
            }
            snap_cash = float(latest_snapshot.get("cash", 0.0))
            if snap_holdings != replayed.holdings or abs(snap_cash - replayed.cash) > 0.01:
                warning = (
                    f"Deployment {deployment_id}: snapshot row drift detected during event-first restore. "
                    f"snapshot_cash={snap_cash:.2f}, ledger_cash={replayed.cash:.2f}, "
                    f"snapshot_holdings={snap_holdings}, ledger_holdings={replayed.holdings}. "
                    "Event ledger remains authoritative."
                )
                engine._recovery_warnings.append(warning)
                logger.warning(warning)
        else:
            info = (
                f"Deployment {deployment_id}: restored from event ledger checkpoints "
                f"without snapshot rows. Latest checkpoint={replayed.latest_snapshot_date}."
            )
            engine._recovery_warnings.append(info)
            logger.warning(info)

    def _check_legacy_snapshot_event_consistency(
        self,
        *,
        engine: PaperTradingEngine,
        deployment_id: str,
        snapshots: list[dict],
        latest_snapshot: dict,
        events: list[DeploymentEvent],
    ) -> None:
        baseline_cash, baseline_holdings, replay_events = self._build_replay_baseline(
            engine=engine,
            snapshots=snapshots,
            events=events,
        )
        if not replay_events:
            return

        replayed = LiveLedger().replay(
            replay_events,
            initial_cash=baseline_cash,
            initial_holdings=baseline_holdings,
        )
        snap_holdings = {
            sym: int(qty) for sym, qty in latest_snapshot.get("holdings", {}).items()
        }
        snap_cash = float(latest_snapshot.get("cash", 0.0))
        if replayed.holdings == snap_holdings and abs(replayed.cash - snap_cash) <= 0.01:
            return

        warning = (
            f"Deployment {deployment_id}: snapshot/event drift detected during legacy restore. "
            f"snapshot_cash={snap_cash:.2f}, replay_cash={replayed.cash:.2f}, "
            f"snapshot_holdings={snap_holdings}, replay_holdings={replayed.holdings}. "
            "Snapshot rows remain authoritative until snapshot checkpoints exist in the event log."
        )
        engine._recovery_warnings.append(warning)
        logger.warning(warning)

    def _apply_legacy_post_snapshot_events(
        self,
        *,
        engine: PaperTradingEngine,
        deployment_id: str,
        post_snapshot_events: list[DeploymentEvent],
    ) -> None:
        if not post_snapshot_events:
            return

        replayed = LiveLedger().replay(
            post_snapshot_events,
            initial_cash=engine.cash,
            initial_holdings=engine.holdings,
        )
        engine.cash = replayed.cash
        engine.holdings = dict(replayed.holdings)
        engine._order_statuses.update(replayed.order_statuses)
        engine.trades.extend(replayed.trades)
        engine.risk_events.extend(replayed.risk_events)
        engine._last_prices.update(replayed.last_prices)
        if engine._last_prices:
            post_equity = engine._mark_to_market(engine._last_prices)
            engine.prev_weights = engine._compute_weights(engine._last_prices, post_equity)

        info = (
            f"Deployment {deployment_id}: applied {len(post_snapshot_events)} legacy post-snapshot "
            "events during restore."
        )
        engine._recovery_warnings.append(info)
        logger.info(info)

    def _restore_from_legacy_event_replay(
        self,
        *,
        engine: PaperTradingEngine,
        deployment_id: str,
        replayed,
    ) -> None:
        engine.cash = replayed.cash
        engine.holdings = dict(replayed.holdings)
        engine.prev_returns = dict(replayed.latest_prev_returns)
        engine.equity_curve = []
        engine.dates = []
        engine.trades = list(replayed.trades)
        engine.risk_events = list(replayed.risk_events)
        engine._last_prices = dict(replayed.last_prices)
        if engine._last_prices:
            event_equity = engine._mark_to_market(engine._last_prices)
            engine.prev_weights = engine._compute_weights(
                engine._last_prices,
                event_equity,
            )
        else:
            engine.prev_weights = dict(replayed.latest_weights)
        warning = (
            f"Deployment {deployment_id}: restored from legacy events without snapshot checkpoints. "
            "Account state and order statuses were replayed, but equity curve requires "
            "snapshot rows or snapshot_saved events."
        )
        engine._recovery_warnings.append(warning)
        logger.warning(warning)

    def _build_replay_baseline(
        self,
        *,
        engine: PaperTradingEngine,
        snapshots: list[dict],
        events: list[DeploymentEvent],
    ) -> tuple[float, dict[str, int], list[DeploymentEvent]]:
        """Build the replay baseline from the latest snapshot before the first event day.

        Legacy deployments may only start emitting OMS events mid-stream.
        In that case, replay should start from the latest pre-event snapshot,
        not from the deployment's original initial cash, otherwise every
        restored deployment would drift forever.
        """
        first_event_date = min(
            (
                event_date
                for event in events
                if (event_date := self._event_business_date(event)) is not None
            ),
            default=None,
        )
        if first_event_date is None:
            return engine.spec.initial_cash, {}, events

        baseline_snapshot = None
        for snapshot in snapshots:
            snapshot_date = snapshot.get("snapshot_date")
            if isinstance(snapshot_date, str):
                snapshot_date = date.fromisoformat(snapshot_date)
            if isinstance(snapshot_date, date) and snapshot_date < first_event_date:
                baseline_snapshot = snapshot
            elif isinstance(snapshot_date, date) and snapshot_date >= first_event_date:
                break

        replay_events = [
            event
            for event in events
            if (event_date := self._event_business_date(event)) is not None
            and event_date >= first_event_date
        ]
        if baseline_snapshot is None:
            return engine.spec.initial_cash, {}, replay_events

        baseline_cash = float(baseline_snapshot.get("cash", engine.spec.initial_cash))
        baseline_holdings = {
            sym: int(qty)
            for sym, qty in baseline_snapshot.get("holdings", {}).items()
        }
        return baseline_cash, baseline_holdings, replay_events

    @staticmethod
    def _event_business_date(event: DeploymentEvent) -> date | None:
        payload = event.payload or {}
        raw_date = payload.get("business_date") or payload.get("snapshot_date")
        if isinstance(raw_date, date):
            return raw_date
        if isinstance(raw_date, str):
            return date.fromisoformat(raw_date)
        parts = (event.client_order_id or "").split(":")
        if len(parts) >= 2:
            try:
                return date.fromisoformat(parts[1])
            except ValueError:
                pass
        if isinstance(event.event_ts, datetime):
            return event.event_ts.date()
        return None

    def _instantiate(self, spec: DeploymentSpec) -> tuple:
        """Create strategy + optimizer + risk_manager from DeploymentSpec.

        Uses the same pattern as _create_strategy in ez/api/routes/portfolio.py
        to handle TopNRotation/MultiFactorRotation/StrategyEnsemble.

        Returns (strategy, optimizer | None, risk_manager | None).
        """
        # -- Strategy --
        strategy = self._create_strategy_from_spec(spec)

        # -- Optimizer --
        optimizer = None
        if spec.optimizer and spec.optimizer != "none":
            optimizer = self._create_optimizer(spec)

        # -- Risk Manager --
        risk_manager = None
        if spec.risk_control:
            risk_manager = self._create_risk_manager(spec)

        return strategy, optimizer, risk_manager

    def _create_strategy_from_spec(self, spec: DeploymentSpec):
        """Instantiate a PortfolioStrategy from spec. Mirrors _create_strategy()."""
        from ez.portfolio.portfolio_strategy import (
            PortfolioStrategy, TopNRotation, MultiFactorRotation,
        )

        name = spec.strategy_name
        params = dict(spec.strategy_params)

        if name == "TopNRotation":
            factor_name = params.pop("factor", "momentum_rank_20")
            factor = self._resolve_factor(factor_name)
            top_n = params.pop("top_n", 10)
            return TopNRotation(factor=factor, top_n=top_n, **params)

        elif name == "MultiFactorRotation":
            factor_names = params.pop("factors", ["momentum_rank_20"])
            factors = [self._resolve_factor(fn) for fn in factor_names]
            top_n = params.pop("top_n", 10)
            return MultiFactorRotation(factors=factors, top_n=top_n, **params)

        elif name == "StrategyEnsemble":
            from ez.portfolio.ensemble import StrategyEnsemble

            sub_defs = params.pop("sub_strategies", [])
            mode = params.pop("mode", "equal")
            ensemble_weights = params.pop("ensemble_weights", None)
            warmup_rebalances = params.pop("warmup_rebalances", 8)
            correlation_threshold = params.pop("correlation_threshold", 0.9)

            sub_strategies = []
            for sub_def in sub_defs:
                sub_name = sub_def.get("name", "")
                sub_params = dict(sub_def.get("params", {}))
                # Create a minimal sub-spec to reuse this method
                sub_spec_like = type("_SubSpec", (), {
                    "strategy_name": sub_name,
                    "strategy_params": sub_params,
                })()
                sub_strat = self._create_strategy_from_spec(sub_spec_like)
                sub_strategies.append(sub_strat)

            return StrategyEnsemble(
                strategies=sub_strategies,
                mode=mode,
                ensemble_weights=ensemble_weights,
                warmup_rebalances=warmup_rebalances,
                correlation_threshold=correlation_threshold,
            )

        else:
            # Fallback: lookup in registry
            registry = PortfolioStrategy.get_registry()
            if name in registry:
                cls = registry[name]
                return cls(**params)
            # Try resolve_class for key-based lookup
            cls = PortfolioStrategy.resolve_class(name)
            return cls(**params)

    def _resolve_factor(self, factor_name: str):
        """Resolve a cross-sectional factor by name."""
        from ez.portfolio.cross_factor import (
            CrossSectionalFactor, MomentumRank, VolumeRank,
            ReverseVolatilityRank,
        )

        builtin_map = {
            "momentum_rank_20": lambda: MomentumRank(period=20),
            "momentum_rank_60": lambda: MomentumRank(period=60),
            "volume_rank": VolumeRank,
            "reverse_volatility_rank": ReverseVolatilityRank,
        }
        if factor_name in builtin_map:
            return builtin_map[factor_name]()

        # Try registry
        registry = CrossSectionalFactor.get_registry()
        if factor_name in registry:
            cls = registry[factor_name]
            return cls()

        # Try resolve_class
        cls = CrossSectionalFactor.resolve_class(factor_name)
        return cls()

    def _create_optimizer(self, spec: DeploymentSpec):
        """Create a PortfolioOptimizer from spec."""
        from ez.portfolio.optimizer import (
            MeanVarianceOptimizer, MinVarianceOptimizer,
            RiskParityOptimizer, OptimizationConstraints,
        )

        opt_params = spec.optimizer_params
        constraints = OptimizationConstraints(
            max_weight=opt_params.get("max_weight", 0.10),
            max_industry_weight=opt_params.get("max_industry_weight", 0.30),
        )
        cov_lookback = opt_params.get("cov_lookback", 60)

        if spec.optimizer == "mean_variance":
            risk_aversion = opt_params.get("risk_aversion", 1.0)
            return MeanVarianceOptimizer(
                risk_aversion=risk_aversion,
                constraints=constraints,
                cov_lookback=cov_lookback,
            )
        elif spec.optimizer == "min_variance":
            return MinVarianceOptimizer(
                constraints=constraints,
                cov_lookback=cov_lookback,
            )
        else:
            return RiskParityOptimizer(
                constraints=constraints,
                cov_lookback=cov_lookback,
            )

    def _create_risk_manager(self, spec: DeploymentSpec):
        """Create a RiskManager from spec."""
        from ez.portfolio.risk_manager import RiskManager, RiskConfig

        risk_params = spec.risk_params
        return RiskManager(RiskConfig(
            max_drawdown_threshold=risk_params.get("max_drawdown_threshold", 0.20),
            drawdown_reduce_ratio=risk_params.get("drawdown_reduce_ratio", 0.50),
            drawdown_recovery_ratio=risk_params.get("drawdown_recovery_ratio", 0.10),
            max_turnover=risk_params.get("max_turnover", 0.50),
        ))
