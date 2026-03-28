"""Order matching abstraction.

V1: SimpleMatcher — instant fill at given price, proportional commission.
V2.1: SlippageMatcher — adds market impact model.
V2.2: EventDrivenMatcher — tick-level with partial fills.

All implementations share the same Matcher ABC so the engine is agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FillResult:
    """Result of a single fill attempt."""

    shares: float
    fill_price: float
    commission: float
    net_amount: float  # cash delta: negative for buy, positive for sell


class Matcher(ABC):
    """ABC for order matching. Engine delegates all fill logic here."""

    @abstractmethod
    def fill_buy(self, price: float, amount: float) -> FillResult:
        """Fill a buy order.

        Args:
            price: execution price per share.
            amount: total cash to invest (before commission).

        Returns:
            FillResult with shares acquired and commission paid.
            If commission >= amount, returns zero-fill (skip).
        """

    @abstractmethod
    def fill_sell(self, price: float, shares: float) -> FillResult:
        """Fill a sell order.

        Args:
            price: execution price per share.
            shares: number of shares to sell.

        Returns:
            FillResult with cash received after commission.
        """


class SimpleMatcher(Matcher):
    """Instant fill at given price with proportional commission.

    Commission = max(trade_value * rate, min_commission).
    Buy: commission capped — skip if comm >= amount.
    Sell: commission capped at sell value to prevent negative cash.
    """

    def __init__(
        self, commission_rate: float = 0.0003, min_commission: float = 5.0
    ) -> None:
        self._rate = commission_rate
        self._min_comm = min_commission

    def fill_buy(self, price: float, amount: float) -> FillResult:
        if amount <= 0 or price <= 0:
            return FillResult(shares=0, fill_price=price, commission=0, net_amount=0)

        comm = max(amount * self._rate, self._min_comm)
        if comm >= amount:
            return FillResult(shares=0, fill_price=price, commission=0, net_amount=0)

        shares = (amount - comm) / price
        return FillResult(
            shares=shares,
            fill_price=price,
            commission=comm,
            net_amount=-amount,
        )

    def fill_sell(self, price: float, shares: float) -> FillResult:
        if shares <= 0 or price <= 0:
            return FillResult(shares=0, fill_price=price, commission=0, net_amount=0)

        value = shares * price
        comm = max(value * self._rate, self._min_comm)
        if comm > value:
            comm = value  # cap commission at sell value
        return FillResult(
            shares=shares,
            fill_price=price,
            commission=comm,
            net_amount=value - comm,
        )
