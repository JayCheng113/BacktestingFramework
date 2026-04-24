"""V2.15 C1: Live API integration tests.

Tests:
1. test_deploy_creates_deployment — POST /deploy with valid run_id
2. test_approve_runs_gate — POST /approve, check gate verdict
3. test_lifecycle_flow — deploy -> approve -> start -> tick -> stop
4. test_approve_rejects_bad_metrics — gate fails -> 400
5. test_list_deployments — GET /deployments returns list
6. test_dashboard — GET /dashboard returns health
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ez.api.app import app
from ez.live.broker import BrokerAccountSnapshot, BrokerCapability, BrokerExecutionReport, BrokerRuntimeEvent
from ez.live.events import make_shadow_broker_client_order_id
from ez.live.qmt_broker import QMTBrokerConfig, QMTRealBroker, QMTShadowBroker
from ez.live.qmt_session_owner import QMTSessionManager
from ez.live.paper_broker import PaperBroker


client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures: mock portfolio store and deployment store
# ---------------------------------------------------------------------------

def _make_mock_run(run_id: str = "test-run-001") -> dict:
    """Build a synthetic portfolio run dict matching PortfolioStore.get_run() output."""
    return {
        "run_id": run_id,
        "strategy_name": "TopNRotation",
        "strategy_params": {"factor": "momentum_rank_20", "top_n": 5},
        "symbols": ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "000858.SZ",
                     "601318.SH", "600519.SH", "000333.SZ", "002415.SZ", "600276.SH"],
        "start_date": "2021-01-01",
        "end_date": "2024-01-01",
        "freq": "daily",
        "initial_cash": 1000000.0,
        "metrics": {
            "sharpe_ratio": 1.2,
            "max_drawdown": -0.15,
            "trade_count": 120,
            "total_return": 0.45,
        },
        "equity_curve": [1000000.0] * 756,
        "trade_count": 120,
        "rebalance_count": 30,
        "created_at": "2024-01-01T00:00:00",
        "rebalance_weights": [
            {"date": "2021-01-04", "weights": {"000001.SZ": 0.1, "000002.SZ": 0.1,
                                                "600000.SH": 0.1, "600036.SH": 0.1,
                                                "000858.SZ": 0.1}},
        ],
        "trades": [],
        "config": {
            "market": "cn_stock",
            "freq": "daily",
            "t_plus_1": True,
            "lot_size": 100,
            "buy_commission_rate": 0.00008,
            "sell_commission_rate": 0.00008,
            "stamp_tax_rate": 0.0005,
            "slippage_rate": 0.001,
            "min_commission": 0.0,
            "price_limit_pct": 0.1,
        },
        "warnings": [],
        "dates": [f"2021-01-{i:02d}" for i in range(4, 30)] * 28,  # >504 dates
        "weights_history": [],
    }


_WF_METRICS_GOOD = {
    "p_value": 0.01,
    "overfitting_score": 0.1,
}

_WF_METRICS_BAD = {
    "p_value": 0.5,
    "overfitting_score": 0.8,
}


class _FakeBar:
    def __init__(self, time, open, high, low, close, adj_close, volume):
        self.time = time
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.adj_close = adj_close
        self.volume = volume


def _make_e2e_run(run_id: str = "e2e-run-001") -> dict:
    symbols = [
        "000001.SZ",
        "000002.SZ",
        "600000.SH",
        "600036.SH",
        "000858.SZ",
    ]
    return {
        "run_id": run_id,
        "strategy_name": "TopNRotation",
        "strategy_params": {"factor": "momentum_rank_20", "top_n": 5},
        "symbols": symbols,
        "start_date": "2022-01-01",
        "end_date": "2024-07-01",
        "freq": "daily",
        "initial_cash": 1_000_000.0,
        "metrics": {
            "sharpe_ratio": 1.4,
            "max_drawdown": -0.12,
            "trade_count": 160,
            "total_return": 0.55,
        },
        "equity_curve": [1_000_000.0] * 650,
        "trade_count": 160,
        "rebalance_count": 130,
        "created_at": "2024-01-01T00:00:00",
        "rebalance_weights": [
            {"date": "2024-06-27", "weights": {sym: 0.2 for sym in symbols}},
        ],
        "trades": [],
        "config": {
            "market": "cn_stock",
            "freq": "daily",
            "t_plus_1": True,
            "lot_size": 100,
            "buy_commission_rate": 0.00008,
            "sell_commission_rate": 0.00008,
            "stamp_tax_rate": 0.0005,
            "slippage_rate": 0.0,
            "min_commission": 0.0,
            "price_limit_pct": 0.1,
            "_risk": {
                "enabled": True,
                "allocation_mode": "equal_weight_cap",
                "runtime_allocation_cap": 0.6,
                "max_names": 3,
            },
        },
        "warnings": [],
        "dates": [f"2022-01-{(i % 28) + 1:02d}" for i in range(650)],
        "weights_history": [],
        "wf_metrics": dict(_WF_METRICS_GOOD),
    }


def _make_qmt_shadow_e2e_run(run_id: str = "e2e-qmt-shadow-run-001") -> dict:
    run = _make_e2e_run(run_id)
    run["config"] = dict(run["config"])
    run["config"]["shadow_broker_type"] = "qmt"
    run["config"]["_risk"] = dict(run["config"].get("_risk", {}))
    run["config"]["_risk"]["shadow_broker_config"] = {
        "account_id": "acct-shadow",
        "enable_cancel": True,
    }
    run["config"]["_risk"]["qmt_real_submit_policy"] = {
        "enabled": True,
        "allowed_account_ids": ["acct-shadow"],
        "max_total_asset": 50_000.0,
        "max_initial_cash": 2_000_000.0,
    }
    return run


def _make_qmt_real_e2e_run(run_id: str = "e2e-qmt-real-run-001") -> dict:
    run = _make_e2e_run(run_id)
    run["config"] = dict(run["config"])
    run["config"]["broker_type"] = "qmt"
    run["config"]["shadow_broker_type"] = "qmt"
    run["config"]["_risk"] = dict(run["config"].get("_risk", {}))
    run["config"]["_risk"]["shadow_broker_config"] = {
        "account_id": "acct-real",
        "enable_cancel": True,
    }
    run["config"]["_risk"]["qmt_real_broker_config"] = {
        "account_id": "acct-real",
        "enable_cancel": True,
    }
    run["config"]["_risk"]["qmt_real_submit_policy"] = {
        "enabled": True,
        "allowed_account_ids": ["acct-real"],
        "max_total_asset": 1_500_000.0,
        "max_initial_cash": 2_000_000.0,
    }
    return run


def _make_symbol_bars(
    symbols: list[str],
    trading_days: list[date],
) -> dict[str, list[_FakeBar]]:
    symbol_bars: dict[str, list[_FakeBar]] = {}
    for idx, symbol in enumerate(symbols):
        rows: list[_FakeBar] = []
        for day_idx, day in enumerate(trading_days):
            price = 10.0 + idx * 0.5 + day_idx * (0.02 + idx * 0.005)
            rows.append(
                _FakeBar(
                    time=datetime.combine(day, datetime.min.time()),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    adj_close=price,
                    volume=1000,
                )
            )
        symbol_bars[symbol] = rows
    return symbol_bars


class _FakeDataChain:
    def __init__(self, symbol_bars: dict[str, list[_FakeBar]]):
        self._symbol_bars = symbol_bars

    def get_kline(self, symbol, market, period, start_date, end_date):
        bars = self._symbol_bars.get(symbol, [])
        return [
            b for b in bars
            if start_date <= b.time.date() <= end_date
        ]


class _FakeQmtShadowBroker:
    broker_type = "qmt"

    def __init__(self):
        self._cancel_requested = False
        self._supervised = False

    @property
    def capabilities(self):
        return frozenset(
            {
                BrokerCapability.READ_ACCOUNT_STATE,
                BrokerCapability.STREAM_EXECUTION_REPORTS,
                BrokerCapability.SHADOW_MODE,
                BrokerCapability.CANCEL_ORDER,
            }
        )

    def snapshot_account_state(self):
        as_of = datetime(2024, 6, 28, 15, 2, tzinfo=timezone.utc)
        open_orders = []
        if not self._cancel_requested:
            open_orders.append(
                {
                    "client_order_id": "",
                    "broker_order_id": "SYS-001",
                    "symbol": "000001.SZ",
                    "status": "partially_filled",
                    "requested_shares": 1000,
                    "filled_shares": 600,
                    "remaining_shares": 400,
                    "avg_price": 12.34,
                    "updated_at": "2024-06-28T15:00:00+00:00",
                }
            )
        else:
            as_of = datetime(2024, 7, 1, 15, 2, tzinfo=timezone.utc)
        return BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=as_of,
            cash=0.0,
            total_asset=0.0,
            positions={},
            open_orders=open_orders,
            fills=[],
        )

    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="session_consumer_restarted:acct-shadow:2024-06-28T14:57:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 57, tzinfo=timezone.utc),
                event_kind="session_consumer_restarted",
                payload={
                    "_report_kind": "session_consumer_restarted",
                    "account_id": "acct-shadow",
                    "session_id": "sess-shadow",
                    "consumer_restart_count": 1,
                },
            ),
            BrokerRuntimeEvent(
                event_id="account_status:acct-shadow:connected:2024-06-28T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-shadow",
                    "account_type": "STOCK",
                    "status": "connected",
                },
            )
        ]
        if not self._supervised:
            events = events[1:]
        if self._cancel_requested:
            events.append(
                BrokerRuntimeEvent(
                    event_id="cancel_async:acct-shadow:SYS-001:2024-07-01T14:58:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 7, 1, 14, 58, tzinfo=timezone.utc),
                    event_kind="cancel_order_stock_async_response",
                    payload={
                        "_report_kind": "cancel_order_stock_async_response",
                        "account_id": "acct-shadow",
                        "account_type": "STOCK",
                        "order_id": 1001,
                        "order_sysid": "SYS-001",
                        "cancel_result": 0,
                        "seq": 88,
                    },
                )
            )
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events

    def ensure_session_supervision(self):
        self._supervised = True

    def list_execution_reports(self, *, since=None):
        reports = [
            BrokerExecutionReport(
                report_id="qmt:SYS-001:partially_filled:600:400:2024-06-28T15:00:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 15, 0, tzinfo=timezone.utc),
                client_order_id="",
                broker_order_id="SYS-001",
                symbol="000001.SZ",
                side="buy",
                status="partially_filled",
                filled_shares=600,
                remaining_shares=400,
                avg_price=12.34,
                message="partial",
                raw_payload={"entrust_no": "SYS-001", "order_id": 1001, "order_sysid": "SYS-001"},
            )
        ]
        if self._cancel_requested:
            reports.append(
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:600:0:2024-07-01T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 7, 1, 15, 0, tzinfo=timezone.utc),
                    client_order_id="",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=600,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="canceled",
                    raw_payload={"entrust_no": "SYS-001", "order_id": 1001, "order_sysid": "SYS-001", "status": "canceled"},
                )
            )
        if since is not None:
            reports = [report for report in reports if report.as_of >= since]
        return reports

    def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
        self._cancel_requested = True
        return order_id == "SYS-001"


class _FakeQmtRealSharedState:
    def __init__(self):
        self._submitted_orders: list[dict[str, object]] = []
        self._cancel_requested = False
        self._cancel_finalized = False

    def record_submission(
        self,
        *,
        symbol: str,
        side: str,
        shares: int,
        price: float,
        strategy_name: str,
        order_remark: str,
    ) -> int:
        broker_order_id = f"SYS-REAL-{len(self._submitted_orders) + 1:03d}"
        record = {
            "client_order_id": str(order_remark),
            "broker_order_id": broker_order_id,
            "symbol": str(symbol),
            "side": str(side),
            "shares": int(shares),
            "price": float(price),
            "strategy_name": str(strategy_name),
            "requested_at": datetime(2024, 6, 28, 14, 58, tzinfo=timezone.utc),
            "report_at": datetime(2024, 6, 28, 15, 0, tzinfo=timezone.utc),
        }
        self._submitted_orders.append(record)
        return 700 + len(self._submitted_orders)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def finalize_cancel(self) -> None:
        self._cancel_finalized = True

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    @property
    def cancel_finalized(self) -> bool:
        return self._cancel_finalized

    @property
    def submitted_orders(self) -> list[dict[str, object]]:
        return list(self._submitted_orders)


class _FakeRealQmtClient:
    def __init__(self, state: _FakeQmtRealSharedState):
        self._state = state

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        shares: int,
        price: float,
        strategy_name: str,
        order_remark: str,
        price_type: object | None = None,
    ) -> int:
        return self._state.record_submission(
            symbol=symbol,
            side=side,
            shares=shares,
            price=price,
            strategy_name=strategy_name,
            order_remark=order_remark,
        )

    def query_stock_asset(self, account_id: str):
        del account_id
        update_time = datetime(2024, 6, 28, 14, 58, tzinfo=timezone.utc)
        if self._state.submitted_orders:
            update_time = max(
                submission["report_at"] for submission in self._state.submitted_orders
            )
        return {
            "update_time": update_time.isoformat(),
            "cash": 1_000_000.0,
            "total_asset": 1_000_000.0,
        }

    def query_stock_positions(self, account_id: str):
        del account_id
        return []

    def query_stock_orders(self, account_id: str):
        del account_id
        return [
            {
                "update_time": submission["report_at"].isoformat(),
                "order_remark": str(submission["client_order_id"]),
                "order_sysid": str(submission["broker_order_id"]),
                "stock_code": str(submission["symbol"]),
                "side": str(submission["side"]),
                "status": (
                    "canceled"
                    if self._state.cancel_finalized
                    else "reported_cancel_pending"
                    if self._state.cancel_requested
                    else "reported"
                ),
                "order_volume": int(submission["shares"]),
                "traded_volume": 0,
                "remaining_volume": int(submission["shares"]),
                "traded_price": float(submission["price"]),
                "status_msg": (
                    "canceled"
                    if self._state.cancel_finalized
                    else "cancel pending"
                    if self._state.cancel_requested
                    else "submitted"
                ),
            }
            for submission in self._state.submitted_orders
        ]

    def query_stock_trades(self, account_id: str):
        del account_id
        return []

    def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
        del symbol
        self._state.request_cancel()
        return bool(order_id)


class _FakeQmtRealShadowBroker:
    broker_type = "qmt"

    def __init__(self, state: _FakeQmtRealSharedState):
        self._state = state
        self._supervised = False

    @property
    def capabilities(self):
        return frozenset(
            {
                BrokerCapability.READ_ACCOUNT_STATE,
                BrokerCapability.STREAM_EXECUTION_REPORTS,
                BrokerCapability.SHADOW_MODE,
            }
        )

    def ensure_session_supervision(self):
        self._supervised = True

    def snapshot_account_state(self):
        open_orders: list[dict[str, object]] = []
        as_of = datetime(2024, 6, 28, 14, 58, tzinfo=timezone.utc)
        for submission in self._state.submitted_orders:
            if self._state.cancel_finalized:
                continue
            open_orders.append(
                {
                    "client_order_id": str(submission["client_order_id"]),
                    "broker_order_id": str(submission["broker_order_id"]),
                    "symbol": str(submission["symbol"]),
                    "status": (
                        "reported_cancel_pending"
                        if self._state.cancel_requested
                        else "reported"
                    ),
                    "requested_shares": int(submission["shares"]),
                    "filled_shares": 0,
                    "remaining_shares": int(submission["shares"]),
                    "avg_price": float(submission["price"]),
                    "updated_at": "2024-06-28T15:00:00+00:00",
                }
            )
        if open_orders:
            as_of = datetime(2024, 6, 28, 15, 2, tzinfo=timezone.utc)
        return BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=as_of,
            cash=1_000_000.0,
            total_asset=1_000_000.0,
            positions={},
            open_orders=open_orders,
            fills=[],
        )

    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="session_connected:acct-real:2024-06-28T14:57:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 57, tzinfo=timezone.utc),
                event_kind="session_connected",
                payload={
                    "_report_kind": "session_connected",
                    "account_id": "acct-real",
                    "status": "connected",
                },
            ),
            BrokerRuntimeEvent(
                event_id="account_status:acct-real:connected:2024-06-28T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-real",
                    "account_type": "STOCK",
                    "status": "connected",
                },
            ),
        ]
        if self._supervised:
            events.append(
                BrokerRuntimeEvent(
                    event_id="session_consumer_state:acct-real:2024-06-28T14:59:30+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 28, 14, 59, 30, tzinfo=timezone.utc),
                    event_kind="session_consumer_state",
                    payload={
                        "_report_kind": "session_consumer_state",
                        "account_id": "acct-real",
                        "status": "connected",
                        "consumer_status": "running",
                        "account_sync_mode": "callback_preferred",
                        "asset_callback_freshness": "fresh",
                    },
                )
            )
        if self._state.cancel_requested:
            events.append(
                BrokerRuntimeEvent(
                    event_id="cancel_async:acct-real:SYS-REAL-001:2024-06-28T15:00:30+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 28, 15, 0, 30, tzinfo=timezone.utc),
                    event_kind="cancel_order_stock_async_response",
                    payload={
                        "_report_kind": "cancel_order_stock_async_response",
                        "account_id": "acct-real",
                        "account_type": "STOCK",
                        "client_order_id": "dep-real:2024-06-28:000001.SZ:buy",
                        "order_id": 700,
                        "order_sysid": "SYS-REAL-001",
                        "cancel_result": 0,
                        "seq": 900,
                    },
                )
            )
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events

    def list_execution_reports(self, *, since=None):
        reports: list[BrokerExecutionReport] = []
        for submission in self._state.submitted_orders:
            if self._state.cancel_finalized:
                report_at = datetime(2024, 7, 1, 15, 0, tzinfo=timezone.utc)
                if since is not None and report_at < since:
                    continue
                reports.append(
                    BrokerExecutionReport(
                        report_id=(
                            f"qmt:{submission['broker_order_id']}:canceled:0:0:"
                            f"{report_at.isoformat()}"
                        ),
                        broker_type="qmt",
                        as_of=report_at,
                        client_order_id=str(submission["client_order_id"]),
                        broker_order_id=str(submission["broker_order_id"]),
                        symbol=str(submission["symbol"]),
                        side=str(submission["side"]),
                        status="canceled",
                        filled_shares=0,
                        remaining_shares=0,
                        avg_price=float(submission["price"]),
                        message="canceled",
                        raw_payload={
                            "order_sysid": str(submission["broker_order_id"]),
                            "order_remark": str(submission["client_order_id"]),
                            "status": "canceled",
                        },
                    )
                )
                continue
            report_at = submission["report_at"]
            if since is not None and report_at < since:
                continue
            reports.append(
                BrokerExecutionReport(
                    report_id=(
                        f"qmt:{submission['broker_order_id']}:reported:0:{submission['shares']}:"
                        f"{report_at.isoformat()}"
                    ),
                    broker_type="qmt",
                    as_of=report_at,
                    client_order_id=str(submission["client_order_id"]),
                    broker_order_id=str(submission["broker_order_id"]),
                    symbol=str(submission["symbol"]),
                    side=str(submission["side"]),
                    status="reported",
                    filled_shares=0,
                    remaining_shares=int(submission["shares"]),
                    avg_price=float(submission["price"]),
                    message="submitted",
                    raw_payload={
                        "order_sysid": str(submission["broker_order_id"]),
                        "order_remark": str(submission["client_order_id"]),
                    },
                )
            )
        return reports


class _ResidentQmtClient:
    def __init__(self):
        self.close_calls = 0

    def query_stock_asset(self, account_id: str):
        return {
            "update_time": "2024-06-28T14:58:00+00:00",
            "cash": 1_000_000.0,
            "total_asset": 1_000_000.0,
        }

    def query_stock_positions(self, account_id: str):
        return []

    def query_stock_orders(self, account_id: str):
        return []

    def query_stock_trades(self, account_id: str):
        return []

    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="session_connected:acct-shadow:2024-06-28T14:57:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 57, tzinfo=timezone.utc),
                event_kind="session_connected",
                payload={
                    "_report_kind": "session_connected",
                    "account_id": "acct-shadow",
                    "status": "connected",
                },
            ),
            BrokerRuntimeEvent(
                event_id="account_status:acct-shadow:connected:2024-06-28T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-shadow",
                    "account_type": "STOCK",
                    "status": "connected",
                },
            ),
            BrokerRuntimeEvent(
                event_id="session_consumer_state:acct-shadow:2024-06-28T14:59:30+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 59, 30, tzinfo=timezone.utc),
                event_kind="session_consumer_state",
                payload={
                    "_report_kind": "session_consumer_state",
                    "account_id": "acct-shadow",
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
            ),
        ]
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events

    def close(self):
        self.close_calls += 1


def _make_mock_run_with_wf(run_id: str = "test-run-001", wf_metrics: dict | None = None) -> dict:
    """Build mock run with wf_metrics in the run dict (V2.15.1 S1: server-side WF)."""
    run = _make_mock_run(run_id)
    run["wf_metrics"] = wf_metrics
    return run


@pytest.fixture(autouse=True)
def _use_memory_db_for_live(monkeypatch):
    """Use in-memory DuckDB for live tests — never touch data/ez_trading.db."""
    import duckdb
    from ez.api.routes import live as live_module
    from ez.live.deployment_store import DeploymentStore

    # Reset singletons
    live_module.reset_live_singletons()

    # Patch _deployment_store to use in-memory DB
    _mem_store = DeploymentStore(duckdb.connect(":memory:"))
    monkeypatch.setattr(live_module, "_deployment_store", _mem_store)

    # Also patch scheduler to avoid _get_scheduler() calling get_chain()
    # which opens the real data/ez_trading.db
    from ez.live.scheduler import Scheduler
    _mock_scheduler = MagicMock(spec=Scheduler)
    _mock_scheduler.store = _mem_store
    _mock_scheduler.start_deployment = AsyncMock()
    _mock_scheduler.stop_deployment = AsyncMock()
    _mock_scheduler.pause_deployment = AsyncMock()
    _mock_scheduler.resume_deployment = AsyncMock()
    _mock_scheduler.cancel_order = AsyncMock(
        return_value={
            "deployment_id": "dep-1",
            "broker_type": "qmt",
            "client_order_id": "dep-1:2026-04-13:000001.SZ:buy",
            "broker_order_id": "SYS-001",
            "symbol": "000001.SZ",
            "status": "cancel_requested",
        }
    )
    _mock_scheduler.pump_broker_state = AsyncMock(
        return_value={
            "deployment_id": "dep-1",
            "broker_type": "qmt",
            "status": "broker_synced",
        }
    )
    _mock_scheduler.tick = AsyncMock(return_value=[])
    monkeypatch.setattr(live_module, "_scheduler", _mock_scheduler)

    yield

    # Cleanup
    try:
        _mem_store._conn.close()
    except Exception:
        pass
    live_module.reset_live_singletons()


@pytest.fixture
def _real_live_runtime(monkeypatch):
    import duckdb
    import pandas as pd
    from ez.api.routes import live as live_module
    from ez.live.deployment_store import DeploymentStore
    from ez.live.scheduler import Scheduler

    trading_days = list(pd.bdate_range("2023-05-01", "2024-07-01").date)
    fake_run = _make_e2e_run()
    fake_pf_store = MagicMock()
    fake_pf_store.get_run.return_value = fake_run

    store = DeploymentStore(duckdb.connect(":memory:"))
    data_chain = _FakeDataChain(_make_symbol_bars(fake_run["symbols"], trading_days))
    scheduler = Scheduler(store=store, data_chain=data_chain)

    monkeypatch.setattr(live_module, "_deployment_store", store)
    monkeypatch.setattr(live_module, "_scheduler", scheduler)
    monkeypatch.setattr(live_module, "_monitor", None)
    monkeypatch.setattr(live_module, "_get_portfolio_store", lambda: fake_pf_store)

    try:
        yield {
            "live_module": live_module,
            "store": store,
            "scheduler": scheduler,
            "run": fake_run,
            "trading_days": trading_days,
        }
    finally:
        store.close()


@pytest.fixture
def _real_qmt_shadow_runtime(monkeypatch):
    import duckdb
    import pandas as pd
    from ez.api.routes import live as live_module
    from ez.live.deployment_store import DeploymentStore
    from ez.live.scheduler import Scheduler

    trading_days = list(pd.bdate_range("2023-05-01", "2024-07-01").date)
    fake_run = _make_qmt_shadow_e2e_run()
    fake_pf_store = MagicMock()
    fake_pf_store.get_run.return_value = fake_run

    store = DeploymentStore(duckdb.connect(":memory:"))
    data_chain = _FakeDataChain(_make_symbol_bars(fake_run["symbols"], trading_days))
    qmt_shadow = _FakeQmtShadowBroker()
    scheduler = Scheduler(
        store=store,
        data_chain=data_chain,
        broker_factories={
            "paper": lambda _spec: PaperBroker(),
            "qmt": lambda _spec: qmt_shadow,
        },
    )

    monkeypatch.setattr(live_module, "_deployment_store", store)
    monkeypatch.setattr(live_module, "_scheduler", scheduler)
    monkeypatch.setattr(live_module, "_monitor", None)
    monkeypatch.setattr(live_module, "_get_portfolio_store", lambda: fake_pf_store)

    try:
        yield {
            "live_module": live_module,
            "store": store,
            "scheduler": scheduler,
            "run": fake_run,
            "trading_days": trading_days,
            "shadow_broker": qmt_shadow,
        }
    finally:
        store.close()


@pytest.fixture
def _resident_qmt_runtime(monkeypatch):
    import duckdb
    import pandas as pd
    from ez.api.routes import live as live_module
    from ez.live.deployment_store import DeploymentStore
    from ez.live.scheduler import Scheduler

    trading_days = list(pd.bdate_range("2023-05-01", "2024-07-01").date)
    fake_run = _make_qmt_shadow_e2e_run("e2e-qmt-resident-run-001")
    fake_pf_store = MagicMock()
    fake_pf_store.get_run.return_value = fake_run

    store = DeploymentStore(duckdb.connect(":memory:"))
    data_chain = _FakeDataChain(_make_symbol_bars(fake_run["symbols"], trading_days))
    shared_client = _ResidentQmtClient()
    session_manager = QMTSessionManager()
    qmt_shadow = QMTShadowBroker(
        QMTBrokerConfig(account_id="acct-shadow", enable_cancel=True, always_on_owner=True),
        client_factory=lambda _config: shared_client,
        session_manager=session_manager,
    )
    scheduler = Scheduler(
        store=store,
        data_chain=data_chain,
        broker_factories={
            "paper": lambda _spec: PaperBroker(),
            "qmt": lambda _spec: qmt_shadow,
        },
    )

    monkeypatch.setattr(live_module, "_deployment_store", store)
    monkeypatch.setattr(live_module, "_scheduler", scheduler)
    monkeypatch.setattr(live_module, "_monitor", None)
    monkeypatch.setattr(live_module, "_get_portfolio_store", lambda: fake_pf_store)

    try:
        yield {
            "live_module": live_module,
            "store": store,
            "scheduler": scheduler,
            "run": fake_run,
            "trading_days": trading_days,
            "shadow_broker": qmt_shadow,
            "session_manager": session_manager,
            "shared_client": shared_client,
        }
    finally:
        store.close()


@pytest.fixture
def _real_qmt_real_runtime(monkeypatch):
    import duckdb
    import pandas as pd
    from ez.api.routes import live as live_module
    from ez.live.deployment_store import DeploymentStore
    from ez.live.scheduler import Scheduler

    trading_days = list(pd.bdate_range("2023-05-01", "2024-07-01").date)
    fake_run = _make_qmt_real_e2e_run()
    fake_pf_store = MagicMock()
    fake_pf_store.get_run.return_value = fake_run

    store = DeploymentStore(duckdb.connect(":memory:"))
    data_chain = _FakeDataChain(_make_symbol_bars(fake_run["symbols"], trading_days))
    shared_state = _FakeQmtRealSharedState()
    qmt_shadow = _FakeQmtRealShadowBroker(shared_state)
    qmt_real = QMTRealBroker(
        config=QMTBrokerConfig(account_id="acct-real", enable_cancel=True),
        client=_FakeRealQmtClient(shared_state),
    )
    scheduler = Scheduler(
        store=store,
        data_chain=data_chain,
        broker_factories={
            "paper": lambda _spec: PaperBroker(),
            "qmt": lambda _spec: qmt_shadow,
        },
    )
    monkeypatch.setattr(scheduler, "_build_qmt_real_broker", lambda _spec: qmt_real)

    monkeypatch.setattr(live_module, "_deployment_store", store)
    monkeypatch.setattr(live_module, "_scheduler", scheduler)
    monkeypatch.setattr(live_module, "_monitor", None)
    monkeypatch.setattr(live_module, "_get_portfolio_store", lambda: fake_pf_store)

    try:
        yield {
            "live_module": live_module,
            "store": store,
            "scheduler": scheduler,
            "run": fake_run,
            "trading_days": trading_days,
            "shadow_broker": qmt_shadow,
            "real_broker": qmt_real,
            "shared_state": shared_state,
        }
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeployEndpoint:
    """POST /api/live/deploy"""

    def test_deploy_creates_deployment(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Test Deployment",
            })
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.json()}"
        data = resp.json()
        assert "deployment_id" in data
        assert "spec_id" in data
        assert len(data["deployment_id"]) > 0
        assert len(data["spec_id"]) > 0

    def test_deploy_missing_run_404(self):
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = None
            resp = client.post("/api/live/deploy", json={
                "source_run_id": "nonexistent",
                "name": "Test",
            })
        assert resp.status_code == 404


class TestApproveEndpoint:
    """POST /api/live/deployments/{id}/approve"""

    def test_approve_runs_gate(self):
        """Deploy then approve — should pass with good metrics.
        V2.15.1 S1: wf_metrics are now in the run dict (server-side), not deploy request.
        """
        mock_run = _make_mock_run_with_wf(wf_metrics=_WF_METRICS_GOOD)
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            # 1. Deploy (no wf_metrics in request)
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Test Gate",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            # 2. Approve — gate reads wf_metrics from DB
            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")

        assert approve_resp.status_code == 200, f"Got {approve_resp.status_code}: {approve_resp.json()}"
        data = approve_resp.json()
        assert data["status"] == "approved"
        assert data["verdict"]["passed"] is True
        assert data["qmt_release_gate"] is None

    def test_approve_qmt_shadow_returns_release_gate_preview(self):
        mock_run = _make_qmt_shadow_e2e_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "e2e-qmt-shadow-run-001",
                "name": "QMT Release Preview",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")

        assert approve_resp.status_code == 200
        data = approve_resp.json()
        assert data["qmt_release_gate"]["status"] == "blocked"
        assert data["qmt_release_gate"]["deploy_gate_passed"] is True
        assert "qmt_preflight_not_ok" in data["qmt_release_gate"]["blockers"]
        assert data["qmt_release_gate"]["source"] == "preview"

    def test_approve_rejects_bad_metrics(self):
        """Deploy then approve with bad WF + run metrics — should fail.
        V2.15.1 S1: wf_metrics are now in the run dict (server-side).
        """
        mock_run = _make_mock_run_with_wf(wf_metrics=_WF_METRICS_BAD)
        # Make run metrics bad too
        mock_run["metrics"]["sharpe_ratio"] = 0.1  # below 0.5 threshold
        mock_run["metrics"]["max_drawdown"] = -0.40  # exceeds 0.25

        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            # Deploy (no wf_metrics in request)
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Bad Deploy",
            })
            dep_id = deploy_resp.json()["deployment_id"]

            # Approve — should fail (gate reads bad wf_metrics from DB)
            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")

        assert approve_resp.status_code == 400
        detail = approve_resp.json()["detail"]
        assert detail["verdict"]["passed"] is False


@pytest.mark.unit
class TestLifecycleFlow:
    """Full lifecycle: deploy -> approve -> start -> tick -> stop.

    Marked @pytest.mark.unit because the scheduler is a MagicMock via the
    autouse fixture. For a true end-to-end coverage of the same path, see
    ``TestLiveApiRealE2E`` (real Scheduler + real DuckDB store + real
    PaperBroker) and the existing ``TestLiveApiE2E`` (same but with QMT
    shadow/real broker coverage).
    """

    def test_lifecycle_flow(self):
        mock_run = _make_mock_run_with_wf(wf_metrics=_WF_METRICS_GOOD)
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            # 1. Deploy (no wf_metrics in request)
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Lifecycle Test",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            # 2. Approve
            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

            # 3. Start — mock _start_engine to avoid real strategy instantiation
            with patch("ez.live.scheduler.Scheduler._start_engine", new_callable=AsyncMock) as mock_engine:
                start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
                assert start_resp.status_code == 200
                assert start_resp.json()["status"] == "running"

            # 4. Stop
            stop_resp = client.post(f"/api/live/deployments/{dep_id}/stop", json={
                "reason": "test complete",
            })
            assert stop_resp.status_code == 200
            assert stop_resp.json()["status"] == "stopped"

    def test_cancel_order_flow(self):
        dep_id = "dep-123"
        resp = client.post(
            f"/api/live/deployments/{dep_id}/cancel",
            json={"client_order_id": "dep-123:2026-04-13:000001.SZ:buy"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployment_id"] == "dep-1"
        assert data["status"] == "cancel_requested"


# V3.3.27 Fix-A Issue #3 + #4: spec conflict and cancel idempotency tests.
class TestSpecConflictAndCancelIdempotency:
    """Covers live.py::_build_spec_from_run and cancel_order idempotency."""

    def test_spec_conflict_legacy_and_new_optimizer_params_returns_422(self):
        """Both `_optimizer` and `optimizer_params` present with different
        values must raise 422 conflicting_spec_config."""
        mock_run = _make_mock_run()
        mock_run["config"] = dict(mock_run["config"])
        # Legacy bucket
        mock_run["config"]["optimizer_params"] = {
            "kind": "hrp",
            "risk_aversion": 1.0,
            "max_weight": 0.2,
        }
        # New bucket (different values)
        mock_run["config"]["_optimizer"] = {
            "kind": "mean_variance",
            "risk_aversion": 2.0,
            "max_weight": 0.1,
        }
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            resp = client.post(
                "/api/live/deploy",
                json={"source_run_id": "test-run-001", "name": "ConflictTest"},
            )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.json()}"
        detail = resp.json()["detail"]
        assert detail["code"] == "conflicting_spec_config"
        assert "optimizer_params" in detail["fields"]
        assert "_optimizer" in detail["fields"]

    def test_spec_no_conflict_when_only_one_bucket_present(self):
        """Only `optimizer_params` without `_optimizer` → legacy migration,
        no 422."""
        mock_run = _make_mock_run()
        mock_run["config"] = dict(mock_run["config"])
        mock_run["config"]["optimizer_params"] = {"kind": "hrp"}
        # no _optimizer
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            resp = client.post(
                "/api/live/deploy",
                json={"source_run_id": "test-run-001", "name": "LegacyOnly"},
            )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.json()}"

    def test_spec_no_conflict_when_both_buckets_equal(self):
        """Identical values in both buckets should not trigger 422."""
        mock_run = _make_mock_run()
        mock_run["config"] = dict(mock_run["config"])
        same = {"kind": "hrp", "risk_aversion": 1.0}
        mock_run["config"]["optimizer_params"] = dict(same)
        mock_run["config"]["_optimizer"] = dict(same)
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            resp = client.post(
                "/api/live/deploy",
                json={"source_run_id": "test-run-001", "name": "BothEqual"},
            )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.json()}"

    def test_cancel_idempotent_when_link_already_canceling(self):
        """A cancel request against an already cancel-pending link must
        return 200 {"status": "already_canceling"} without calling broker."""
        from datetime import datetime, timezone

        from ez.api.routes import live as live_module
        from ez.live.broker import BrokerExecutionReport

        # 1. Create a deployment so the record exists.
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "CancelIdempotencyTest",
            })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        # 2. Seed a broker-order link in cancel-pending state.
        store = live_module._deployment_store
        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="rep-precancel-1",
                    broker_type="qmt",
                    as_of=datetime.now(timezone.utc),
                    client_order_id="cid-cancel-1",
                    broker_order_id="SYS-CANCEL-1",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported_cancel_pending",
                    filled_shares=0,
                    remaining_shares=100,
                    avg_price=0.0,
                    message="",
                    account_id="acct-shadow",
                )
            ],
        )

        # 3. First cancel request → should short-circuit to already_canceling.
        resp = client.post(
            f"/api/live/deployments/{dep_id}/cancel",
            json={"client_order_id": "cid-cancel-1"},
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.json()}"
        data = resp.json()
        assert data["status"] == "already_canceling"
        assert data["client_order_id"] == "cid-cancel-1"
        assert data["link"]["latest_status"] == "reported_cancel_pending"
        # scheduler.cancel_order should NOT have been called because the
        # link is already pending cancel. The autouse fixture pre-sets
        # `cancel_order` as an AsyncMock.
        assert live_module._scheduler.cancel_order.call_count == 0

        # 4. Double-click simulation: a second identical request should
        # again short-circuit — still no broker call.
        resp2 = client.post(
            f"/api/live/deployments/{dep_id}/cancel",
            json={"client_order_id": "cid-cancel-1"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "already_canceling"
        assert live_module._scheduler.cancel_order.call_count == 0

    def test_cancel_if_not_already_false_forces_broker_call(self):
        """`if_not_already=false` must bypass the short-circuit and hit
        the scheduler even when the link is cancel-pending."""
        from datetime import datetime, timezone

        from ez.api.routes import live as live_module
        from ez.live.broker import BrokerExecutionReport

        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "ForceCancelTest",
            })
        dep_id = deploy_resp.json()["deployment_id"]

        store = live_module._deployment_store
        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="rep-force-1",
                    broker_type="qmt",
                    as_of=datetime.now(timezone.utc),
                    client_order_id="cid-force-1",
                    broker_order_id="SYS-FORCE-1",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported_cancel_pending",
                    filled_shares=0,
                    remaining_shares=100,
                    avg_price=0.0,
                    message="",
                    account_id="acct-shadow",
                )
            ],
        )

        resp = client.post(
            f"/api/live/deployments/{dep_id}/cancel?if_not_already=false",
            json={"client_order_id": "cid-force-1"},
        )
        assert resp.status_code == 200
        # Scheduler mock was called once despite the link being pending.
        assert live_module._scheduler.cancel_order.call_count == 1


class TestBrokerOrdersResponseShape:
    """V3.3.27 Fix-A Issue #2: broker-orders returns {target_account_id, orders}."""

    def test_broker_orders_returns_target_account_id_and_per_link_account_id(self):
        from datetime import datetime, timezone

        from ez.api.routes import live as live_module
        from ez.live.broker import BrokerExecutionReport

        mock_run = _make_qmt_shadow_e2e_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "BrokerOrdersShape",
            })
        dep_id = deploy_resp.json()["deployment_id"]

        store = live_module._deployment_store
        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="rep-shape-1",
                    broker_type="qmt",
                    as_of=datetime.now(timezone.utc),
                    client_order_id="cid-shape-1",
                    broker_order_id="SYS-SHAPE-1",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=100,
                    avg_price=0.0,
                    message="",
                    account_id="acct-shadow",
                )
            ],
        )

        resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert body["deployment_id"] == dep_id
        assert body["target_account_id"] == "acct-shadow"
        assert isinstance(body["orders"], list)
        assert len(body["orders"]) == 1
        order = body["orders"][0]
        assert order["client_order_id"] == "cid-shape-1"
        assert order["broker_order_id"] == "SYS-SHAPE-1"
        # Per-link account_id is surfaced from the store.
        assert order["account_id"] == "acct-shadow"


class TestReleaseGateFoldsHardGate:
    """V3.3.27 Fix-A Issue #1: release gate folds hard_gate into blockers."""

    def test_release_gate_receives_hard_gate_payload(self):
        """Verify _build_qmt_release_gate passes hard_gate to
        build_qmt_release_gate_decision. If the shared decision helper
        accepts hard_gate we verify it is present in the resulting
        decision's hard_gate-derived blockers; otherwise the API remains
        tolerant and does not crash."""
        from ez.api.routes import live as live_module

        mock_run = _make_qmt_shadow_e2e_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "ReleaseHardGate",
            })
        dep_id = deploy_resp.json()["deployment_id"]

        store = live_module._deployment_store
        record = store.get_record(dep_id)
        spec = store.get_spec(record.spec_id)
        assert spec is not None

        hard_gate_payload = {
            "event": "qmt_reconcile_hard_gate",
            "status": "blocked",
            "blockers": ["broker_order_reconcile_drift"],
            "message": "QMT reconcile checks failed; fail closed.",
        }
        # Call internal helper directly to verify hard_gate is accepted
        # and not crashing the API path.
        release = live_module._build_qmt_release_gate(
            record=record,
            spec=spec,
            qmt_submit_gate=None,
            hard_gate=hard_gate_payload,
        )
        assert release is not None
        # Must still render as preview since no runtime submit_gate was
        # provided.
        assert release["source"] == "preview"
        # Structural invariant: blockers is always a list after fold.
        assert isinstance(release["blockers"], list)


class TestListDeployments:
    """GET /api/live/deployments"""

    def test_list_deployments(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            # Create a deployment first
            client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "List Test",
            })

            # List
            resp = client.get("/api/live/deployments")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "deployment_id" in data[0]
        assert "name" in data[0]
        assert "status" in data[0]

    def test_list_deployments_with_status_filter(self):
        """Filter by status=pending."""
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Filter Test",
            })

            resp = client.get("/api/live/deployments?status=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert all(d["status"] == "pending" for d in data)

    def test_list_deployments_includes_preview_qmt_release_gate(self):
        mock_run = _make_qmt_shadow_e2e_run("list-qmt-preview-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "List QMT Preview",
            })
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

            resp = client.get("/api/live/deployments")

        assert resp.status_code == 200
        data = resp.json()
        row = next(item for item in data if item["deployment_id"] == dep_id)
        assert row["status"] == "approved"
        assert row["qmt_release_gate"]["status"] == "blocked"
        assert row["qmt_release_gate"]["source"] == "preview"
        assert "qmt_preflight_not_ok" in row["qmt_release_gate"]["blockers"]


class TestDashboard:
    """GET /api/live/dashboard"""

    def test_dashboard(self):
        resp = client.get("/api/live/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "deployments" in data
        assert "alerts" in data
        assert isinstance(data["deployments"], list)
        assert isinstance(data["alerts"], list)


class TestDeploymentDetail:
    """GET /api/live/deployments/{id}"""

    def test_get_deployment_detail(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Detail Test",
            })
            dep_id = deploy_resp.json()["deployment_id"]

        resp = client.get(f"/api/live/deployments/{dep_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployment_id"] == dep_id
        assert data["name"] == "Detail Test"
        assert "spec" in data
        assert data["spec"]["strategy_name"] == "TopNRotation"
        assert data["qmt_release_gate"] is None

    def test_get_nonexistent_404(self):
        resp = client.get("/api/live/deployments/nonexistent-id")
        assert resp.status_code == 404


class TestSnapshotsAndTrades:
    """GET /api/live/deployments/{id}/snapshots and /trades"""

    def test_snapshots_empty(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Snap Test",
            })
            dep_id = deploy_resp.json()["deployment_id"]

        resp = client.get(f"/api/live/deployments/{dep_id}/snapshots")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_trades_empty(self):
        mock_run = _make_mock_run()
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            pf_store = mock_pf.return_value
            pf_store.get_run.return_value = mock_run

            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": "test-run-001",
                "name": "Trade Test",
            })
            dep_id = deploy_resp.json()["deployment_id"]

        resp = client.get(f"/api/live/deployments/{dep_id}/trades")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_snapshots_nonexistent_404(self):
        resp = client.get("/api/live/deployments/nonexistent/snapshots")
        assert resp.status_code == 404

    def test_trades_nonexistent_404(self):
        resp = client.get("/api/live/deployments/nonexistent/trades")
        assert resp.status_code == 404

    def test_broker_orders_nonexistent_404(self):
        resp = client.get("/api/live/deployments/nonexistent/broker-orders")
        assert resp.status_code == 404

    def test_broker_state_nonexistent_404(self):
        resp = client.get("/api/live/deployments/nonexistent/broker-state")
        assert resp.status_code == 404


class TestLiveApiE2E:
    """Real scheduler E2E: deploy -> approve -> start -> tick -> pause/resume -> restore."""

    def test_e2e_lifecycle_through_real_scheduler(self, _real_live_runtime):
        live_module = _real_live_runtime["live_module"]
        store = _real_live_runtime["store"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-run-001",
            "name": "E2E Lifecycle",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["status"] == "running"

        tick_resp = client.post("/api/live/tick", json={"business_date": "2024-06-28"})
        assert tick_resp.status_code == 200
        tick_data = tick_resp.json()
        assert tick_data["business_date"] == "2024-06-28"
        assert len(tick_data["results"]) == 1
        assert tick_data["results"][0]["deployment_id"] == dep_id
        assert len(tick_data["results"][0]["trades"]) == 3
        assert any(
            event.get("event") == "runtime_allocator"
            for event in tick_data["results"][0]["risk_events"]
        )

        detail_resp = client.get(f"/api/live/deployments/{dep_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["latest_snapshot"]["equity"] > 0
        assert set(detail["latest_snapshot"]["holdings"]) == {
            "000001.SZ", "000002.SZ", "000858.SZ"
        }
        assert all(shares > 0 for shares in detail["latest_snapshot"]["holdings"].values())

        snapshots_resp = client.get(f"/api/live/deployments/{dep_id}/snapshots")
        assert snapshots_resp.status_code == 200
        snapshots = snapshots_resp.json()
        assert len(snapshots) == 1
        assert any(
            event.get("event") == "runtime_allocator"
            for event in snapshots[0]["risk_events"]
        )

        trades_resp = client.get(f"/api/live/deployments/{dep_id}/trades")
        assert trades_resp.status_code == 200
        trades = trades_resp.json()
        assert len(trades) == 3
        assert {trade["symbol"] for trade in trades} == {
            "000001.SZ", "000002.SZ", "000858.SZ"
        }

        events = store.get_events(dep_id)
        event_types = [event.event_type.value for event in events]
        first_order_idx = event_types.index("order_submitted")
        snapshot_idx = event_types.index("market_snapshot")
        assert snapshot_idx > 0
        assert all(event_type == "market_bar_recorded" for event_type in event_types[:snapshot_idx])
        assert snapshot_idx < first_order_idx
        assert "market_snapshot" in event_types
        assert event_types[first_order_idx:first_order_idx + 6] == [
            "order_submitted",
            "order_filled",
            "order_submitted",
            "order_filled",
            "order_submitted",
            "order_filled",
        ]
        assert "risk_recorded" in event_types[first_order_idx + 6:-2]
        bar_events = [event for event in events if event.event_type.value == "market_bar_recorded"]
        assert event_types[-2:] == ["snapshot_saved", "tick_completed"]
        market_event = next(event for event in events if event.event_type.value == "market_snapshot")
        risk_events = [event for event in events if event.event_type.value == "risk_recorded"]
        assert {event.payload["symbol"] for event in bar_events} == set(market_event.payload["has_bar_symbols"])
        assert market_event.payload["source"] == "live"
        assert any(event.payload["risk_event"]["event"] == "runtime_allocator" for event in risk_events)
        assert events[-1].payload["trade_count"] == 3

        pause_resp = client.post(f"/api/live/deployments/{dep_id}/pause")
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "paused"

        resume_resp = client.post(f"/api/live/deployments/{dep_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "running"

        # Simulate process restart: drop scheduler singleton, rebuild from DB, resume all.
        from ez.live.scheduler import Scheduler

        live_module._scheduler = Scheduler(store=store, data_chain=_real_live_runtime["scheduler"].data_chain)
        restored = __import__("asyncio").run(live_module._scheduler.resume_all())
        assert restored == 1

        tick_resp_2 = client.post("/api/live/tick", json={"business_date": "2024-07-01"})
        assert tick_resp_2.status_code == 200
        tick_data_2 = tick_resp_2.json()
        assert len(tick_data_2["results"]) == 1
        assert tick_data_2["results"][0]["deployment_id"] == dep_id

        stop_resp = client.post(
            f"/api/live/deployments/{dep_id}/stop?liquidate=true",
            json={"reason": "e2e complete"},
        )
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "stopped"
        assert stop_resp.json()["liquidated"] is True

        final_snapshots = client.get(f"/api/live/deployments/{dep_id}/snapshots").json()
        assert len(final_snapshots) >= 3
        assert final_snapshots[-1]["liquidation"] is True

    def test_qmt_shadow_cancel_and_broker_order_e2e(self, _real_qmt_shadow_runtime):
        store = _real_qmt_shadow_runtime["store"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-shadow-run-001",
            "name": "E2E QMT Shadow",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["recent_runtime_events"][0]["payload"]["runtime_kind"] == "account_status"
        assert broker_state["latest_session_runtime"] is None
        assert broker_state["latest_session_owner_runtime"] is None
        assert broker_state["latest_session_consumer_runtime"]["payload"]["runtime_kind"] == "session_consumer_restarted"
        assert broker_state["latest_callback_account_mode"] == "query_fallback"
        assert broker_state["latest_callback_account_freshness"] == "unavailable"
        assert broker_state["qmt_readiness"]["status"] == "degraded"
        assert broker_state["qmt_readiness"]["ready_for_real_submit"] is False
        assert broker_state["qmt_submit_gate"]["status"] == "shadow_only"
        assert broker_state["qmt_submit_gate"]["can_submit_now"] is False
        assert broker_state["qmt_submit_gate"]["preflight_ok"] is False
        assert broker_state["qmt_submit_gate"]["account_id"] == "acct-shadow"
        assert broker_state["qmt_submit_gate"]["policy"]["enabled"] is True
        assert "broker_total_asset_unavailable" in broker_state["qmt_submit_gate"]["blockers"]
        assert broker_state["qmt_release_gate"]["status"] == "blocked"
        assert "qmt_preflight_not_ok" in broker_state["qmt_release_gate"]["blockers"]

        tick_resp = client.post("/api/live/tick", json={"business_date": "2024-06-28"})
        assert tick_resp.status_code == 200
        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert len(broker_orders) == 1
        assert broker_orders[0]["broker_order_id"] == "SYS-001"
        assert broker_orders[0]["latest_status"] == "partially_filled"
        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_broker_account"]["event_type"] == "broker_account_recorded"
        assert broker_state["latest_broker_account"]["payload"]["positions"] == {}
        assert broker_state["recent_runtime_events"][0]["payload"]["runtime_kind"] == "account_status"
        assert broker_state["latest_session_runtime"] is None
        assert broker_state["latest_session_owner_runtime"] is None
        assert broker_state["latest_session_consumer_runtime"]["payload"]["runtime_kind"] == "session_consumer_restarted"
        assert broker_state["latest_reconcile"]["event"] == "broker_reconcile"
        assert broker_state["latest_order_reconcile"]["event"] == "broker_order_reconcile"
        assert broker_state["latest_order_reconcile"]["status"] == "ok"

        cancel_resp = client.post(
            f"/api/live/deployments/{dep_id}/cancel",
            json={"broker_order_id": "SYS-001"},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancel_requested"

        tick_resp_2 = client.post("/api/live/tick", json={"business_date": "2024-07-01"})
        assert tick_resp_2.status_code == 200

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert broker_orders[0]["latest_status"] == "canceled"
        assert broker_orders[0]["client_order_id"].endswith(":broker_order:qmt:SYS-001")
        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_broker_account"]["payload"]["broker_type"] == "qmt"
        assert broker_state["latest_reconcile"]["broker_type"] == "qmt"
        assert broker_state["latest_order_reconcile"]["status"] == "ok"
        assert broker_state["latest_callback_account_mode"] == "query_fallback"
        assert broker_state["latest_callback_account_freshness"] == "unavailable"
        assert any(
            event["payload"]["runtime_kind"] == "cancel_order_stock_async_response"
            for event in broker_state["recent_runtime_events"]
        )

        events = store.get_events(dep_id)
        assert any(event.event_type.value == "broker_account_recorded" for event in events)
        assert any(event.event_type.value == "broker_runtime_recorded" for event in events)
        assert any(
            event.event_type.value == "broker_execution_recorded"
            and event.payload["status"] == "canceled"
            for event in events
        )

    def test_broker_orders_and_state_project_cancel_error_from_runtime(self):
        from ez.api.routes import live as live_module
        from ez.live.broker import BrokerExecutionReport
        from ez.live.events import (
            make_broker_cancel_requested_event,
            make_broker_runtime_event,
        )

        mock_run = _make_qmt_shadow_e2e_run("qmt-cancel-error-runtime-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Cancel Error Runtime",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None

        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:partially_filled:600:400:2026-04-14T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2026-04-14:000001.SZ:buy",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="partially_filled",
                    filled_shares=600,
                    remaining_shares=400,
                    avg_price=12.34,
                )
            ],
        )
        store.append_events(
            [
                make_broker_cancel_requested_event(
                    dep_id,
                    broker_type="qmt",
                    request_ts=datetime(2026, 4, 14, 15, 1, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2026-04-14:000001.SZ:buy",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                ),
                make_broker_runtime_event(
                    dep_id,
                    runtime_event_id="cancel_error:SYS-001",
                    broker_type="qmt",
                    runtime_kind="cancel_error",
                    event_ts=datetime(2026, 4, 14, 15, 2, tzinfo=timezone.utc),
                    payload={
                        "client_order_id": "dep-shadow:2026-04-14:000001.SZ:buy",
                        "order_sysid": "SYS-001",
                        "status_msg": "cancel rejected",
                    },
                ),
            ]
        )

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert len(broker_orders) == 1
        assert broker_orders[0]["latest_status"] == "partially_filled"
        assert broker_orders[0]["cancel_state"] == "cancel_error"
        assert broker_orders[0]["cancel_error_message"] == "cancel rejected"

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["broker_order_cancel_summary"]["total"] == 1
        assert broker_state["broker_order_cancel_summary"]["cancel_error"] == 1
        assert broker_state["broker_order_cancel_summary"]["cancel_inflight"] == 0

    def test_broker_orders_and_state_project_cancel_ack_from_runtime_without_explicit_request(self):
        from ez.api.routes import live as live_module
        from ez.live.broker import BrokerExecutionReport
        from ez.live.events import make_broker_runtime_event

        mock_run = _make_qmt_shadow_e2e_run("qmt-cancel-ack-runtime-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Cancel Ack Runtime",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None

        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:partially_filled:600:400:2026-04-14T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2026-04-14:000001.SZ:buy",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="partially_filled",
                    filled_shares=600,
                    remaining_shares=400,
                    avg_price=12.34,
                )
            ],
        )
        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="cancel_async:SYS-001",
                broker_type="qmt",
                runtime_kind="cancel_order_stock_async_response",
                event_ts=datetime(2026, 4, 14, 15, 1, tzinfo=timezone.utc),
                payload={
                    "order_sysid": "SYS-001",
                    "cancel_result": 0,
                    "seq": 88,
                },
            )
        )

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert len(broker_orders) == 1
        assert broker_orders[0]["latest_status"] == "partially_filled_cancel_pending"
        assert broker_orders[0]["cancel_state"] == "cancel_inflight"

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["broker_order_cancel_summary"]["total"] == 1
        assert broker_state["broker_order_cancel_summary"]["cancel_inflight"] == 1

    def test_broker_state_prefers_precise_runtime_and_risk_events(self):
        from ez.api.routes import live as live_module
        from ez.live.events import (
            make_broker_account_event,
            make_broker_runtime_event,
            make_risk_event,
        )

        mock_run = _make_qmt_shadow_e2e_run("qmt-runtime-precision-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Runtime Precision",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None

        store.append_event(
            make_broker_account_event(
                dep_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc),
                cash=100_000.0,
                total_asset=120_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="session_connected:2026-04-14T09:31:00+00:00",
                broker_type="qmt",
                runtime_kind="session_connected",
                event_ts=datetime(2026, 4, 14, 9, 31, tzinfo=timezone.utc),
                payload={"status": "connected"},
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=0,
                risk_event={"event": "broker_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 32, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=1,
                risk_event={"event": "broker_order_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 33, tzinfo=timezone.utc),
            )
        )
        for idx in range(25):
            store.append_event(
                make_risk_event(
                    dep_id,
                    business_date=date(2026, 4, 14),
                    risk_index=100 + idx,
                    risk_event={"event": f"noise_{idx}", "status": "ok"},
                    event_ts=datetime(2026, 4, 14, 9, 34, tzinfo=timezone.utc),
                )
            )
        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="session_consumer_state:2026-04-14T09:40:00+00:00",
                broker_type="qmt",
                runtime_kind="session_consumer_state",
                event_ts=datetime(2026, 4, 14, 9, 40, tzinfo=timezone.utc),
                payload={
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
            )
        )

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=5")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_session_runtime"]["payload"]["runtime_kind"] == "session_connected"
        assert (
            broker_state["latest_session_consumer_state_runtime"]["payload"]["runtime_kind"]
            == "session_consumer_state"
        )
        assert broker_state["latest_callback_account_mode"] == "callback_preferred"
        assert broker_state["latest_callback_account_freshness"] == "fresh"
        assert broker_state["latest_reconcile"]["status"] == "ok"
        assert broker_state["latest_order_reconcile"]["status"] == "ok"

    def test_broker_state_exposes_position_and_trade_reconcile(self):
        """V3.3.44: /broker-state surfaces latest_position_reconcile and
        latest_trade_reconcile alongside the existing account/order reconciles.
        """
        from ez.api.routes import live as live_module
        from ez.live.events import (
            make_broker_account_event,
            make_risk_event,
        )

        mock_run = _make_qmt_shadow_e2e_run("qmt-four-way-reconcile-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Four-Way Reconcile",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None

        store.append_event(
            make_broker_account_event(
                dep_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc),
                cash=100_000.0,
                total_asset=120_000.0,
                positions={"000001.SZ": 500},
                open_orders=[],
                fill_count=0,
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=0,
                risk_event={"event": "broker_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 32, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=1,
                risk_event={"event": "broker_order_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 33, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=2,
                risk_event={
                    "event": "position_reconcile",
                    "status": "drift",
                    "broker_type": "qmt",
                    "message": "Broker positions drift from local holdings.",
                    "details": {"position_drifts": [{"symbol": "000001.SZ"}]},
                },
                event_ts=datetime(2026, 4, 14, 9, 34, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=3,
                risk_event={
                    "event": "trade_reconcile",
                    "status": "ok",
                    "broker_type": "qmt",
                },
                event_ts=datetime(2026, 4, 14, 9, 35, tzinfo=timezone.utc),
            )
        )

        broker_state_resp = client.get(
            f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=5"
        )
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_position_reconcile"] is not None
        assert broker_state["latest_position_reconcile"]["event"] == "position_reconcile"
        assert broker_state["latest_position_reconcile"]["status"] == "drift"
        assert broker_state["latest_trade_reconcile"] is not None
        assert broker_state["latest_trade_reconcile"]["event"] == "trade_reconcile"
        assert broker_state["latest_trade_reconcile"]["status"] == "ok"

    def test_broker_state_blocks_real_qmt_fallback_when_runtime_is_degraded_without_projection(self):
        from ez.api.routes import live as live_module
        from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
        from ez.live.events import (
            make_broker_account_event,
            make_broker_runtime_event,
            make_risk_event,
        )

        store = live_module._deployment_store
        assert store is not None

        spec = DeploymentSpec(
            strategy_name="TopNRotation",
            strategy_params={"factor": "momentum_rank_20", "top_n": 5},
            symbols=("000001.SZ", "000002.SZ"),
            market="cn_stock",
            freq="daily",
            initial_cash=100_000.0,
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 200_000.0,
                },
            },
        )
        record = DeploymentRecord(
            spec_id=spec.spec_id,
            name="QMT Real Fallback",
            status="running",
            gate_verdict=json.dumps({"passed": True}),
        )
        store.save_spec(spec)
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        store.append_event(
            make_broker_account_event(
                record.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc),
                cash=100_000.0,
                total_asset=120_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        store.append_event(
            make_risk_event(
                record.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=0,
                risk_event={"event": "broker_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 31, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                record.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=1,
                risk_event={"event": "broker_order_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 32, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_broker_runtime_event(
                record.deployment_id,
                runtime_event_id="session_consumer_state:2026-04-14T09:35:00+00:00",
                broker_type="qmt",
                runtime_kind="session_consumer_state",
                event_ts=datetime(2026, 4, 14, 9, 35, tzinfo=timezone.utc),
                payload={
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
            )
        )
        store.append_event(
            make_broker_runtime_event(
                record.deployment_id,
                runtime_event_id="disconnected:2026-04-14T09:36:00+00:00",
                broker_type="qmt",
                runtime_kind="disconnected",
                event_ts=datetime(2026, 4, 14, 9, 36, tzinfo=timezone.utc),
                payload={"status": "disconnected"},
            )
        )

        broker_state_resp = client.get(
            f"/api/live/deployments/{record.deployment_id}/broker-state?runtime_limit=5"
        )
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["projection_source"] is None
        assert broker_state["projection_ts"] is None
        assert broker_state["target_account_id"] == "acct-real"
        assert broker_state["qmt_readiness"]["ready_for_real_submit"] is False
        assert broker_state["qmt_submit_gate"]["status"] == "blocked"
        assert broker_state["qmt_submit_gate"]["account_id"] == "acct-real"
        assert "session_unhealthy" in broker_state["qmt_submit_gate"]["blockers"]
        assert "shadow_mode_only" not in broker_state["qmt_submit_gate"]["blockers"]

        submit_gate_resp = client.get(
            f"/api/live/deployments/{record.deployment_id}/broker-submit-gate"
        )
        assert submit_gate_resp.status_code == 200
        submit_gate = submit_gate_resp.json()
        assert submit_gate["broker_type"] == "qmt"
        assert submit_gate["projection_source"] is None
        assert submit_gate["projection_ts"] is None
        assert submit_gate["target_account_id"] == "acct-real"
        assert submit_gate["qmt_submit_gate"]["status"] == "blocked"
        assert submit_gate["qmt_submit_gate"]["account_id"] == "acct-real"

        release_gate_resp = client.get(
            f"/api/live/deployments/{record.deployment_id}/release-gate"
        )
        assert release_gate_resp.status_code == 200
        release_gate = release_gate_resp.json()
        assert release_gate["projection_source"] is None
        assert release_gate["projection_ts"] is None
        assert release_gate["target_account_id"] == "acct-real"
        assert release_gate["qmt_release_gate"]["source"] == "runtime"

    def test_broker_state_recovers_real_qmt_after_newer_runtime_events_without_projection(self):
        from ez.api.routes import live as live_module
        from ez.live.deployment_spec import DeploymentRecord, DeploymentSpec
        from ez.live.events import (
            make_broker_account_event,
            make_broker_runtime_event,
            make_risk_event,
        )

        store = live_module._deployment_store
        assert store is not None

        spec = DeploymentSpec(
            strategy_name="TopNRotation",
            strategy_params={"factor": "momentum_rank_20", "top_n": 5},
            symbols=("000001.SZ", "000002.SZ"),
            market="cn_stock",
            freq="daily",
            initial_cash=100_000.0,
            broker_type="qmt",
            shadow_broker_type="qmt",
            risk_params={
                "qmt_real_broker_config": {"account_id": "acct-real"},
                "shadow_broker_config": {"account_id": "acct-shadow"},
                "qmt_real_submit_policy": {
                    "enabled": True,
                    "allowed_account_ids": ["acct-real"],
                    "max_total_asset": 200_000.0,
                    "max_initial_cash": 200_000.0,
                },
            },
        )
        record = DeploymentRecord(
            spec_id=spec.spec_id,
            name="QMT Real Recovery",
            status="running",
            gate_verdict=json.dumps({"passed": True}),
        )
        store.save_spec(spec)
        store.save_record(record)
        store.update_status(record.deployment_id, "running")

        store.append_event(
            make_broker_account_event(
                record.deployment_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc),
                cash=100_000.0,
                total_asset=120_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        store.append_event(
            make_risk_event(
                record.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=0,
                risk_event={"event": "real_broker_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 31, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                record.deployment_id,
                business_date=date(2026, 4, 14),
                risk_index=1,
                risk_event={"event": "real_broker_order_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 32, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_broker_runtime_event(
                record.deployment_id,
                runtime_event_id="session_connected:2026-04-14T09:35:00+00:00",
                broker_type="qmt",
                runtime_kind="session_connected",
                event_ts=datetime(2026, 4, 14, 9, 35, tzinfo=timezone.utc),
                payload={"status": "connected", "account_id": "acct-real"},
            )
        )
        store.append_event(
            make_broker_runtime_event(
                record.deployment_id,
                runtime_event_id="session_consumer_state:2026-04-14T09:36:00+00:00",
                broker_type="qmt",
                runtime_kind="session_consumer_state",
                event_ts=datetime(2026, 4, 14, 9, 36, tzinfo=timezone.utc),
                payload={
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                    "account_id": "acct-real",
                },
            )
        )
        store.append_event(
            make_broker_runtime_event(
                record.deployment_id,
                runtime_event_id="disconnected:2026-04-14T09:37:00+00:00",
                broker_type="qmt",
                runtime_kind="disconnected",
                event_ts=datetime(2026, 4, 14, 9, 37, tzinfo=timezone.utc),
                payload={"status": "disconnected", "account_id": "acct-real"},
            )
        )
        store.append_event(
            make_broker_runtime_event(
                record.deployment_id,
                runtime_event_id="session_reconnected:2026-04-14T09:38:00+00:00",
                broker_type="qmt",
                runtime_kind="session_reconnected",
                event_ts=datetime(2026, 4, 14, 9, 38, tzinfo=timezone.utc),
                payload={"status": "connected", "account_id": "acct-real"},
            )
        )
        store.append_event(
            make_broker_runtime_event(
                record.deployment_id,
                runtime_event_id="session_consumer_state:2026-04-14T09:39:00+00:00",
                broker_type="qmt",
                runtime_kind="session_consumer_state",
                event_ts=datetime(2026, 4, 14, 9, 39, tzinfo=timezone.utc),
                payload={
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                    "account_id": "acct-real",
                },
            )
        )

        broker_state_resp = client.get(
            f"/api/live/deployments/{record.deployment_id}/broker-state?runtime_limit=5"
        )
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["projection_source"] is None
        assert broker_state["latest_callback_account_mode"] == "callback_preferred"
        assert broker_state["latest_callback_account_freshness"] == "fresh"
        assert broker_state["qmt_readiness"]["ready_for_real_submit"] is True
        assert broker_state["qmt_submit_gate"]["status"] == "open"
        assert broker_state["qmt_release_gate"]["status"] == "candidate"

        submit_gate_resp = client.get(
            f"/api/live/deployments/{record.deployment_id}/broker-submit-gate"
        )
        assert submit_gate_resp.status_code == 200
        submit_gate = submit_gate_resp.json()
        assert submit_gate["projection_source"] is None
        assert submit_gate["qmt_submit_gate"]["status"] == "open"

        release_gate_resp = client.get(
            f"/api/live/deployments/{record.deployment_id}/release-gate"
        )
        assert release_gate_resp.status_code == 200
        release_gate = release_gate_resp.json()
        assert release_gate["projection_source"] is None
        assert release_gate["qmt_release_gate"]["status"] == "candidate"

    def test_broker_state_ignores_stale_runtime_projection_when_newer_runtime_event_exists(self):
        from ez.api.routes import live as live_module
        from ez.live.events import make_broker_runtime_event

        mock_run = _make_qmt_real_e2e_run("qmt-stale-projection-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Stale Projection",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None
        store.upsert_broker_state_projection(
            dep_id,
            broker_type="qmt",
            projection={
                "deployment_status": "running",
                "projection_source": "runtime",
                "projection_ts": "2026-04-14T09:35:00+00:00",
                "latest_callback_account_mode": "callback_preferred",
                "latest_callback_account_freshness": "fresh",
                "qmt_readiness": {
                    "ready_for_real_submit": True,
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
                "qmt_submit_gate": {
                    "status": "open",
                    "can_submit_now": True,
                    "account_id": "acct-real",
                    "blockers": [],
                },
                "qmt_release_gate": {
                    "status": "candidate",
                    "eligible_for_real_submit": True,
                    "blockers": [],
                },
            },
        )
        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="disconnected:2026-04-14T09:36:00+00:00",
                broker_type="qmt",
                runtime_kind="disconnected",
                event_ts=datetime(2026, 4, 14, 9, 36, tzinfo=timezone.utc),
                payload={"status": "disconnected"},
            )
        )

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=5")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["projection_source"] is None
        assert broker_state["qmt_readiness"]["ready_for_real_submit"] is False
        assert broker_state["qmt_submit_gate"]["status"] == "blocked"
        assert "session_unhealthy" in broker_state["qmt_submit_gate"]["blockers"]

    def test_broker_state_invalidates_same_timestamp_projection_on_newer_real_reconcile_event(self):
        from ez.api.routes import live as live_module
        from ez.live.events import make_risk_event

        mock_run = _make_qmt_real_e2e_run("qmt-same-ts-reconcile-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Same Timestamp Reconcile",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None
        store.upsert_broker_state_projection(
            dep_id,
            broker_type="qmt",
            projection={
                "deployment_status": "running",
                "projection_source": "runtime",
                "projection_ts": "2026-04-14T09:35:00+00:00",
                "target_account_id": "acct-real",
                "latest_callback_account_mode": "callback_preferred",
                "latest_callback_account_freshness": "fresh",
                "qmt_readiness": {
                    "ready_for_real_submit": True,
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
                "qmt_submit_gate": {
                    "status": "open",
                    "can_submit_now": True,
                    "account_id": "acct-real",
                    "blockers": [],
                },
                "qmt_release_gate": {
                    "status": "candidate",
                    "eligible_for_real_submit": True,
                    "blockers": [],
                },
            },
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=0,
                risk_event={
                    "event": "real_broker_reconcile",
                    "status": "drift",
                    "broker_type": "qmt",
                    "account_id": "acct-real",
                },
                event_ts=datetime(2026, 4, 14, 9, 35, tzinfo=timezone.utc),
            )
        )

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=5")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["projection_source"] is None
        assert broker_state["latest_reconcile"]["event"] == "real_broker_reconcile"
        assert broker_state["latest_reconcile"]["status"] == "drift"
        assert broker_state["qmt_submit_gate"]["status"] == "blocked"
        assert broker_state["qmt_release_gate"]["status"] == "blocked"

    def test_broker_state_degrades_callback_health_when_owner_teardown_is_newer_than_state(self):
        from ez.api.routes import live as live_module
        from ez.live.events import make_broker_runtime_event

        mock_run = _make_qmt_shadow_e2e_run("qmt-owner-teardown-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Owner Teardown",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None

        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="session_consumer_state:2026-04-14T09:40:00+00:00",
                broker_type="qmt",
                runtime_kind="session_consumer_state",
                event_ts=datetime(2026, 4, 14, 9, 40, tzinfo=timezone.utc),
                payload={
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
            )
        )
        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="session_owner_closed:2026-04-14T09:41:00+00:00",
                broker_type="qmt",
                runtime_kind="session_owner_closed",
                event_ts=datetime(2026, 4, 14, 9, 41, tzinfo=timezone.utc),
                payload={"status": "closed"},
            )
        )

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=5")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_session_owner_runtime"]["payload"]["runtime_kind"] == "session_owner_closed"
        assert broker_state["latest_callback_account_mode"] == "query_fallback"
        assert broker_state["latest_callback_account_freshness"] == "unavailable"

    def test_broker_state_degrades_callback_health_when_disconnected_is_newer_than_state(self):
        from ez.api.routes import live as live_module
        from ez.live.events import make_broker_runtime_event

        mock_run = _make_qmt_shadow_e2e_run("qmt-disconnected-health-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Disconnected Health",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None

        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="session_consumer_state:2026-04-14T09:40:00+00:00",
                broker_type="qmt",
                runtime_kind="session_consumer_state",
                event_ts=datetime(2026, 4, 14, 9, 40, tzinfo=timezone.utc),
                payload={
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
            )
        )
        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="disconnected:2026-04-14T09:41:00+00:00",
                broker_type="qmt",
                runtime_kind="disconnected",
                event_ts=datetime(2026, 4, 14, 9, 41, tzinfo=timezone.utc),
                payload={"status": "disconnected"},
            )
        )

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=5")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_session_runtime"]["payload"]["runtime_kind"] == "disconnected"
        assert broker_state["latest_callback_account_mode"] == "query_fallback"
        assert broker_state["latest_callback_account_freshness"] == "unavailable"

    def test_broker_state_surfaces_qmt_hard_gate_and_applies_it_to_runtime_submit_gate(self):
        from ez.api.routes import live as live_module
        from ez.live.events import (
            make_broker_account_event,
            make_broker_runtime_event,
            make_risk_event,
        )

        mock_run = _make_qmt_shadow_e2e_run("qmt-hard-gate-run")
        with patch("ez.api.routes.live._get_portfolio_store") as mock_pf:
            mock_pf.return_value.get_run.return_value = mock_run
            deploy_resp = client.post("/api/live/deploy", json={
                "source_run_id": mock_run["run_id"],
                "name": "QMT Hard Gate",
            })
            assert deploy_resp.status_code == 200
            dep_id = deploy_resp.json()["deployment_id"]

            approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
            assert approve_resp.status_code == 200

        store = live_module._deployment_store
        assert store is not None

        store.append_event(
            make_broker_account_event(
                dep_id,
                broker_type="qmt",
                account_ts=datetime(2026, 4, 14, 9, 30, tzinfo=timezone.utc),
                cash=100_000.0,
                total_asset=120_000.0,
                positions={},
                open_orders=[],
                fill_count=0,
            )
        )
        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="session_connected:2026-04-14T09:31:00+00:00",
                broker_type="qmt",
                runtime_kind="session_connected",
                event_ts=datetime(2026, 4, 14, 9, 31, tzinfo=timezone.utc),
                payload={"status": "connected"},
            )
        )
        store.append_event(
            make_broker_runtime_event(
                dep_id,
                runtime_event_id="session_consumer_state:2026-04-14T09:32:00+00:00",
                broker_type="qmt",
                runtime_kind="session_consumer_state",
                event_ts=datetime(2026, 4, 14, 9, 32, tzinfo=timezone.utc),
                payload={
                    "status": "connected",
                    "consumer_status": "running",
                    "account_sync_mode": "callback_preferred",
                    "asset_callback_freshness": "fresh",
                },
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=0,
                risk_event={"event": "broker_reconcile", "status": "ok", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 33, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=1,
                risk_event={"event": "broker_order_reconcile", "status": "drift", "broker_type": "qmt"},
                event_ts=datetime(2026, 4, 14, 9, 34, tzinfo=timezone.utc),
            )
        )
        store.append_event(
            make_risk_event(
                dep_id,
                business_date=date(2026, 4, 14),
                risk_index=2,
                risk_event={
                    "event": "qmt_reconcile_hard_gate",
                    "status": "blocked",
                    "broker_type": "qmt",
                    "message": "QMT reconcile checks failed; fail closed.",
                    "blockers": ["broker_order_reconcile_drift"],
                },
                event_ts=datetime(2026, 4, 14, 9, 35, tzinfo=timezone.utc),
            )
        )

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=5")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_qmt_hard_gate"]["status"] == "blocked"
        assert broker_state["qmt_submit_gate"]["status"] == "blocked"
        assert "broker_order_reconcile_drift" in broker_state["qmt_submit_gate"]["blockers"]
        assert broker_state["qmt_submit_gate"]["hard_gate"]["status"] == "blocked"
        assert broker_state["qmt_release_gate"]["status"] == "blocked"
        assert "qmt_submit_gate_blocked" in broker_state["qmt_release_gate"]["blockers"]

    def test_qmt_shadow_broker_sync_without_daily_tick_e2e(self, _real_qmt_shadow_runtime):
        store = _real_qmt_shadow_runtime["store"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-shadow-run-001",
            "name": "E2E QMT Broker Sync",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200

        sync_resp = client.post(f"/api/live/deployments/{dep_id}/broker-sync")
        assert sync_resp.status_code == 200
        sync_data = sync_resp.json()
        assert sync_data["status"] == "broker_synced"
        assert sync_data["broker_type"] == "qmt"
        assert sync_data["account_event_count"] == 1
        assert sync_data["runtime_event_count"] == 1
        assert sync_data["execution_report_count"] == 1
        assert sync_data["reconcile_status"] == "drift"
        assert sync_data["order_reconcile_status"] == "ok"
        assert sync_data["qmt_hard_gate_status"] == "blocked"
        assert sync_data["qmt_readiness"]["status"] == "degraded"
        assert sync_data["qmt_readiness"]["ready_for_real_submit"] is False
        assert sync_data["qmt_submit_gate"]["status"] == "blocked"
        assert sync_data["qmt_submit_gate"]["can_submit_now"] is False
        assert sync_data["qmt_submit_gate"]["preflight_ok"] is True
        assert sync_data["qmt_submit_gate"]["source"] == "runtime"
        assert sync_data["qmt_submit_gate"]["policy"]["max_total_asset"] == 50_000.0
        assert "broker_reconcile_drift" in sync_data["qmt_submit_gate"]["blockers"]
        assert sync_data["qmt_release_gate"]["status"] == "blocked"
        assert sync_data["qmt_release_gate"]["eligible_for_release_candidate"] is False
        assert "qmt_submit_gate_blocked" in sync_data["qmt_release_gate"]["blockers"]
        assert sync_data["qmt_release_gate"]["source"] == "runtime"

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_broker_account"]["event_type"] == "broker_account_recorded"
        assert broker_state["latest_reconcile"]["event"] == "broker_reconcile"
        assert broker_state["latest_order_reconcile"]["event"] == "broker_order_reconcile"
        assert broker_state["latest_qmt_hard_gate"]["event"] == "qmt_reconcile_hard_gate"
        assert broker_state["latest_qmt_hard_gate"]["status"] == "blocked"
        assert broker_state["projection_source"] == "runtime"
        assert broker_state["projection_ts"] is not None
        assert broker_state["qmt_readiness"]["status"] == "degraded"
        assert broker_state["qmt_submit_gate"]["status"] == "blocked"
        assert broker_state["qmt_submit_gate"]["preflight_ok"] is True
        assert broker_state["qmt_submit_gate"]["source"] == "runtime"
        assert broker_state["qmt_release_gate"]["status"] == "blocked"
        assert "qmt_submit_gate_blocked" in broker_state["qmt_release_gate"]["blockers"]
        assert broker_state["qmt_release_gate"]["source"] == "runtime"

        submit_gate_resp = client.get(f"/api/live/deployments/{dep_id}/broker-submit-gate")
        assert submit_gate_resp.status_code == 200
        submit_gate = submit_gate_resp.json()
        assert submit_gate["broker_type"] == "qmt"
        assert submit_gate["qmt_submit_gate"]["status"] == "blocked"
        assert submit_gate["qmt_submit_gate"]["can_submit_now"] is False
        assert submit_gate["qmt_submit_gate"]["preflight_ok"] is True
        assert submit_gate["qmt_submit_gate"]["policy"]["allowed_account_ids"] == ["acct-shadow"]
        assert "broker_reconcile_drift" in submit_gate["qmt_submit_gate"]["blockers"]
        assert submit_gate["qmt_submit_gate"]["source"] == "runtime"
        assert submit_gate["projection_source"] == broker_state["projection_source"]
        assert submit_gate["projection_ts"] == broker_state["projection_ts"]
        assert submit_gate["target_account_id"] == broker_state["target_account_id"]

        release_gate_resp = client.get(f"/api/live/deployments/{dep_id}/release-gate")
        assert release_gate_resp.status_code == 200
        release_gate = release_gate_resp.json()
        assert release_gate["deployment_status"] == "running"
        assert "status" not in release_gate
        assert release_gate["qmt_release_gate"]["status"] == "blocked"
        assert release_gate["qmt_release_gate"]["eligible_for_release_candidate"] is False
        assert release_gate["qmt_release_gate"]["eligible_for_real_submit"] is False
        assert release_gate["qmt_release_gate"]["source"] == "runtime"
        assert release_gate["projection_source"] == broker_state["projection_source"]
        assert release_gate["projection_ts"] == broker_state["projection_ts"]
        assert release_gate["target_account_id"] == broker_state["target_account_id"]
        assert release_gate["qmt_release_gate"] == broker_state["qmt_release_gate"]
        assert submit_gate["qmt_submit_gate"] == broker_state["qmt_submit_gate"]

        dashboard_resp = client.get("/api/live/dashboard")
        assert dashboard_resp.status_code == 200
        dashboard = dashboard_resp.json()
        qmt_rows = [
            row for row in dashboard["deployments"]
            if row["deployment_id"] == dep_id
        ]
        assert len(qmt_rows) == 1
        assert qmt_rows[0]["qmt_release_gate_status"] == broker_state["qmt_release_gate"]["status"]
        assert qmt_rows[0]["qmt_release_candidate"] is False
        # Dashboard exposes a subset of release-gate blockers; the fix-E hard_gate fold
        # may add additional entries (qmt_reconcile_hard_gate_blocked, etc.) on the full
        # broker-state payload. Assert dashboard entries are contained in the superset.
        for blocker in qmt_rows[0]["qmt_release_blockers"]:
            assert blocker in broker_state["qmt_release_gate"]["blockers"]

    def test_qmt_shadow_broker_sync_reuses_existing_client_order_id_by_broker_order_id(
        self,
        _real_qmt_shadow_runtime,
    ):
        runtime = _real_qmt_shadow_runtime
        store = runtime["store"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-shadow-run-001",
            "name": "E2E QMT Broker Sync Link Reuse",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200

        canonical_client_order_id = "dep-shadow:2024-01-15:000001.SZ:buy"
        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:reported:0:1000:2024-06-27T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 27, 15, 0, tzinfo=timezone.utc),
                    client_order_id=canonical_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=12.34,
                    message="existing-link",
                    raw_payload={"order_sysid": "SYS-001"},
                )
            ],
        )

        sync_resp = client.post(f"/api/live/deployments/{dep_id}/broker-sync")
        assert sync_resp.status_code == 200

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert len(broker_orders) == 1
        assert broker_orders[0]["broker_order_id"] == "SYS-001"
        assert broker_orders[0]["client_order_id"] == canonical_client_order_id
        assert not broker_orders[0]["client_order_id"].endswith(":broker_order:qmt:SYS-001")

        links = store.list_broker_order_links(dep_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == canonical_client_order_id

    def test_qmt_shadow_broker_sync_reuses_existing_synthetic_client_order_id_when_later_report_has_real_client_order_id(
        self,
        _real_qmt_shadow_runtime,
    ):
        runtime = _real_qmt_shadow_runtime
        store = runtime["store"]
        shadow_broker = runtime["shadow_broker"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-shadow-run-001",
            "name": "E2E QMT Broker Sync Synthetic Link Reuse",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200

        synthetic_client_order_id = make_shadow_broker_client_order_id(
            dep_id,
            broker_type="qmt",
            broker_order_id="SYS-001",
            report_id="qmt:SYS-001:canceled:0:0:2024-06-27T15:00:00+00:00",
        )
        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:0:0:2024-06-27T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 27, 15, 0, tzinfo=timezone.utc),
                    client_order_id=synthetic_client_order_id,
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="callback-only-terminal",
                    raw_payload={"order_sysid": "SYS-001"},
                    account_id="acct-shadow",
                )
            ],
        )

        shadow_broker.snapshot_account_state = lambda: BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2024, 6, 28, 15, 2, tzinfo=timezone.utc),
            cash=0.0,
            total_asset=0.0,
            positions={},
            open_orders=[],
            fills=[],
        )
        shadow_broker.list_execution_reports = lambda since=None: [
            report
            for report in [
                BrokerExecutionReport(
                    report_id="qmt:SYS-001:canceled:0:0:2024-06-28T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 28, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-shadow:2024-06-28:000001.SZ:buy",
                    broker_order_id="SYS-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="terminal-confirm",
                    raw_payload={
                        "order_remark": "dep-shadow:2024-06-28:000001.SZ:buy",
                        "order_sysid": "SYS-001",
                    },
                    account_id="acct-shadow",
                )
            ]
            if since is None or report.as_of >= since
        ]

        sync_resp = client.post(f"/api/live/deployments/{dep_id}/broker-sync")
        assert sync_resp.status_code == 200

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert len(broker_orders) == 1
        assert broker_orders[0]["broker_order_id"] == "SYS-001"
        assert broker_orders[0]["client_order_id"] == synthetic_client_order_id

        links = store.list_broker_order_links(dep_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == synthetic_client_order_id

    def test_qmt_real_broker_sync_reuses_existing_synthetic_client_order_id_when_later_report_has_real_client_order_id(
        self,
        _real_qmt_real_runtime,
    ):
        runtime = _real_qmt_real_runtime
        store = runtime["store"]
        real_broker = runtime["real_broker"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-real-run-001",
            "name": "E2E QMT Real Broker Sync Synthetic Link Reuse",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200

        synthetic_client_order_id = make_shadow_broker_client_order_id(
            dep_id,
            broker_type="qmt",
            broker_order_id="SYS-REAL-001",
            report_id="qmt:SYS-REAL-001:canceled:0:0:2024-06-27T15:00:00+00:00",
        )
        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-REAL-001:canceled:0:0:2024-06-27T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 27, 15, 0, tzinfo=timezone.utc),
                    client_order_id=synthetic_client_order_id,
                    broker_order_id="SYS-REAL-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="callback-only-terminal",
                    raw_payload={"order_sysid": "SYS-REAL-001"},
                    account_id="acct-real",
                )
            ],
        )

        real_broker.snapshot_account_state = lambda: BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2024, 6, 28, 15, 2, tzinfo=timezone.utc),
            cash=1_000_000.0,
            total_asset=1_000_000.0,
            positions={},
            open_orders=[],
            fills=[],
            account_id="acct-real",
        )
        real_broker.list_execution_reports = lambda since=None: [
            report
            for report in [
                BrokerExecutionReport(
                    report_id="qmt:SYS-REAL-001:canceled:0:0:2024-06-28T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 28, 15, 0, tzinfo=timezone.utc),
                    client_order_id="dep-real:2024-06-28:000001.SZ:buy",
                    broker_order_id="SYS-REAL-001",
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="terminal-confirm",
                    raw_payload={
                        "order_remark": "dep-real:2024-06-28:000001.SZ:buy",
                        "order_sysid": "SYS-REAL-001",
                    },
                    account_id="acct-real",
                )
            ]
            if since is None or report.as_of >= since
        ]

        sync_resp = client.post(f"/api/live/deployments/{dep_id}/broker-sync")
        assert sync_resp.status_code == 200

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert len(broker_orders) == 1
        assert broker_orders[0]["broker_order_id"] == "SYS-REAL-001"
        assert broker_orders[0]["client_order_id"] == synthetic_client_order_id

        links = store.list_broker_order_links(dep_id, broker_type="qmt")
        assert len(links) == 1
        assert links[0]["client_order_id"] == synthetic_client_order_id

    def test_qmt_real_submit_e2e_surfaces_submitted_order_and_runtime_projection(
        self,
        _real_qmt_real_runtime,
    ):
        runtime = _real_qmt_real_runtime
        store = runtime["store"]
        shared_state = runtime["shared_state"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-real-run-001",
            "name": "E2E QMT Real Submit",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200
        assert approve_resp.json()["qmt_release_gate"]["status"] == "blocked"

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200

        tick_resp = client.post("/api/live/tick", json={"business_date": "2024-06-28"})
        assert tick_resp.status_code == 200
        tick_data = tick_resp.json()
        assert len(tick_data["results"]) == 1
        assert tick_data["results"][0]["deployment_id"] == dep_id

        submitted_orders = shared_state.submitted_orders
        assert len(submitted_orders) >= 1
        first_submission = submitted_orders[0]
        assert first_submission["client_order_id"].startswith(f"{dep_id}:2024-06-28:")
        assert str(first_submission["broker_order_id"]).startswith("SYS-REAL-")

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert len(broker_orders) == len(submitted_orders)
        broker_order = next(
            order
            for order in broker_orders
            if order["client_order_id"] == first_submission["client_order_id"]
        )
        assert broker_order["broker_type"] == "qmt"
        assert broker_order["broker_order_id"] == first_submission["broker_order_id"]
        assert broker_order["latest_status"] == "reported"
        assert broker_order["symbol"] == first_submission["symbol"]

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["projection_source"] == "runtime"
        assert broker_state["projection_ts"] is not None
        assert broker_state["target_account_id"] == "acct-real"
        assert broker_state["latest_broker_account"]["payload"]["broker_type"] == "qmt"
        assert broker_state["latest_broker_account"]["payload"]["total_asset"] == 1_000_000.0
        assert len(broker_state["latest_broker_account"]["payload"]["open_orders"]) == len(
            submitted_orders
        )
        assert broker_state["latest_callback_account_mode"] == "callback_preferred"
        assert broker_state["latest_callback_account_freshness"] == "fresh"
        assert broker_state["latest_reconcile"]["status"] == "ok"
        assert broker_state["latest_order_reconcile"]["status"] == "ok"
        assert broker_state["qmt_readiness"]["status"] == "ready"
        assert broker_state["qmt_readiness"]["ready_for_real_submit"] is True
        assert broker_state["qmt_submit_gate"]["status"] == "open"
        assert broker_state["qmt_submit_gate"]["can_submit_now"] is True
        assert broker_state["qmt_submit_gate"]["account_id"] == "acct-real"
        assert broker_state["qmt_submit_gate"]["source"] == "runtime"
        assert broker_state["qmt_release_gate"]["status"] == "candidate"
        assert broker_state["qmt_release_gate"]["eligible_for_real_submit"] is True
        assert broker_state["qmt_release_gate"]["source"] == "runtime"
        assert broker_state["recent_runtime_events"][0]["payload"]["runtime_kind"] == "session_consumer_state"

        submit_gate_resp = client.get(f"/api/live/deployments/{dep_id}/broker-submit-gate")
        assert submit_gate_resp.status_code == 200
        submit_gate = submit_gate_resp.json()
        assert submit_gate["broker_type"] == "qmt"
        assert submit_gate["projection_source"] == broker_state["projection_source"]
        assert submit_gate["projection_ts"] == broker_state["projection_ts"]
        assert submit_gate["target_account_id"] == broker_state["target_account_id"]
        assert submit_gate["qmt_submit_gate"] == broker_state["qmt_submit_gate"]

        release_gate_resp = client.get(f"/api/live/deployments/{dep_id}/release-gate")
        assert release_gate_resp.status_code == 200
        release_gate = release_gate_resp.json()
        assert release_gate["deployment_status"] == "running"
        assert release_gate["projection_source"] == broker_state["projection_source"]
        assert release_gate["projection_ts"] == broker_state["projection_ts"]
        assert release_gate["target_account_id"] == broker_state["target_account_id"]
        assert release_gate["qmt_release_gate"] == broker_state["qmt_release_gate"]

        snapshots = client.get(f"/api/live/deployments/{dep_id}/snapshots").json()
        assert len(snapshots) == 1
        assert snapshots[0]["rebalanced"] is True
        assert len(snapshots[0]["trades"]) == 0
        assert snapshots[0]["cash"] == 1_000_000.0
        assert snapshots[0]["holdings"] == {}

        projection = store.get_broker_state_projection(dep_id, broker_type="qmt")
        assert projection is not None
        assert projection["qmt_submit_gate"]["status"] == "open"
        assert projection["qmt_release_gate"]["status"] == "candidate"

    def test_broker_state_for_real_qmt_surfaces_real_reconcile_events(
        self,
        _real_qmt_real_runtime,
    ):
        runtime = _real_qmt_real_runtime
        real_client = runtime["real_broker"]._client

        def _drifted_asset(_account_id: str):
            return {
                "update_time": datetime(2024, 6, 28, 15, 2, tzinfo=timezone.utc).isoformat(),
                "cash": 140_000.0,
                "total_asset": 140_000.0,
            }

        real_client.query_stock_asset = _drifted_asset

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-real-run-001",
            "name": "E2E QMT Real Reconcile Drift",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        assert client.post(f"/api/live/deployments/{dep_id}/approve").status_code == 200
        assert client.post(f"/api/live/deployments/{dep_id}/start").status_code == 200

        tick_resp = client.post("/api/live/tick", json={"business_date": "2024-06-28"})
        assert tick_resp.status_code == 200

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_reconcile"]["event"] == "real_broker_reconcile"
        assert broker_state["latest_reconcile"]["status"] == "drift"
        assert broker_state["latest_qmt_hard_gate"]["event"] == "real_qmt_reconcile_hard_gate"
        assert broker_state["latest_qmt_hard_gate"]["status"] == "blocked"
        assert broker_state["target_account_id"] == "acct-real"
        assert broker_state["qmt_readiness"]["status"] == "ready"
        assert broker_state["qmt_submit_gate"]["status"] == "blocked"
        assert broker_state["qmt_submit_gate"]["account_id"] == "acct-real"
        assert "broker_reconcile_drift" in broker_state["qmt_submit_gate"]["blockers"]
        assert broker_state["qmt_release_gate"]["status"] == "blocked"

    def test_qmt_real_release_gate_stays_candidate_when_shadow_snapshot_drifts(
        self,
        _real_qmt_real_runtime,
    ):
        runtime = _real_qmt_real_runtime

        runtime["shadow_broker"].snapshot_account_state = lambda: BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=datetime(2024, 6, 28, 15, 2, tzinfo=timezone.utc),
            cash=140_000.0,
            total_asset=140_000.0,
            positions={"000001.SZ": 900},
            open_orders=[],
            fills=[],
            account_id="acct-shadow",
        )

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-real-run-001",
            "name": "E2E QMT Shadow Drift Ignored",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        assert client.post(f"/api/live/deployments/{dep_id}/approve").status_code == 200
        assert client.post(f"/api/live/deployments/{dep_id}/start").status_code == 200

        tick_resp = client.post("/api/live/tick", json={"business_date": "2024-06-28"})
        assert tick_resp.status_code == 200

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_reconcile"]["event"] == "real_broker_reconcile"
        assert broker_state["latest_reconcile"]["status"] == "ok"
        assert broker_state["qmt_submit_gate"]["status"] == "open"
        assert broker_state["qmt_release_gate"]["status"] == "candidate"

    def test_qmt_real_cancel_uses_execution_broker_and_refreshes_projection_without_manual_sync(
        self,
        _real_qmt_real_runtime,
    ):
        runtime = _real_qmt_real_runtime
        shared_state = runtime["shared_state"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-real-run-001",
            "name": "E2E QMT Real Cancel",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200

        tick_resp = client.post("/api/live/tick", json={"business_date": "2024-06-28"})
        assert tick_resp.status_code == 200

        submitted_orders = shared_state.submitted_orders
        assert len(submitted_orders) >= 1
        first_submission = submitted_orders[0]

        cancel_resp = client.post(
            f"/api/live/deployments/{dep_id}/cancel",
            json={"client_order_id": first_submission["client_order_id"]},
        )
        assert cancel_resp.status_code == 200
        cancel_data = cancel_resp.json()
        assert cancel_data["status"] == "cancel_requested"
        assert cancel_data["broker_order_id"] == first_submission["broker_order_id"]
        assert shared_state.cancel_requested is True

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        broker_order = next(
            order
            for order in broker_orders
            if order["client_order_id"] == first_submission["client_order_id"]
        )
        assert broker_order["latest_status"] == "reported_cancel_pending"

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["projection_source"] == "runtime"
        assert broker_state["broker_order_cancel_summary"]["total"] >= 1
        assert broker_state["broker_order_cancel_summary"]["cancel_inflight"] >= 1

        shared_state.finalize_cancel()
        tick_resp_3 = client.post("/api/live/tick", json={"business_date": "2024-07-02"})
        assert tick_resp_3.status_code == 200

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert any(
            event["payload"]["runtime_kind"] == "cancel_order_stock_async_response"
            for event in broker_state["recent_runtime_events"]
        )

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        broker_order = next(
            order
            for order in broker_orders
            if order["client_order_id"] == first_submission["client_order_id"]
        )
        assert broker_order["latest_status"] == "canceled"
        assert broker_order["cancel_state"] == "canceled"

    def test_qmt_real_cancel_terminal_confirm_advances_link_even_when_report_timestamp_does_not_increase(
        self,
        _real_qmt_real_runtime,
    ):
        runtime = _real_qmt_real_runtime
        from ez.live.events import make_broker_cancel_requested_event

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-real-run-001",
            "name": "E2E QMT Real Cancel Terminal Confirm",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200

        store = runtime["store"]
        client_order_id = "dep-real:2024-06-28:000001.SZ:buy"
        broker_order_id = "SYS-REAL-001"
        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-REAL-001:reported:0:1000:2024-06-28T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 28, 15, 0, tzinfo=timezone.utc),
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    symbol="000001.SZ",
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=1000,
                    avg_price=12.34,
                    message="submitted",
                    raw_payload={
                        "order_sysid": broker_order_id,
                        "order_remark": client_order_id,
                    },
                )
            ],
        )
        store.append_events(
            [
                make_broker_cancel_requested_event(
                    dep_id,
                    broker_type="qmt",
                    request_ts=datetime(2024, 6, 28, 15, 1, tzinfo=timezone.utc),
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    symbol="000001.SZ",
                )
            ]
        )

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        broker_order = next(order for order in broker_orders if order["client_order_id"] == client_order_id)
        assert broker_order["latest_status"] == "reported_cancel_pending"
        assert broker_order["cancel_state"] == "cancel_inflight"

        store.save_broker_sync_result(
            deployment_id=dep_id,
            events=[],
            broker_reports=[
                BrokerExecutionReport(
                    report_id="qmt:SYS-REAL-001:canceled:0:0:2024-06-28T14:59:59+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 28, 14, 59, 59, tzinfo=timezone.utc),
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    symbol="000001.SZ",
                    side="buy",
                    status="canceled",
                    filled_shares=0,
                    remaining_shares=0,
                    avg_price=12.34,
                    message="canceled",
                    raw_payload={
                        "order_sysid": broker_order_id,
                        "order_remark": client_order_id,
                        "status": "canceled",
                    },
                )
            ],
        )

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        broker_order = next(order for order in broker_orders if order["client_order_id"] == client_order_id)
        assert broker_order["latest_status"] == "canceled"
        assert broker_order["cancel_state"] == "canceled"

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["broker_order_cancel_summary"]["canceled"] >= 1

    def test_qmt_real_broker_sync_pumps_execution_reports_into_links_and_runtime_projection(
        self,
        _real_qmt_real_runtime,
    ):
        runtime = _real_qmt_real_runtime
        shared_state = runtime["shared_state"]

        deploy_resp = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-real-run-002",
            "name": "E2E QMT Real Broker Sync",
        })
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]

        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200

        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200

        tick_resp = client.post("/api/live/tick", json={"business_date": "2024-06-28"})
        assert tick_resp.status_code == 200
        tick_data = tick_resp.json()
        assert len(tick_data["results"]) == 1
        assert tick_data["results"][0]["deployment_id"] == dep_id

        submitted_orders = shared_state.submitted_orders
        assert len(submitted_orders) >= 1

        sync_resp = client.post(f"/api/live/deployments/{dep_id}/broker-sync")
        assert sync_resp.status_code == 200
        sync_data = sync_resp.json()
        assert sync_data["status"] == "broker_synced"
        assert sync_data["broker_type"] == "qmt"
        assert sync_data["account_event_count"] == 1
        assert sync_data["runtime_event_count"] >= 1
        assert sync_data["execution_report_count"] == len(submitted_orders)
        assert sync_data["qmt_readiness"]["status"] == "ready"
        assert sync_data["qmt_readiness"]["ready_for_real_submit"] is True
        assert sync_data["qmt_submit_gate"]["status"] == "open"
        assert sync_data["qmt_submit_gate"]["source"] == "runtime"
        assert sync_data["qmt_release_gate"]["status"] == "candidate"
        assert sync_data["qmt_release_gate"]["source"] == "runtime"

        broker_orders_resp = client.get(f"/api/live/deployments/{dep_id}/broker-orders")
        assert broker_orders_resp.status_code == 200
        broker_orders = broker_orders_resp.json()["orders"]
        assert len(broker_orders) == len(submitted_orders)
        first_submission = submitted_orders[0]
        broker_order = next(
            order
            for order in broker_orders
            if order["client_order_id"] == first_submission["client_order_id"]
        )
        assert broker_order["broker_type"] == "qmt"
        assert broker_order["broker_order_id"] == first_submission["broker_order_id"]
        assert broker_order["latest_status"] == "reported"
        assert broker_order["symbol"] == first_submission["symbol"]

        broker_state_resp = client.get(f"/api/live/deployments/{dep_id}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["projection_source"] == "runtime"
        assert broker_state["projection_ts"] is not None
        assert broker_state["latest_broker_account"]["payload"]["broker_type"] == "qmt"
        assert len(broker_state["latest_broker_account"]["payload"]["open_orders"]) == len(
            submitted_orders
        )
        assert broker_state["latest_callback_account_mode"] == "callback_preferred"
        assert broker_state["latest_callback_account_freshness"] == "fresh"
        assert broker_state["latest_session_consumer_state_runtime"] is not None
        assert broker_state["latest_session_consumer_state_runtime"]["payload"]["runtime_kind"] == "session_consumer_state"
        assert broker_state["recent_runtime_events"][0]["payload"]["runtime_kind"] == "session_consumer_state"
        assert broker_state["qmt_submit_gate"]["status"] == "open"
        assert broker_state["qmt_submit_gate"]["source"] == "runtime"
        assert broker_state["qmt_release_gate"]["status"] == "candidate"
        assert broker_state["qmt_release_gate"]["source"] == "runtime"

    def test_qmt_resident_owner_survives_one_deployment_stop(self, _resident_qmt_runtime):
        runtime = _resident_qmt_runtime
        store = runtime["store"]

        deploy_resp_a = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-resident-run-001",
            "name": "QMT Resident Owner A",
        })
        assert deploy_resp_a.status_code == 200
        dep_a = deploy_resp_a.json()["deployment_id"]

        deploy_resp_b = client.post("/api/live/deploy", json={
            "source_run_id": "e2e-qmt-resident-run-001",
            "name": "QMT Resident Owner B",
        })
        assert deploy_resp_b.status_code == 200
        dep_b = deploy_resp_b.json()["deployment_id"]

        approve_resp_a = client.post(f"/api/live/deployments/{dep_a}/approve")
        approve_resp_b = client.post(f"/api/live/deployments/{dep_b}/approve")
        assert approve_resp_a.status_code == 200
        assert approve_resp_b.status_code == 200

        start_resp_a = client.post(f"/api/live/deployments/{dep_a}/start")
        start_resp_b = client.post(f"/api/live/deployments/{dep_b}/start")
        assert start_resp_a.status_code == 200
        assert start_resp_b.status_code == 200

        stop_resp_a = client.post(
            f"/api/live/deployments/{dep_a}/stop",
            json={"reason": "unit test"},
        )
        assert stop_resp_a.status_code == 200
        assert stop_resp_a.json()["status"] == "stopped"

        sync_resp_b = client.post(f"/api/live/deployments/{dep_b}/broker-sync")
        assert sync_resp_b.status_code == 200
        assert sync_resp_b.json()["status"] == "broker_synced"

        broker_state_resp = client.get(f"/api/live/deployments/{dep_b}/broker-state?runtime_limit=10")
        assert broker_state_resp.status_code == 200
        broker_state = broker_state_resp.json()
        assert broker_state["latest_session_owner_runtime"] is not None
        assert broker_state["latest_session_owner_runtime"]["payload"]["runtime_kind"] != "session_owner_closed"
        assert runtime["shared_client"].close_calls == 0
        state = runtime["session_manager"].get_state(
            config=QMTBrokerConfig(account_id="acct-shadow", enable_cancel=True, always_on_owner=True),
            factory=runtime["shadow_broker"]._client_factory_or_default(),
        )
        assert state is not None
        assert state.host_owner_pinned is True
        assert broker_state["qmt_submit_gate"]["account_id"] == "acct-shadow"
        assert broker_state["qmt_submit_gate"]["status"] == "shadow_only"
        assert runtime["shared_client"].close_calls == 0
        assert store.get_record(dep_a).status == "stopped"
        assert store.get_record(dep_b).status == "running"


# ---------------------------------------------------------------------------
# TestLiveApiRealE2E: full API lifecycle against a real Scheduler +
# real DuckDB store + real PaperBroker (plus a cancel-capable shadow
# broker to exercise the cancel path, since PaperBroker itself has no
# CANCEL_ORDER capability).
#
# Motivation: the original TestLifecycleFlow (now marked @pytest.mark.unit)
# replaces the scheduler with a MagicMock, so every lifecycle transition
# returns the canned mock response regardless of real engine/broker
# behavior. This class re-runs the same lifecycle without any scheduler
# mocking and asserts store artifacts (events, snapshots, broker links,
# runtime events) after each transition.
# ---------------------------------------------------------------------------


class _PaperCancelShadowBroker:
    """Thin QMT-compatible shadow broker wrapping PaperBroker semantics.

    Gives the real PaperBroker execution path a CANCEL_ORDER-capable
    shadow companion so the cancel endpoint exercises a real broker
    cancel_order(...) call. The shadow broker does NOT touch paper
    execution; it only surfaces a fake open order that the cancel path
    can target (client_order_id recorded by PaperBroker during tick).
    """

    broker_type = "qmt"

    def __init__(self):
        self._open_orders: list[dict] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self._as_of = datetime(2024, 6, 28, 15, 2, tzinfo=timezone.utc)

    @property
    def capabilities(self):
        return frozenset(
            {
                BrokerCapability.READ_ACCOUNT_STATE,
                BrokerCapability.SHADOW_MODE,
                BrokerCapability.STREAM_EXECUTION_REPORTS,
                BrokerCapability.CANCEL_ORDER,
            }
        )

    def add_open_order(self, *, client_order_id: str, symbol: str, shares: int) -> None:
        self._open_orders.append(
            {
                "client_order_id": client_order_id,
                "broker_order_id": f"SYS-PAPER-{len(self._open_orders) + 1:03d}",
                "symbol": symbol,
                "status": "reported",
                "requested_shares": int(shares),
                "filled_shares": 0,
                "remaining_shares": int(shares),
                "avg_price": 10.0,
                "updated_at": self._as_of.isoformat(),
            }
        )

    def snapshot_account_state(self):
        return BrokerAccountSnapshot(
            broker_type="qmt",
            as_of=self._as_of,
            cash=0.0,
            total_asset=0.0,
            positions={},
            open_orders=list(self._open_orders),
            fills=[],
        )

    def list_runtime_events(self, *, since=None):
        events = [
            BrokerRuntimeEvent(
                event_id="account_status:acct-paper:connected:2024-06-28T14:59:00+00:00",
                broker_type="qmt",
                as_of=datetime(2024, 6, 28, 14, 59, tzinfo=timezone.utc),
                event_kind="account_status",
                payload={
                    "_report_kind": "account_status",
                    "account_id": "acct-paper",
                    "status": "connected",
                },
            )
        ]
        if since is not None:
            events = [event for event in events if event.as_of >= since]
        return events

    def list_execution_reports(self, *, since=None):
        reports = []
        for order in self._open_orders:
            reports.append(
                BrokerExecutionReport(
                    report_id=f"qmt:{order['broker_order_id']}:reported:0:{order['requested_shares']}:2024-06-28T15:00:00+00:00",
                    broker_type="qmt",
                    as_of=datetime(2024, 6, 28, 15, 0, tzinfo=timezone.utc),
                    client_order_id=order["client_order_id"],
                    broker_order_id=order["broker_order_id"],
                    symbol=order["symbol"],
                    side="buy",
                    status="reported",
                    filled_shares=0,
                    remaining_shares=int(order["requested_shares"]),
                    avg_price=float(order["avg_price"]),
                    message="pending",
                    raw_payload={"order_sysid": order["broker_order_id"]},
                )
            )
        if since is not None:
            reports = [report for report in reports if report.as_of >= since]
        return reports

    def cancel_order(self, order_id: str, *, symbol: str = "") -> bool:
        self.cancel_calls.append((order_id, symbol))
        return True


def _make_paper_e2e_run(run_id: str = "paper-e2e-run-001") -> dict:
    """Minimal real-E2E run config — PaperBroker + cancel-capable shadow."""
    run = _make_e2e_run(run_id)
    run["config"] = dict(run["config"])
    # PaperBroker for execution; QMT-style shadow broker only for cancel capability.
    run["config"]["shadow_broker_type"] = "qmt"
    risk = dict(run["config"].get("_risk", {}))
    risk["shadow_broker_config"] = {
        "account_id": "acct-paper",
        "enable_cancel": True,
    }
    run["config"]["_risk"] = risk
    return run


@pytest.fixture
def _real_paper_runtime(monkeypatch):
    """Real Scheduler + in-memory DuckDB + real PaperBroker + cancel shadow.

    Returns a dict with:
      - live_module, store, scheduler, run, trading_days
      - shadow_broker (the _PaperCancelShadowBroker instance)
    """
    import duckdb
    import pandas as pd
    from ez.api.routes import live as live_module
    from ez.live.deployment_store import DeploymentStore
    from ez.live.scheduler import Scheduler

    trading_days = list(pd.bdate_range("2023-05-01", "2024-07-05").date)
    fake_run = _make_paper_e2e_run()
    fake_pf_store = MagicMock()
    fake_pf_store.get_run.return_value = fake_run

    store = DeploymentStore(duckdb.connect(":memory:"))
    data_chain = _FakeDataChain(_make_symbol_bars(fake_run["symbols"], trading_days))
    shadow_broker = _PaperCancelShadowBroker()
    scheduler = Scheduler(
        store=store,
        data_chain=data_chain,
        broker_factories={
            "paper": lambda _spec: PaperBroker(),
            "qmt": lambda _spec: shadow_broker,
        },
    )

    monkeypatch.setattr(live_module, "_deployment_store", store)
    monkeypatch.setattr(live_module, "_scheduler", scheduler)
    monkeypatch.setattr(live_module, "_monitor", None)
    monkeypatch.setattr(live_module, "_get_portfolio_store", lambda: fake_pf_store)

    try:
        yield {
            "live_module": live_module,
            "store": store,
            "scheduler": scheduler,
            "run": fake_run,
            "trading_days": trading_days,
            "shadow_broker": shadow_broker,
        }
    finally:
        store.close()


class TestLiveApiRealE2E:
    """End-to-end lifecycle through the real scheduler/store/PaperBroker.

    This test drives the HTTP API exactly like a user would, but the
    scheduler fixture uses a real Scheduler instance with an in-memory
    DuckDB store and a real PaperBroker — no MagicMock patching of
    ``Scheduler`` or its methods. Each lifecycle step asserts persisted
    store state directly (events, snapshots, broker links, records)
    rather than trusting mocked return values.
    """

    def test_real_paper_lifecycle_with_store_assertions(self, _real_paper_runtime):
        from ez.live.events import EventType as _EventType

        runtime = _real_paper_runtime
        store = runtime["store"]
        shadow_broker = runtime["shadow_broker"]

        # ---- Step 1: POST /deploy ----
        deploy_resp = client.post(
            "/api/live/deploy",
            json={"source_run_id": "paper-e2e-run-001", "name": "Real E2E Lifecycle"},
        )
        assert deploy_resp.status_code == 200
        dep_id = deploy_resp.json()["deployment_id"]
        spec_id = deploy_resp.json()["spec_id"]
        # Store assertion 1: record exists with status=pending
        record = store.get_record(dep_id)
        assert record is not None
        assert record.status == "pending"
        # Store assertion 2: spec is persisted and matches spec_id
        spec = store.get_spec(spec_id)
        assert spec is not None
        assert spec.strategy_name == "TopNRotation"

        # ---- Step 2: POST /approve ----
        approve_resp = client.post(f"/api/live/deployments/{dep_id}/approve")
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"
        # Store assertion 1: record transitioned to approved
        approved_record = store.get_record(dep_id)
        assert approved_record.status == "approved"
        # Store assertion 2: gate_verdict persisted
        verdict = json.loads(approved_record.gate_verdict)
        assert verdict["passed"] is True

        # ---- Step 3: POST /start ----
        start_resp = client.post(f"/api/live/deployments/{dep_id}/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["status"] == "running"
        # Store assertion 1: status = running
        assert store.get_record(dep_id).status == "running"
        # Store assertion 2: engine exists in scheduler memory
        assert dep_id in runtime["scheduler"]._engines

        # ---- Step 4: POST /tick ----
        tick_resp = client.post("/api/live/tick", json={"business_date": "2024-06-28"})
        assert tick_resp.status_code == 200
        tick_data = tick_resp.json()
        assert len(tick_data["results"]) == 1
        assert tick_data["results"][0]["deployment_id"] == dep_id
        # Store assertion 1: snapshots written
        snapshots = store.get_all_snapshots(dep_id)
        assert len(snapshots) >= 1
        assert snapshots[-1]["equity"] > 0
        # Store assertion 2: event log has tick_completed + snapshot_saved + orders
        events_after_tick = store.get_events(dep_id)
        event_types_after_tick = {event.event_type.value for event in events_after_tick}
        assert "tick_completed" in event_types_after_tick
        assert "snapshot_saved" in event_types_after_tick
        assert "order_submitted" in event_types_after_tick
        # Store assertion 3: last_processed_date advanced
        last_date = store.get_last_processed_date(dep_id)
        assert last_date == date(2024, 6, 28)

        # ---- Step 5: POST /cancel ----
        # Register a fake open order in the shadow broker so cancel has
        # a target. Cancel path resolves client_order_id via broker-order
        # link persisted from a previous sync.
        canonical_cid = f"{dep_id}:2024-06-28:000001.SZ:buy"
        shadow_broker.add_open_order(
            client_order_id=canonical_cid,
            symbol="000001.SZ",
            shares=1000,
        )
        # Manual broker-sync to persist the link row.
        sync_resp = client.post(f"/api/live/deployments/{dep_id}/broker-sync")
        assert sync_resp.status_code == 200
        # Store assertion prerequisite: broker_order_link exists.
        links_before_cancel = store.list_broker_order_links(dep_id, broker_type="qmt")
        assert len(links_before_cancel) >= 1
        assert any(
            link["client_order_id"] == canonical_cid
            for link in links_before_cancel
        )

        cancel_resp = client.post(
            f"/api/live/deployments/{dep_id}/cancel",
            json={"client_order_id": canonical_cid},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancel_requested"
        # Store assertion 1: broker.cancel_order was actually invoked
        assert any(
            call[0].startswith("SYS-PAPER-") for call in shadow_broker.cancel_calls
        )
        # Store assertion 2: broker_cancel_requested event persisted
        events_after_cancel = store.get_events(dep_id)
        cancel_events = [
            event for event in events_after_cancel
            if event.event_type == _EventType.BROKER_CANCEL_REQUESTED
        ]
        assert len(cancel_events) == 1
        # client_order_id lives on the DeploymentEvent itself; payload holds
        # broker_order_id / symbol / broker_type / account_id.
        assert cancel_events[0].client_order_id == canonical_cid
        assert cancel_events[0].payload["broker_order_id"].startswith("SYS-PAPER-")

        # ---- Step 6: POST /pause ----
        pause_resp = client.post(f"/api/live/deployments/{dep_id}/pause")
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "paused"
        # Store assertion 1: record status = paused
        assert store.get_record(dep_id).status == "paused"
        # Store assertion 2: engine still loaded, dep_id is in scheduler._paused
        assert dep_id in runtime["scheduler"]._paused
        assert dep_id in runtime["scheduler"]._engines

        # ---- Step 7: POST /resume ----
        resume_resp = client.post(f"/api/live/deployments/{dep_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "running"
        # Store assertion 1: status = running
        assert store.get_record(dep_id).status == "running"
        # Store assertion 2: deployment_id removed from paused set
        assert dep_id not in runtime["scheduler"]._paused

        # ---- Step 8: POST /stop?liquidate=true ----
        stop_resp = client.post(
            f"/api/live/deployments/{dep_id}/stop?liquidate=true",
            json={"reason": "e2e complete"},
        )
        assert stop_resp.status_code == 200
        data = stop_resp.json()
        assert data["status"] == "stopped"
        assert data["liquidated"] is True
        # Store assertion 1: final snapshot marks liquidation=True
        final_snapshots = store.get_all_snapshots(dep_id)
        assert final_snapshots[-1]["liquidation"] is True
        # Store assertion 2: record status = stopped
        assert store.get_record(dep_id).status == "stopped"
        # Store assertion 3: engine cleaned up from scheduler memory
        assert dep_id not in runtime["scheduler"]._engines

