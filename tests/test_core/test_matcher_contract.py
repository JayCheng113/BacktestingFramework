"""Contract test for Matcher ABC — any implementation must pass these.

Add new Matcher implementations to `all_matchers()` — contract tests auto-validate.
"""
import pytest
from ez.core.matcher import Matcher, SimpleMatcher, SlippageMatcher


def all_matchers() -> list[Matcher]:
    """Return instances of every Matcher implementation to validate."""
    return [
        SimpleMatcher(commission_rate=0.001, min_commission=0.0),
        SimpleMatcher(commission_rate=0.0003, min_commission=5.0),
        SimpleMatcher(commission_rate=0.0, min_commission=0.0),
        SlippageMatcher(slippage_rate=0.001, commission_rate=0.001, min_commission=0.0),
        SlippageMatcher(slippage_rate=0.005, commission_rate=0.0003, min_commission=5.0),
        SlippageMatcher(slippage_rate=0.0, commission_rate=0.0, min_commission=0.0),
    ]


@pytest.fixture(params=all_matchers(), ids=lambda m: f"{m.__class__.__name__}(slip={getattr(m,'_slip',0)},rate={m._rate},min={m._min_comm})")
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


class TestAsymmetricCommission:
    """V2.12.2 codex: sell_commission_rate is applied on sells when set."""

    def test_simple_asymmetric_sell_rate(self):
        m = SimpleMatcher(commission_rate=0.001, min_commission=0.0,
                          sell_commission_rate=0.003)
        buy = m.fill_buy(price=100.0, amount=10000.0)
        sell = m.fill_sell(price=100.0, shares=buy.shares)
        # buy commission: 10000 * 0.001 = 10
        # sell value: shares * 100 ≈ (10000 - 10)/100 * 100 = 9990
        # sell commission: 9990 * 0.003 = 29.97
        assert abs(buy.commission - 10.0) < 0.01
        assert abs(sell.commission - 29.97) < 0.01

    def test_simple_none_defaults_to_buy_rate(self):
        """When sell_commission_rate is None, both sides use commission_rate."""
        m = SimpleMatcher(commission_rate=0.002, min_commission=0.0)
        buy = m.fill_buy(price=100.0, amount=10000.0)
        sell = m.fill_sell(price=100.0, shares=buy.shares)
        # Both sides use 0.002
        assert abs(buy.commission - 20.0) < 0.01
        # sell value ≈ 9980, commission ≈ 19.96
        assert abs(sell.commission - 19.96) < 0.02

    def test_slippage_asymmetric_sell_rate(self):
        m = SlippageMatcher(slippage_rate=0.0, commission_rate=0.001,
                            min_commission=0.0, sell_commission_rate=0.005)
        sell = m.fill_sell(price=100.0, shares=100.0)
        # value = 10000, commission = 10000 * 0.005 = 50
        assert abs(sell.commission - 50.0) < 0.01

    def test_negative_sell_rate_rejected(self):
        with pytest.raises(ValueError):
            SimpleMatcher(commission_rate=0.001, sell_commission_rate=-0.001)
        with pytest.raises(ValueError):
            SlippageMatcher(slippage_rate=0.001, commission_rate=0.001,
                            sell_commission_rate=-0.001)
