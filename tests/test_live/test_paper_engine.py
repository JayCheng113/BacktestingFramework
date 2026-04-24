"""Tests for PaperTradingEngine (V2.15 A5).

Uses synthetic data — no real data providers or network calls.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ez.live.deployment_spec import DeploymentSpec
from ez.live.events import EventType
from ez.live.paper_engine import PaperTradingEngine
from ez.portfolio.execution import CostModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(symbols=("AAA", "BBB"), market="cn_stock", freq="weekly",
               initial_cash=1_000_000.0, **overrides) -> DeploymentSpec:
    defaults = dict(
        strategy_name="TestStrat",
        strategy_params={},
        symbols=symbols,
        market=market,
        freq=freq,
        initial_cash=initial_cash,
        t_plus_1=True,
        lot_size=100,
        buy_commission_rate=0.00008,
        sell_commission_rate=0.00008,
        stamp_tax_rate=0.0005,
        slippage_rate=0.0,
        min_commission=0.0,
    )
    defaults.update(overrides)
    return DeploymentSpec(**defaults)


def _make_bars_df(dates: list[date], close: float = 10.0) -> pd.DataFrame:
    """Create a simple DataFrame indexed by datetime with constant price."""
    rows = []
    for d in dates:
        rows.append({
            "open": close, "high": close, "low": close,
            "close": close, "adj_close": close, "volume": 1000,
        })
    df = pd.DataFrame(rows, index=pd.to_datetime(dates))
    df.index.name = "date"
    return df


def _make_bars_df_prices(date_price_pairs: list[tuple[date, float]]) -> pd.DataFrame:
    """Create a DataFrame with varying prices."""
    rows = []
    dates = []
    for d, p in date_price_pairs:
        dates.append(d)
        rows.append({
            "open": p, "high": p, "low": p,
            "close": p, "adj_close": p, "volume": 1000,
        })
    df = pd.DataFrame(rows, index=pd.to_datetime(dates))
    df.index.name = "date"
    return df


class FakeBar:
    """Minimal bar object matching the Bar interface for data_chain.get_kline."""
    def __init__(self, time, open, high, low, close, adj_close, volume):
        self.time = time
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.adj_close = adj_close
        self.volume = volume


def _mock_data_chain(symbol_bars: dict[str, list[FakeBar]]) -> MagicMock:
    """Create a mock DataProviderChain that returns bars per symbol."""
    chain = MagicMock()
    def get_kline(symbol, market, period, start_date, end_date):
        return symbol_bars.get(symbol, [])
    chain.get_kline.side_effect = get_kline
    return chain


def _make_fake_bars(dates: list[date], close: float = 10.0) -> list[FakeBar]:
    return [
        FakeBar(
            time=datetime.combine(d, datetime.min.time()),
            open=close, high=close, low=close,
            close=close, adj_close=close, volume=1000,
        )
        for d in dates
    ]


def _make_fake_bars_prices(date_price_pairs: list[tuple[date, float]]) -> list[FakeBar]:
    return [
        FakeBar(
            time=datetime.combine(d, datetime.min.time()),
            open=price, high=price, low=price,
            close=price, adj_close=price, volume=1000,
        )
        for d, price in date_price_pairs
    ]


def _make_strategy(target_weights: dict[str, float], lookback_days: int = 30):
    """Create a mock PortfolioStrategy returning fixed target_weights."""
    strat = MagicMock()
    strat.lookback_days = lookback_days
    strat.generate_weights.return_value = target_weights
    return strat


def _make_calendar_patch(trading_days: list[date], rebalance_days: list[date]):
    """Create a mock TradingCalendar."""
    cal = MagicMock()
    cal.start = trading_days[0] if trading_days else date(2024, 1, 1)
    cal.end = trading_days[-1] if trading_days else date(2024, 12, 31)
    cal.rebalance_dates.return_value = rebalance_days
    return cal


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExecuteDayRebalance:
    """Test that execute_day on a rebalance day produces trades and updates state."""

    def test_execute_day_rebalance(self):
        today = date(2024, 6, 28)  # a Friday
        lookback_dates = [today - timedelta(days=i) for i in range(35, 0, -1)]
        lookback_dates.append(today)  # include today

        spec = _make_spec(symbols=("AAA",), slippage_rate=0.0, min_commission=0.0)
        bars = _make_fake_bars(lookback_dates, close=10.0)
        chain = _mock_data_chain({"AAA": bars})

        # Strategy wants 50% in AAA
        strategy = _make_strategy({"AAA": 0.5})

        engine = PaperTradingEngine(
            spec=spec, strategy=strategy, data_chain=chain,
        )

        # Inject a calendar that says today is a rebalance day
        cal = _make_calendar_patch(lookback_dates, [today])
        engine._calendar = cal

        result = engine.execute_day(today)

        assert result["rebalanced"] is True
        assert len(result["trades"]) > 0, "Should have at least one trade"
        assert result["equity"] > 0

        # Holdings should have AAA
        assert "AAA" in engine.holdings
        assert engine.holdings["AAA"] > 0

        # Cash should be reduced (bought shares)
        assert engine.cash < spec.initial_cash

        # Equity curve recorded
        assert len(engine.equity_curve) == 1
        assert len(engine.dates) == 1
        assert engine.dates[0] == today

    def test_execute_day_surfaces_pretrade_risk_events(self):
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(35, 0, -1)]
        lookback_dates.append(today)

        spec = _make_spec(
            symbols=("AAA",),
            risk_control=True,
            risk_params={"kill_switch": True},
            slippage_rate=0.0,
            min_commission=0.0,
        )
        bars = _make_fake_bars(lookback_dates, close=10.0)
        chain = _mock_data_chain({"AAA": bars})
        strategy = _make_strategy({"AAA": 0.5})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])

        result = engine.execute_day(today)

        assert result["rebalanced"] is True
        assert result["trades"] == []
        assert len(result["risk_events"]) == 1
        assert result["risk_events"][0]["event"] == "pretrade_reject"
        assert result["risk_events"][0]["rule"] == "kill_switch"
        assert len(result["_oms_events"]) == 1
        assert result["_oms_events"][0].event_type == EventType.ORDER_REJECTED

    def test_execute_day_applies_runtime_allocation_gate_and_tracks_statuses(self):
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(35, 0, -1)]
        lookback_dates.append(today)

        spec = _make_spec(
            symbols=("AAA", "BBB"),
            risk_control=True,
            risk_params={"runtime_allocation_cap": 0.50},
            slippage_rate=0.0,
            min_commission=0.0,
        )
        bars = {
            "AAA": _make_fake_bars(lookback_dates, close=10.0),
            "BBB": _make_fake_bars(lookback_dates, close=10.0),
        }
        chain = _mock_data_chain(bars)
        strategy = _make_strategy({"AAA": 0.6, "BBB": 0.4})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])

        result = engine.execute_day(today)

        assert result["risk_events"][0]["event"] == "runtime_allocation_gate"
        assert result["holdings"] == {"AAA": 30_000, "BBB": 20_000}
        assert len(engine._order_statuses) == 2
        assert set(engine._order_statuses.values()) == {"filled"}

    def test_execute_day_applies_runtime_allocator_policy(self):
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(35, 0, -1)]
        lookback_dates.append(today)

        spec = _make_spec(
            symbols=("AAA", "BBB", "CCC"),
            risk_control=True,
            risk_params={
                "allocation_mode": "equal_weight_cap",
                "runtime_allocation_cap": 0.6,
                "max_names": 2,
            },
            slippage_rate=0.0,
            min_commission=0.0,
        )
        bars = {
            "AAA": _make_fake_bars(lookback_dates, close=10.0),
            "BBB": _make_fake_bars(lookback_dates, close=10.0),
            "CCC": _make_fake_bars(lookback_dates, close=10.0),
        }
        chain = _mock_data_chain(bars)
        strategy = _make_strategy({"AAA": 0.5, "BBB": 0.3, "CCC": 0.2})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])

        result = engine.execute_day(today)

        assert result["holdings"] == {"AAA": 30_000, "BBB": 30_000}
        assert result["risk_events"][0]["event"] == "runtime_allocator"
        assert result["risk_events"][0]["details"]["dropped_symbols"] == ["CCC"]
        assert set(engine._order_statuses.values()) == {"filled"}

    def test_execute_day_uses_risk_budget_allocator_from_history_vols(self):
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(40, 0, -1)]
        lookback_dates.append(today)
        aaa_prices = []
        bbb_prices = []
        for idx, d in enumerate(lookback_dates):
            aaa_prices.append((d, 10.0 + ((-1) ** idx) * 1.5))
            bbb_prices.append((d, 10.0 + ((-1) ** idx) * 0.2))

        spec = _make_spec(
            symbols=("AAA", "BBB"),
            risk_control=True,
            risk_params={
                "allocation_mode": "risk_budget_cap",
                "runtime_allocation_cap": 0.6,
                "target_portfolio_vol": 0.15,
                "vol_lookback_days": 20,
            },
            slippage_rate=0.0,
            min_commission=0.0,
        )
        bars = {
            "AAA": _make_fake_bars_prices(aaa_prices),
            "BBB": _make_fake_bars_prices(bbb_prices),
        }
        chain = _mock_data_chain(bars)
        strategy = _make_strategy({"AAA": 0.3, "BBB": 0.3})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])

        result = engine.execute_day(today)

        assert result["risk_events"][0]["event"] == "runtime_allocator"
        details = result["risk_events"][0]["details"]
        assert details["allocation_mode"] == "risk_budget_cap"
        assert details["adjusted_weights"]["BBB"] > details["adjusted_weights"]["AAA"]
        assert details["estimated_portfolio_vol"] > details["target_portfolio_vol"]

    def test_execute_day_uses_constrained_optimizer_allocator_with_current_weights(self):
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(40, 0, -1)]
        lookback_dates.append(today)

        spec = _make_spec(
            symbols=("AAA", "BBB"),
            risk_control=True,
            risk_params={
                "allocation_mode": "constrained_opt",
                "runtime_allocation_cap": 0.8,
                "max_daily_turnover": 0.4,
            },
            slippage_rate=0.0,
            min_commission=0.0,
        )
        bars = {
            "AAA": _make_fake_bars(lookback_dates, close=10.0),
            "BBB": _make_fake_bars(lookback_dates, close=10.0),
        }
        chain = _mock_data_chain(bars)
        strategy = _make_strategy({"AAA": 0.1, "BBB": 0.8})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])
        engine.holdings = {"AAA": 50_000}
        engine.cash = 500_000.0

        result = engine.execute_day(today)

        assert result["holdings"] == {"AAA": 35_000, "BBB": 25_000}
        assert result["risk_events"][0]["event"] == "runtime_allocator"
        assert result["risk_events"][0]["details"]["allocation_mode"] == "constrained_opt"
        assert result["risk_events"][0]["details"]["effective_turnover"] == pytest.approx(0.4)
        assert set(engine._order_statuses.values()) == {"filled"}

    def test_execute_day_uses_covariance_aware_allocator_from_history(self):
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(60, 0, -1)]
        lookback_dates.append(today)
        aaa_prices = []
        bbb_prices = []
        for idx, d in enumerate(lookback_dates):
            aaa_prices.append((d, 10.0 + ((-1) ** idx) * 1.5))
            bbb_prices.append((d, 10.0 + ((-1) ** idx) * 0.2))

        spec = _make_spec(
            symbols=("AAA", "BBB"),
            risk_control=True,
            risk_params={
                "allocation_mode": "constrained_opt",
                "runtime_allocation_cap": 0.8,
                "covariance_risk_aversion": 8.0,
                "risk_budget_strength": 0.5,
                "covariance_lookback_days": 40,
            },
            slippage_rate=0.0,
            min_commission=0.0,
        )
        bars = {
            "AAA": _make_fake_bars_prices(aaa_prices),
            "BBB": _make_fake_bars_prices(bbb_prices),
        }
        chain = _mock_data_chain(bars)
        strategy = _make_strategy({"AAA": 0.4, "BBB": 0.4})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])

        result = engine.execute_day(today)

        details = result["risk_events"][0]["details"]
        assert details["allocation_mode"] == "constrained_opt"
        assert details["covariance_used"] is True
        assert details["adjusted_weights"]["BBB"] > details["adjusted_weights"]["AAA"]
        assert result["holdings"]["BBB"] > result["holdings"].get("AAA", 0)

    def test_execute_day_surfaces_market_bar_payloads(self):
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(35, 0, -1)]
        lookback_dates.append(today)

        spec = _make_spec(symbols=("AAA",), slippage_rate=0.0, min_commission=0.0)
        bars = {"AAA": _make_fake_bars(lookback_dates, close=10.0)}
        chain = _mock_data_chain(bars)
        strategy = _make_strategy({"AAA": 0.5})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])

        result = engine.execute_day(today)

        assert result["_market_snapshot"]["has_bar_symbols"] == ["AAA"]
        assert len(result["_market_bars"]) == 1
        assert result["_market_bars"][0]["symbol"] == "AAA"
        assert result["_market_bars"][0]["adj_close"] == 10.0

    def test_strategy_receives_datetime_not_date(self):
        """Verify that strategy.generate_weights() receives datetime, not date."""
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(35, 0, -1)]
        lookback_dates.append(today)

        spec = _make_spec(symbols=("AAA",))
        bars = _make_fake_bars(lookback_dates, close=10.0)
        chain = _mock_data_chain({"AAA": bars})
        strategy = _make_strategy({"AAA": 0.5})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])

        engine.execute_day(today)

        # Check the second argument to generate_weights is a datetime
        call_args = strategy.generate_weights.call_args
        date_arg = call_args[0][1]  # positional arg index 1
        assert isinstance(date_arg, datetime), f"Expected datetime, got {type(date_arg)}"


class TestExecuteDayNonRebalance:
    """Test that non-rebalance days record equity without trading."""

    def test_no_trades_on_non_rebalance_day(self):
        today = date(2024, 6, 26)  # Wednesday
        lookback_dates = [today - timedelta(days=i) for i in range(35, 0, -1)]
        lookback_dates.append(today)

        spec = _make_spec(symbols=("AAA",))
        bars = _make_fake_bars(lookback_dates, close=10.0)
        chain = _mock_data_chain({"AAA": bars})
        strategy = _make_strategy({"AAA": 0.5})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        # Calendar says NO rebalance dates at all
        engine._calendar = _make_calendar_patch(lookback_dates, [])

        result = engine.execute_day(today)

        assert result["rebalanced"] is False
        assert result["trades"] == []
        assert result["equity"] == spec.initial_cash  # only cash, no holdings
        assert len(engine.equity_curve) == 1

        # Strategy should NOT have been called
        strategy.generate_weights.assert_not_called()


class TestExecuteDayEmptyData:
    """Test graceful handling when no data is available."""

    def test_no_data_returns_cash_equity(self):
        today = date(2024, 6, 28)
        spec = _make_spec(symbols=("AAA", "BBB"))
        chain = _mock_data_chain({})  # no data for any symbol
        strategy = _make_strategy({"AAA": 0.5, "BBB": 0.5})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)

        result = engine.execute_day(today)

        assert result["rebalanced"] is False
        assert result["trades"] == []
        assert result["equity"] == spec.initial_cash
        assert len(engine.equity_curve) == 1
        assert engine.dates[0] == today

    def test_data_fetch_exception_is_handled(self):
        """If get_kline raises, the symbol is skipped gracefully."""
        today = date(2024, 6, 28)
        spec = _make_spec(symbols=("AAA",))

        chain = MagicMock()
        chain.get_kline.side_effect = RuntimeError("network error")
        strategy = _make_strategy({"AAA": 0.5})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)

        # Should not raise
        result = engine.execute_day(today)
        assert result["equity"] == spec.initial_cash


class TestEquityUsePostTrade:
    """After a rebalance with costs, equity should reflect trading costs."""

    def test_equity_less_than_initial_after_trade_with_costs(self):
        """Trading costs (commission + stamp tax) should reduce equity."""
        today = date(2024, 6, 28)
        lookback_dates = [today - timedelta(days=i) for i in range(35, 0, -1)]
        lookback_dates.append(today)

        spec = _make_spec(
            symbols=("AAA",),
            buy_commission_rate=0.001,   # 0.1% commission
            sell_commission_rate=0.001,
            stamp_tax_rate=0.001,        # 0.1% stamp tax
            slippage_rate=0.001,         # 0.1% slippage
            min_commission=5.0,
            initial_cash=1_000_000.0,
        )

        bars = _make_fake_bars(lookback_dates, close=10.0)
        chain = _mock_data_chain({"AAA": bars})

        # Strategy wants 90% in AAA — big trade means noticeable costs
        strategy = _make_strategy({"AAA": 0.9})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        engine._calendar = _make_calendar_patch(lookback_dates, [today])

        result = engine.execute_day(today)

        assert result["rebalanced"] is True
        assert len(result["trades"]) > 0

        # Equity after trade should be less than initial cash
        # because of buy commission + slippage
        assert result["equity"] < spec.initial_cash, (
            f"Expected equity < {spec.initial_cash} due to trading costs, "
            f"got {result['equity']}"
        )

    def test_multiple_days_equity_curve_grows(self):
        """Run two consecutive days — equity curve should have two entries."""
        day1 = date(2024, 6, 27)
        day2 = date(2024, 6, 28)
        all_dates = [day1 - timedelta(days=i) for i in range(35, 0, -1)]
        all_dates.extend([day1, day2])

        spec = _make_spec(symbols=("AAA",), slippage_rate=0.0, min_commission=0.0)
        bars = _make_fake_bars(all_dates, close=10.0)
        chain = _mock_data_chain({"AAA": bars})
        strategy = _make_strategy({"AAA": 0.5})

        engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)
        # Only day1 is rebalance
        engine._calendar = _make_calendar_patch(all_dates, [day1])

        r1 = engine.execute_day(day1)
        r2 = engine.execute_day(day2)

        assert len(engine.equity_curve) == 2
        assert len(engine.dates) == 2
        assert engine.dates == [day1, day2]
        assert r1["rebalanced"] is True
        assert r2["rebalanced"] is False
