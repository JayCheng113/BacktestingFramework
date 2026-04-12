"""Concrete research steps.

V2.20.0 MVP:
- DataLoadStep — fetch market data via the data provider chain
- RunStrategiesStep — run a list of strategies and collect returns + metrics
- ReportStep — render a markdown report from accumulated artifacts

V2.20.1:
- NestedOOSStep — IS optimize → OOS validate → baseline compare

V2.20.x will add PairedBootstrapStep, WalkForwardStep, RunPortfolioStep.
"""
from .data_load import DataLoadStep
from .run_strategies import RunStrategiesStep
from .report import ReportStep
from .nested_oos import NestedOOSStep

__all__ = ["DataLoadStep", "RunStrategiesStep", "ReportStep", "NestedOOSStep"]
