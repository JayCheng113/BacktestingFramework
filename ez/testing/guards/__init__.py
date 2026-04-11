"""ez.testing.guards — save-time guard framework (V2.19.0).

Public API:
  - Guard, GuardContext, GuardResult, GuardSeverity, GuardKind, GuardTier
  - GuardSuite, SuiteResult, load_user_class, default_guards
  - build_mock_panel, build_shuffled_panel, target_date_at, MOCK_*
  - LookaheadGuard, NaNInfGuard, WeightSumGuard,
    NonNegativeWeightsGuard, DeterminismGuard
"""
from .base import (
    Guard,
    GuardContext,
    GuardResult,
    GuardSeverity,
    GuardKind,
    GuardTier,
)
from .mock_data import (
    build_mock_panel,
    build_shuffled_panel,
    target_date_at,
    MOCK_N_DAYS,
    MOCK_SYMBOLS,
    MOCK_SEED,
    SHUFFLE_SEED,
)
from .suite import GuardSuite, SuiteResult, default_guards, load_user_class
from .lookahead import LookaheadGuard
from .nan_inf import NaNInfGuard
from .weight_sum import WeightSumGuard
from .non_negative import NonNegativeWeightsGuard
from .determinism import DeterminismGuard

__all__ = [
    "Guard",
    "GuardContext",
    "GuardResult",
    "GuardSeverity",
    "GuardKind",
    "GuardTier",
    "build_mock_panel",
    "build_shuffled_panel",
    "target_date_at",
    "MOCK_N_DAYS",
    "MOCK_SYMBOLS",
    "MOCK_SEED",
    "SHUFFLE_SEED",
    "GuardSuite",
    "SuiteResult",
    "default_guards",
    "load_user_class",
    "LookaheadGuard",
    "NaNInfGuard",
    "WeightSumGuard",
    "NonNegativeWeightsGuard",
    "DeterminismGuard",
]
