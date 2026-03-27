"""Data validation rules applied before storage.

[CORE] — append-only. New rules can be added, existing rules must not be removed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ez.types import Bar


@dataclass
class ValidationResult:
    valid_bars: list[Bar] = field(default_factory=list)
    invalid_bars: list[Bar] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid_count(self) -> int:
        return len(self.valid_bars)

    @property
    def invalid_count(self) -> int:
        return len(self.invalid_bars)


class DataValidator:
    """Validates bars before storage."""

    @staticmethod
    def validate_bars(bars: list[Bar]) -> ValidationResult:
        result = ValidationResult()
        for bar in bars:
            errors = DataValidator._check_bar(bar)
            if errors:
                result.invalid_bars.append(bar)
                result.errors.extend(errors)
            else:
                result.valid_bars.append(bar)
        return result

    @staticmethod
    def _check_bar(bar: Bar) -> list[str]:
        errors = []
        if bar.low > bar.high:
            errors.append(f"OHLC consistency: low ({bar.low}) > high ({bar.high}) for {bar.symbol} at {bar.time}")
        if bar.low > bar.open or bar.low > bar.close:
            errors.append(f"OHLC consistency: low ({bar.low}) > open/close for {bar.symbol} at {bar.time}")
        if bar.high < bar.open or bar.high < bar.close:
            errors.append(f"OHLC consistency: high ({bar.high}) < open/close for {bar.symbol} at {bar.time}")
        if bar.volume < 0:
            errors.append(f"Negative volume ({bar.volume}) for {bar.symbol} at {bar.time}")
        return errors
