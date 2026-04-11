"""GuardSuite: orchestrates multiple guards and collects results.

Also exposes `load_user_class` — imports a user file in-process and
returns the target subclass for the given kind. Used by the sandbox
integration layer.
"""
from __future__ import annotations
import importlib.util
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .base import Guard, GuardContext, GuardResult, GuardSeverity, GuardKind


@dataclass(frozen=True)
class SuiteResult:
    results: tuple[GuardResult, ...]
    total_runtime_ms: float

    @property
    def blocked(self) -> bool:
        return any(r.blocked for r in self.results)

    @property
    def warnings(self) -> list[GuardResult]:
        return [r for r in self.results if r.severity == GuardSeverity.WARN]

    @property
    def blocks(self) -> list[GuardResult]:
        return [r for r in self.results if r.severity == GuardSeverity.BLOCK]

    def to_payload(self) -> dict:
        return {
            "blocked": self.blocked,
            "n_warnings": len(self.warnings),
            "n_blocks": len(self.blocks),
            "total_runtime_ms": round(self.total_runtime_ms, 2),
            "guards": [
                {
                    "name": r.guard_name,
                    "severity": r.severity.value,
                    "tier": r.tier,
                    "message": r.message,
                    "runtime_ms": round(r.runtime_ms, 2),
                    "details": r.details,
                }
                for r in self.results
            ],
        }


def default_guards() -> list[Guard]:
    """Return the default guard set for production saves.

    Order: Tier 1 (block) first, Tier 2 (warn) last. Suite runs ALL
    applicable guards — even after a block — so the user sees the full
    picture.
    """
    from .lookahead import LookaheadGuard
    from .nan_inf import NaNInfGuard
    from .weight_sum import WeightSumGuard
    from .non_negative import NonNegativeWeightsGuard
    from .determinism import DeterminismGuard
    return [
        LookaheadGuard(),
        NaNInfGuard(),
        WeightSumGuard(),
        NonNegativeWeightsGuard(),
        DeterminismGuard(),
    ]


class GuardSuite:
    def __init__(self, guards: Iterable[Guard] | None = None):
        self.guards = list(guards) if guards is not None else default_guards()

    def run(self, context: GuardContext) -> SuiteResult:
        t0 = time.perf_counter()
        results: list[GuardResult] = []
        for guard in self.guards:
            if not guard.applies(context.kind):
                continue
            try:
                result = guard.check(context)
            except Exception as e:
                result = GuardResult(
                    guard_name=guard.name,
                    severity=GuardSeverity.BLOCK,
                    tier=guard.tier,
                    message=(
                        f"{guard.name}: guard itself raised (guard bug, not user bug): "
                        f"{type(e).__name__}: {e}"
                    ),
                )
            results.append(result)
        total = (time.perf_counter() - t0) * 1000
        return SuiteResult(results=tuple(results), total_runtime_ms=total)


def load_user_class(
    file_path: Path, module_name: str, kind: GuardKind,
) -> tuple[type | None, str | None]:
    """Import a user file and return (class, error_message).

    Returns (None, error) if file cannot be imported or no target class found.
    Runs in the SAME process as the sandbox — user code has already passed
    syntax + security checks by the time this is called.
    """
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            return None, f"Could not create module spec for {file_path}"
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    except Exception as e:
        return None, f"Import failed: {type(e).__name__}: {e}"

    if kind == "strategy":
        from ez.strategy.base import Strategy as _Base
    elif kind == "factor":
        from ez.factor.base import Factor as _Base
    elif kind == "cross_factor":
        from ez.portfolio.cross_factor import CrossSectionalFactor as _Base
    elif kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy as _Base
    elif kind == "ml_alpha":
        from ez.portfolio.ml_alpha import MLAlpha as _Base
    else:
        return None, f"Unknown kind: {kind}"

    for v in vars(mod).values():
        if isinstance(v, type) and issubclass(v, _Base) and v is not _Base:
            return v, None
    return None, f"No {_Base.__name__} subclass found in module"
