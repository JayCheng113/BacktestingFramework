"""PipelineContext: shared state passed between research steps.

Steps consume artifacts produced by upstream steps and write their own
to ``artifacts``. The context is mutable across the pipeline run but
each step's input/output is recorded as a ``StepRecord`` for audit.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class StepRecord:
    """Audit record for a single step execution."""
    step_name: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    status: str  # "success" | "failed"
    error: str | None = None
    # Keys this step wrote to PipelineContext.artifacts.
    written_keys: tuple[str, ...] = ()


@dataclass
class PipelineContext:
    """Shared state across a ResearchPipeline run.

    Attributes
    ----------
    config : dict
        Static configuration for the pipeline (date ranges, symbols, etc).
        Steps read from this but should NOT mutate it.
    artifacts : dict
        Step-produced data. Each step writes its outputs here under
        well-known keys (e.g. ``universe_data``, ``returns``,
        ``metrics``). Downstream steps read from here.
    history : list[StepRecord]
        Append-only audit log of step executions.
    """
    config: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    history: list[StepRecord] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        """Read an artifact (returns default if missing)."""
        return self.artifacts.get(key, default)

    def require(self, key: str) -> Any:
        """Read an artifact, raising KeyError with a friendly message if missing."""
        if key not in self.artifacts:
            available = sorted(self.artifacts.keys())
            raise KeyError(
                f"Required artifact '{key}' not found in pipeline context. "
                f"Available artifacts: {available}. "
                f"Did you forget an upstream step?"
            )
        return self.artifacts[key]
