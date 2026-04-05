import pandas as pd
import pytest
from ez.backtest.engine import VectorizedBacktestEngine
from ez.strategy.builtin.ma_cross import MACrossStrategy


def test_engine_runs_without_error(sample_df):
    engine = VectorizedBacktestEngine()
    strategy = MACrossStrategy(short_period=5, long_period=10)
    result = engine.run(sample_df, strategy, initial_capital=100000)
    assert result is not None
    assert len(result.equity_curve) > 0


def test_engine_shifts_signals(sample_df):
    engine = VectorizedBacktestEngine()
    strategy = MACrossStrategy(short_period=5, long_period=10)
    result = engine.run(sample_df, strategy, initial_capital=100000)
    assert result.equity_curve.iloc[0] == pytest.approx(100000, rel=0.01)


def test_engine_respects_commission(sample_df):
    engine_no_comm = VectorizedBacktestEngine(commission_rate=0.0)
    engine_with_comm = VectorizedBacktestEngine(commission_rate=0.01)
    strategy = MACrossStrategy(short_period=5, long_period=10)
    r1 = engine_no_comm.run(sample_df, strategy, initial_capital=100000)
    r2 = engine_with_comm.run(sample_df, strategy, initial_capital=100000)
    assert r1.equity_curve.iloc[-1] >= r2.equity_curve.iloc[-1]


def test_engine_produces_significance(sample_df):
    engine = VectorizedBacktestEngine()
    strategy = MACrossStrategy(short_period=5, long_period=10)
    result = engine.run(sample_df, strategy, initial_capital=100000)
    assert result.significance is not None
    assert isinstance(result.significance.monte_carlo_p_value, float)


class _AlwaysInStrategy:
    """Always-long strategy that holds from first to last bar.

    Used to verify V2.12.2 round 5 terminal liquidation fix — buy-and-hold
    strategies previously had trade_count=0 because no close signal was
    generated and the engine did not synthesize a terminal trade.
    """
    def required_factors(self):
        return []
    def generate_signals(self, df):
        return pd.Series([1.0] * len(df), index=df.index)


def test_engine_terminal_liquidation_records_held_position(sample_df):
    """V2.12.2 codex round 5: held-to-end strategies must produce a
    terminal TradeRecord so trade_count / win_rate / profit_factor /
    avg_holding_days don't silently report 0."""
    engine = VectorizedBacktestEngine()
    strategy = _AlwaysInStrategy()
    result = engine.run(sample_df, strategy, initial_capital=100000)
    # Prior version: trades == [] because no explicit close signal fired.
    # Fix: synthesize terminal TradeRecord on last bar.
    assert len(result.trades) >= 1, (
        "Buy-and-hold strategy should have a terminal liquidation trade; "
        "prior version silently dropped the held-to-end position."
    )
    assert result.metrics["trade_count"] >= 1
    # Holding period should span at least some time (not same-bar close)
    last_trade = result.trades[-1]
    holding_days = (last_trade.exit_time - last_trade.entry_time).days
    assert holding_days > 0, "Terminal trade should have positive holding duration"
