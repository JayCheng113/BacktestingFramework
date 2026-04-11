"""ResearchPipeline orchestrator + ResearchStep abstract base class."""
from __future__ import annotations
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable

from .context import PipelineContext, StepRecord

logger = logging.getLogger(__name__)


class StepError(RuntimeError):
    """Raised when a research step fails. Wraps the original exception."""
    def __init__(self, step_name: str, original: Exception):
        self.step_name = step_name
        self.original = original
        super().__init__(f"Step '{step_name}' failed: {type(original).__name__}: {original}")


class ResearchStep(ABC):
    """A single unit of work in a research pipeline.

    Steps are stateless objects (configuration in __init__, no
    instance state across runs). The ``run`` method receives the
    pipeline context, mutates ``context.artifacts``, and returns the
    same context (chainable).

    Subclasses MUST set ``name`` and implement ``run``. They SHOULD
    declare ``writes`` (the artifact keys they will populate) so that
    the pipeline can audit data flow.
    """

    name: str = "ResearchStep"
    # Tuple of artifact keys this step is expected to write. Used for
    # the StepRecord audit trail and for static analysis of pipelines.
    writes: tuple[str, ...] = ()

    @abstractmethod
    def run(self, context: PipelineContext) -> PipelineContext:
        """Execute the step. MUST return the (potentially mutated) context.

        Implementations should:
          1. Read inputs via ``context.require(key)`` (raises KeyError if missing)
          2. Perform the work
          3. Write outputs via ``context.artifacts[key] = value``
          4. Return the context

        Raise any exception on failure — the pipeline wraps it as
        ``StepError`` with the step name.
        """
        raise NotImplementedError


class ResearchPipeline:
    """Run a sequence of ResearchSteps against a shared context.

    Each step's success/failure is recorded in ``context.history``.
    On step failure, the pipeline raises ``StepError`` immediately
    (no continuation). The context is returned partially populated
    so the caller can inspect what succeeded.

    Example
    -------
    >>> pipeline = ResearchPipeline([
    ...     DataLoadStep(symbols=["SPY"], start="2020-01-01", end="2024-12-31"),
    ...     RunStrategiesStep(strategies=["BuyHoldSingle"]),
    ...     ReportStep(),
    ... ])
    >>> ctx = pipeline.run(PipelineContext(config={"market": "us_stock"}))
    >>> print(ctx.artifacts["report"])
    """

    def __init__(self, steps: Iterable[ResearchStep]):
        self.steps = list(steps)
        if not self.steps:
            raise ValueError("ResearchPipeline requires at least one step")

    def run(self, context: PipelineContext | None = None) -> PipelineContext:
        ctx = context if context is not None else PipelineContext()
        for step in self.steps:
            t0 = time.perf_counter()
            started = datetime.now()
            try:
                pre_keys = set(ctx.artifacts.keys())
                ctx = step.run(ctx)
                if ctx is None:
                    raise StepError(
                        step.name,
                        TypeError("step.run returned None — must return PipelineContext"),
                    )
                written = tuple(sorted(set(ctx.artifacts.keys()) - pre_keys))
                ctx.history.append(StepRecord(
                    step_name=step.name,
                    started_at=started,
                    finished_at=datetime.now(),
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    status="success",
                    written_keys=written,
                ))
                logger.info(
                    "Step '%s' completed in %.1fms, wrote %s",
                    step.name, (time.perf_counter() - t0) * 1000, written or "()",
                )
            except StepError:
                # Already wrapped — record and re-raise
                raise
            except Exception as e:
                ctx.history.append(StepRecord(
                    step_name=step.name,
                    started_at=started,
                    finished_at=datetime.now(),
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    status="failed",
                    error=f"{type(e).__name__}: {e}",
                ))
                logger.error("Step '%s' failed: %s", step.name, e)
                raise StepError(step.name, e) from e
        return ctx
