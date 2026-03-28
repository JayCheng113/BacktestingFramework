"""Tests for ez.core.matcher — SimpleMatcher fill logic."""
import pytest
from ez.core.matcher import FillResult, SimpleMatcher


@pytest.fixture
def matcher():
    return SimpleMatcher(commission_rate=0.001, min_commission=0.0)


@pytest.fixture
def matcher_with_min():
    return SimpleMatcher(commission_rate=0.001, min_commission=5.0)


class TestFillBuy:
    def test_basic_buy(self, matcher):
        r = matcher.fill_buy(price=10.0, amount=1000.0)
        assert r.commission == pytest.approx(1.0)  # 1000 * 0.001
        assert r.shares == pytest.approx(99.9)  # (1000 - 1) / 10
        assert r.fill_price == 10.0
        assert r.net_amount == pytest.approx(-1000.0)

    def test_min_commission_applied(self, matcher_with_min):
        r = matcher_with_min.fill_buy(price=10.0, amount=1000.0)
        assert r.commission == pytest.approx(5.0)  # min_commission > 1000*0.001
        assert r.shares == pytest.approx(99.5)  # (1000 - 5) / 10

    def test_skip_when_commission_exceeds_amount(self, matcher_with_min):
        r = matcher_with_min.fill_buy(price=10.0, amount=4.0)  # min_comm=5 > amount=4
        assert r.shares == 0
        assert r.commission == 0
        assert r.net_amount == 0

    def test_zero_amount(self, matcher):
        r = matcher.fill_buy(price=10.0, amount=0)
        assert r.shares == 0

    def test_zero_price(self, matcher):
        r = matcher.fill_buy(price=0, amount=1000.0)
        assert r.shares == 0


class TestFillSell:
    def test_basic_sell(self, matcher):
        r = matcher.fill_sell(price=10.0, shares=100.0)
        assert r.commission == pytest.approx(1.0)  # 1000 * 0.001
        assert r.shares == 100.0
        assert r.fill_price == 10.0
        assert r.net_amount == pytest.approx(999.0)  # 1000 - 1

    def test_min_commission_applied(self, matcher_with_min):
        r = matcher_with_min.fill_sell(price=10.0, shares=100.0)
        assert r.commission == pytest.approx(5.0)
        assert r.net_amount == pytest.approx(995.0)

    def test_commission_capped_at_sell_value(self, matcher_with_min):
        # sell value = 0.01 * 10 = 0.1, min_comm = 5 > 0.1 → cap at 0.1
        r = matcher_with_min.fill_sell(price=10.0, shares=0.01)
        assert r.commission == pytest.approx(0.1)
        assert r.net_amount == pytest.approx(0.0)

    def test_zero_shares(self, matcher):
        r = matcher.fill_sell(price=10.0, shares=0)
        assert r.shares == 0
        assert r.net_amount == 0

    def test_zero_price(self, matcher):
        r = matcher.fill_sell(price=0, shares=100.0)
        assert r.shares == 0


class TestFillResultImmutable:
    def test_frozen(self, matcher):
        r = matcher.fill_buy(price=10.0, amount=1000.0)
        with pytest.raises(AttributeError):
            r.shares = 999
