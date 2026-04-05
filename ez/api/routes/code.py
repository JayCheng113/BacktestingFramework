"""V2.7+V2.9: Code editor API — template, validate, save, list strategies + portfolio."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ez.agent.sandbox import (
    _safe_filename,
    _get_dir,
    _VALID_KINDS,
    check_syntax,
    get_template,
    list_user_strategies,
    list_portfolio_files,
    save_and_validate_strategy,
    save_and_validate_code,
)


def _get_registry_for_kind(kind: str) -> dict | None:
    """Get the name-keyed _registry dict for a given kind (read path).

    Read callers can use this single dict since it is the backward-compat
    view. Cleanup callers must use `_get_all_registries_for_kind` instead.
    """
    if kind == "strategy":
        from ez.strategy.base import Strategy
        return Strategy._registry
    elif kind == "factor":
        from ez.factor.base import Factor
        return Factor._registry
    elif kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy
        return PortfolioStrategy._registry
    elif kind == "cross_factor":
        from ez.portfolio.cross_factor import CrossSectionalFactor
        return CrossSectionalFactor._registry
    return None


def _get_all_registries_for_kind(kind: str) -> list[dict]:
    """Get ALL registry dicts for a given kind (cleanup path).

    V2.12.2 codex reviewer: Factor, CrossSectionalFactor, and
    PortfolioStrategy all use dual-dict registries (`_registry` name-keyed
    + `_registry_by_key` module.class-keyed). Cleanup must scrub BOTH
    dicts or the full-key dict leaks zombies. Strategy is single-dict so
    returns just the one entry.
    """
    if kind == "strategy":
        from ez.strategy.base import Strategy
        return [Strategy._registry]
    elif kind == "factor":
        from ez.factor.base import Factor
        return [Factor._registry, Factor._registry_by_key]
    elif kind == "portfolio_strategy":
        from ez.portfolio.portfolio_strategy import PortfolioStrategy
        return [PortfolioStrategy._registry, PortfolioStrategy._registry_by_key]
    elif kind == "cross_factor":
        from ez.portfolio.cross_factor import CrossSectionalFactor
        return [CrossSectionalFactor._registry, CrossSectionalFactor._registry_by_key]
    return []


def _validate_kind(kind: str) -> None:
    if kind not in _VALID_KINDS:
        raise HTTPException(status_code=422, detail=f"Invalid kind: {kind}. Must be one of: {sorted(_VALID_KINDS)}")

router = APIRouter()

from ez.config import get_project_root
_PROJECT_ROOT = get_project_root()
_STRATEGIES_DIR = _PROJECT_ROOT / "strategies"


class TemplateRequest(BaseModel):
    kind: str = "strategy"  # "strategy" | "factor" | "portfolio_strategy" | "cross_factor"
    class_name: str = ""
    description: str = ""


class ValidateRequest(BaseModel):
    code: str


class SaveRequest(BaseModel):
    filename: str
    code: str
    overwrite: bool = False
    kind: str = "strategy"


@router.post("/template")
def generate_template(req: TemplateRequest):
    """Generate a Python template for a strategy or factor."""
    valid_kinds = ("strategy", "factor", "portfolio_strategy", "cross_factor")
    if req.kind not in valid_kinds:
        raise HTTPException(status_code=422, detail=f"kind must be one of {valid_kinds}")
    code = get_template(kind=req.kind, class_name=req.class_name, description=req.description)
    return {"code": code, "kind": req.kind}


@router.post("/validate")
def validate_code(req: ValidateRequest):
    """Check syntax and forbidden imports without saving."""
    errors = check_syntax(req.code)
    return {"valid": len(errors) == 0, "errors": errors}


@router.post("/save")
def save_code(req: SaveRequest):
    """Save code and run contract test. kind determines target directory."""
    result = save_and_validate_code(req.filename, req.code, kind=req.kind, overwrite=req.overwrite)
    if not result["success"]:
        raise HTTPException(status_code=422, detail=result)
    return result


@router.get("/files")
def list_files(kind: str = Query(default="")):
    """List user code files. kind: empty/strategy/factor=strategies, portfolio_strategy, cross_factor."""
    if kind in ("portfolio_strategy", "cross_factor", "factor"):
        return list_portfolio_files(kind)
    if kind and kind not in ("", "strategy"):
        raise HTTPException(status_code=422, detail=f"Invalid kind: {kind}. Must be one of: strategy, factor, portfolio_strategy, cross_factor")
    return list_user_strategies()


@router.get("/files/{filename}")
def read_file(filename: str, kind: str = Query(default="strategy")):
    """Read a code file. kind determines which directory to look in."""
    _validate_kind(kind)
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")
    target_dir = _get_dir(kind)
    target = target_dir / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return {"filename": safe_name, "code": target.read_text(encoding="utf-8"), "kind": kind}


@router.delete("/files/{filename}")
def delete_file(filename: str, kind: str = Query(default="strategy")):
    """Delete a code file and unregister from registry.

    Order: clean registry FIRST, then delete file. If registry cleanup fails,
    file is still deleted but warning is returned (no zombie state).
    """
    _validate_kind(kind)
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")
    target_dir = _get_dir(kind)
    target = target_dir / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    # Step 1: Clean registry BEFORE deleting file (prevents zombie state).
    # V2.12.2 codex reviewer: clean BOTH name-keyed and module.class-keyed
    # dicts so dual-dict registries (Factor/CrossSectionalFactor/
    # PortfolioStrategy) don't leak zombies.
    import sys
    cleanup_warning = ""
    stem = safe_name.replace(".py", "")
    module_name = f"{target_dir.name}.{stem}"
    try:
        for registry in _get_all_registries_for_kind(kind):
            old_keys = [k for k, v in registry.items() if v.__module__ == module_name]
            for k in old_keys:
                del registry[k]
        if module_name in sys.modules:
            del sys.modules[module_name]
    except Exception as e:
        cleanup_warning = f"注册表清理部分失败: {e}"

    # Step 2: Delete file
    target.unlink()

    result = {"deleted": safe_name}
    if cleanup_warning:
        result["warning"] = cleanup_warning
    return result


class PromoteRequest(BaseModel):
    filename: str  # research_xxx.py


@router.post("/promote")
def promote_research_strategy(req: PromoteRequest):
    """Copy a research_ strategy to a user strategy (remove research_ prefix), register globally."""
    src = req.filename
    if not src.startswith("research_") or not src.endswith(".py"):
        raise HTTPException(status_code=400, detail="只能注册 research_ 开头的策略文件")
    src_path = _STRATEGIES_DIR / src
    if not src_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {src}")

    # New filename: remove research_ prefix, validate with same rules as other endpoints
    dst = src.replace("research_", "", 1)
    safe_dst = _safe_filename(dst)
    if not safe_dst:
        raise HTTPException(status_code=400, detail=f"目标文件名不合法: {dst}")

    code = src_path.read_text(encoding="utf-8")

    # Rename class: ResearchXxx → Xxx (only when followed by uppercase = class name pattern)
    import re
    code = re.sub(r'class Research([A-Z]\w*)\(', r'class \1(', code)
    code = re.sub(r'return "Research([A-Z])', r'return "\1', code)

    result = save_and_validate_strategy(safe_dst, code, overwrite=False)
    if not result["success"]:
        raise HTTPException(status_code=422, detail=result["errors"][0] if result["errors"] else "验证失败")
    return {"success": True, "filename": safe_dst, "path": result.get("path", "")}


@router.get("/registry")
def get_registry():
    """List all registered strategies/factors with builtin/user classification."""
    from ez.strategy.base import Strategy
    from ez.factor.base import Factor
    from ez.portfolio.portfolio_strategy import PortfolioStrategy
    from ez.portfolio.cross_factor import CrossSectionalFactor
    # Ensure all lazy-loaded builtin modules are registered
    try:
        import ez.factor.builtin.fundamental  # noqa: F401
        import ez.portfolio.builtin_strategies  # noqa: F401
    except ImportError:
        pass

    def _classify(registry: dict, user_prefixes: tuple[str, ...]) -> dict:
        builtin, user = [], []
        for name, cls in registry.items():
            mod = getattr(cls, '__module__', '') or ''
            desc = ''
            try:
                if hasattr(cls, 'get_description'):
                    desc = cls.get_description().strip()[:100]
                elif cls.__doc__:
                    desc = cls.__doc__.strip().split('\n')[0][:100]
            except Exception:
                pass
            short_name = name.rsplit(".", 1)[-1] if "." in name else name
            info = {"name": short_name, "module": mod, "description": desc}
            if any(mod.startswith(p) for p in user_prefixes):
                # Find the .py filename from module name
                parts = mod.rsplit('.', 1)
                info["filename"] = parts[-1] + '.py' if len(parts) > 1 else ''
                info["editable"] = True
                user.append(info)
            else:
                info["editable"] = False
                builtin.append(info)
        return {"builtin": builtin, "user": user}

    return {
        "strategy": _classify(Strategy.get_registry(), ("strategies.",)),
        "factor": _classify(Factor.get_registry(), ("factors.",)),
        "portfolio_strategy": _classify(PortfolioStrategy.get_registry(), ("portfolio_strategies.",)),
        "cross_factor": _classify(CrossSectionalFactor.get_registry(), ("cross_factors.",)),
    }


@router.delete("/cleanup-research-strategies")
def cleanup_research_strategies():
    """Delete all research_* strategy files and clean up Strategy registry."""
    import sys
    from ez.strategy.base import Strategy

    strategies_dir = _get_dir("strategy")
    deleted = []
    for f in sorted(strategies_dir.glob("research_*.py")):
        stem = f.stem
        module_name = f"strategies.{stem}"
        # Clean registry FIRST, then delete file
        old_keys = [k for k, v in Strategy._registry.items() if v.__module__ == module_name]
        for k in old_keys:
            del Strategy._registry[k]
        if module_name in sys.modules:
            del sys.modules[module_name]
        f.unlink()
        deleted.append(f.name)

    return {"deleted": deleted, "count": len(deleted)}


@router.post("/refresh")
def refresh_registries():
    """Re-scan all user directories, reload registries from scratch.

    Clears ALL user registry entries and sys.modules first, then re-imports.
    This correctly handles: file modified, file deleted, file added.
    """
    import sys
    from ez.strategy.loader import load_all_strategies, load_user_factors
    from ez.portfolio.loader import load_portfolio_strategies, load_cross_factors

    # Step 1: Clear ALL user entries from registries + sys.modules
    # (so loaders don't skip already-imported modules).
    # V2.12.2 codex reviewer: clean BOTH dicts for dual-dict registries so
    # the full-key dict doesn't leak zombies.
    for kind, prefix in [("strategy", "strategies"), ("factor", "factors"),
                         ("portfolio_strategy", "portfolio_strategies"), ("cross_factor", "cross_factors")]:
        mods_to_remove: set[str] = set()
        for registry in _get_all_registries_for_kind(kind):
            user_keys = [k for k, v in registry.items()
                         if (v.__module__ or '').startswith(f"{prefix}.")]
            for k in user_keys:
                mods_to_remove.add(registry[k].__module__)
                del registry[k]
        for mod in mods_to_remove:
            if mod in sys.modules:
                del sys.modules[mod]

    # Step 2: Re-scan and reload (all user modules now cleared, loader will re-import)
    load_all_strategies()
    load_user_factors()
    load_portfolio_strategies()
    load_cross_factors()

    from ez.strategy.base import Strategy
    from ez.factor.base import Factor
    from ez.portfolio.portfolio_strategy import PortfolioStrategy
    from ez.portfolio.cross_factor import CrossSectionalFactor

    return {
        "strategies": len(Strategy.get_registry()),
        "factors": len(Factor.get_registry()),
        "portfolio_strategies": len(PortfolioStrategy.get_registry()),
        "cross_factors": len(CrossSectionalFactor.get_registry()),
    }
