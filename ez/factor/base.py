"""Factor abstract base class.

[CORE] — interface frozen after V1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Factor(ABC):
    """Base class for all factors (technical indicators, alpha factors, etc.).

    Subclasses auto-register via __init_subclass__. Access via get_registry().

    V2.12.2 codex: dual-dict registry mirrors PortfolioStrategy — one keyed
    by `module.class` (authoritative) and one keyed by `__name__` (backward
    compat). Name collision logs a warning; use `resolve_class()` to
    disambiguate. Prior version silently overwrote on collision, so two
    unrelated factors with the same class name (e.g. a user factor shadowing
    a builtin with a typo) could cause the wrong class to be used.
    """

    # Authoritative: "module.class" → class (unique)
    _registry_by_key: dict[str, type] = {}
    # Backward-compat: "class_name" → class (last-write-wins with warning)
    _registry: dict[str, type] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, '__abstractmethods__', None):
            key = f"{cls.__module__}.{cls.__name__}"
            Factor._registry_by_key[key] = cls

            name = cls.__name__
            existing = Factor._registry.get(name)
            if existing is not None and existing is not cls:
                # V2.19.0 codex round-2 S7: skip the warning for transient
                # guard probe imports — `_guard_probe.*` modules are
                # one-shot and immediately cleaned up by `drop_probe_module`
                # after the guard suite finishes. Their displacement of the
                # name-keyed dict is by design and does not warrant a log
                # line on every save.
                if not cls.__module__.startswith("_guard_probe."):
                    import logging
                    logging.getLogger(__name__).warning(
                        "Factor name collision: '%s' previously registered by "
                        "%s.%s, now replaced by %s.%s. Use Factor.resolve_class() "
                        "with the full 'module.class' key to disambiguate.",
                        name,
                        existing.__module__, existing.__name__,
                        cls.__module__, cls.__name__,
                    )
            Factor._registry[name] = cls

    @classmethod
    def get_registry(cls) -> dict[str, type]:
        return dict(cls._registry)

    @classmethod
    def resolve_class(cls, name: str) -> type:
        """Resolve a factor identifier to a class using three-stage matching.

        Order:
        1. Exact key match (`module.class`) — unambiguous
        2. Unique class-name match — backward-compat
        3. Ambiguous name → ValueError with all candidate keys

        Raises:
            KeyError: name not found
            ValueError: multiple classes share this __name__
        """
        exact = cls._registry_by_key.get(name)
        if exact is not None:
            return exact
        matches = [(k, c) for k, c in cls._registry_by_key.items() if c.__name__ == name]
        if len(matches) == 1:
            return matches[0][1]
        if len(matches) > 1:
            keys = [k for k, _ in matches]
            raise ValueError(
                f"Factor name '{name}' is ambiguous — multiple classes "
                f"registered: {keys}. Submit the full module.class key."
            )
        raise KeyError(name)

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique factor name (e.g., 'ma_20')."""
        ...

    @property
    @abstractmethod
    def warmup_period(self) -> int:
        """Minimum historical bars needed before producing valid values."""
        ...

    @abstractmethod
    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """Compute factor and return DataFrame with new column(s) added.

        Input: DataFrame with at minimum 'adj_close' column.
        Output: Same DataFrame with factor column(s) appended.
        First `warmup_period` rows may have NaN for the new column(s).
        """
        ...
