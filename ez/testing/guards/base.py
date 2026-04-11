"""Guard framework core — base types and abstract base class.

A Guard inspects a user-authored strategy/factor/portfolio file and returns
a GuardResult indicating pass, warn, or block. Guards run at save time via
GuardSuite and are integrated into the sandbox save flow.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

GuardKind = Literal["strategy", "factor", "cross_factor", "portfolio_strategy", "ml_alpha"]
GuardTier = Literal["block", "warn"]


class GuardSeverity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class GuardContext:
    """Everything a guard needs to analyze a user code file.

    The sandbox builds this once per save. `user_class` is populated by
    GuardSuite.run() via load_user_class() before any guard.check() fires.
    """
    filename: str
    module_name: str
    file_path: Path
    kind: GuardKind
    user_class: type | None = None
    instantiation_error: str | None = None


@dataclass(frozen=True)
class GuardResult:
    """Outcome of a single guard run."""
    guard_name: str
    severity: GuardSeverity
    tier: GuardTier
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    runtime_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return self.severity == GuardSeverity.PASS

    @property
    def blocked(self) -> bool:
        return self.severity == GuardSeverity.BLOCK


class Guard(ABC):
    """Abstract guard. Subclasses implement `check()`."""
    name: str = "Guard"
    tier: GuardTier = "block"
    applies_to: tuple[GuardKind, ...] = ()

    @abstractmethod
    def check(self, context: GuardContext) -> GuardResult:
        """Run the guard against the user code.

        Implementations MUST return a GuardResult. They MUST NOT raise —
        wrap internal errors as a block-severity result with a descriptive
        message. (GuardSuite catches exceptions as a defensive second
        line, but guards should not rely on that.)
        """
        raise NotImplementedError

    def applies(self, kind: GuardKind) -> bool:
        return kind in self.applies_to
