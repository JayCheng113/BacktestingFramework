"""ez.research — research workflow orchestration (V2.20.0).

Provides a thin pipeline layer above ``ez.agent.runner`` to express
multi-step research workflows (load data → run experiments → compute
statistics → generate report) as composable, cacheable, testable steps.

The motivation is the ``validation/`` directory: 30+ one-shot phase
scripts that each re-implement data loading, optimizer wiring, metric
calculation, and report formatting. ``ez.research`` lets the user
write a 20-line pipeline declaration instead of a 200-line script
and reuse the same primitive steps across studies.

Public API
----------
- ``PipelineContext`` — shared state passed between steps
- ``ResearchStep`` — abstract base class for steps
- ``ResearchPipeline`` — orchestrator that runs steps in order
- ``StepRecord`` — execution metadata for one step

Concrete steps in ``ez.research.steps``:
- ``DataLoadStep`` — fetch market data via the data chain
- ``RunStrategiesStep`` — run several strategies, collect returns + metrics
- ``ReportStep`` — render a markdown report from accumulated artifacts

Status: experimental. The pipeline contract may change as more steps
are added (V2.20.x will add NestedOOSStep, PairedBootstrapStep, etc).
"""
from .context import PipelineContext, StepRecord
from .pipeline import ResearchPipeline, ResearchStep, StepError

__all__ = [
    "PipelineContext",
    "StepRecord",
    "ResearchPipeline",
    "ResearchStep",
    "StepError",
]
