"""Full pipeline: data -> factor -> strategy -> backtest -> metrics."""
from datetime import date

from tests.mocks.mock_provider import MockDataProvider
from ez.data.store import DuckDBStore
from ez.data.provider import DataProviderChain
from ez.strategy.builtin.ma_cross import MACrossStrategy
from ez.backtest.engine import VectorizedBacktestEngine
from ez.backtest.walk_forward import WalkForwardValidator
import pandas as pd


def test_full_pipeline_with_mock(tmp_path):
    store = DuckDBStore(str(tmp_path / "test.db"))
    provider = MockDataProvider()
    chain = DataProviderChain(providers=[provider], store=store)

    bars = chain.get_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2025, 12, 31))
    assert len(bars) > 50

    df = pd.DataFrame([
        {
            "time": b.time, "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
        }
        for b in bars
    ]).set_index("time")

    strategy = MACrossStrategy(short_period=5, long_period=10)
    engine = VectorizedBacktestEngine(commission_rate=0.0003)
    result = engine.run(df, strategy, initial_capital=100000)

    assert result.metrics["sharpe_ratio"] is not None
    assert len(result.equity_curve) > 0
    assert result.equity_curve.iloc[-1] > 0
    assert result.significance is not None

    cached = store.query_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2025, 12, 31))
    assert len(cached) == len(bars)
    store.close()


def test_walk_forward_pipeline(sample_df):
    strategy = MACrossStrategy(short_period=5, long_period=10)
    validator = WalkForwardValidator()
    wf = validator.validate(sample_df, strategy, n_splits=3)
    assert len(wf.splits) > 0
    assert wf.overfitting_score >= 0
    assert "sharpe_ratio" in wf.oos_metrics
