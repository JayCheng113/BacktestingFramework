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
