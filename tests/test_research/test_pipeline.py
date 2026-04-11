"""Unit tests for ez.research.pipeline + ez.research.context."""
from __future__ import annotations
import pytest

from ez.research.context import PipelineContext, StepRecord
from ez.research.pipeline import ResearchPipeline, ResearchStep, StepError


# ============================================================
# Stub steps for testing the pipeline framework itself
# ============================================================

class _PassStep(ResearchStep):
    name = "pass_step"
    writes = ("pass_marker",)

    def run(self, context):
        context.artifacts["pass_marker"] = True
        return context


class _SquareStep(ResearchStep):
    name = "square_step"
    writes = ("squared",)

    def run(self, context):
        x = context.require("x")
        context.artifacts["squared"] = x * x
        return context


class _RaisingStep(ResearchStep):
    name = "raising_step"

    def run(self, context):
        raise ValueError("intentional failure")


class _NoneReturningStep(ResearchStep):
    name = "none_returning_step"

    def run(self, context):
        return None  # incorrect: should return context


# ============================================================
# PipelineContext tests
# ============================================================

def test_context_default_empty():
    ctx = PipelineContext()
    assert ctx.config == {}
    assert ctx.artifacts == {}
    assert ctx.history == []


def test_context_get_returns_default_when_missing():
    ctx = PipelineContext()
    assert ctx.get("missing") is None
    assert ctx.get("missing", "default") == "default"


def test_context_require_returns_value():
    ctx = PipelineContext()
    ctx.artifacts["x"] = 42
    assert ctx.require("x") == 42


def test_context_require_raises_friendly_error():
    ctx = PipelineContext()
    ctx.artifacts["a"] = 1
    ctx.artifacts["b"] = 2
    with pytest.raises(KeyError) as exc_info:
        ctx.require("missing")
    msg = str(exc_info.value)
    assert "missing" in msg
    assert "['a', 'b']" in msg
    assert "upstream step" in msg


# ============================================================
# ResearchPipeline tests
# ============================================================

def test_pipeline_requires_at_least_one_step():
    with pytest.raises(ValueError, match="at least one step"):
        ResearchPipeline([])


def test_pipeline_runs_single_step():
    pipeline = ResearchPipeline([_PassStep()])
    ctx = pipeline.run()
    assert ctx.artifacts["pass_marker"] is True
    assert len(ctx.history) == 1
    assert ctx.history[0].step_name == "pass_step"
    assert ctx.history[0].status == "success"


def test_pipeline_chains_steps_with_artifact_passing():
    initial = PipelineContext(artifacts={"x": 5})
    pipeline = ResearchPipeline([_PassStep(), _SquareStep()])
    ctx = pipeline.run(initial)
    assert ctx.artifacts["pass_marker"] is True
    assert ctx.artifacts["squared"] == 25
    assert len(ctx.history) == 2


def test_pipeline_records_written_keys():
    pipeline = ResearchPipeline([_PassStep()])
    ctx = pipeline.run()
    record = ctx.history[0]
    assert record.written_keys == ("pass_marker",)


def test_pipeline_wraps_step_failure_as_step_error():
    pipeline = ResearchPipeline([_RaisingStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    assert exc_info.value.step_name == "raising_step"
    assert "intentional failure" in str(exc_info.value)
    assert isinstance(exc_info.value.original, ValueError)


def test_pipeline_records_failed_step_in_history():
    pipeline = ResearchPipeline([_RaisingStep()])
    ctx = PipelineContext()
    with pytest.raises(StepError):
        pipeline.run(ctx)
    assert len(ctx.history) == 1
    assert ctx.history[0].status == "failed"
    assert "intentional failure" in ctx.history[0].error


def test_pipeline_stops_on_first_failure():
    pipeline = ResearchPipeline([_RaisingStep(), _PassStep()])
    ctx = PipelineContext()
    with pytest.raises(StepError):
        pipeline.run(ctx)
    assert len(ctx.history) == 1  # second step never ran
    assert "pass_marker" not in ctx.artifacts


def test_pipeline_detects_step_returning_none():
    pipeline = ResearchPipeline([_NoneReturningStep()])
    with pytest.raises(StepError) as exc_info:
        pipeline.run()
    assert "must return PipelineContext" in str(exc_info.value)


def test_pipeline_history_records_duration():
    pipeline = ResearchPipeline([_PassStep()])
    ctx = pipeline.run()
    assert ctx.history[0].duration_ms >= 0
    assert ctx.history[0].finished_at >= ctx.history[0].started_at
