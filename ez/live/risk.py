"""Pre-trade risk checks for live OMS order intents."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ez.live._utils import (
    positive_or_none as _positive_or_none,
    fraction_or_none as _fraction_or_none,
)
from ez.live.capital_policy import (
    CapitalPolicyEngine,
    CapitalRejectReason,
)
from ez.live.events import Order


@dataclass(slots=True)
class PreTradeRiskConfig:
    kill_switch: bool = False
    max_order_notional: float | None = None
    max_position_weight: float | None = None
    max_daily_turnover: float | None = None
    max_concentration: float | None = None
    max_gross_exposure: float | None = None
    runtime_allocation_cap: float | None = None

    @classmethod
    def from_params(cls, params: dict[str, Any] | None) -> "PreTradeRiskConfig":
        params = params or {}
        return cls(
            kill_switch=bool(params.get("kill_switch", False)),
            max_order_notional=_positive_or_none(params.get("max_order_notional")),
            max_position_weight=_positive_or_none(params.get("max_position_weight")),
            max_daily_turnover=_positive_or_none(params.get("max_daily_turnover")),
            max_concentration=_positive_or_none(params.get("max_concentration")),
            max_gross_exposure=_positive_or_none(params.get("max_gross_exposure")),
            runtime_allocation_cap=_fraction_or_none(params.get("runtime_allocation_cap")),
        )


@dataclass(slots=True)
class RiskFailure:
    code: str
    rule: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RejectedOrder:
    order: Order
    failure: RiskFailure

    @property
    def reason(self) -> str:
        return self.failure.code

    @property
    def rule(self) -> str:
        return self.failure.rule

    @property
    def message(self) -> str:
        return self.failure.message

    @property
    def details(self) -> dict[str, Any]:
        return self.failure.details


@dataclass(slots=True)
class PreTradeRiskDecision:
    accepted_orders: list[Order]
    rejected_orders: list[RejectedOrder]
    risk_events: list[dict[str, Any]]


class PreTradeRiskEngine:
    """Evaluate order intents before they enter execution."""

    def __init__(
        self,
        config: PreTradeRiskConfig | None = None,
        *,
        capital_policy: CapitalPolicyEngine | None = None,
        broker_type: str = "paper",
    ):
        self.config = config or PreTradeRiskConfig()
        self.capital_policy = capital_policy
        self.broker_type = broker_type

    def evaluate_orders(
        self,
        *,
        business_date: date,
        orders: list[Order],
        holdings: dict[str, int],
        prices: dict[str, float],
        equity: float,
    ) -> PreTradeRiskDecision:
        accepted_orders: list[Order] = []
        rejected_orders: list[RejectedOrder] = []
        risk_events: list[dict[str, Any]] = []
        projected_holdings = {sym: int(shares) for sym, shares in holdings.items()}
        cumulative_notional = 0.0

        for order in orders:
            price = float(prices.get(order.symbol, 0.0) or 0.0)
            notional = float(order.shares) * price
            delta = order.shares if order.side == "buy" else -order.shares
            projected_after = _apply_delta(
                projected_holdings, symbol=order.symbol, delta=delta
            )
            metrics = _portfolio_metrics(projected_after, prices=prices, equity=equity)

            # Capital policy runs BEFORE all existing rules. It owns the
            # kill-switch / stage / per-stage caps — those are coarser than
            # the fine-grained notional/weight rules below and must fail
            # closed first for audit clarity.
            capital_failure = self._capital_policy_failure(
                order=order,
                price=price,
                notional=notional,
                projected_holdings=projected_holdings,
                cumulative_notional=cumulative_notional,
                prices=prices,
            )
            if capital_failure is not None:
                rejected_orders.append(
                    RejectedOrder(order=order, failure=capital_failure)
                )
                risk_events.append(
                    {
                        "date": business_date.isoformat(),
                        "event": "pretrade_reject",
                        "rule": capital_failure.rule,
                        "reason": capital_failure.code,
                        "message": capital_failure.message,
                        "symbol": order.symbol,
                        "side": order.side,
                        "shares": int(order.shares),
                        "notional": notional,
                        "details": capital_failure.details,
                    }
                )
                continue

            failure = self._reject_failure(
                order=order,
                price=price,
                notional=notional,
                equity=equity,
                cumulative_notional=cumulative_notional,
                projected_metrics=metrics,
            )
            if failure is not None:
                rejected_orders.append(RejectedOrder(order=order, failure=failure))
                risk_events.append(
                    {
                        "date": business_date.isoformat(),
                        "event": "pretrade_reject",
                        "rule": failure.rule,
                        "reason": failure.code,
                        "message": failure.message,
                        "symbol": order.symbol,
                        "side": order.side,
                        "shares": int(order.shares),
                        "notional": notional,
                        "details": failure.details,
                    }
                )
                continue

            accepted_orders.append(order)
            cumulative_notional += notional
            projected_holdings = projected_after

        return PreTradeRiskDecision(
            accepted_orders=accepted_orders,
            rejected_orders=rejected_orders,
            risk_events=risk_events,
        )

    def _capital_policy_failure(
        self,
        *,
        order: Order,
        price: float,
        notional: float,
        projected_holdings: dict[str, int],
        cumulative_notional: float,
        prices: dict[str, float],
    ) -> RiskFailure | None:
        """Run the stage-based capital policy (if configured) for `order`.

        Uses the CURRENT holdings snapshot (pre-order) because the capital
        policy's position/exposure caps are stated in terms of the state
        BEFORE this order is applied; the policy then projects its own
        post-order values internally.
        """
        policy = self.capital_policy
        if policy is None:
            return None

        current_position_value = float(
            projected_holdings.get(order.symbol, 0)
        ) * float(price)
        current_total_exposure = 0.0
        for sym, shares in projected_holdings.items():
            p = float(prices.get(sym, 0.0) or 0.0)
            if shares <= 0 or p <= 0:
                continue
            current_total_exposure += float(shares) * p

        reject: CapitalRejectReason | None = policy.check_order(
            symbol=order.symbol,
            side=order.side,
            notional=float(notional),
            current_position_value=current_position_value,
            current_total_exposure=current_total_exposure,
            cumulative_day_notional=float(cumulative_notional),
            broker_type=self.broker_type,
        )
        if reject is None:
            return None

        # Map capital rule onto the shared RiskFailure envelope so existing
        # event consumers (risk_events, OMS event payloads) continue to
        # work without knowing about capital_policy specifics.
        return RiskFailure(
            code=f"risk:capital_stage_{reject.stage}_{reject.rule}",
            rule=reject.rule,
            message=reject.message,
            details=dict(reject.details),
        )

    def _reject_failure(
        self,
        *,
        order: Order,
        price: float,
        notional: float,
        equity: float,
        cumulative_notional: float,
        projected_metrics: dict[str, Any],
    ) -> RiskFailure | None:
        if self.config.kill_switch:
            return _failure(
                "kill_switch",
                "Kill switch is active; all new orders are blocked.",
            )

        max_order_notional = self.config.max_order_notional
        if max_order_notional is not None and notional > max_order_notional + 1e-9:
            return _failure(
                "max_order_notional",
                "Order notional exceeds the configured cap.",
                details={
                    "order_notional": notional,
                    "max_order_notional": max_order_notional,
                },
            )

        max_daily_turnover = self.config.max_daily_turnover
        if (
            max_daily_turnover is not None
            and equity > 0
            and cumulative_notional + notional > equity * max_daily_turnover + 1e-9
        ):
            return _failure(
                "max_daily_turnover",
                "Projected daily turnover would exceed the configured limit.",
                details={
                    "projected_turnover": (cumulative_notional + notional) / equity,
                    "max_daily_turnover": max_daily_turnover,
                },
            )

        if price <= 0 or equity <= 0:
            return None

        # max_position_weight only applies to buy orders. Sells can only reduce
        # the target symbol's own weight, so they cannot violate this cap for
        # the symbol being traded. (If another symbol becomes the projected
        # concentration outlier, the max_concentration check below catches it.)
        if order.side == "buy":
            max_position_weight = self.config.max_position_weight
            symbol_weight = float(projected_metrics["weights"].get(order.symbol, 0.0))
            if (
                max_position_weight is not None
                and symbol_weight > max_position_weight + 1e-9
            ):
                return _failure(
                    "max_position_weight",
                    "Projected position weight exceeds the configured per-symbol limit.",
                    details={
                        "projected_symbol_weight": symbol_weight,
                        "max_position_weight": max_position_weight,
                    },
                )

        # max_concentration applies to both sides: a sell can push the OTHER
        # remaining holding's share of total equity above the cap, so sells
        # must be evaluated against projected portfolio metrics too.
        max_concentration = self.config.max_concentration
        max_weight = float(projected_metrics["max_weight"])
        max_weight_symbol = str(projected_metrics["max_weight_symbol"])
        if max_concentration is not None and max_weight > max_concentration + 1e-9:
            return _failure(
                "max_concentration",
                "Projected portfolio concentration exceeds the configured cap.",
                details={
                    "projected_max_weight": max_weight,
                    "max_concentration": max_concentration,
                    "max_weight_symbol": max_weight_symbol,
                },
            )

        # max_gross_exposure is a leverage cap; sells only reduce it, so it
        # matters mainly on the buy side. We still compute both sides' gross
        # exposure so audit payloads stay consistent; a sell will naturally
        # pass this check.
        if order.side == "buy":
            max_gross_exposure = self.config.max_gross_exposure
            gross_exposure = float(projected_metrics["gross_exposure"])
            if (
                max_gross_exposure is not None
                and gross_exposure > max_gross_exposure + 1e-9
            ):
                return _failure(
                    "max_gross_exposure",
                    "Projected gross exposure exceeds the configured leverage cap.",
                    details={
                        "projected_gross_exposure": gross_exposure,
                        "max_gross_exposure": max_gross_exposure,
                    },
                )

        return None


def _apply_delta(
    holdings: dict[str, int],
    *,
    symbol: str,
    delta: int,
) -> dict[str, int]:
    next_holdings = {sym: int(shares) for sym, shares in holdings.items()}
    next_shares = next_holdings.get(symbol, 0) + int(delta)
    if next_shares > 0:
        next_holdings[symbol] = next_shares
    else:
        next_holdings.pop(symbol, None)
    return next_holdings


def _portfolio_metrics(
    holdings: dict[str, int],
    *,
    prices: dict[str, float],
    equity: float,
) -> dict[str, Any]:
    if equity <= 0:
        return {
            "gross_exposure": 0.0,
            "max_weight": 0.0,
            "max_weight_symbol": "",
            "weights": {},
        }

    weights: dict[str, float] = {}
    gross_exposure = 0.0
    for symbol, shares in holdings.items():
        price = float(prices.get(symbol, 0.0) or 0.0)
        if shares <= 0 or price <= 0:
            continue
        weight = (shares * price) / equity
        if weight <= 0:
            continue
        weights[symbol] = weight
        gross_exposure += weight

    max_weight_symbol = max(weights, key=weights.get, default="")
    max_weight = weights.get(max_weight_symbol, 0.0)
    return {
        "gross_exposure": gross_exposure,
        "max_weight": max_weight,
        "max_weight_symbol": max_weight_symbol,
        "weights": weights,
    }


def _failure(rule: str, message: str, details: dict[str, Any] | None = None) -> RiskFailure:
    return RiskFailure(
        code=f"risk:{rule}",
        rule=rule,
        message=message,
        details=details or {},
    )


