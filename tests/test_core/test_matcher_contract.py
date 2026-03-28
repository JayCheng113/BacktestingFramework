"""Contract test for Matcher ABC — any implementation must pass these.

When V2.1 adds a C++ matcher, parametrize `all_matchers()` to include it.
This guarantees behavioral equivalence across Python and C++ implementations.
"""
import pytest
from ez.core.matcher import Matcher, SimpleMatcher


def all_matchers() -> list[Matcher]:
    """Return instances of every Matcher implementation to validate."""
    return [
        SimpleMatcher(commission_rate=0.001, min_commission=0.0),
        SimpleMatcher(commission_rate=0.0003, min_commission=5.0),
        SimpleMatcher(commission_rate=0.0, min_commission=0.0),
    ]


@pytest.fixture(params=all_matchers(), ids=lambda m: f"{m.__class__.__name__}(rate={m._rate},min={m._min_comm})")
def matcher(request):
    return request.param


class TestMatcherContract:
    """Invariants that ANY Matcher implementation must satisfy."""

    # -- fill_buy invariants --

    def test_buy_shares_non_negative(self, matcher):
        r = matcher.fill_buy(price=50.0, amount=10000.0)
        assert r.shares >= 0

    def test_buy_commission_non_negative(self, matcher):
        r = matcher.fill_buy(price=50.0, amount=10000.0)
        assert r.commission >= 0

    def test_buy_net_amount_non_positive(self, matcher):
        """Buy should cost money: net_amount <= 0."""
        r = matcher.fill_buy(price=50.0, amount=10000.0)
        assert r.net_amount <= 0

    def test_buy_zero_amount_is_noop(self, matcher):
        r = matcher.fill_buy(price=50.0, amount=0.0)
        assert r.shares == 0
        assert r.commission == 0
        assert r.net_amount == 0

    def test_buy_negative_amount_is_noop(self, matcher):
        r = matcher.fill_buy(price=50.0, amount=-100.0)
        assert r.shares == 0

    def test_buy_zero_price_is_noop(self, matcher):
        r = matcher.fill_buy(price=0.0, amount=10000.0)
        assert r.shares == 0

    def test_buy_negative_price_is_noop(self, matcher):
        r = matcher.fill_buy(price=-10.0, amount=10000.0)
        assert r.shares == 0

    def test_buy_shares_times_price_le_amount(self, matcher):
        """Cannot buy more than the cash provided."""
        r = matcher.fill_buy(price=50.0, amount=10000.0)
        if r.shares > 0:
            assert r.shares * r.fill_price <= 10000.0

    # -- fill_sell invariants --

    def test_sell_shares_non_negative(self, matcher):
        r = matcher.fill_sell(price=50.0, shares=200.0)
        assert r.shares >= 0

    def test_sell_commission_non_negative(self, matcher):
        r = matcher.fill_sell(price=50.0, shares=200.0)
        assert r.commission >= 0

    def test_sell_net_amount_non_negative(self, matcher):
        """Sell should yield money: net_amount >= 0."""
        r = matcher.fill_sell(price=50.0, shares=200.0)
        assert r.net_amount >= 0

    def test_sell_zero_shares_is_noop(self, matcher):
        r = matcher.fill_sell(price=50.0, shares=0.0)
        assert r.shares == 0
        assert r.commission == 0
        assert r.net_amount == 0

    def test_sell_negative_shares_is_noop(self, matcher):
        r = matcher.fill_sell(price=50.0, shares=-100.0)
        assert r.shares == 0

    def test_sell_zero_price_is_noop(self, matcher):
        r = matcher.fill_sell(price=0.0, shares=200.0)
        assert r.shares == 0

    def test_sell_net_amount_le_gross_value(self, matcher):
        """Commission means you receive <= gross value."""
        r = matcher.fill_sell(price=50.0, shares=200.0)
        gross = 200.0 * 50.0
        assert r.net_amount <= gross

    # -- round-trip consistency --

    def test_round_trip_commission_positive(self, matcher):
        """Buy then sell: total commission should be >= 0."""
        buy = matcher.fill_buy(price=100.0, amount=50000.0)
        if buy.shares > 0:
            sell = matcher.fill_sell(price=100.0, shares=buy.shares)
            total_comm = buy.commission + sell.commission
            assert total_comm >= 0

    def test_round_trip_at_same_price_loses_commission(self, matcher):
        """Buy and sell at same price: net loss = total commission."""
        buy = matcher.fill_buy(price=100.0, amount=50000.0)
        if buy.shares > 0:
            sell = matcher.fill_sell(price=100.0, shares=buy.shares)
            cash_change = buy.net_amount + sell.net_amount
            # cash_change should be negative (lost commission) or zero (zero-commission)
            assert cash_change <= 1e-10
