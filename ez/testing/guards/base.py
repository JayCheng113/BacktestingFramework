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
    """Guard 执行结果的严重级别。

    `PASS` 表示检查通过，`WARN` 会在 UI 中提示但不阻止保存，`BLOCK`
    会阻止用户代码进入生产注册表。该枚举只承载状态，不执行检查逻辑。
    """

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
        """返回该 guard 是否通过。

        Returns:
            当严重级别为 `PASS` 时返回 True；警告和阻断都返回 False。
        """
        return self.severity == GuardSeverity.PASS

    @property
    def blocked(self) -> bool:
        """返回该 guard 是否阻断保存。

        Returns:
            当严重级别为 `BLOCK` 时返回 True，用于 sandbox 决定是否回滚。
        """
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
        """判断当前 guard 是否适用于指定代码类型。

        Args:
            kind: 用户保存的代码类别，例如 strategy、factor 或 ml_alpha。

        Returns:
            如果 `kind` 出现在 `applies_to` 白名单中则返回 True。
        """
        return kind in self.applies_to
