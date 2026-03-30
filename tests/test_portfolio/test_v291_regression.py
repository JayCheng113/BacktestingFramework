"""V2.9.1 regression tests — C1 adj/raw limit, C2 NaN carry-forward,
builtin strategy behavior, pre-index consistency."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import MomentumRank
from ez.portfolio.engine import CostModel, run_portfolio_backtest
from ez.portfolio.portfolio_strategy import TopNRotation, PortfolioStrategy
from ez.portfolio.universe import Universe


def _make_data(symbols, n=200, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    data = {}
    for i, sym in enumerate(symbols):
        prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.015, n))
        data[sym] = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n),
        }, index=dates)
    return data, dates


# ─── C1: 涨跌停必须用 raw close (不是 adj_close) ───

class TestC1RawCloseLimitCheck:
    """Engine uses raw close (not adj_close) for limit up/down check."""

    def test_adj_close_split_does_not_block_trade(self):
        """adj_close can differ from close due to splits/dividends.
        Limit check must use raw close, so a stock with 5% raw change
        but 50% adj_close change (from ex-dividend) should NOT be blocked."""
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        prices_raw = np.full(30, 10.0)
        # Day 15: raw close +5% (not limit up), but adj_close jumps 50% (ex-div adjustment)
        prices_raw[15] = 10.5
        prices_adj = np.full(30, 10.0)
        prices_adj[15] = 15.0  # adj_close much higher due to backward adjustment

        data = {"A": pd.DataFrame({
            "open": prices_raw, "high": prices_raw * 1.01,
            "low": prices_raw * 0.99, "close": prices_raw,
            "adj_close": prices_adj, "volume": np.full(30, 100000),
        }, index=dates)}

        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class AlwaysBuy(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 1.0}

        result = run_portfolio_backtest(
            strategy=AlwaysBuy(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=100000, lot_size=1,
            limit_pct=0.10,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        # A should still be tradeable (raw close only +5%, not limit up)
        a_buys = [t for t in result.trades if t["side"] == "buy"]
        assert len(a_buys) > 0, "Stock should be bought — raw close is only +5%"

    def test_raw_close_limit_up_blocks_buy(self):
        """When raw close is +10% (limit up), buy must be blocked.
        Uses daily freq so day 15 is always a rebalance day."""
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        prices_raw = np.full(30, 10.0)
        # Day 15: raw close exactly +10% (limit up)
        prices_raw[15] = 11.0
        prices_adj = prices_raw.copy()  # no split, adj == raw

        data = {"A": pd.DataFrame({
            "open": prices_raw, "high": prices_raw * 1.01,
            "low": prices_raw * 0.99, "close": prices_raw,
            "adj_close": prices_adj, "volume": np.full(30, 100000),
        }, index=dates)}

        cal = TradingCalendar.from_dates([d.date() for d in dates])
        day15 = dates[15].date()

        class AlwaysBuy(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 1.0}

        # Use daily freq so EVERY day is a rebalance day (including day 15)
        result = run_portfolio_backtest(
            strategy=AlwaysBuy(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="daily", initial_cash=100000, lot_size=1,
            limit_pct=0.10,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        # Day 15 IS a rebalance day (daily freq), and A is limit-up → no buy
        buys_on_day15 = [t for t in result.trades
                         if t["date"] == day15.isoformat() and t["side"] == "buy"]
        assert len(buys_on_day15) == 0, "Limit-up stock should not be bought on day 15"


# ─── C2: NaN carry-forward (equity never zeros) ───

class TestC2NanCarryForward:
    """NaN prices must carry forward from prev_prices, not default to 0."""

    def test_nan_price_does_not_zero_equity(self):
        """If a stock has NaN close on some days, equity should not drop to 0."""
        dates = pd.date_range("2024-01-02", periods=50, freq="B")
        prices = np.full(50, 10.0)
        adj_prices = prices.copy()
        # Inject NaN on days 25-27 (simulate suspension)
        adj_prices[25:28] = np.nan
        prices_raw = prices.copy()
        prices_raw[25:28] = np.nan

        data = {"A": pd.DataFrame({
            "open": prices, "high": prices * 1.01,
            "low": prices * 0.99, "close": prices_raw,
            "adj_close": adj_prices, "volume": np.full(50, 100000),
        }, index=dates)}

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
        # Equity should NEVER drop to 0 or near 0 due to NaN
        for i, eq in enumerate(result.equity_curve):
            assert eq > 50000, f"Equity dropped to {eq} on day {i} — NaN carry-forward failed"

    def test_nan_only_stock_stays_cash(self):
        """If ALL prices are NaN for a stock, engine should stay in cash."""
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        data = {"A": pd.DataFrame({
            "open": np.full(30, np.nan), "high": np.full(30, np.nan),
            "low": np.full(30, np.nan), "close": np.full(30, np.nan),
            "adj_close": np.full(30, np.nan), "volume": np.full(30, 0),
        }, index=dates)}

        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class AlwaysFull(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 1.0}

        result = run_portfolio_backtest(
            strategy=AlwaysFull(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=100000, lot_size=1,
        )
        # All equity should be initial cash (no trades possible)
        for eq in result.equity_curve:
            assert eq == 100000.0


# ─── Builtin Strategy Behavior Tests (M6) ───

class TestEtfMacdRotation:
    """EtfMacdRotation basic behavior."""

    def test_generates_weights(self):
        from ez.portfolio.builtin_strategies import EtfMacdRotation
        symbols = [f"ETF{i}" for i in range(5)]
        data, dates = _make_data(symbols, n=350, seed=77)
        cal = TradingCalendar.from_dates([d.date() for d in dates])

        result = run_portfolio_backtest(
            strategy=EtfMacdRotation(top_n=2, rank_period=20),
            universe=Universe(symbols), universe_data=data, calendar=cal,
            start=dates[50].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=1_000_000, lot_size=100,
        )
        assert len(result.equity_curve) > 100
        # Should have at least some trades (unless panic filter blocks everything)
        # Accounting invariant holds (engine asserts internally)

    def test_panic_filter_all_negative(self):
        """When >75% stocks have negative returns, strategy returns empty."""
        from ez.portfolio.builtin_strategies import EtfMacdRotation
        strat = EtfMacdRotation(top_n=2)
        # Create downtrending data
        dates = pd.date_range("2023-01-02", periods=100, freq="B")
        data = {}
        for i in range(5):
            prices = 10 * np.cumprod(1 + np.full(100, -0.005))  # all declining
            data[f"S{i}"] = pd.DataFrame({
                "open": prices, "high": prices, "low": prices,
                "close": prices, "adj_close": prices,
                "volume": np.full(100, 100000),
            }, index=dates)
        weights = strat.generate_weights(data, datetime(2023, 6, 1), {}, {})
        # With all declining, panic filter should return empty
        assert len(weights) == 0

    def test_top_n_validation(self):
        from ez.portfolio.builtin_strategies import EtfMacdRotation
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            EtfMacdRotation(top_n=0)

    def test_parameters_schema(self):
        from ez.portfolio.builtin_strategies import EtfMacdRotation
        schema = EtfMacdRotation.get_parameters_schema()
        assert "top_n" in schema
        assert "rank_period" in schema
        assert schema["top_n"]["default"] == 2


class TestEtfSectorSwitch:
    """EtfSectorSwitch basic behavior."""

    def test_generates_weights(self):
        from ez.portfolio.builtin_strategies import EtfSectorSwitch
        symbols = [f"ETF{i}" for i in range(5)]
        data, dates = _make_data(symbols, n=350, seed=88)
        cal = TradingCalendar.from_dates([d.date() for d in dates])

        result = run_portfolio_backtest(
            strategy=EtfSectorSwitch(top_n=1),
            universe=Universe(symbols), universe_data=data, calendar=cal,
            start=dates[50].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=1_000_000, lot_size=100,
        )
        assert len(result.equity_curve) > 100

    def test_stateful_voting(self):
        """State should accumulate across calls (cW, W, penaltyW)."""
        from ez.portfolio.builtin_strategies import EtfSectorSwitch
        strat = EtfSectorSwitch(top_n=1)
        dates = pd.date_range("2023-01-02", periods=100, freq="B")
        data = {}
        for i in range(3):
            prices = 10 * np.cumprod(1 + np.random.default_rng(i + 10).normal(0.001, 0.01, 100))
            data[f"S{i}"] = pd.DataFrame({
                "open": prices, "high": prices, "low": prices,
                "close": prices, "adj_close": prices,
                "volume": np.full(100, 100000),
            }, index=dates)

        # Call twice — state should accumulate
        w1 = strat.generate_weights(data, datetime(2023, 4, 1), {}, {})
        w2 = strat.generate_weights(data, datetime(2023, 5, 1), {}, {})
        assert "cW" in strat.state
        assert "W" in strat.state

    def test_top_n_validation(self):
        from ez.portfolio.builtin_strategies import EtfSectorSwitch
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            EtfSectorSwitch(top_n=0)


class TestEtfStockEnhance:
    """EtfStockEnhance basic behavior."""

    def test_generates_weights(self):
        from ez.portfolio.builtin_strategies import EtfStockEnhance
        symbols = [f"ETF{i}" for i in range(3)] + [f"STK{i}" for i in range(3)]
        data, dates = _make_data(symbols, n=350, seed=99)
        cal = TradingCalendar.from_dates([d.date() for d in dates])

        result = run_portfolio_backtest(
            strategy=EtfStockEnhance(top_n=1, stock_ratio=0.3),
            universe=Universe(symbols), universe_data=data, calendar=cal,
            start=dates[50].date(), end=dates[-1].date(),
            freq="weekly", initial_cash=1_000_000, lot_size=100,
        )
        assert len(result.equity_curve) > 100

    def test_stock_ratio_zero_equals_inner(self):
        """stock_ratio=0 should give same weights as pure EtfSectorSwitch."""
        from ez.portfolio.builtin_strategies import EtfStockEnhance, EtfSectorSwitch
        strat_enhance = EtfStockEnhance(top_n=1, stock_ratio=0.0)
        strat_inner = EtfSectorSwitch(top_n=1)
        dates = pd.date_range("2023-01-02", periods=100, freq="B")
        data = {}
        for i in range(3):
            prices = 10 * np.cumprod(1 + np.random.default_rng(i + 20).normal(0.001, 0.01, 100))
            data[f"S{i}"] = pd.DataFrame({
                "open": prices, "high": prices, "low": prices,
                "close": prices, "adj_close": prices,
                "volume": np.full(100, 100000),
            }, index=dates)
        dt = datetime(2023, 4, 1)
        w_enhance = strat_enhance.generate_weights(data, dt, {}, {})
        w_inner = strat_inner.generate_weights(data, dt, {}, {})
        # With stock_ratio=0, EtfStockEnhance should return exactly the same
        # symbols and weights as EtfSectorSwitch
        assert set(w_enhance.keys()) == set(w_inner.keys()), \
            f"Symbol mismatch: enhance={set(w_enhance.keys())}, inner={set(w_inner.keys())}"
        for sym in w_enhance:
            assert abs(w_enhance[sym] - w_inner[sym]) < 1e-10, \
                f"Weight mismatch for {sym}: enhance={w_enhance[sym]}, inner={w_inner[sym]}"

    def test_top_n_validation(self):
        from ez.portfolio.builtin_strategies import EtfStockEnhance
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            EtfStockEnhance(top_n=0)

    def test_parameters_schema(self):
        from ez.portfolio.builtin_strategies import EtfStockEnhance
        schema = EtfStockEnhance.get_parameters_schema()
        assert "top_n" in schema
        assert "stock_ratio" in schema


# ─── Pre-index consistency: engine results deterministic ───

class TestPreIndexConsistency:
    """Pre-indexed engine should produce same results as before."""

    def test_deterministic_results(self):
        """Running same backtest twice gives identical equity curves."""
        symbols = [f"S{i}" for i in range(5)]
        data, dates = _make_data(symbols, n=200, seed=42)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)

        def run():
            return run_portfolio_backtest(
                strategy=TopNRotation(MomentumRank(20), top_n=2),
                universe=universe, universe_data=data, calendar=cal,
                start=dates[30].date(), end=dates[-1].date(),
                freq="monthly", initial_cash=1_000_000, lot_size=100,
            )

        r1 = run()
        r2 = run()
        assert r1.equity_curve == r2.equity_curve
        assert len(r1.trades) == len(r2.trades)
        for t1, t2 in zip(r1.trades, r2.trades):
            assert t1["symbol"] == t2["symbol"]
            assert t1["shares"] == t2["shares"]
            assert abs(t1["price"] - t2["price"]) < 1e-10

    def test_metrics_consistent(self):
        """Metrics should be identical between runs."""
        symbols = [f"S{i}" for i in range(5)]
        data, dates = _make_data(symbols, n=200, seed=42)
        cal = TradingCalendar.from_dates([d.date() for d in dates])

        def run():
            return run_portfolio_backtest(
                strategy=TopNRotation(MomentumRank(20), top_n=2),
                universe=Universe(symbols), universe_data=data, calendar=cal,
                start=dates[30].date(), end=dates[-1].date(),
                freq="monthly",
            )

        r1 = run()
        r2 = run()
        for k in r1.metrics:
            assert r1.metrics[k] == r2.metrics[k], f"Metric {k} differs: {r1.metrics[k]} vs {r2.metrics[k]}"


# ─── Registry: builtin strategies always registered ───

class TestBuiltinRegistration:
    """All 5 builtin strategies must be in registry."""

    def test_all_builtins_registered(self):
        registry = PortfolioStrategy.get_registry()
        expected = ["TopNRotation", "MultiFactorRotation",
                    "EtfMacdRotation", "EtfSectorSwitch", "EtfStockEnhance"]
        for name in expected:
            assert name in registry, f"{name} not in registry"

    def test_descriptions_non_empty(self):
        registry = PortfolioStrategy.get_registry()
        builtins = ["TopNRotation", "MultiFactorRotation",
                    "EtfMacdRotation", "EtfSectorSwitch", "EtfStockEnhance"]
        for name in builtins:
            cls = registry[name]
            if hasattr(cls, "get_description"):
                desc = cls.get_description()
                assert isinstance(desc, str) and len(desc) > 0, f"{name} has empty description"


# ─── Issue 1 fix: SellSideTaxMatcher (stamp tax sell-only) ───

class TestSellSideTaxMatcher:
    """Stamp tax must only be charged on sells, not buys."""

    def test_stamp_tax_sell_only(self):
        """_SellSideTaxMatcher wraps inner matcher, adds tax only on fill_sell."""
        from ez.api.routes.backtest import _SellSideTaxMatcher
        from ez.core.matcher import SimpleMatcher

        inner = SimpleMatcher(commission_rate=0.0003, min_commission=0)
        wrapper = _SellSideTaxMatcher(inner, stamp_tax_rate=0.001)

        # Buy: wrapper should match inner exactly
        buy_inner = inner.fill_buy(10.0, 10000.0)
        buy_wrapper = wrapper.fill_buy(10.0, 10000.0)
        assert buy_inner.commission == buy_wrapper.commission
        assert buy_inner.net_amount == buy_wrapper.net_amount

        # Sell: wrapper should add stamp tax
        sell_inner = inner.fill_sell(10.0, 100)
        sell_wrapper = wrapper.fill_sell(10.0, 100)
        expected_tax = 100 * 10.0 * 0.001  # shares * price * tax_rate
        assert sell_wrapper.commission == pytest.approx(sell_inner.commission + expected_tax)
        assert sell_wrapper.net_amount == pytest.approx(sell_inner.net_amount - expected_tax)

    def test_stamp_tax_zero_shares_no_tax(self):
        from ez.api.routes.backtest import _SellSideTaxMatcher
        from ez.core.matcher import SimpleMatcher

        inner = SimpleMatcher(commission_rate=0.0003, min_commission=5)
        wrapper = _SellSideTaxMatcher(inner, stamp_tax_rate=0.001)
        # Sell 0 shares: no tax
        result = wrapper.fill_sell(10.0, 0)
        assert result.shares == 0
        assert result.commission == 0

    def test_net_amount_never_negative(self):
        """Stamp tax must not push net_amount below 0."""
        from ez.api.routes.backtest import _SellSideTaxMatcher
        from ez.core.matcher import SimpleMatcher

        # Extreme: high commission + high tax, tiny sell
        inner = SimpleMatcher(commission_rate=0.5, min_commission=0)  # 50% commission
        wrapper = _SellSideTaxMatcher(inner, stamp_tax_rate=0.5)  # 50% stamp tax
        result = wrapper.fill_sell(1.0, 1)  # sell 1 share at 1.0 = value 1.0
        # Inner: comm = 0.5, net = 0.5. Tax would be 0.5, but capped to keep net >= 0
        assert result.net_amount >= 0, f"net_amount={result.net_amount} is negative"


# ─── Issue 2 fix: limit tolerance precision ───

class TestLimitTolerancePrecision:
    """Limit check should not block trades at 9.9% (old 0.001 tolerance)."""

    def test_995_pct_not_blocked(self):
        """A +9.95% change should NOT be treated as limit up (10%).
        Start from day 10 so first rebalance must buy → verifies not blocked."""
        dates = pd.date_range("2024-01-02", periods=20, freq="B")
        prices = np.full(20, 10.0)
        # Day 10: +9.95% — NOT limit up
        prices[10] = 10.995

        data = {"A": pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": np.full(20, 100000),
        }, index=dates)}

        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class AlwaysBuy(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 1.0}

        # Start on day 10 — first rebalance MUST buy (going from cash to holding)
        day10 = dates[10].date()
        result = run_portfolio_backtest(
            strategy=AlwaysBuy(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=day10, end=dates[-1].date(),
            freq="daily", initial_cash=100000, lot_size=1,
            limit_pct=0.10,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        buys_on_day10 = [t for t in result.trades
                         if t["date"] == day10.isoformat() and t["side"] == "buy"]
        # +9.95% is NOT limit up → buy MUST succeed on first rebalance
        assert len(buys_on_day10) > 0, "+9.95% was wrongly blocked as limit up"

    def test_exact_10pct_blocked_on_first_day(self):
        """Exactly +10% on the first trading day MUST be blocked (limit up)."""
        dates = pd.date_range("2024-01-02", periods=20, freq="B")
        prices = np.full(20, 10.0)
        prices[10] = 11.0  # exactly +10% = limit up

        data = {"A": pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": np.full(20, 100000),
        }, index=dates)}

        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class AlwaysBuy(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 1.0}

        # Start on day 10 — prev_raw_close should be initialized from day 9 (=10.0)
        day10 = dates[10].date()
        result = run_portfolio_backtest(
            strategy=AlwaysBuy(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=day10, end=dates[-1].date(),
            freq="daily", initial_cash=100000, lot_size=1,
            limit_pct=0.10,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        buys_on_day10 = [t for t in result.trades
                         if t["date"] == day10.isoformat() and t["side"] == "buy"]
        # +10% = limit up → buy MUST be blocked on first day
        assert len(buys_on_day10) == 0, "Limit-up stock bought on first day (prev_raw_close not initialized)"


# ─── Issue 3 fix: unsorted input doesn't break engine ───

class TestUnsortedInput:
    """Engine should handle unsorted DataFrame index gracefully."""

    def test_unsorted_dates_still_works(self):
        """Shuffle DataFrame rows — engine should sort internally and produce valid results."""
        symbols = ["A"]
        rng = np.random.default_rng(42)
        dates = pd.date_range("2024-01-02", periods=100, freq="B")
        prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.01, 100))
        df = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, 100),
        }, index=dates)
        # Shuffle rows (unsorted input)
        shuffled = df.sample(frac=1, random_state=42)
        data = {"A": shuffled}

        cal = TradingCalendar.from_dates([d.date() for d in dates])

        class AlwaysFull(PortfolioStrategy):
            def generate_weights(self, universe_data, dt, pw, pr):
                return {"A": 1.0}

        # Should not crash, and equity should be reasonable
        result = run_portfolio_backtest(
            strategy=AlwaysFull(), universe=Universe(["A"]),
            universe_data=data, calendar=cal,
            start=dates[20].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=100000, lot_size=1,
            cost_model=CostModel(buy_commission_rate=0, sell_commission_rate=0,
                                  min_commission=0, stamp_tax_rate=0, slippage_rate=0),
        )
        assert len(result.equity_curve) > 50
        assert all(eq > 0 for eq in result.equity_curve)
