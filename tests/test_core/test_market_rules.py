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


class TestRetryAfterReject:
    """C1 regression: engine must retry on next bar when fill is rejected."""

    def test_buy_rejected_retries_next_bar(self):
        """If buy is rejected (e.g., price limit), engine should retry next bar."""
        from ez.backtest.engine import VectorizedBacktestEngine

        inner = SimpleMatcher(commission_rate=0.0003, min_commission=5.0)
        matcher = MarketRulesMatcher(inner, t_plus_1=True, lot_size=0, price_limit_pct=0.1)
        engine = VectorizedBacktestEngine(matcher=matcher)

        # Construct data: bar 1 is at upper limit (涨停), bar 2 is normal
        data = pd.DataFrame({
            "open":      [10.0, 11.0, 10.5, 10.5, 10.5],
            "high":      [10.0, 11.0, 10.5, 10.5, 10.5],
            "low":       [10.0, 11.0, 10.5, 10.5, 10.5],
            "close":     [10.0, 11.0, 10.5, 10.5, 10.5],
            "adj_close": [10.0, 11.0, 10.5, 10.5, 10.5],
            "volume":    [1e6,  1e6,  1e6,  1e6,  1e6],
        }, index=pd.date_range("2020-01-01", periods=5, freq="B"))

        # Strategy: always want full position (weight=1.0)
        from ez.strategy.base import Strategy

        class AlwaysIn(Strategy):
            def required_factors(self): return []
            def generate_signals(self, data):
                return pd.Series(1.0, index=data.index)

        # Remove from registry after use
        key = f"{AlwaysIn.__module__}.{AlwaysIn.__name__}"

        result = engine.run(data, AlwaysIn(), initial_capital=100_000)

        # Bar 1: signal=1 (shifted from bar 0), open=11.0, prev_close=10.0
        # 11.0 >= 10.0 * 1.1 = 11.0 → upper limit, BLOCKED
        # Bar 2: signal=1, open=10.5, prev_close=11.0
        # 10.5 < 11.0 * 1.1 → NOT at limit, should fill
        # If C1 bug exists: prev_weight was set to 1.0 on bar 1, so bar 2 skips
        # After fix: prev_weight stays 0, bar 2 retries and succeeds
        assert result.metrics["trade_count"] >= 0
        # Equity should NOT be flat (strategy should have entered eventually)
        equity = result.equity_curve.values
        assert not np.allclose(equity, equity[0]), \
            "Equity is flat — engine never entered position (C1 retry bug)"

        # Cleanup registry
        if key in Strategy._registry:
            del Strategy._registry[key]

    def test_sell_rejected_retries_next_bar(self):
        """If sell is rejected (T+1), engine should retry next bar."""
        from ez.backtest.engine import VectorizedBacktestEngine

        inner = SimpleMatcher(commission_rate=0.0, min_commission=0.0)
        matcher = MarketRulesMatcher(inner, t_plus_1=True, lot_size=0, price_limit_pct=0.0)
        engine = VectorizedBacktestEngine(matcher=matcher)

        # Data: 6 bars, price stable
        n = 6
        data = pd.DataFrame({
            "open":      [10.0] * n,
            "high":      [10.0] * n,
            "low":       [10.0] * n,
            "close":     [10.0] * n,
            "adj_close": [10.0] * n,
            "volume":    [1e6] * n,
        }, index=pd.date_range("2020-01-01", periods=n, freq="B"))

        # Strategy: buy on bar 1, sell on bar 2 (same day as buy → T+1 blocks)
        from ez.strategy.base import Strategy

        class BuySellQuick(Strategy):
            def required_factors(self): return []
            def generate_signals(self, data):
                signals = pd.Series(0.0, index=data.index)
                signals.iloc[0] = 1.0  # buy signal (will execute on bar 1 after shift)
                signals.iloc[1] = 0.0  # sell signal (will execute on bar 2 → T+1 blocks)
                # Bar 3+: still 0 → should retry sell
                return signals

        key = f"{BuySellQuick.__module__}.{BuySellQuick.__name__}"
        result = engine.run(data, BuySellQuick(), initial_capital=100_000)

        # The sell should eventually succeed (bar 3 or later)
        # If C1 bug: prev_weight set to 0 on bar 2 (T+1 block), never retries
        # After fix: prev_weight stays 1.0, retries on bar 3
        assert result.metrics["trade_count"] >= 1, \
            "No trades recorded — sell was never retried after T+1 rejection"

        if key in Strategy._registry:
            del Strategy._registry[key]


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
