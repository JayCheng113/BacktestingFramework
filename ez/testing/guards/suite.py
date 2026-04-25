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


_PROBE_COUNTER = 0
_PROBE_LOCK = __import__("threading").Lock()


def _unique_probe_module_name(stem: str) -> str:
    """Return a globally-unique module name for a one-shot guard import.

    V2.19.0 post-review I6: using the canonical production module name
    (``factors.foo``, ``strategies.bar``, etc.) causes
    ``Strategy.__init_subclass__`` to **raise** ``ValueError`` on the
    full-key collision (Strategy registry is keyed by ``module.class``),
    blocking the guard entirely. A unique probe name
    (``_guard_probe._probe{N}_{stem}``) gives the probe class its own
    full-key entry in ``_registry_by_key``, so the import succeeds.

    **Note**: the unique full-key does NOT prevent the name-keyed
    ``_registry`` (for Factor / CrossSectionalFactor / PortfolioStrategy)
    from being **transiently displaced** — those registries are keyed by
    ``__name__`` only, and ``__init_subclass__`` is last-write-wins. The
    probe class displaces the production class for the duration of the
    guard run; ``drop_probe_module`` restores it via reverse walk over
    ``_registry_by_key`` (last-write-wins semantics).

    Codex round-2 finding S7: the name-keyed displacement is silent now —
    ``Factor.__init_subclass__`` (and the CrossSectionalFactor /
    PortfolioStrategy equivalents) check ``cls.__module__.startswith
    ("_guard_probe.")`` and skip the collision warning. The displacement
    still happens; the audit log noise does not.
    """
    global _PROBE_COUNTER
    with _PROBE_LOCK:
        _PROBE_COUNTER += 1
        n = _PROBE_COUNTER
    return f"_guard_probe._probe{n}_{stem}"


def drop_probe_module(module_name: str, kind: GuardKind) -> None:
    """Remove a guard-probe module's registry entries + sys.modules entry.

    Called by `_run_guards` / tests to reclaim registry pollution after the
    guard suite has finished.

    **Dual-dict restore (V2.19.0 post-review follow-up)**: `Factor`,
    `CrossSectionalFactor`, and `PortfolioStrategy` have dual registries —
    ``_registry`` is last-write-wins keyed by ``__name__``, while
    ``_registry_by_key`` is authoritative keyed by ``module.class``. When
    the probe imports the file under a unique module name, the
    ``__init_subclass__`` hook inserts a NEW class object into both dicts.
    Because the probe class has the same ``__name__`` as the production
    class, it **displaces** the production entry in ``_registry``. Dropping
    the probe by ``__module__`` filter leaves ``_registry`` missing the
    production entry entirely.

    Fix: after popping probe entries, rebuild any missing name-keyed
    entries from the authoritative ``_registry_by_key`` (which was not
    touched on the production side because the probe class has a
    different ``module.class`` key).
    """
    if kind == "strategy":
        from ez.strategy.base import Strategy as _Base
        bases = [_Base._registry]
        has_dual_dict = False
    elif kind == "factor":
        from ez.factor.base import Factor as _Base
        bases = [_Base._registry, _Base._registry_by_key]
        has_dual_dict = True
    elif kind in ("cross_factor", "ml_alpha"):
        from ez.portfolio.cross_factor import CrossSectionalFactor as _Base
        bases = [_Base._registry, _Base._registry_by_key]
        has_dual_dict = True
    elif kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy as _Base
        bases = [_Base._registry, _Base._registry_by_key]
        has_dual_dict = True
    else:
        return

    # Step 1: pop all entries whose __module__ matches the probe module name.
    for reg in bases:
        for k in [k for k, v in reg.items() if getattr(v, "__module__", None) == module_name]:
            reg.pop(k, None)

    # Step 2: for dual-dict kinds, restore any production class whose
    # name-keyed entry was displaced by the probe import. The name-keyed
    # dict is **last-write-wins** per `__init_subclass__` semantics — so
    # when we restore, we must pick the LAST same-named class in the
    # full-keyed dict's insertion order, NOT the first.
    #
    # Codex round-2 finding P1 #1: a naive forward-scan restore that
    # stops at the first match would silently downgrade the name-keyed
    # dict to an older same-named class, causing /api/code routes and
    # frontend dropdowns to show the wrong production class.
    if has_dual_dict:
        name_keyed = bases[0]
        full_keyed = bases[1]
        # Identify names that are currently missing from name_keyed.
        missing = {
            cls.__name__
            for cls in full_keyed.values()
            if cls.__name__ not in name_keyed
        }
        if missing:
            # Walk full_keyed in REVERSE insertion order. For each missing
            # name, the first reverse-hit is the last-inserted same-named
            # class — this matches last-write-wins.
            for cls in reversed(list(full_keyed.values())):
                if cls.__name__ in missing:
                    name_keyed[cls.__name__] = cls
                    missing.discard(cls.__name__)
                    if not missing:
                        break

    if module_name in sys.modules:
        del sys.modules[module_name]


def load_user_class(
    file_path: Path, module_name: str, kind: GuardKind,
) -> tuple[type | None, str | None]:
    """Import a user file under a unique probe module name and return (class, error).

    Uses a unique ``_guard_probe._probeN_{stem}`` module name so that
    ``__init_subclass__`` does not collide with the hot-reloaded
    production class. The caller is responsible for cleaning up via
    ``drop_probe_module`` after the guard suite finishes.

    The returned `class` object will have `__module__` pointing at the
    probe module name, NOT the production name — callers must use the
    class for in-process inspection only, not register it with the
    engine or persist it.
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
        from ez.portfolio.ml.alpha import MLAlpha as _Base
    else:
        return None, f"Unknown kind: {kind}"

    for v in vars(mod).values():
        if isinstance(v, type) and issubclass(v, _Base) and v is not _Base:
            return v, None
    return None, f"No {_Base.__name__} subclass found in module"
