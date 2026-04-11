"""Unit tests for RunStrategiesStep.

Uses real ez.backtest.engine + builtin Strategy on synthetic data.
This is integration-light: tests verify the step's data flow + error
handling, not the engine's correctness (which is covered by tests/test_backtest/).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from ez.research.context import PipelineContext
from ez.research.pipeline import ResearchPipeline, StepError
from ez.research.steps.run_strategies import RunStrategiesStep
from ez.strategy.base import Strategy
from ez.factor.base import Factor


# ============================================================
# Test fixtures: minimal synthetic strategy + data
# ============================================================

class _NoopFactor(Factor):
    """A trivial factor for the test strategy."""
    name = "noop"
    warmup_period = 0
    def compute(self, data):
        out = data.copy()
        out[self.name] = 0.0
        return out


class _BuyHoldStrategy(Strategy):
    """Always-long buy & hold."""
    def required_factors(self):
        return [_NoopFactor()]
    def generate_signals(self, data):
        return pd.Series([1.0] * len(data), index=data.index)


class _RaisingStrategy(Strategy):
    """Strategy that crashes during signal generation."""
    def required_factors(self):
        return [_NoopFactor()]
    def generate_signals(self, data):
        raise RuntimeError("intentional strategy crash")


def _make_df(start: str, periods: int, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=periods, freq="B")
    r = rng.normal(0.0005, 0.015, periods)
    price = 100 * np.cumprod(1 + r)
    return pd.DataFrame({
        "open": price,
        "high": price * 1.01,
        "low": price * 0.99,
        "close": price,
        "adj_close": price,
        "volume": rng.integers(100_000, 1_000_000, periods).astype(float),
    }, index=idx)


def _make_context(symbols: dict[str, pd.DataFrame]) -> PipelineContext:
    return PipelineContext(artifacts={"universe_data": symbols})


# ============================================================
# Constructor validation
# ============================================================

def test_raises_when_strategies_empty():
    with pytest.raises(ValueError, match="at least one strategy"):
        RunStrategiesStep(strategies={})


# ============================================================
# Single-strategy happy path
# ============================================================

def test_runs_single_strategy_and_writes_artifacts():
    ctx = _make_context({"AAA": _make_df("2024-01-01", 100)})
    step = RunStrategiesStep(strategies={"AAA": _BuyHoldStrategy()})
    out = step.run(ctx)

    assert "returns" in out.artifacts
    assert "metrics" in out.artifacts
    assert "equity_curves" in out.artifacts

    returns_df = out.artifacts["returns"]
    assert isinstance(returns_df, pd.DataFrame)
    assert "AAA" in returns_df.columns
    assert len(returns_df) > 0

    metrics = out.artifacts["metrics"]
    assert "AAA" in metrics
    assert "sharpe_ratio" in metrics["AAA"]
    assert "total_return" in metrics["AAA"]

    equity = out.artifacts["equity_curves"]
    assert "AAA" in equity
    assert len(equity["AAA"]) > 0


# ============================================================
# Multi-strategy
# ============================================================

def test_runs_multiple_strategies_aligned_to_common_index():
    ctx = _make_context({
        "AAA": _make_df("2024-01-01", 100, seed=1),
        "BBB": _make_df("2024-01-01", 100, seed=2),
        "CCC": _make_df("2024-01-01", 100, seed=3),
    })
    step = RunStrategiesStep(strategies={
        "AAA": _BuyHoldStrategy(),
        "BBB": _BuyHoldStrategy(),
        "CCC": _BuyHoldStrategy(),
    })
    out = step.run(ctx)
    returns_df = out.artifacts["returns"]
    assert set(returns_df.columns) == {"AAA", "BBB", "CCC"}
    assert isinstance(returns_df.index, pd.DatetimeIndex)

    metrics = out.artifacts["metrics"]
    assert set(metrics.keys()) == {"AAA", "BBB", "CCC"}


# ============================================================
# Error handling
# ============================================================

def test_skips_strategy_when_symbol_missing_from_universe_data():
    """If a strategy's label has no matching symbol, skip with warning."""
    ctx = _make_context({"AAA": _make_df("2024-01-01", 100)})
    step = RunStrategiesStep(strategies={
        "AAA": _BuyHoldStrategy(),
        "MISSING": _BuyHoldStrategy(),
    })
    out = step.run(ctx)
    assert set(out.artifacts["returns"].columns) == {"AAA"}
    skipped = out.artifacts["run_strategies_skipped"]
    assert any(label == "MISSING" and "not in universe_data" in reason for label, reason in skipped)


def test_skips_strategy_that_raises():
    ctx = _make_context({
        "AAA": _make_df("2024-01-01", 100),
        "BBB": _make_df("2024-01-01", 100),
    })
    step = RunStrategiesStep(strategies={
        "AAA": _BuyHoldStrategy(),
        "BBB": _RaisingStrategy(),
    })
    out = step.run(ctx)
    assert set(out.artifacts["returns"].columns) == {"AAA"}
    skipped = out.artifacts["run_strategies_skipped"]
    assert any(
        label == "BBB" and "intentional strategy crash" in reason
        for label, reason in skipped
    )


def test_raises_when_all_strategies_failed():
    ctx = _make_context({"AAA": _make_df("2024-01-01", 100)})
    step = RunStrategiesStep(strategies={"AAA": _RaisingStrategy()})
    with pytest.raises(RuntimeError, match="no strategies ran successfully"):
        step.run(ctx)


def test_raises_when_universe_data_missing():
    """Step requires universe_data — if upstream forgot DataLoadStep, error clearly."""
    ctx = PipelineContext()
    step = RunStrategiesStep(strategies={"AAA": _BuyHoldStrategy()})
    with pytest.raises(KeyError, match="universe_data"):
        step.run(ctx)


# ============================================================
# Pipeline integration
# ============================================================

def test_pipeline_chains_data_load_and_run_strategies(monkeypatch):
    """End-to-end via pipeline: monkey-patched data load → real strategy run."""
    from ez.research.steps.data_load import DataLoadStep

    def fake_fetch(self, symbol, market, period, start, end):
        return _make_df("2024-01-01", 100)
    monkeypatch.setattr(DataLoadStep, "_fetch_one", fake_fetch)

    pipeline = ResearchPipeline([
        DataLoadStep(symbols=["AAA", "BBB"], start_date="2024-01-01", end_date="2024-12-31"),
        RunStrategiesStep(strategies={
            "AAA": _BuyHoldStrategy(),
            "BBB": _BuyHoldStrategy(),
        }),
    ])
    ctx = pipeline.run()
    assert set(ctx.artifacts["returns"].columns) == {"AAA", "BBB"}
    assert "metrics" in ctx.artifacts
    assert len(ctx.history) == 2
    assert ctx.history[0].step_name == "data_load"
    assert ctx.history[1].step_name == "run_strategies"
