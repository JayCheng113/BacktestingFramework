"""Unit tests for DataLoadStep.

Uses monkeypatch on `_fetch_one` to avoid touching the real data chain
in test environments. The data chain itself is tested separately under
tests/test_data/.
"""
from __future__ import annotations
from datetime import date

import pandas as pd
import pytest

from ez.research.context import PipelineContext
from ez.research.pipeline import ResearchPipeline, StepError
from ez.research.steps.data_load import DataLoadStep


def _make_df(start: str, periods: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="B")
    return pd.DataFrame({
        "open": range(100, 100 + periods),
        "high": range(101, 101 + periods),
        "low": range(99, 99 + periods),
        "close": range(100, 100 + periods),
        "adj_close": range(100, 100 + periods),
        "volume": [1_000_000] * periods,
    }, index=idx)


@pytest.fixture
def fake_chain(monkeypatch):
    """Patch DataLoadStep._fetch_one to return synthetic frames."""
    def fake_fetch(self, symbol, market, period, start, end):
        if symbol == "EMPTY":
            return pd.DataFrame()
        if symbol == "MISSING":
            raise FileNotFoundError(f"no data for {symbol}")
        return _make_df("2024-01-01", 50)
    monkeypatch.setattr(DataLoadStep, "_fetch_one", fake_fetch)


# ============================================================
# Configuration resolution
# ============================================================

def test_constructor_args_take_precedence_over_config(fake_chain):
    """Explicit constructor args win over context.config defaults."""
    step = DataLoadStep(symbols=["AAA"], start_date="2024-01-01", end_date="2024-12-31")
    ctx = PipelineContext(config={"symbols": ["BBB"], "start_date": "2020-01-01"})
    out = step.run(ctx)
    assert "AAA" in out.artifacts["universe_data"]
    assert "BBB" not in out.artifacts["universe_data"]


def test_falls_back_to_config_when_no_constructor_args(fake_chain):
    step = DataLoadStep()
    ctx = PipelineContext(config={
        "symbols": ["XYZ"],
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    })
    out = step.run(ctx)
    assert "XYZ" in out.artifacts["universe_data"]


def test_raises_when_symbols_missing():
    step = DataLoadStep()
    ctx = PipelineContext(config={"start_date": "2024-01-01", "end_date": "2024-12-31"})
    with pytest.raises(ValueError, match="symbols"):
        step.run(ctx)


def test_raises_when_dates_missing(fake_chain):
    step = DataLoadStep(symbols=["AAA"])
    ctx = PipelineContext()
    with pytest.raises(ValueError, match="start_date and end_date"):
        step.run(ctx)


# ============================================================
# Date coercion
# ============================================================

def test_accepts_string_dates(fake_chain):
    step = DataLoadStep(symbols=["AAA"], start_date="2024-01-01", end_date="2024-12-31")
    ctx = PipelineContext()
    out = step.run(ctx)
    assert "AAA" in out.artifacts["universe_data"]


def test_accepts_date_objects(fake_chain):
    step = DataLoadStep(
        symbols=["AAA"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
    )
    ctx = PipelineContext()
    out = step.run(ctx)
    assert "AAA" in out.artifacts["universe_data"]


def test_accepts_datetime_objects(fake_chain):
    from datetime import datetime as dt
    step = DataLoadStep(
        symbols=["AAA"],
        start_date=dt(2024, 1, 1, 9, 30),
        end_date=dt(2024, 12, 31, 15, 0),
    )
    ctx = PipelineContext()
    out = step.run(ctx)
    assert "AAA" in out.artifacts["universe_data"]


# ============================================================
# Multi-symbol fetch + skip handling
# ============================================================

def test_loads_multiple_symbols(fake_chain):
    step = DataLoadStep(
        symbols=["AAA", "BBB", "CCC"],
        start_date="2024-01-01", end_date="2024-12-31",
    )
    out = step.run(PipelineContext())
    ud = out.artifacts["universe_data"]
    assert set(ud.keys()) == {"AAA", "BBB", "CCC"}
    for df in ud.values():
        assert len(df) == 50
        assert "adj_close" in df.columns


def test_skips_empty_dataframe(fake_chain):
    step = DataLoadStep(
        symbols=["AAA", "EMPTY", "BBB"],
        start_date="2024-01-01", end_date="2024-12-31",
    )
    out = step.run(PipelineContext())
    ud = out.artifacts["universe_data"]
    assert set(ud.keys()) == {"AAA", "BBB"}
    skipped = out.artifacts["data_load_skipped"]
    assert ("EMPTY", "empty dataframe") in skipped


def test_skips_fetch_error(fake_chain):
    step = DataLoadStep(
        symbols=["AAA", "MISSING", "BBB"],
        start_date="2024-01-01", end_date="2024-12-31",
    )
    out = step.run(PipelineContext())
    ud = out.artifacts["universe_data"]
    assert set(ud.keys()) == {"AAA", "BBB"}
    skipped = out.artifacts["data_load_skipped"]
    assert any(s[0] == "MISSING" and "FileNotFoundError" in s[1] for s in skipped)


def test_raises_when_all_symbols_failed(monkeypatch):
    """If every symbol fails, the step raises so downstream steps don't get a no-op context."""
    def all_fail(self, symbol, market, period, start, end):
        raise FileNotFoundError(f"no data for {symbol}")
    monkeypatch.setattr(DataLoadStep, "_fetch_one", all_fail)
    step = DataLoadStep(
        symbols=["AAA", "BBB"],
        start_date="2024-01-01", end_date="2024-12-31",
    )
    with pytest.raises(RuntimeError, match="no symbols loaded"):
        step.run(PipelineContext())


# ============================================================
# Pipeline integration
# ============================================================

def test_pipeline_wraps_failure_as_step_error(monkeypatch):
    def all_fail(self, symbol, market, period, start, end):
        raise FileNotFoundError("nope")
    monkeypatch.setattr(DataLoadStep, "_fetch_one", all_fail)
    pipeline = ResearchPipeline([
        DataLoadStep(symbols=["X"], start_date="2024-01-01", end_date="2024-12-31"),
    ])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    assert exc_info.value.step_name == "data_load"
