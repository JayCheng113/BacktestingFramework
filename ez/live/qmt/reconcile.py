"""Broker-state reconciliation helpers for shadow/read-only live brokers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from ez.live._utils import utc_now as _utc_now
from ez.live.broker import BrokerAccountSnapshot, BrokerExecutionReport
from ez.live.events import (
    broker_order_status_is_terminal,
    broker_order_status_rank,
    normalize_broker_order_status,
)


@dataclass(slots=True)
class PositionDrift:
    symbol: str
    local_shares: int
    broker_shares: int
    share_delta: int
    notional_delta: float


@dataclass(slots=True)
class BrokerReconciliationResult:
    broker_type: str
    compared_at: datetime
    status: str
    cash_delta: float
    total_asset_delta: float
    max_notional_drift: float
    position_drifts: list[PositionDrift]
    message: str

    @property
    def has_drift(self) -> bool:
        return self.status == "drift"


@dataclass(slots=True)
class BrokerOrderDrift:
    order_key: str
    symbol: str
    local_status: str
    broker_status: str
    reason: str


@dataclass(slots=True)
class BrokerOrderReconciliationResult:
    broker_type: str
    compared_at: datetime
    status: str
    local_open_order_count: int
    broker_open_order_count: int
    missing_local_orders: list[BrokerOrderDrift]
    missing_broker_orders: list[BrokerOrderDrift]
    status_drifts: list[BrokerOrderDrift]
    message: str

    @property
    def has_drift(self) -> bool:
        return self.status == "drift"


def reconcile_broker_snapshot(
    *,
    local_cash: float,
    local_holdings: dict[str, int],
    local_equity: float,
    prices: dict[str, float],
    broker_snapshot: BrokerAccountSnapshot,
    cash_tolerance: float = 1.0,
    notional_tolerance: float = 100.0,
    share_tolerance: int = 0,
) -> BrokerReconciliationResult:
    """Compare local ledger state against a broker account snapshot.

    This is intentionally O(number_of_symbols) and uses the latest known price
    map to estimate notional drift. It is designed for daily shadow-mode
    verification, not tick-level microstructure reconciliation.
    """
    symbols = set(local_holdings) | set(broker_snapshot.positions)
    position_drifts: list[PositionDrift] = []
    max_notional_drift = 0.0
    for symbol in sorted(symbols):
        local_shares = int(local_holdings.get(symbol, 0))
        broker_shares = int(broker_snapshot.positions.get(symbol, 0))
        share_delta = broker_shares - local_shares
        price = float(prices.get(symbol, 0.0) or 0.0)
        notional_delta = share_delta * price
        max_notional_drift = max(max_notional_drift, abs(notional_delta))
        if abs(share_delta) > share_tolerance or abs(notional_delta) > notional_tolerance:
            position_drifts.append(
                PositionDrift(
                    symbol=symbol,
                    local_shares=local_shares,
                    broker_shares=broker_shares,
                    share_delta=share_delta,
                    notional_delta=notional_delta,
                )
            )

    broker_position_value = sum(
        int(shares) * float(prices.get(symbol, 0.0) or 0.0)
        for symbol, shares in broker_snapshot.positions.items()
    )
    broker_equity_estimate = float(broker_snapshot.cash) + broker_position_value
    cash_delta = float(broker_snapshot.cash) - float(local_cash)
    total_asset_delta = broker_equity_estimate - float(local_equity)

    status = "ok"
    message = "Broker snapshot matches local ledger within tolerance."
    if abs(cash_delta) > cash_tolerance or position_drifts:
        status = "drift"
        message = "Broker snapshot drift exceeds configured tolerances."

    return BrokerReconciliationResult(
        broker_type=broker_snapshot.broker_type,
        compared_at=broker_snapshot.as_of,
        status=status,
        cash_delta=cash_delta,
        total_asset_delta=total_asset_delta,
        max_notional_drift=max_notional_drift,
        position_drifts=position_drifts,
        message=message,
    )


_TERMINAL_ORDER_STATUSES = frozenset(
    {
        "filled",
        "canceled",
        "partially_canceled",
        "rejected",
        "junk",
    }
)


def _order_key_from_mapping(order: dict) -> str:
    broker_order_id = str(order.get("broker_order_id", "") or "")
    if broker_order_id:
        return broker_order_id
    client_order_id = str(order.get("client_order_id", "") or "")
    if client_order_id:
        return client_order_id
    return ""


def _comparable_open_status(status: str) -> str:
    normalized = normalize_broker_order_status(status)
    if normalized == "reported_cancel_pending":
        return "reported"
    if normalized == "partially_filled_cancel_pending":
        return "partially_filled"
    return normalized


def reconcile_broker_orders(
    *,
    broker_snapshot: BrokerAccountSnapshot,
    local_order_links: list[dict],
    broker_reports: list[BrokerExecutionReport] | None = None,
) -> BrokerOrderReconciliationResult:
    """Compare tracked local broker-order links against broker open orders.

    This intentionally stays O(number_of_orders) and treats the broker snapshot
    as the source of truth for currently open orders. Local links in terminal
    states are ignored; only potentially live orders are reconciled.
    """

    latest_reports_by_key: dict[str, dict[str, object]] = {}
    for report in broker_reports or []:
        normalized_status = normalize_broker_order_status(
            report.status,
            filled_shares=report.filled_shares,
            remaining_shares=report.remaining_shares,
        )
        report_keys = []
        for candidate in (report.broker_order_id, report.client_order_id):
            key = str(candidate or "").strip()
            if key and key not in report_keys:
                report_keys.append(key)
        if not report_keys:
            continue
        current_record = {
            "report": report,
            "status": normalized_status,
            "rank": broker_order_status_rank(normalized_status),
        }
        for key in report_keys:
            current = latest_reports_by_key.get(key)
            if current is None:
                latest_reports_by_key[key] = current_record
                continue
            current_report = current["report"]
            current_rank = int(current["rank"])
            incoming_rank = int(current_record["rank"])
            if incoming_rank > current_rank or (
                incoming_rank == current_rank and report.as_of >= current_report.as_of
            ):
                latest_reports_by_key[key] = current_record

    broker_open_orders: list[dict] = []
    for raw_order in broker_snapshot.open_orders or []:
        order = dict(raw_order)
        key = _order_key_from_mapping(order)
        latest_record = latest_reports_by_key.get(key)
        if latest_record is not None:
            latest_report = latest_record["report"]
            latest_status = str(latest_record["status"] or "")
            order["status"] = latest_status
            if latest_report.client_order_id and not order.get("client_order_id"):
                order["client_order_id"] = latest_report.client_order_id
            if latest_report.broker_order_id and not order.get("broker_order_id"):
                order["broker_order_id"] = latest_report.broker_order_id
            if latest_report.symbol and not order.get("symbol"):
                order["symbol"] = latest_report.symbol
        normalized_status = normalize_broker_order_status(
            str(order.get("status", "") or ""),
            filled_shares=int(order.get("filled_shares", 0) or 0),
            remaining_shares=int(order.get("remaining_shares", 0) or 0),
        )
        order["status"] = normalized_status
        if broker_order_status_is_terminal(normalized_status):
            continue
        broker_open_orders.append(order)
    local_open_links = []
    for raw_link in local_order_links or []:
        link = dict(raw_link)
        key = str(link.get("broker_order_id", "") or "") or str(
            link.get("client_order_id", "") or ""
        )
        latest_record = latest_reports_by_key.get(key)
        if latest_record is not None:
            latest_report = latest_record["report"]
            latest_status = str(latest_record["status"] or "")
            if latest_report.client_order_id and not link.get("client_order_id"):
                link["client_order_id"] = latest_report.client_order_id
            if latest_report.broker_order_id and not link.get("broker_order_id"):
                link["broker_order_id"] = latest_report.broker_order_id
            if latest_report.symbol and not link.get("symbol"):
                link["symbol"] = latest_report.symbol
            link["latest_report_id"] = latest_report.report_id
            link["last_report_ts"] = latest_report.as_of
            link["latest_status"] = latest_status
        normalized_link_status = normalize_broker_order_status(
            str(link.get("latest_status", "") or "")
        )
        link["latest_status"] = normalized_link_status
        if broker_order_status_is_terminal(normalized_link_status):
            continue
        local_open_links.append(link)

    broker_by_key = {
        key: order
        for order in broker_open_orders
        if (key := _order_key_from_mapping(order))
    }
    local_by_key = {}
    for link in local_open_links:
        key = str(link.get("broker_order_id", "") or "") or str(
            link.get("client_order_id", "") or ""
        )
        if key:
            local_by_key[key] = link

    missing_local_orders: list[BrokerOrderDrift] = []
    for key, order in broker_by_key.items():
        if key in local_by_key:
            continue
        missing_local_orders.append(
            BrokerOrderDrift(
                order_key=key,
                symbol=str(order.get("symbol", "") or ""),
                local_status="missing",
                broker_status=str(order.get("status", "") or ""),
                reason="broker_open_order_missing_local_link",
            )
        )

    missing_broker_orders: list[BrokerOrderDrift] = []
    for key, link in local_by_key.items():
        if key in broker_by_key:
            continue
        missing_broker_orders.append(
            BrokerOrderDrift(
                order_key=key,
                symbol=str(link.get("symbol", "") or ""),
                local_status=str(link.get("latest_status", "") or ""),
                broker_status="missing",
                reason="local_open_link_missing_broker_order",
            )
        )

    status_drifts: list[BrokerOrderDrift] = []
    for key in sorted(set(local_by_key) & set(broker_by_key)):
        local_status = _comparable_open_status(
            str(local_by_key[key].get("latest_status", "") or "")
        )
        broker_status = _comparable_open_status(
            str(broker_by_key[key].get("status", "") or "")
        )
        if local_status and broker_status and local_status != broker_status:
            status_drifts.append(
                BrokerOrderDrift(
                    order_key=key,
                    symbol=str(
                        broker_by_key[key].get("symbol", "")
                        or local_by_key[key].get("symbol", "")
                        or ""
                    ),
                    local_status=local_status,
                    broker_status=broker_status,
                    reason="order_status_mismatch",
                )
            )

    status = "ok"
    message = "Broker open orders match local broker-order links."
    if missing_local_orders or missing_broker_orders or status_drifts:
        status = "drift"
        message = "Broker open orders drift from local broker-order links."

    return BrokerOrderReconciliationResult(
        broker_type=broker_snapshot.broker_type,
        compared_at=broker_snapshot.as_of,
        status=status,
        local_open_order_count=len(local_by_key),
        broker_open_order_count=len(broker_by_key),
        missing_local_orders=missing_local_orders,
        missing_broker_orders=missing_broker_orders,
        status_drifts=status_drifts,
        message=message,
    )


# ---------------------------------------------------------------------------
# V3.3.44 — Position-only reconcile (holdings precision, independent from
# account/cash drift so scheduler can fail closed on position drift even
# when cash is within tolerance).
# ---------------------------------------------------------------------------


def _broker_type_from_positions(
    broker_positions: list[dict[str, Any]] | None,
    fallback: str,
) -> str:
    for pos in broker_positions or []:
        broker_type = str(pos.get("broker_type", "") or "").strip()
        if broker_type:
            return broker_type
    return fallback


def reconcile_broker_positions(
    *,
    local_holdings: dict[str, int],
    broker_positions: list[dict[str, Any]],
    share_tolerance: int = 0,
    broker_type: str = "qmt",
    compared_at: datetime | None = None,
) -> BrokerReconciliationResult:
    """Independent position reconcile: compare local holdings vs broker positions.

    Unlike ``reconcile_broker_snapshot`` this path does not compare cash or
    total asset; the scheduler persists the result alongside the account
    reconcile so position drift can fail closed even when cash is within
    tolerance. Each ``broker_positions`` entry is expected to carry the
    normalized xtquant ``XtPosition`` fields:

    - ``symbol`` (``stock_code`` alias accepted)
    - ``volume`` (total position)
    - ``can_use_volume`` (available, i.e. not T+1 frozen / not on-road)
    - ``frozen_volume`` (explicit freeze, optional)
    - ``on_road_volume`` (rights in transit, optional)

    Drift reasons encode the specific gap so the runtime gate / monitor can
    distinguish a pure share mismatch from a T+1 freeze or rights-in-transit
    situation.
    """
    compared_at = compared_at or _utc_now()
    effective_broker_type = _broker_type_from_positions(
        broker_positions, broker_type
    )
    broker_by_symbol: dict[str, dict[str, int]] = {}
    for pos in broker_positions or []:
        raw_symbol = pos.get("symbol") or pos.get("stock_code") or ""
        symbol = str(raw_symbol or "").strip()
        if not symbol:
            continue
        volume = int(pos.get("volume", 0) or 0)
        can_use = int(
            pos.get("can_use_volume", pos.get("available_volume", volume))
            or 0
        )
        frozen = int(pos.get("frozen_volume", 0) or 0)
        on_road = int(pos.get("on_road_volume", 0) or 0)
        broker_by_symbol[symbol] = {
            "volume": volume,
            "can_use_volume": can_use,
            "frozen_volume": frozen,
            "on_road_volume": on_road,
        }

    position_drifts: list[PositionDrift] = []
    symbols = set(local_holdings) | set(broker_by_symbol)
    for symbol in sorted(symbols):
        local_shares = int(local_holdings.get(symbol, 0))
        broker_entry = broker_by_symbol.get(symbol, {})
        broker_shares = int(broker_entry.get("volume", 0))
        share_delta = broker_shares - local_shares
        if abs(share_delta) <= share_tolerance:
            continue
        position_drifts.append(
            PositionDrift(
                symbol=symbol,
                local_shares=local_shares,
                broker_shares=broker_shares,
                share_delta=share_delta,
                notional_delta=0.0,
            )
        )

    status = "drift" if position_drifts else "ok"
    message = (
        "Broker positions match local holdings within tolerance."
        if status == "ok"
        else "Broker positions drift from local holdings."
    )
    return BrokerReconciliationResult(
        broker_type=effective_broker_type,
        compared_at=compared_at,
        status=status,
        cash_delta=0.0,
        total_asset_delta=0.0,
        max_notional_drift=0.0,
        position_drifts=position_drifts,
        message=message,
    )


# ---------------------------------------------------------------------------
# V3.3.44 — Intraday trade reconcile
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TradeDrift:
    broker_trade_id: str
    symbol: str
    side: str
    broker_volume: int
    local_volume: int
    volume_delta: int
    reason: str  # "missing_local" | "missing_broker" | "volume_mismatch" | "price_mismatch"


@dataclass(slots=True)
class BrokerTradeReconciliationResult:
    broker_type: str
    compared_at: datetime
    status: str  # "ok" | "drift"
    business_date: date
    broker_trade_count: int
    local_trade_count: int
    trade_drifts: list[TradeDrift]
    message: str

    @property
    def has_drift(self) -> bool:
        return self.status == "drift"


def _normalize_trade_side(raw: Any) -> str:
    side = str(raw or "").strip().lower()
    if side in {"buy", "b", "23", "long"}:
        return "buy"
    if side in {"sell", "s", "24", "short"}:
        return "sell"
    return side


def reconcile_broker_trades(
    *,
    local_trades: list[dict[str, Any]],
    broker_trades: list[dict[str, Any]],
    business_date: date,
    broker_type: str = "qmt",
    price_tolerance: float = 0.01,
    share_tolerance: int = 0,
    compared_at: datetime | None = None,
) -> BrokerTradeReconciliationResult:
    """Intraday trade reconcile: broker fills vs local ledger fills.

    Aggregates both sides by ``(symbol, side)`` for the given ``business_date``
    and reports drift when per-bucket volumes or volume-weighted prices do not
    match within tolerance. ``broker_trades`` is expected to be pre-normalized
    by the client into::

        {
            "traded_id": "...",
            "symbol": "000001.SZ",
            "side": "buy" | "sell",
            "shares": int,
            "price": float,
            "amount": float (optional),
        }

    Local fills come from the append-only ledger and use
    ``{symbol, side, shares, price, ...}``.

    Drift reasons:
    - ``missing_local``   — broker reports a bucket not present locally
    - ``missing_broker``  — local reports a bucket not present on broker side
    - ``volume_mismatch`` — both sides report the bucket but shares differ
    - ``price_mismatch``  — both sides agree on shares but VWAP differs
    """
    compared_at = compared_at or _utc_now()

    def _aggregate(
        trades: list[dict[str, Any]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        aggregated: dict[tuple[str, str], dict[str, Any]] = {}
        for trade in trades or []:
            raw_symbol = trade.get("symbol") or trade.get("stock_code") or ""
            symbol = str(raw_symbol or "").strip()
            if not symbol:
                continue
            side = _normalize_trade_side(trade.get("side"))
            if side not in {"buy", "sell"}:
                continue
            shares = int(trade.get("shares", trade.get("traded_volume", 0)) or 0)
            if shares <= 0:
                continue
            price = float(trade.get("price", trade.get("traded_price", 0.0)) or 0.0)
            traded_id = str(
                trade.get("traded_id")
                or trade.get("broker_trade_id")
                or ""
            )
            key = (symbol, side)
            bucket = aggregated.setdefault(
                key,
                {
                    "symbol": symbol,
                    "side": side,
                    "shares": 0,
                    "notional": 0.0,
                    "traded_ids": [],
                },
            )
            bucket["shares"] += shares
            bucket["notional"] += price * shares
            if traded_id:
                bucket["traded_ids"].append(traded_id)
        return aggregated

    broker_buckets = _aggregate(broker_trades)
    local_buckets = _aggregate(local_trades)

    broker_trade_count = sum(
        1
        for t in broker_trades or []
        if str(t.get("symbol") or t.get("stock_code") or "").strip()
        and _normalize_trade_side(t.get("side")) in {"buy", "sell"}
        and int(t.get("shares", t.get("traded_volume", 0)) or 0) > 0
    )
    local_trade_count = sum(
        1
        for t in local_trades or []
        if str(t.get("symbol") or t.get("stock_code") or "").strip()
        and _normalize_trade_side(t.get("side")) in {"buy", "sell"}
        and int(t.get("shares", t.get("traded_volume", 0)) or 0) > 0
    )

    drifts: list[TradeDrift] = []
    all_keys = set(broker_buckets) | set(local_buckets)
    for key in sorted(all_keys):
        symbol, side = key
        broker_bucket = broker_buckets.get(key)
        local_bucket = local_buckets.get(key)
        if broker_bucket is None:
            # Local has trades the broker does not know about.
            drifts.append(
                TradeDrift(
                    broker_trade_id="",
                    symbol=symbol,
                    side=side,
                    broker_volume=0,
                    local_volume=int(local_bucket["shares"]),
                    volume_delta=-int(local_bucket["shares"]),
                    reason="missing_broker",
                )
            )
            continue
        if local_bucket is None:
            broker_traded_ids = broker_bucket.get("traded_ids") or []
            drifts.append(
                TradeDrift(
                    broker_trade_id=str(broker_traded_ids[0])
                    if broker_traded_ids
                    else "",
                    symbol=symbol,
                    side=side,
                    broker_volume=int(broker_bucket["shares"]),
                    local_volume=0,
                    volume_delta=int(broker_bucket["shares"]),
                    reason="missing_local",
                )
            )
            continue
        broker_shares = int(broker_bucket["shares"])
        local_shares = int(local_bucket["shares"])
        volume_delta = broker_shares - local_shares
        if abs(volume_delta) > share_tolerance:
            broker_traded_ids = broker_bucket.get("traded_ids") or []
            drifts.append(
                TradeDrift(
                    broker_trade_id=str(broker_traded_ids[0])
                    if broker_traded_ids
                    else "",
                    symbol=symbol,
                    side=side,
                    broker_volume=broker_shares,
                    local_volume=local_shares,
                    volume_delta=volume_delta,
                    reason="volume_mismatch",
                )
            )
            continue
        # Volumes agree — now compare VWAP for the bucket.
        denom_b = broker_shares or 1
        denom_l = local_shares or 1
        broker_vwap = float(broker_bucket["notional"]) / denom_b
        local_vwap = float(local_bucket["notional"]) / denom_l
        if abs(broker_vwap - local_vwap) > price_tolerance:
            broker_traded_ids = broker_bucket.get("traded_ids") or []
            drifts.append(
                TradeDrift(
                    broker_trade_id=str(broker_traded_ids[0])
                    if broker_traded_ids
                    else "",
                    symbol=symbol,
                    side=side,
                    broker_volume=broker_shares,
                    local_volume=local_shares,
                    volume_delta=0,
                    reason="price_mismatch",
                )
            )

    status = "drift" if drifts else "ok"
    message = (
        "Broker trades match local fills within tolerance."
        if status == "ok"
        else "Broker trades drift from local fills."
    )
    return BrokerTradeReconciliationResult(
        broker_type=broker_type,
        compared_at=compared_at,
        status=status,
        business_date=business_date,
        broker_trade_count=broker_trade_count,
        local_trade_count=local_trade_count,
        trade_drifts=drifts,
        message=message,
    )
