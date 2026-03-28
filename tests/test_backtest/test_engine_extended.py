"""Extended engine tests — edge cases, position sizing, empty data."""
import numpy as np
import pandas as pd
import pytest
from ez.backtest.engine import VectorizedBacktestEngine
from ez.strategy.base import Strategy
from ez.factor.base import Factor
from ez.factor.builtin.technical import MA


class NullStrategy(Strategy):
    """Strategy that never trades (all signals = 0)."""
    def required_factors(self): return []
    def generate_signals(self, data): return pd.Series(0.0, index=data.index)


class AlwaysInStrategy(Strategy):
    """Strategy that is always fully invested."""
    def required_factors(self): return []
    def generate_signals(self, data): return pd.Series(1.0, index=data.index)


@pytest.fixture
def engine():
    return VectorizedBacktestEngine(commission_rate=0.001, min_commission=0.0)


@pytest.fixture
def simple_df():
    """10-bar DataFrame with known prices."""
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    prices = [10.0, 10.5, 11.0, 10.8, 10.2, 10.5, 11.2, 11.5, 11.0, 10.8]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 0.3 for p in prices],
        "low": [p - 0.3 for p in prices],
        "close": prices,
        "adj_close": prices,
        "volume": [1000000] * 10,
    }, index=dates)


def test_null_strategy_no_trades(engine, simple_df):
    result = engine.run(simple_df, NullStrategy(), initial_capital=100000)
    assert result.metrics["trade_count"] == 0
    assert result.metrics["win_rate"] == 0.0
    assert result.equity_curve.iloc[0] == 100000
    assert result.equity_curve.iloc[-1] == 100000  # no trades -> no change


def test_always_in_strategy_tracks_market(engine, simple_df):
    result = engine.run(simple_df, AlwaysInStrategy(), initial_capital=100000)
    assert len(result.equity_curve) == 10
    assert result.equity_curve.iloc[-1] != 100000  # should have changed


def test_empty_dataframe_after_warmup(engine):
    """Data shorter than warmup period should return minimal result."""
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    df = pd.DataFrame({
        "open": [10.0, 10.5, 11.0], "high": [10.5, 11.0, 11.5],
        "low": [9.5, 10.0, 10.5], "close": [10.5, 11.0, 10.8],
        "adj_close": [10.5, 11.0, 10.8], "volume": [100] * 3,
    }, index=dates)
    strategy = type("BigWarmup", (Strategy,), {
        "required_factors": lambda self: [MA(period=20)],
        "generate_signals": lambda self, data: pd.Series(1.0, index=data.index),
    })()
    result = engine.run(df, strategy, initial_capital=100000)
    assert result.metrics["trade_count"] == 0
    assert result.equity_curve.iloc[0] == 100000


def test_single_bar_after_warmup(engine):
    """Single tradeable bar should not crash."""
    dates = pd.date_range("2024-01-02", periods=6, freq="B")
    df = pd.DataFrame({
        "open": [10.0] * 6, "high": [10.5] * 6, "low": [9.5] * 6,
        "close": [10.0] * 6, "adj_close": [10.0] * 6, "volume": [100] * 6,
    }, index=dates)
    strategy = type("MA5", (Strategy,), {
        "required_factors": lambda self: [MA(period=5)],
        "generate_signals": lambda self, data: pd.Series(1.0, index=data.index),
    })()
    result = engine.run(df, strategy, initial_capital=100000)
    assert len(result.equity_curve) >= 1


def test_commission_reduces_equity(simple_df):
    e_no_comm = VectorizedBacktestEngine(commission_rate=0.0, min_commission=0.0)
    e_high_comm = VectorizedBacktestEngine(commission_rate=0.05, min_commission=0.0)
    r1 = e_no_comm.run(simple_df, AlwaysInStrategy(), 100000)
    r2 = e_high_comm.run(simple_df, AlwaysInStrategy(), 100000)
    assert r1.equity_curve.iloc[-1] >= r2.equity_curve.iloc[-1]


def test_equity_curve_length_matches_data(engine, simple_df):
    strategy = type("MA3", (Strategy,), {
        "required_factors": lambda self: [MA(period=3)],
        "generate_signals": lambda self, data: pd.Series(1.0, index=data.index),
    })()
    result = engine.run(simple_df, strategy, initial_capital=100000)
    warmup = 3
    assert len(result.equity_curve) == len(simple_df) - warmup


def test_benchmark_is_buy_and_hold(engine, simple_df):
    """Benchmark should reflect buy-and-hold returns."""
    result = engine.run(simple_df, NullStrategy(), initial_capital=100000)
    # Benchmark starts at initial_capital and tracks adj_close returns
    assert result.benchmark_curve.iloc[0] == pytest.approx(100000, rel=0.01)
    assert len(result.benchmark_curve) == len(result.equity_curve)


def test_daily_returns_first_is_zero(engine, simple_df):
    result = engine.run(simple_df, AlwaysInStrategy(), initial_capital=100000)
    assert result.daily_returns.iloc[0] == 0.0


def test_significance_returned(engine, simple_df):
    result = engine.run(simple_df, AlwaysInStrategy(), initial_capital=100000)
    assert result.significance is not None
    assert isinstance(result.significance.monte_carlo_p_value, float)


class ScaleInStrategy(Strategy):
    """Strategy that scales into position: 0 → 0.3 → 0.6 → 1.0 → 0."""
    def required_factors(self): return []
    def generate_signals(self, data):
        n = len(data)
        signals = [0.0] * n
        # Scale in over bars 1-3, then exit at bar 6
        if n > 6:
            signals[1] = 0.3
            signals[2] = 0.6
            signals[3] = 1.0
            signals[4] = 1.0
            signals[5] = 1.0
            signals[6] = 0.0
        return pd.Series(signals, index=data.index)


def test_cumulative_entry_commission(simple_df):
    """Multiple buys should accumulate entry commission in trade record."""
    engine = VectorizedBacktestEngine(commission_rate=0.001, min_commission=0.0)
    result = engine.run(simple_df, ScaleInStrategy(), initial_capital=100000)
    # Should produce at least 1 trade with commission from multiple buys
    if result.trades:
        t = result.trades[0]
        # Commission should reflect BOTH entry (multiple buys) and exit
        assert t.commission > 0
        # PnL should include all commissions
        # Verify pnl accounts for entry comm: pnl = (exit-entry)*shares - exit_comm - entry_comm
        # So pnl + commission should roughly equal raw price gain * shares
        assert t.pnl < (t.exit_price - t.entry_price) * 10000  # pnl < raw gain (commission reduces it)


def test_buy_fail_does_not_zero_equity(simple_df):
    """When min_commission makes buy impossible, equity must not drop to zero."""
    engine = VectorizedBacktestEngine(commission_rate=0.001, min_commission=200000.0)
    result = engine.run(simple_df, AlwaysInStrategy(), initial_capital=100000)
    # Every bar should have equity == initial capital (no trades possible)
    assert (result.equity_curve == 100000).all()
    assert result.metrics["trade_count"] == 0


def test_partial_sell_pnl_accounting(simple_df):
    """Scale-out PnL must match equity change."""
    engine = VectorizedBacktestEngine(commission_rate=0.001, min_commission=0.0)
    result = engine.run(simple_df, ScaleInStrategy(), initial_capital=100000)
    total_trade_pnl = sum(t.pnl for t in result.trades)
    equity_change = result.equity_curve.iloc[-1] - 100000
    assert abs(total_trade_pnl - equity_change) < 1.0  # within $1 rounding


def test_metrics_keys_complete(engine, simple_df):
    result = engine.run(simple_df, AlwaysInStrategy(), initial_capital=100000)
    required_keys = {"sharpe_ratio", "total_return", "max_drawdown", "max_drawdown_duration", "win_rate", "trade_count", "profit_factor", "avg_holding_days"}
    assert required_keys.issubset(set(result.metrics.keys()))
