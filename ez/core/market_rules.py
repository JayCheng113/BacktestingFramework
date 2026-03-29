"""A-share market rules as a Matcher decorator.

Wraps an inner Matcher (SimpleMatcher/SlippageMatcher) and enforces:
- T+1: cannot sell shares bought on the same bar
- Price limits: cannot buy at upper limit (涨停), cannot sell at lower limit (跌停)
- Lot size: shares must be multiples of lot_size (A-share = 100)

Usage:
    inner = SimpleMatcher(commission_rate=0.0003)
    matcher = MarketRulesMatcher(inner, t_plus_1=True, lot_size=100)
    engine = VectorizedBacktestEngine(matcher=matcher)
"""
from __future__ import annotations

from ez.core.matcher import FillResult, Matcher


def _zero_fill(price: float) -> FillResult:
    return FillResult(shares=0, fill_price=price, commission=0, net_amount=0)


class MarketRulesMatcher(Matcher):
    """Decorator that adds A-share market rules to any Matcher."""

    def __init__(
        self,
        inner: Matcher,
        t_plus_1: bool = True,
        price_limit_pct: float = 0.1,
        lot_size: int = 100,
    ):
        if price_limit_pct < 0 or price_limit_pct > 1:
            raise ValueError("price_limit_pct must be in [0, 1]")
        if lot_size < 0:
            raise ValueError("lot_size must be >= 0 (0 = disabled)")
        self._inner = inner
        self._t1 = t_plus_1
        self._limit = price_limit_pct
        self._lot = lot_size
        # Bar state — updated via on_bar() called by engine
        self._bar = -1
        self._prev_close = 0.0
        self._buy_bar = -1  # last bar where a buy was filled

    def on_bar(self, bar_index: int, prev_close: float) -> None:
        """Called by engine at each bar before fill_buy/fill_sell."""
        self._bar = bar_index
        self._prev_close = prev_close

    def fill_buy(self, price: float, amount: float) -> FillResult:
        if amount <= 0 or price <= 0:
            return _zero_fill(price)

        # 涨停不可买: price at or above upper limit
        if self._limit > 0 and self._prev_close > 0:
            upper = self._prev_close * (1 + self._limit)
            if price >= upper - 1e-6:
                return _zero_fill(price)

        fill = self._inner.fill_buy(price, amount)
        if fill.shares <= 0:
            return fill

        # 整手: round down to lot_size multiples
        if self._lot > 0:
            lots = int(fill.shares // self._lot)
            if lots == 0:
                return _zero_fill(price)
            actual_shares = lots * self._lot
            if actual_shares < fill.shares:
                # Re-call inner matcher with reduced amount for correct commission
                reduced_amount = actual_shares * fill.fill_price * 1.01  # slight buffer for commission
                fill = self._inner.fill_buy(price, reduced_amount)
                # Re-round in case inner produced slightly different shares
                if fill.shares > 0:
                    final_shares = int(fill.shares // self._lot) * self._lot
                    if final_shares == 0:
                        return _zero_fill(price)
                    if final_shares != fill.shares:
                        actual_amount = final_shares * fill.fill_price + fill.commission
                        fill = FillResult(
                            shares=final_shares,
                            fill_price=fill.fill_price,
                            commission=fill.commission,
                            net_amount=-(actual_amount),
                        )

        if fill.shares > 0:
            self._buy_bar = self._bar
        return fill

    def fill_sell(self, price: float, shares: float) -> FillResult:
        if shares <= 0 or price <= 0:
            return _zero_fill(price)

        # T+1: cannot sell shares bought on the same bar
        if self._t1 and self._buy_bar == self._bar:
            return _zero_fill(price)

        # 跌停不可卖: price at or below lower limit
        if self._limit > 0 and self._prev_close > 0:
            lower = self._prev_close * (1 - self._limit)
            if price <= lower + 1e-6:
                return _zero_fill(price)

        # 整手: round down to lot_size multiples for sell
        if self._lot > 0:
            lots = int(shares // self._lot)
            if lots == 0:
                return _zero_fill(price)
            shares = lots * self._lot

        return self._inner.fill_sell(price, shares)
