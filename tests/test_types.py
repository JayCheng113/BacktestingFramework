from datetime import datetime
from ez.types import Bar, TradeRecord, BacktestResult, SignificanceTest, WalkForwardResult, FactorAnalysis
import pandas as pd


def test_bar_creation():
    bar = Bar(
        time=datetime(2024, 1, 2),
        symbol="000001.SZ",
        market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2,
        adj_close=10.15, volume=1000000,
    )
    assert bar.symbol == "000001.SZ"
    assert bar.market == "cn_stock"
    assert bar.volume == 1000000


def test_trade_record_creation():
    tr = TradeRecord(
        entry_time=datetime(2024, 1, 2),
        exit_time=datetime(2024, 1, 10),
        entry_price=10.0, exit_price=11.0,
        weight=1.0, pnl=1000.0, pnl_pct=0.1, commission=3.0,
    )
    assert tr.pnl_pct == 0.1


def test_significance_test_creation():
    sig = SignificanceTest(
        sharpe_ci_lower=0.5, sharpe_ci_upper=1.5,
        monte_carlo_p_value=0.03, is_significant=True,
    )
    assert sig.is_significant is True


def test_backtest_result_creation():
    result = BacktestResult(
        equity_curve=pd.Series([100000, 101000, 102000]),
        benchmark_curve=pd.Series([100000, 100500, 101000]),
        trades=[],
        metrics={"sharpe_ratio": 1.5},
        signals=pd.Series([0.0, 1.0, 1.0]),
        daily_returns=pd.Series([0.0, 0.01, 0.0099]),
        significance=SignificanceTest(
            sharpe_ci_lower=0.5, sharpe_ci_upper=2.5,
            monte_carlo_p_value=0.02, is_significant=True,
        ),
    )
    assert result.metrics["sharpe_ratio"] == 1.5
    assert result.significance.is_significant is True
