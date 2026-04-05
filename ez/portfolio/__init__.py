"""ez/portfolio — Portfolio backtesting module (V2.9)."""
# Import builtins to trigger __init_subclass__ auto-registration
from ez.portfolio.builtin_strategies import EtfMacdRotation, EtfSectorSwitch, EtfStockEnhance  # noqa: F401

# V2.13 Phase 1: MLAlpha framework (sklearn-based walk-forward ML factor)
from ez.portfolio.ml_alpha import (  # noqa: F401
    MLAlpha,
    ML_ALPHA_TEMPLATE,
    UnsupportedEstimatorError,
)
