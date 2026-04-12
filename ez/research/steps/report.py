"""ReportStep: render a markdown report from accumulated artifacts.

V2.20.0 MVP uses pure-Python f-string rendering (no jinja dependency).
The default template summarizes the most common phase-script outputs:
title, run window, strategy metrics table, returns sample, audit log.

Custom templates can be passed as a callable
``template_fn(context) -> str`` for full flexibility.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from ..pipeline import ResearchStep
from ..context import PipelineContext


def _md_escape(s) -> str:
    """Escape characters that break a markdown table cell.

    Codex round-3 P3-1: pipe and newline both terminate or split a
    markdown table cell. A strategy label like ``"A|B"`` or a string
    metric like ``"line1\\nline2"`` would corrupt the table layout.
    """
    text = str(s)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _format_metric(value) -> str:
    """Format a single metric value for the table."""
    if value is None:
        return "—"
    if isinstance(value, float):
        if abs(value) < 1e-10:
            return "0.00"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:.4f}"
    return _md_escape(value)


def default_template(context: PipelineContext) -> str:
    """Render a standard markdown report from the typical pipeline artifacts.

    Sections:
      1. Title and run timestamp
      2. Pipeline configuration summary
      3. Strategy metrics table (if 'metrics' artifact exists)
      4. Returns sample (last 5 dates × all strategies)
      5. Audit log (one row per step with status + duration)
    """
    lines: list[str] = []
    title = context.config.get("title", "Research Report")
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
    lines.append("")

    # Configuration summary
    # Codex round-5 P3-2: escape backticks/newlines in values so a
    # config value containing ` or \n doesn't break the inline code span.
    if context.config:
        lines.append("## Configuration")
        lines.append("")
        for key in sorted(context.config.keys()):
            if key == "title":
                continue
            val = context.config[key]
            val_str = str(val).replace("\\", "\\\\").replace("`", "\\`").replace("\n", " ").replace("\r", " ")
            key_str = _md_escape(key)
            lines.append(f"- **{key_str}**: `{val_str}`")
        lines.append("")

    # Metrics table
    metrics = context.get("metrics")
    if metrics:
        lines.append("## Strategy Metrics")
        lines.append("")
        all_metric_keys = sorted({k for d in metrics.values() for k in d.keys()})
        # Pick the top 6 most common metrics for compactness
        preferred = ["total_return", "sharpe_ratio", "max_drawdown", "win_rate",
                     "trade_count", "profit_factor"]
        ordered_keys = [k for k in preferred if k in all_metric_keys]
        # Append any remaining metrics
        for k in all_metric_keys:
            if k not in ordered_keys:
                ordered_keys.append(k)
        # Limit to 8 columns for readability
        ordered_keys = ordered_keys[:8]

        header = "| Strategy | " + " | ".join(ordered_keys) + " |"
        sep = "|" + "|".join(["---"] * (len(ordered_keys) + 1)) + "|"
        lines.append(header)
        lines.append(sep)
        for label in sorted(metrics.keys()):
            row = [_md_escape(label)] + [_format_metric(metrics[label].get(k)) for k in ordered_keys]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # Nested OOS results (V2.20.1)
    nested_oos = context.get("nested_oos_results")
    if nested_oos and isinstance(nested_oos, dict):
        candidates = nested_oos.get("candidates", [])
        if candidates:
            lines.append("## Nested OOS Results")
            lines.append("")
            is_w = nested_oos.get("is_window", ("?", "?"))
            oos_w = nested_oos.get("oos_window", ("?", "?"))
            lines.append(f"IS window: `{is_w[0]}` → `{is_w[1]}`, "
                         f"OOS window: `{oos_w[0]}` → `{oos_w[1]}`")
            lines.append("")
            # Candidates table: objective | status | IS sharpe | IS ret | OOS sharpe | OOS ret | OOS MDD
            lines.append("| Objective | Status | IS Sharpe | IS Ret | OOS Sharpe | OOS Ret | OOS MDD |")
            lines.append("|---|---|---|---|---|---|---|")
            for c in candidates:
                obj = _md_escape(c.get("objective", "?"))
                status = _md_escape(c.get("status", "?"))
                is_m = c.get("is_metrics", {})
                oos_m = c.get("oos_metrics", {})
                lines.append(
                    f"| {obj} | {status} "
                    f"| {_format_metric(is_m.get('sharpe'))} "
                    f"| {_format_metric(is_m.get('ret'))} "
                    f"| {_format_metric(oos_m.get('sharpe'))} "
                    f"| {_format_metric(oos_m.get('ret'))} "
                    f"| {_format_metric(oos_m.get('dd'))} |"
                )
            # Baseline row
            bl_oos = nested_oos.get("baseline_oos")
            bl_is = nested_oos.get("baseline_is")
            if bl_oos or bl_is:
                bl_i = bl_is or {}
                bl_o = bl_oos or {}
                lines.append(
                    f"| **(Baseline)** | — "
                    f"| {_format_metric(bl_i.get('sharpe'))} "
                    f"| {_format_metric(bl_i.get('ret'))} "
                    f"| {_format_metric(bl_o.get('sharpe'))} "
                    f"| {_format_metric(bl_o.get('ret'))} "
                    f"| {_format_metric(bl_o.get('dd'))} |"
                )
            lines.append("")

    # Returns sample
    returns = context.get("returns")
    if returns is not None and isinstance(returns, pd.DataFrame) and len(returns) > 0:
        lines.append("## Returns Sample (last 5 dates)")
        lines.append("")
        sample = returns.tail(5)
        cols = [_md_escape(c) for c in sample.columns]
        header = "| Date | " + " | ".join(cols) + " |"
        sep = "|" + "|".join(["---"] * (len(cols) + 1)) + "|"
        lines.append(header)
        lines.append(sep)
        for ts, row in sample.iterrows():
            date_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)
            vals = [_format_metric(v) for v in row.values]
            lines.append("| " + date_str + " | " + " | ".join(vals) + " |")
        lines.append("")

    # Audit log
    if context.history:
        lines.append("## Pipeline Audit Log")
        lines.append("")
        lines.append("| # | Step | Status | Duration (ms) | Wrote |")
        lines.append("|---|---|---|---|---|")
        for i, rec in enumerate(context.history, 1):
            written = ", ".join(_md_escape(k) for k in rec.written_keys) if rec.written_keys else "—"
            lines.append(
                f"| {i} | {_md_escape(rec.step_name)} | {rec.status} | {rec.duration_ms:.1f} | {written} |"
            )
        lines.append("")

    # Skipped items (data load + run strategies)
    # Codex round-4 P3-A: escape `reason` strings — exception messages
    # routinely contain newlines (full tracebacks) which would split a
    # bullet list item visually and corrupt downstream sections.
    skipped_data = context.get("data_load_skipped")
    skipped_runs = context.get("run_strategies_skipped")
    if skipped_data or skipped_runs:
        lines.append("## Warnings")
        lines.append("")
        if skipped_data:
            lines.append("**Data load skipped**:")
            for sym, reason in skipped_data:
                lines.append(f"- `{_md_escape(sym)}`: {_md_escape(reason)}")
            lines.append("")
        if skipped_runs:
            lines.append("**Strategy runs skipped**:")
            for label, reason in skipped_runs:
                lines.append(f"- `{_md_escape(label)}`: {_md_escape(reason)}")
            lines.append("")

    return "\n".join(lines)


class ReportStep(ResearchStep):
    name = "report"
    writes = ("report",)

    def __init__(
        self,
        template_fn: Callable[[PipelineContext], str] | None = None,
        output_path: str | Path | None = None,
    ):
        """
        Parameters
        ----------
        template_fn : callable, optional
            Custom template function (context → markdown string).
            Defaults to ``default_template``.
        output_path : str | Path, optional
            If provided, write the rendered report to this path AND
            store it under ``artifacts['report_path']``.
        """
        self.template_fn = template_fn or default_template
        self.output_path = Path(output_path) if output_path is not None else None

    def run(self, context: PipelineContext) -> PipelineContext:
        report_md = self.template_fn(context)
        context.artifacts["report"] = report_md
        if self.output_path is not None:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(report_md, encoding="utf-8")
            context.artifacts["report_path"] = str(self.output_path)
        return context
