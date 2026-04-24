"""Broker adapter contracts for live execution.

This keeps OMS/scheduler code pointed at a stable broker interface so
paper execution and future real-broker adapters share one contract.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from ez.live.events import Order
from ez.portfolio.execution import CostModel


class BrokerCapability(StrEnum):
    TARGET_WEIGHT_EXECUTION = "target_weight_execution"
    READ_ACCOUNT_STATE = "read_account_state"
    STREAM_EXECUTION_REPORTS = "stream_execution_reports"
    CANCEL_ORDER = "cancel_order"
    SHADOW_MODE = "shadow_mode"


@dataclass(slots=True)
class BrokerFillReport:
    order_id: str
    client_order_id: str
    deployment_id: str
    symbol: str
    side: str
    shares: int
    price: float
    amount: float
    commission: float
    stamp_tax: float
    cost: float
    business_date: date
    requested_shares: int = 0
    remaining_shares: int = 0
    slice_index: int = 1
    total_slices: int = 1

    def to_trade_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "shares": self.shares,
            "price": self.price,
            "cost": self.cost,
            "amount": self.amount,
        }


@dataclass(slots=True)
class BrokerOrderReport:
    order_id: str
    client_order_id: str
    deployment_id: str
    symbol: str
    side: str
    requested_shares: int
    filled_shares: int
    remaining_shares: int
    status: str
    price: float
    amount: float
    commission: float
    stamp_tax: float
    cost: float
    business_date: date
    broker_order_id: str = ""
    broker_submit_id: str = ""


@dataclass(slots=True)
class BrokerExecutionResult:
    fills: list[BrokerFillReport]
    order_reports: list[BrokerOrderReport]
    holdings: dict[str, int]
    cash: float
    trade_volume: float


@dataclass(slots=True)
class BrokerExecutionReport:
    report_id: str
    broker_type: str
    as_of: datetime
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    status: str
    filled_shares: int
    remaining_shares: int
    avg_price: float
    message: str = ""
    raw_payload: dict[str, Any] | None = None
    account_id: str = ""


@dataclass(slots=True)
class BrokerRuntimeEvent:
    event_id: str
    broker_type: str
    as_of: datetime
    event_kind: str
    payload: dict[str, Any]


@dataclass(slots=True)
class BrokerAccountSnapshot:
    broker_type: str
    as_of: datetime
    cash: float
    total_asset: float
    positions: dict[str, int]
    open_orders: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    account_id: str = ""


@dataclass(slots=True)
class BrokerSyncBundle:
    snapshot: BrokerAccountSnapshot | None
    execution_reports: list[BrokerExecutionReport]
    runtime_events: list[BrokerRuntimeEvent]
    cursor_state: dict[str, Any] | None = None


class BrokerAdapter(ABC):
    """Stable execution contract for paper and future real brokers."""

    broker_type = "unknown"

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[BrokerCapability]:
        """Declared broker capabilities for routing and safety checks."""

    @abstractmethod
    def execute_target_weights(
        self,
        *,
        business_date: date,
        target_weights: dict[str, float],
        holdings: dict[str, int],
        equity: float,
        cash: float,
        prices: dict[str, float],
        raw_close_today: dict[str, float],
        prev_raw_close: dict[str, float],
        has_bar_today: set[str],
        cost_model: CostModel,
        lot_size: int,
        limit_pct: float,
        t_plus_1: bool,
        requested_orders: list[Order] | None = None,
        execution_slices: int = 1,
    ) -> BrokerExecutionResult:
        """Execute a target-weight rebalance request."""

    def snapshot_account_state(self) -> BrokerAccountSnapshot | None:
        """Optional read-only broker snapshot used by future shadow/reconcile flows."""
        return None

    def list_execution_reports(
        self,
        *,
        since: datetime | None = None,
    ) -> list[BrokerExecutionReport]:
        """Optional real-broker execution reports used by shadow/reconcile flows."""
        return []

    def list_runtime_events(
        self,
        *,
        since: datetime | None = None,
    ) -> list[BrokerRuntimeEvent]:
        """Optional broker session/runtime events (account status, async responses, etc.)."""
        return []

    def collect_sync_state(
        self,
        *,
        since_reports: datetime | None = None,
        since_runtime: datetime | None = None,
        cursor_state: dict[str, Any] | None = None,
    ) -> BrokerSyncBundle:
        """Optional efficient bundle fetch for broker snapshot + reports + runtime events.

        Adapters that can share one broker round-trip across account state,
        execution reports, and runtime events should override this. The
        default keeps correctness by delegating to the existing point reads.
        """
        return BrokerSyncBundle(
            snapshot=self.snapshot_account_state(),
            execution_reports=self.list_execution_reports(since=since_reports),
            runtime_events=self.list_runtime_events(since=since_runtime),
            cursor_state=dict(cursor_state) if isinstance(cursor_state, dict) else None,
        )

    def cancel_order(self, client_order_id: str, *, symbol: str = "") -> bool:
        """Optional cancellation path for future real-broker adapters."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support order cancellation"
        )
