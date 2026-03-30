"""Strategy abstract base class with auto-registration.

[CORE] — interface frozen after V1.
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod

import pandas as pd

from ez.factor.base import Factor


class Strategy(ABC):
    """Base class for all strategies. Subclasses auto-register."""

    _registry: dict[str, type[Strategy]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not inspect.isabstract(cls):
            key = f"{cls.__module__}.{cls.__name__}"
            if key in cls._registry:
                raise ValueError(f"Strategy '{key}' already registered by {cls._registry[key]}")
            cls._registry[key] = cls

    @classmethod
    def get_registry(cls) -> dict[str, type]:
        """Public accessor for the strategy registry."""
        return dict(cls._registry)

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        """Parameter schema for frontend form rendering."""
        return {}

    @abstractmethod
    def required_factors(self) -> list[Factor]:
        """Factors this strategy depends on. Engine computes them automatically."""
        ...

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Return target position weights: 0.0 (no position) to 1.0 (full position)."""
        ...
