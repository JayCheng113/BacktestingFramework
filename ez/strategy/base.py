"""Strategy abstract base class with auto-registration.

[CORE] — interface frozen after V1.
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod

import pandas as pd

from ez.factor.base import Factor


class AmbiguousStrategyName(ValueError):
    """Raised when a strategy name matches multiple registered classes.

    Callers should catch this and convert to a user-facing error (e.g., HTTP 409)
    with the `candidate_keys` attribute listing all colliding keys, so the client
    can disambiguate by submitting the full `module.class` key.
    """

    def __init__(self, name: str, candidate_keys: list[str]) -> None:
        self.name = name
        self.candidate_keys = candidate_keys
        super().__init__(
            f"Strategy name '{name}' is ambiguous — multiple classes registered: "
            f"{candidate_keys}. Please submit the full key instead."
        )


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

    @classmethod
    def resolve_class(cls, name: str) -> type[Strategy]:
        """Resolve a strategy identifier to a class (without instantiation).

        V2.12.1 codex post-review: shared three-stage resolver used by both
        the API route (`ez/api/routes/backtest.py::_get_strategy`) and the
        agent runner (`ez/agent/runner.py::_resolve_strategy`). Prior to
        consolidation, only the API route was hardened — the agent runner
        (research pipeline, experiment tool, chat assistant backtest tool)
        silently picked the first-match class, the exact vulnerable path
        that `promote_research_strategy` was designed to trigger.

        Resolution order:
        1. Exact key match (`module.class`) — unambiguous, always preferred
        2. Unique class-name match — backward-compatible with existing callers
           that submit just `cls.__name__`
        3. Multiple classes share this `__name__` → raises AmbiguousStrategyName
           (callers should convert to user-facing error)

        Raises:
            KeyError: name not found in registry
            AmbiguousStrategyName: multiple classes share this __name__
        """
        # 1. Exact key match (module.class)
        exact = cls._registry.get(name)
        if exact is not None:
            return exact
        # 2. Class-name match (backward compat)
        matches = [(k, c) for k, c in cls._registry.items() if c.__name__ == name]
        if len(matches) == 1:
            return matches[0][1]
        if len(matches) > 1:
            raise AmbiguousStrategyName(name, [k for k, _ in matches])
        raise KeyError(name)

    @abstractmethod
    def required_factors(self) -> list[Factor]:
        """Factors this strategy depends on. Engine computes them automatically."""
        ...

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Return target position weights: 0.0 (no position) to 1.0 (full position)."""
        ...
