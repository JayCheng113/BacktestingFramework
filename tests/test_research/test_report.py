"""Unit tests for ReportStep + default_template."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from ez.research.context import PipelineContext, StepRecord
from ez.research.steps.report import ReportStep, default_template


# ============================================================
# default_template content tests
# ============================================================

def test_template_renders_title_and_timestamp():
    ctx = PipelineContext(config={"title": "Test Report"})
    md = default_template(ctx)
    assert "# Test Report" in md
    assert "_Generated:" in md
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in md


def test_template_renders_default_title_when_missing():
    ctx = PipelineContext()
    md = default_template(ctx)
    assert "# Research Report" in md


def test_template_renders_config_section():
    ctx = PipelineContext(config={
        "title": "X",
        "symbols": ["AAA", "BBB"],
        "start_date": "2024-01-01",
    })
    md = default_template(ctx)
    assert "## Configuration" in md
    assert "**symbols**" in md
    assert "**start_date**" in md
    assert "['AAA', 'BBB']" in md


def test_template_renders_metrics_table():
    ctx = PipelineContext(artifacts={
        "metrics": {
            "AAA": {"sharpe_ratio": 1.23, "total_return": 0.45, "max_drawdown": -0.10},
            "BBB": {"sharpe_ratio": 0.87, "total_return": 0.15, "max_drawdown": -0.20},
        }
    })
    md = default_template(ctx)
    assert "## Strategy Metrics" in md
    assert "| AAA |" in md
    assert "| BBB |" in md
    assert "1.2300" in md  # sharpe formatted
    assert "sharpe_ratio" in md
    assert "total_return" in md


def test_template_renders_returns_sample():
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    rets = pd.DataFrame({"AAA": range(10), "BBB": range(10, 20)}, index=idx, dtype=float)
    ctx = PipelineContext(artifacts={"returns": rets})
    md = default_template(ctx)
    assert "## Returns Sample" in md
    # Last 5 dates only
    assert "2024-01-12" in md or "2024-01-11" in md  # last few business days


def test_template_renders_audit_log():
    ctx = PipelineContext()
    ctx.history.append(StepRecord(
        step_name="data_load",
        started_at=datetime.now(),
        finished_at=datetime.now(),
        duration_ms=12.5,
        status="success",
        written_keys=("universe_data",),
    ))
    md = default_template(ctx)
    assert "## Pipeline Audit Log" in md
    assert "| 1 | data_load | success | 12.5 |" in md
    assert "universe_data" in md


def test_template_renders_warnings_when_skipped_present():
    ctx = PipelineContext(artifacts={
        "data_load_skipped": [("BAD", "404 not found")],
        "run_strategies_skipped": [("CRASH", "RuntimeError: oops")],
    })
    md = default_template(ctx)
    assert "## Warnings" in md
    assert "BAD" in md
    assert "404 not found" in md
    assert "CRASH" in md
    assert "RuntimeError" in md


def test_template_omits_sections_when_artifacts_missing():
    """Empty context should produce a minimal but valid markdown."""
    ctx = PipelineContext()
    md = default_template(ctx)
    assert "# Research Report" in md
    assert "## Strategy Metrics" not in md
    assert "## Returns Sample" not in md


# ============================================================
# ReportStep.run tests
# ============================================================

def test_step_writes_report_artifact():
    ctx = PipelineContext(config={"title": "X"})
    step = ReportStep()
    out = step.run(ctx)
    assert "report" in out.artifacts
    assert "# X" in out.artifacts["report"]


def test_step_with_custom_template_fn():
    def my_template(context):
        return "custom: " + context.config.get("title", "")
    ctx = PipelineContext(config={"title": "Hello"})
    step = ReportStep(template_fn=my_template)
    out = step.run(ctx)
    assert out.artifacts["report"] == "custom: Hello"


def test_step_writes_to_output_path(tmp_path):
    ctx = PipelineContext(config={"title": "X"})
    out_file = tmp_path / "subdir" / "report.md"
    step = ReportStep(output_path=out_file)
    out = step.run(ctx)
    assert out_file.exists()
    assert out.artifacts["report_path"] == str(out_file)
    on_disk = out_file.read_text(encoding="utf-8")
    assert "# X" in on_disk
