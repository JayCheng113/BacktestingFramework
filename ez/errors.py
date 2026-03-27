"""Unified error hierarchy for ez-trading.

[CORE] — append-only. Existing exceptions must not change class hierarchy.
"""


class EzTradingError(Exception):
    """Base exception for all ez-trading errors."""


class DataError(EzTradingError):
    """Data retrieval or validation failure."""


class ProviderError(DataError):
    """Data source connection, auth, or rate-limit error."""


class ValidationError(DataError):
    """Data validation rule failure."""


class FactorError(EzTradingError):
    """Factor computation failure."""


class BacktestError(EzTradingError):
    """Backtest engine error."""


class ConfigError(EzTradingError):
    """Configuration loading or validation error."""
