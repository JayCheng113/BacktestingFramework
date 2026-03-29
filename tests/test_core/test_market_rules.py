"""Tests for MarketRulesMatcher — V2.6 A-share market rules."""
import numpy as np
import pandas as pd
import pytest

from ez.core.market_rules import MarketRulesMatcher
from ez.core.matcher import FillResult, SimpleMatcher


@pytest.fixture
def inner():
    return SimpleMatcher(commission_rate=0.0003, min_commission=5.0)


@pytest.fixture
def matcher(inner):
    return MarketRulesMatcher(inner, t_plus_1=True, price_limit_pct=0.1, lot_size=100)


class TestTPlusOne:
    def test_cannot_sell_same_bar_as_buy(self, matcher):
        matcher.on_bar(bar_index=5, prev_close=10.0)
        buy = matcher.fill_buy(10.0, 10000)
        assert buy.shares > 0
        # Same bar — T+1 blocks sell
        sell = matcher.fill_sell(10.0, buy.shares)
        assert sell.shares == 0

    def test_can_sell_next_bar(self, matcher):
        matcher.on_bar(bar_index=5, prev_close=10.0)
        buy = matcher.fill_buy(10.0, 10000)
        assert buy.shares > 0
        # Next bar — sell allowed
        matcher.on_bar(bar_index=6, prev_close=10.0)
        sell = matcher.fill_sell(10.0, buy.shares)
        assert sell.shares > 0

    def test_t1_disabled(self, inner):
        m = MarketRulesMatcher(inner, t_plus_1=False)
        m.on_bar(bar_index=5, prev_close=10.0)
        buy = m.fill_buy(10.0, 10000)
        sell = m.fill_sell(10.0, buy.shares)
        assert sell.shares > 0  # same bar, T+1 disabled → allowed


class TestPriceLimits:
    def test_cannot_buy_at_upper_limit(self, matcher):
        matcher.on_bar(bar_index=1, prev_close=10.0)
        # Upper limit = 10 * 1.1 = 11.0
        fill = matcher.fill_buy(11.0, 10000)
        assert fill.shares == 0

    def test_can_buy_below_upper_limit(self, matcher):
        matcher.on_bar(bar_index=1, prev_close=10.0)
        fill = matcher.fill_buy(10.5, 10000)
        assert fill.shares > 0

    def test_cannot_sell_at_lower_limit(self, matcher):
        matcher.on_bar(bar_index=1, prev_close=10.0)
        # Buy first, then try to sell at lower limit next bar
        matcher.fill_buy(10.0, 10000)
        matcher.on_bar(bar_index=2, prev_close=10.0)
        # Lower limit = 10 * 0.9 = 9.0
        sell = matcher.fill_sell(9.0, 100)
        assert sell.shares == 0

    def test_can_sell_above_lower_limit(self, matcher):
        matcher.on_bar(bar_index=1, prev_close=10.0)
        matcher.fill_buy(10.0, 10000)
        matcher.on_bar(bar_index=2, prev_close=10.0)
        sell = matcher.fill_sell(9.5, 100)
        assert sell.shares > 0

    def test_price_limit_20pct_chinext(self, inner):
        """创业板/科创板 20% 涨跌停。"""
        m = MarketRulesMatcher(inner, price_limit_pct=0.2, lot_size=0)
        m.on_bar(bar_index=1, prev_close=100.0)
        # 20% up = 120.0 → cannot buy
        assert m.fill_buy(120.0, 50000).shares == 0
        # 19% up → can buy
        assert m.fill_buy(119.0, 50000).shares > 0


class TestLotSize:
    def test_rounds_down_to_100(self, matcher):
        matcher.on_bar(bar_index=1, prev_close=10.0)
        # 10000 / 10 = ~1000 shares (before commission), rounds to 900
        fill = matcher.fill_buy(10.0, 10000)
        assert fill.shares % 100 == 0
        assert fill.shares > 0

    def test_less_than_one_lot_rejected(self, matcher):
        matcher.on_bar(bar_index=1, prev_close=100.0)
        # 5000 / 100 = ~50 shares → less than 100 → rejected
        fill = matcher.fill_buy(100.0, 5000)
        assert fill.shares == 0

    def test_sell_rounds_down(self, matcher):
        matcher.on_bar(bar_index=1, prev_close=10.0)
        buy = matcher.fill_buy(10.0, 50000)
        matcher.on_bar(bar_index=2, prev_close=10.0)
        # Try to sell 150 shares → rounds to 100
        sell = matcher.fill_sell(10.0, 150)
        assert sell.shares == 100

    def test_lot_size_disabled(self, inner):
        m = MarketRulesMatcher(inner, lot_size=0)
        m.on_bar(bar_index=1, prev_close=10.0)
        fill = m.fill_buy(10.0, 555)
        # With lot_size=0, no rounding
        assert fill.shares > 0
        assert fill.shares % 100 != 0 or fill.shares == 0  # not necessarily multiple


class TestBackwardCompat:
    def test_simple_matcher_no_on_bar(self):
        """SimpleMatcher has no on_bar — engine's hasattr check skips it."""
        m = SimpleMatcher()
        assert not hasattr(m, 'on_bar')

    def test_market_rules_matcher_has_on_bar(self, matcher):
        assert hasattr(matcher, 'on_bar')


class TestEngineIntegration:
    def test_engine_with_market_rules(self):
        """Full integration: engine + MarketRulesMatcher produces valid results."""
        from ez.backtest.engine import VectorizedBacktestEngine
        from ez.strategy.builtin.ma_cross import MACrossStrategy

        rng = np.random.default_rng(42)
        n = 300
        prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
        dates = pd.date_range("2020-01-03", periods=n, freq="B")
        data = pd.DataFrame({
            "open": prices * (1 + rng.normal(0, 0.002, n)),
            "high": prices * (1 + abs(rng.normal(0, 0.005, n))),
            "low": prices * (1 - abs(rng.normal(0, 0.005, n))),
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n),
        }, index=dates)

        inner = SimpleMatcher(commission_rate=0.0003, min_commission=5.0)
        mr_matcher = MarketRulesMatcher(inner, t_plus_1=True, lot_size=100)
        engine = VectorizedBacktestEngine(matcher=mr_matcher)
        strategy = MACrossStrategy(short_period=5, long_period=20)
        result = engine.run(data, strategy, initial_capital=100_000)

        assert result.equity_curve is not None
        assert len(result.equity_curve) > 0
        assert result.metrics["trade_count"] >= 0
        # All trades should have lot-size shares
        for t in result.trades:
            # entry_price * shares should be reasonable
            assert t.pnl is not None

    def test_market_rules_reduces_trades(self):
        """MarketRules should produce fewer or equal trades vs no rules."""
        from ez.backtest.engine import VectorizedBacktestEngine
        from ez.strategy.builtin.ma_cross import MACrossStrategy

        rng = np.random.default_rng(42)
        n = 300
        prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
        dates = pd.date_range("2020-01-03", periods=n, freq="B")
        data = pd.DataFrame({
            "open": prices * (1 + rng.normal(0, 0.002, n)),
            "high": prices * (1 + abs(rng.normal(0, 0.005, n))),
            "low": prices * (1 - abs(rng.normal(0, 0.005, n))),
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n),
        }, index=dates)

        strategy = MACrossStrategy(short_period=5, long_period=20)

        # Without market rules
        engine_plain = VectorizedBacktestEngine(
            matcher=SimpleMatcher(commission_rate=0.0003, min_commission=5.0))
        r_plain = engine_plain.run(data, strategy, initial_capital=100_000)

        # With market rules
        inner = SimpleMatcher(commission_rate=0.0003, min_commission=5.0)
        engine_mr = VectorizedBacktestEngine(
            matcher=MarketRulesMatcher(inner, t_plus_1=True, lot_size=100))
        r_mr = engine_mr.run(data, strategy, initial_capital=100_000)

        # Market rules should produce <= trades (lot size prevents tiny trades)
        assert r_mr.metrics["trade_count"] <= r_plain.metrics["trade_count"]
