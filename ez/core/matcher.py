"""Order matching abstraction.

SimpleMatcher — instant fill, no slippage (V1 default).
SlippageMatcher — adds configurable market impact (V2.2).

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
        if commission_rate < 0 or min_commission < 0:
            raise ValueError("commission_rate and min_commission must be >= 0")
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
            comm = value
        return FillResult(
            shares=shares,
            fill_price=price,
            commission=comm,
            net_amount=value - comm,
        )


class SlippageMatcher(Matcher):
    """Fill with configurable slippage + commission.

    Slippage models market impact: buying pushes price up, selling pushes price down.
      buy fill_price  = price * (1 + slippage_rate)
      sell fill_price = price * (1 - slippage_rate)

    Commission: on buys, applied on input cash amount; on sells, applied on
    slipped execution value (shares * fill_price).

    Args:
        slippage_rate: fraction of price impact (e.g., 0.001 = 0.1% = 万一).
        commission_rate: fraction of trade value as commission.
        min_commission: minimum commission per trade.
    """

    def __init__(
        self,
        slippage_rate: float = 0.001,
        commission_rate: float = 0.0003,
        min_commission: float = 5.0,
    ) -> None:
        if slippage_rate < 0 or commission_rate < 0 or min_commission < 0:
            raise ValueError("slippage_rate, commission_rate, min_commission must be >= 0")
        self._slip = slippage_rate
        self._rate = commission_rate
        self._min_comm = min_commission

    def fill_buy(self, price: float, amount: float) -> FillResult:
        if amount <= 0 or price <= 0:
            return FillResult(shares=0, fill_price=price, commission=0, net_amount=0)

        fill_price = price * (1 + self._slip)
        comm = max(amount * self._rate, self._min_comm)
        if comm >= amount:
            return FillResult(shares=0, fill_price=fill_price, commission=0, net_amount=0)

        shares = (amount - comm) / fill_price
        return FillResult(
            shares=shares,
            fill_price=fill_price,
            commission=comm,
            net_amount=-amount,
        )

    def fill_sell(self, price: float, shares: float) -> FillResult:
        if shares <= 0 or price <= 0:
            return FillResult(shares=0, fill_price=price, commission=0, net_amount=0)

        fill_price = price * (1 - self._slip)
        if fill_price <= 0:
            fill_price = 0.0
        value = shares * fill_price
        comm = max(value * self._rate, self._min_comm)
        if comm > value:
            comm = value
        return FillResult(
            shares=shares,
            fill_price=fill_price,
            commission=comm,
            net_amount=value - comm,
        )
