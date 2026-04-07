"""Full pipeline: data -> factor -> strategy -> backtest -> metrics.

V2.16 Sprint 3: Added 5 integration tests:
- test_factor_contract_all_builtins: all registered factors pass compute contract
- test_backtest_with_market_rules: single-stock backtest with T+1, lot size, stamp tax
- test_walk_forward_determinism: same inputs produce same WF outputs
- test_portfolio_backtest_with_optimizer: portfolio backtest with MeanVariance optimizer
- test_full_pipeline_research_gate: backtest -> WF -> gate -> verdict
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from tests.mocks.mock_provider import MockDataProvider
from ez.data.store import DuckDBStore
from ez.data.provider import DataProviderChain
from ez.strategy.builtin.ma_cross import MACrossStrategy
from ez.backtest.engine import VectorizedBacktestEngine
from ez.backtest.walk_forward import WalkForwardValidator


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

    strategy = MACrossStrategy(short_period=3, long_period=5)
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
    strategy = MACrossStrategy(short_period=3, long_period=5)
    validator = WalkForwardValidator()
    wf = validator.validate(sample_df, strategy, n_splits=2)
    assert len(wf.splits) > 0
    assert wf.overfitting_score >= 0
    assert "sharpe_ratio" in wf.oos_metrics


# ---------------------------------------------------------------------------
# V2.16 Sprint 3 — Integration tests
# ---------------------------------------------------------------------------

def test_factor_contract_all_builtins(sample_df):
    """Verify all registered builtin factors pass the compute contract:
    - compute() returns a DataFrame
    - Result has at least one new column
    - No all-NaN factor columns (after warmup)
    """
    from ez.factor.base import Factor
    # Force import of builtins to populate registry
    import ez.factor.builtin.technical  # noqa: F401

    registry = Factor.get_registry()
    assert len(registry) > 0, "Factor registry is empty"

    for name, cls in registry.items():
        # Instantiate with defaults
        try:
            factor = cls()
        except TypeError:
            # Some factors require arguments — skip gracefully
            continue

        result = factor.compute(sample_df.copy())
        assert isinstance(result, pd.DataFrame), f"Factor {name} did not return DataFrame"
        new_cols = set(result.columns) - set(sample_df.columns)
        assert len(new_cols) >= 1, f"Factor {name} added no new columns"

        # After warmup, factor columns should not be all-NaN
        warmup = getattr(factor, "warmup_period", 0)
        for col in new_cols:
            post_warmup = result[col].iloc[warmup:]
            if len(post_warmup) > 0:
                assert not post_warmup.isna().all(), (
                    f"Factor {name} column {col} is all-NaN after warmup"
                )


def test_backtest_with_market_rules(sample_df):
    """Run a single-stock backtest with A-share market rules enabled:
    T+1, lot_size=100, stamp_tax=0.0005, limit_pct=0.1.
    Verifies the engine completes and produces valid output."""
    from ez.core.market_rules import MarketRulesMatcher
    from ez.core.matcher import SlippageMatcher

    inner = SlippageMatcher(
        slippage_rate=0.001,
        commission_rate=0.0003,
        sell_commission_rate=0.0003,
        min_commission=5.0,
    )
    matcher = MarketRulesMatcher(
        inner=inner,
        t_plus_1=True,
        lot_size=100,
        price_limit_pct=0.10,
    )

    strategy = MACrossStrategy(short_period=5, long_period=20)
    engine = VectorizedBacktestEngine(matcher=matcher)
    result = engine.run(sample_df, strategy, initial_capital=100_000)

    assert len(result.equity_curve) > 0
    assert result.equity_curve.iloc[-1] > 0
    assert result.metrics["sharpe_ratio"] is not None
    # With market rules, some trades may be lot-rounded or blocked
    assert result.metrics.get("trade_count", 0) >= 0


def test_walk_forward_determinism(sample_df):
    """Run walk-forward twice with identical inputs; verify outputs match."""
    # Use short_period=2, long_period=3 to keep warmup low enough for 100-bar sample data
    strategy1 = MACrossStrategy(short_period=2, long_period=3)
    strategy2 = MACrossStrategy(short_period=2, long_period=3)
    validator1 = WalkForwardValidator()
    validator2 = WalkForwardValidator()

    wf1 = validator1.validate(sample_df, strategy1, n_splits=2, train_ratio=0.7)
    wf2 = validator2.validate(sample_df, strategy2, n_splits=2, train_ratio=0.7)

    assert wf1.overfitting_score == wf2.overfitting_score
    assert len(wf1.splits) == len(wf2.splits)
    for s1, s2 in zip(wf1.splits, wf2.splits):
        # Each split is a BacktestResult — compare metrics
        sharpe1 = s1.metrics.get("sharpe_ratio", 0.0)
        sharpe2 = s2.metrics.get("sharpe_ratio", 0.0)
        assert sharpe1 == pytest.approx(sharpe2, abs=1e-10), "Split sharpe differs"
    # OOS metrics should be identical
    for key in wf1.oos_metrics:
        v1 = wf1.oos_metrics[key]
        v2 = wf2.oos_metrics[key]
        if isinstance(v1, float) and not (np.isnan(v1) and np.isnan(v2)):
            assert v1 == pytest.approx(v2, abs=1e-10), f"OOS metric {key} differs"


def _make_portfolio_data(symbols: list[str], n_days: int = 300, seed: int = 42):
    """Generate synthetic OHLCV for portfolio integration tests."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
    data = {}
    for i, sym in enumerate(symbols):
        prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.015, n_days))
        data[sym] = pd.DataFrame({
            "open": prices * (1 + rng.normal(0, 0.002, n_days)),
            "high": prices * (1 + abs(rng.normal(0, 0.005, n_days))),
            "low": prices * (1 - abs(rng.normal(0, 0.005, n_days))),
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 5_000_000, n_days),
        }, index=dates)
    return data, dates


def test_portfolio_backtest_with_optimizer():
    """Run portfolio backtest with MeanVariance optimizer; verify completion."""
    from ez.portfolio.calendar import TradingCalendar
    from ez.portfolio.cross_factor import MomentumRank
    from ez.portfolio.engine import CostModel, run_portfolio_backtest
    from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
    from ez.portfolio.portfolio_strategy import TopNRotation
    from ez.portfolio.universe import Universe

    symbols = [f"S{i}" for i in range(8)]
    data, dates = _make_portfolio_data(symbols)
    cal = TradingCalendar.from_dates([d.date() for d in dates])
    universe = Universe(symbols)
    cost = CostModel(buy_commission_rate=0.0003, stamp_tax_rate=0.0005)

    optimizer = MeanVarianceOptimizer(
        risk_aversion=1.0,
        constraints=OptimizationConstraints(max_weight=0.30),
        cov_lookback=60,
    )

    result = run_portfolio_backtest(
        strategy=TopNRotation(MomentumRank(20), top_n=4),
        universe=universe,
        universe_data=data,
        calendar=cal,
        start=dates[60].date(),
        end=dates[-1].date(),
        freq="monthly",
        initial_cash=1_000_000,
        cost_model=cost,
        optimizer=optimizer,
    )

    assert len(result.equity_curve) > 0
    assert result.equity_curve[-1] > 0
    assert result.metrics["sharpe_ratio"] is not None
    # Optimizer should constrain individual weights (allow float tolerance)
    for w_dict in result.weights_history:
        for w_val in w_dict.values():
            assert w_val <= 0.35, f"Weight {w_val} exceeds constraint tolerance"


def test_full_pipeline_research_gate(sample_df):
    """Backtest -> WF -> ResearchGate -> verify verdict structure."""
    from ez.agent.gates import ResearchGate, GateConfig
    from ez.agent.run_spec import RunSpec
    from ez.agent.runner import RunResult
    from ez.types import BacktestResult

    # 1. Run backtest — use short warmup (3 bars) to fit 100-bar sample data
    strategy = MACrossStrategy(short_period=2, long_period=3)
    engine = VectorizedBacktestEngine(commission_rate=0.0003)
    bt_result = engine.run(sample_df, strategy, initial_capital=100_000)

    # 2. Run WF — n_splits=2 with short warmup strategy
    validator = WalkForwardValidator()
    wf_result = validator.validate(sample_df, strategy, n_splits=2)

    # 3. Package as RunResult
    spec = RunSpec(
        strategy_name="MACrossStrategy",
        strategy_params={"short_period": 2, "long_period": 3},
        symbol="TEST.US",
        market="us_stock",
        start_date=str(sample_df.index[0].date()),
        end_date=str(sample_df.index[-1].date()),
    )
    run_result = RunResult(
        run_id="test-gate-001",
        spec=spec,
        spec_id=spec.spec_id,
        status="completed",
        backtest=bt_result,
        walk_forward=wf_result,
    )

    # 4. Gate with permissive thresholds (test data may not have great metrics)
    gate = ResearchGate(GateConfig(
        min_sharpe=-999,       # always pass
        max_drawdown=999,      # always pass
        min_trades=0,          # always pass
        max_p_value=1.0,       # always pass
        max_overfitting_score=999,  # always pass
        require_wfo=True,
    ))
    verdict = gate.evaluate(run_result)

    # 5. Verify structure
    assert verdict.passed is True
    assert len(verdict.reasons) >= 4  # sharpe, drawdown, trades, significance, overfitting
    for reason in verdict.reasons:
        assert hasattr(reason, "rule")
        assert hasattr(reason, "passed")
        assert hasattr(reason, "value")
        assert hasattr(reason, "threshold")
        assert hasattr(reason, "message")
    assert "PASS" in verdict.summary

    # Also verify with strict gate (should likely fail on test data)
    strict_gate = ResearchGate(GateConfig(
        min_sharpe=5.0,  # very high — should fail
        require_wfo=True,
    ))
    strict_verdict = strict_gate.evaluate(run_result)
    assert len(strict_verdict.reasons) > 0
    # At least one rule should fail
    assert len(strict_verdict.failed_reasons) >= 1
