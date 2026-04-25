"""ez/portfolio — Portfolio backtesting module (V2.9)."""
# Import builtins to trigger __init_subclass__ auto-registration
from ez.portfolio.builtin_strategies import EtfMacdRotation  # noqa: F401

# V2.13 Phase 1: MLAlpha framework (sklearn-based walk-forward ML factor)
from ez.portfolio.ml_alpha import (  # noqa: F401
    MLAlpha,
    ML_ALPHA_TEMPLATE,
    UnsupportedEstimatorError,
)

# V2.13 Phase 2: MLDiagnostics (overfitting detection for MLAlpha)
from ez.portfolio.ml_diagnostics import (  # noqa: F401
    MLDiagnostics,
    DiagnosticsResult,
    DiagnosticsConfig,
)

# V2.13 Phase 3: StrategyEnsemble (multi-strategy composition)
from ez.portfolio.ensemble import StrategyEnsemble  # noqa: F401
