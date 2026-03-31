"""Tests for V2.10 fix batch: T+1, directional slippage, sandbox dunder, RSI edge cases."""
import numpy as np
import pandas as pd
import pytest

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.engine import CostModel, run_portfolio_backtest
from ez.portfolio.portfolio_strategy import PortfolioStrategy
from ez.portfolio.universe import Universe


class TestPortfolioT1:
    """T+1: symbol sold today cannot be bought back same day."""

    def test_sold_today_blocks_buy(self):
        """Strategy flips A→B then B→A on consecutive days; T+1 should block same-day rebuy."""
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        data = {}
        for sym in ["A", "B"]:
            p = np.full(30, 10.0)
            data[sym] = pd.DataFrame({
                "open": p, "high": p, "low": p, "close": p,
                "adj_close": p, "volume": np.full(30, 100000.0),
            }, index=dates)
        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class FlipStrategy(PortfolioStrategy):
            """Alternate between A and B each rebalance."""
            def generate_weights(self, universe_data, dt, pw, pr):
                self.state["toggle"] = not self.state.get("toggle", False)
                return {"A": 1.0} if self.state["toggle"] else {"B": 1.0}

        result = run_portfolio_backtest(
            strategy=FlipStrategy(), universe=Universe(["A", "B"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="daily", initial_cash=100000, lot_size=1,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        # On flip days, sell A then try buy A → T+1 blocks rebuy
        for i in range(len(result.trades) - 1):
            t1, t2 = result.trades[i], result.trades[i + 1]
            if t1["date"] == t2["date"] and t1["symbol"] == t2["symbol"]:
                # Same symbol, same day: should never have sell then buy
                assert not (t1["side"] == "sell" and t2["side"] == "buy"), \
                    f"T+1 violated: sell then buy {t1['symbol']} on {t1['date']}"


class TestDirectionalSlippage:
    """Buy slips UP, sell slips DOWN."""

    def test_buy_price_higher_sell_price_lower(self):
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        p = np.full(30, 10.0)
        data = {"A": pd.DataFrame({
            "open": p, "high": p, "low": p, "close": p,
            "adj_close": p, "volume": np.full(30, 100000.0),
        }, index=dates)}
        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class ToggleStrategy(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                self.state["n"] = self.state.get("n", 0) + 1
                return {"A": 1.0} if self.state["n"] % 2 == 1 else {}

        result = run_portfolio_backtest(
            strategy=ToggleStrategy(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=100000, lot_size=1,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0.01),
        )
        buys = [t for t in result.trades if t["side"] == "buy"]
        sells = [t for t in result.trades if t["side"] == "sell"]
        # Buy price should be higher than base (10.0), sell lower
        if buys:
            assert buys[0]["price"] > 10.0, f"Buy price {buys[0]['price']} should be > 10.0 (slippage up)"
        if sells:
            assert sells[0]["price"] < 10.0, f"Sell price {sells[0]['price']} should be < 10.0 (slippage down)"


class TestSandboxDunder:
    """Sandbox must block dict-style dunder access."""

    def test_dict_dunder_blocked(self):
        from ez.agent.sandbox import check_syntax
        # type.__dict__["__subclasses__"] should be caught
        code = 'x = type.__dict__["__subclasses__"](type)'
        errors = check_syntax(code)
        assert any("dunder" in e.lower() or "__dict__" in e for e in errors), \
            f"Expected dunder block, got: {errors}"

    def test_subscript_dunder_blocked(self):
        from ez.agent.sandbox import check_syntax
        code = 'x = vars()["__import__"]("os")'
        errors = check_syntax(code)
        assert any("dunder" in e.lower() or "__import__" in e for e in errors), \
            f"Expected dunder block, got: {errors}"

    def test_normal_dict_access_allowed(self):
        from ez.agent.sandbox import check_syntax
        code = 'x = {"a": 1}["a"]'
        errors = check_syntax(code)
        assert len(errors) == 0, f"Normal dict access blocked: {errors}"


class TestRSIEdgeCases:
    """RSI edge cases: uptrend=100, downtrend=0, flat=50."""

    def test_pure_uptrend_rsi_100(self):
        from ez.factor.builtin.technical import RSI
        rsi = RSI(period=5)
        prices = list(range(10, 30))  # monotonic increase
        df = pd.DataFrame({
            "adj_close": prices, "close": prices,
            "open": prices, "high": prices, "low": prices, "volume": [1000] * len(prices),
        })
        result = rsi.compute(df)
        # After warmup, RSI should be 100 for pure uptrend
        valid = result[rsi.name].dropna()
        assert (valid == 100.0).all(), f"Pure uptrend RSI should be 100, got: {valid.values[-5:]}"

    def test_flat_price_rsi_50(self):
        from ez.factor.builtin.technical import RSI
        rsi = RSI(period=5)
        prices = [10.0] * 20  # perfectly flat
        df = pd.DataFrame({
            "adj_close": prices, "close": prices,
            "open": prices, "high": prices, "low": prices, "volume": [1000] * len(prices),
        })
        result = rsi.compute(df)
        valid = result[rsi.name].dropna()
        assert (valid == 50.0).all(), f"Flat price RSI should be 50, got: {valid.values[-5:]}"


class TestSandboxDictGet:
    """Sandbox must block __dict__.get() bypass."""

    def test_dict_get_subclasses_blocked(self):
        from ez.agent.sandbox import check_syntax
        code = 'x = type.__dict__.get("__subclasses__")(object)'
        errors = check_syntax(code)
        assert any("__dict__" in e or "dunder" in e.lower() for e in errors), \
            f"Expected __dict__ block, got: {errors}"

    def test_dict_attr_blocked(self):
        from ez.agent.sandbox import check_syntax
        code = 'x = object.__dict__'
        errors = check_syntax(code)
        assert any("__dict__" in e for e in errors), f"Expected __dict__ block, got: {errors}"


class TestNaNBarNoTrade:
    """NaN price on a day with bar should NOT allow trading."""

    def test_nan_close_with_bar_blocks_trade(self):
        dates = pd.date_range("2024-01-02", periods=20, freq="B")
        prices = np.full(20, 10.0)
        adj_prices = prices.copy()
        raw_prices = prices.copy()
        # Day 10: bar exists but prices are NaN (suspension with partial data)
        adj_prices[10] = np.nan
        raw_prices[10] = np.nan

        data = {"A": pd.DataFrame({
            "open": prices, "high": prices, "low": prices,
            "close": raw_prices, "adj_close": adj_prices,
            "volume": np.full(20, 100000.0),
        }, index=dates)}

        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.engine import CostModel, run_portfolio_backtest
        from ez.portfolio.universe import Universe
        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class AlwaysFull(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 1.0}

        result = run_portfolio_backtest(
            strategy=AlwaysFull(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="daily", initial_cash=100000, lot_size=1,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        day10 = dates[10].date()
        trades_on_nan_day = [t for t in result.trades if t["date"] == day10.isoformat()]
        assert len(trades_on_nan_day) == 0, "Should not trade on NaN-price day"
