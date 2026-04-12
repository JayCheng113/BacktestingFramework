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
    """Raised when a research step fails. Wraps the original exception.

    Codex round-3 P2-2: now carries the partial PipelineContext so the
    caller can inspect what succeeded before the failure. ``context``
    is the LAST KNOWN GOOD context (the one passed to the failing step,
    not whatever the step returned — which may itself be malformed).
    """
    def __init__(
        self,
        step_name: str,
        original: Exception,
        context: "PipelineContext | None" = None,
    ):
        self.step_name = step_name
        self.original = original
        self.context = context
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
    ...     DataLoadStep(
    ...         symbols=["SPY"],
    ...         start_date="2020-01-01",
    ...         end_date="2024-12-31",
    ...     ),
    ...     RunStrategiesStep(strategies={"SPY": BuyHoldSingle("SPY")}),
    ...     ReportStep(),
    ... ])
    >>> ctx = pipeline.run(PipelineContext(config={"market": "us_stock"}))
    >>> print(ctx.artifacts["report"])
    """

    def __init__(self, steps: Iterable[ResearchStep]):
        self.steps = list(steps)
        if not self.steps:
            raise ValueError("ResearchPipeline requires at least one step")

    def run(
        self,
        context: PipelineContext | None = None,
        *,
        reset: bool = False,
    ) -> PipelineContext:
        """Run all steps in order against the given context.

        Parameters
        ----------
        context : PipelineContext, optional
            Context to mutate. If None, a fresh empty context is created.
        reset : bool, default False
            If True, clear ``context.artifacts`` and ``context.history``
            before the first step runs (``context.config`` is preserved).
            Use this when re-running the same pipeline against the same
            context object — without ``reset``, artifacts from the prior
            run leak into this one (history grows monotonically, and
            steps that don't overwrite their writes silently inherit
            stale values).

            Default is False for backward compatibility (V2.20.0 didn't
            have this), but most callers should pass ``reset=True``
            when re-running. Codex round-3 P2-6.
        """
        ctx = context if context is not None else PipelineContext()
        if reset:
            ctx.artifacts.clear()
            ctx.history.clear()
        for step in self.steps:
            # Codex round-3 P1-2 + P2-1 + P2-2:
            #  - Always keep a reference to the LAST KNOWN GOOD context
            #    (the one passed to step.run) so that if step.run returns
            #    a malformed object, we can still record failure history
            #    on the original context.
            #  - StepError now carries the failed-state context so callers
            #    don't need to pre-create one to inspect partial progress.
            #  - Validate the return type strictly so a None or dict
            #    return becomes a recorded failure, not a raw AttributeError
            #    in downstream code.
            prev_ctx = ctx
            t0 = time.perf_counter()
            started = datetime.now()
            try:
                pre_keys = set(prev_ctx.artifacts.keys())
                returned = step.run(prev_ctx)
                if returned is None:
                    raise TypeError(
                        "step.run returned None — must return a PipelineContext"
                    )
                if not isinstance(returned, PipelineContext):
                    raise TypeError(
                        f"step.run returned {type(returned).__name__}, "
                        f"expected PipelineContext"
                    )
                ctx = returned
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
            except Exception as e:
                # Record on prev_ctx (the last known good context) — never on
                # the malformed return value. This guarantees that failed
                # steps always show up in history, even when the failure
                # was a bad return type or a pre-existing StepError raised
                # from inside the step.
                prev_ctx.history.append(StepRecord(
                    step_name=step.name,
                    started_at=started,
                    finished_at=datetime.now(),
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    status="failed",
                    error=f"{type(e).__name__}: {e}",
                ))
                logger.error("Step '%s' failed: %s", step.name, e)
                # If the inner exception is already a StepError (e.g. raised
                # explicitly inside the step), preserve its original cause
                # rather than double-wrapping. Either way, attach the partial
                # context so callers can inspect history without pre-creating
                # a context.
                if isinstance(e, StepError):
                    e.context = prev_ctx
                    raise
                raise StepError(step.name, e, context=prev_ctx) from e
        return ctx
