"""Concrete research steps.

V2.20.0 MVP:
- DataLoadStep — fetch market data via the data provider chain
- RunStrategiesStep — run a list of strategies and collect returns + metrics
- ReportStep — render a markdown report from accumulated artifacts

V2.20.1:
- NestedOOSStep — IS optimize → OOS validate → baseline compare

V2.20.2:
- RunPortfolioStep — run a portfolio strategy via portfolio engine

V2.20.3:
- WalkForwardStep — rolling N-fold walk-forward weight optimization

V2.20.4:
- PairedBlockBootstrapStep — paired block bootstrap CI for strategy comparison
"""
from .data_load import DataLoadStep
from .run_strategies import RunStrategiesStep
from .run_portfolio import RunPortfolioStep
from .report import ReportStep
from .nested_oos import NestedOOSStep
from .walk_forward import WalkForwardStep
from .paired_bootstrap import PairedBlockBootstrapStep

__all__ = [
    "DataLoadStep",
    "RunStrategiesStep",
    "RunPortfolioStep",
    "ReportStep",
    "NestedOOSStep",
    "WalkForwardStep",
    "PairedBlockBootstrapStep",
]
