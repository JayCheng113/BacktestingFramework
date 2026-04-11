"""Concrete research steps.

V2.20.0 MVP includes:
- DataLoadStep — fetch market data via the data provider chain
- RunStrategiesStep — run a list of strategies and collect returns + metrics
- ReportStep — render a markdown report from accumulated artifacts

V2.20.x will add NestedOOSStep, PairedBootstrapStep, WalkForwardStep,
and OptimizerStep as the migration of validation/ scripts continues.
"""
from .data_load import DataLoadStep
from .run_strategies import RunStrategiesStep
from .report import ReportStep

__all__ = ["DataLoadStep", "RunStrategiesStep", "ReportStep"]
