"""End-to-end tests for ResearchPipeline (load → run → report).

These tests assemble the full V2.20.0 MVP pipeline and verify:
  - Pipeline runs all 3 steps in order
  - Artifacts flow correctly between steps
  - Final report contains expected sections
  - Output file is written correctly

Strategy/Factor are duck-typed (no ABC inheritance) to avoid
polluting the global registries — see test_run_strategies.py rationale.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ez.research import PipelineContext, ResearchPipeline
from ez.research.steps import DataLoadStep, RunStrategiesStep, ReportStep
from ez.research.steps.data_load import DataLoadStep as _DataLoad


# ============================================================
# Synthetic strategy for E2E (duck-typed, no ABC inheritance)
# ============================================================

class _NoopFactor:
    name = "noop"
    warmup_period = 0
    def compute(self, data):
        out = data.copy()
        out[self.name] = 0.0
        return out


class _AlwaysLong:
    """Buy-and-hold for E2E testing (duck-typed)."""
    def required_factors(self):
        return [_NoopFactor()]
    def generate_signals(self, data):
        return pd.Series([1.0] * len(data), index=data.index)


def _synthetic_df(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=100, freq="B")
    r = rng.normal(0.0005, 0.015, 100)
    price = 100 * np.cumprod(1 + r)
    return pd.DataFrame({
        "open": price,
        "high": price * 1.01,
        "low": price * 0.99,
        "close": price,
        "adj_close": price,
        "volume": rng.integers(100_000, 1_000_000, 100).astype(float),
    }, index=idx)


@pytest.fixture
def fake_data_chain(monkeypatch):
    """Patch DataLoadStep to return synthetic frames."""
    seeds = {"AAA": 1, "BBB": 2, "CCC": 3}
    def fake_fetch(self, symbol, market, period, start, end):
        return _synthetic_df(seeds.get(symbol, 0))
    monkeypatch.setattr(_DataLoad, "_fetch_one", fake_fetch)


# ============================================================
# Full pipeline tests
# ============================================================

def test_full_pipeline_load_run_report(fake_data_chain):
    """Three-step pipeline produces a populated report."""
    pipeline = ResearchPipeline([
        DataLoadStep(
            symbols=["AAA", "BBB"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        ),
        RunStrategiesStep(strategies={
            "AAA": _AlwaysLong(),
            "BBB": _AlwaysLong(),
        }),
        ReportStep(),
    ])
    ctx = PipelineContext(config={
        "title": "E2E Test",
        "symbols": ["AAA", "BBB"],
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    })
    out = pipeline.run(ctx)

    # All 3 steps recorded
    assert len(out.history) == 3
    assert [r.step_name for r in out.history] == ["data_load", "run_strategies", "report"]
    assert all(r.status == "success" for r in out.history)

    # Artifacts populated
    assert "universe_data" in out.artifacts
    assert "returns" in out.artifacts
    assert "metrics" in out.artifacts
    assert "report" in out.artifacts

    # Returns shape
    returns = out.artifacts["returns"]
    assert isinstance(returns, pd.DataFrame)
    assert set(returns.columns) == {"AAA", "BBB"}

    # Report content
    report = out.artifacts["report"]
    assert "# E2E Test" in report
    assert "## Configuration" in report
    assert "## Strategy Metrics" in report
    assert "## Returns Sample" in report
    assert "## Pipeline Audit Log" in report
    assert "AAA" in report
    assert "BBB" in report


def test_full_pipeline_writes_report_file(fake_data_chain, tmp_path):
    """Pipeline with output_path writes the report to disk."""
    out_file = tmp_path / "e2e_report.md"
    pipeline = ResearchPipeline([
        DataLoadStep(
            symbols=["AAA"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        ),
        RunStrategiesStep(strategies={"AAA": _AlwaysLong()}),
        ReportStep(output_path=out_file),
    ])
    ctx = PipelineContext(config={"title": "Disk Test"})
    out = pipeline.run(ctx)

    assert out_file.exists()
    on_disk = out_file.read_text(encoding="utf-8")
    assert "# Disk Test" in on_disk
    assert "## Strategy Metrics" in on_disk
    assert "AAA" in on_disk


def test_full_pipeline_partial_failure_records_warning(fake_data_chain, monkeypatch):
    """One strategy crashes — pipeline continues, report has Warnings section."""
    class _CrashStrategy:  # duck-typed, NOT a Strategy subclass
        def required_factors(self):
            return [_NoopFactor()]
        def generate_signals(self, data):
            raise RuntimeError("planned crash")

    pipeline = ResearchPipeline([
        DataLoadStep(
            symbols=["AAA", "BBB"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        ),
        RunStrategiesStep(strategies={
            "AAA": _AlwaysLong(),
            "BBB": _CrashStrategy(),
        }),
        ReportStep(),
    ])
    ctx = PipelineContext(config={"title": "Partial Failure"})
    out = pipeline.run(ctx)

    report = out.artifacts["report"]
    assert "## Warnings" in report
    assert "BBB" in report
    assert "planned crash" in report
    # AAA still made it into the metrics table
    assert "AAA" in report


def test_full_pipeline_skip_on_data_load_failure(monkeypatch):
    """If all data load fails, pipeline raises StepError immediately
    and downstream steps don't run."""
    from ez.research.pipeline import StepError

    def all_fail(self, symbol, market, period, start, end):
        raise FileNotFoundError("no data")
    monkeypatch.setattr(_DataLoad, "_fetch_one", all_fail)

    pipeline = ResearchPipeline([
        DataLoadStep(
            symbols=["AAA"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        ),
        RunStrategiesStep(strategies={"AAA": _AlwaysLong()}),
        ReportStep(),
    ])
    ctx = PipelineContext()
    with pytest.raises(StepError) as exc_info:
        pipeline.run(ctx)
    assert exc_info.value.step_name == "data_load"
    # data_load was attempted but failed; downstream steps did not run
    assert len(ctx.history) == 1
    assert ctx.history[0].status == "failed"
