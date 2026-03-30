"""V2.7+V2.9: Code editor API — template, validate, save, list strategies + portfolio."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ez.agent.sandbox import (
    _safe_filename,
    _get_dir,
    check_syntax,
    get_template,
    list_user_strategies,
    list_portfolio_files,
    save_and_validate_strategy,
    save_and_validate_code,
)

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
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
    """List user code files. kind: empty=strategies, portfolio_strategy, cross_factor."""
    if kind in ("portfolio_strategy", "cross_factor"):
        return list_portfolio_files(kind)
    return list_user_strategies()


@router.get("/files/{filename}")
def read_file(filename: str, kind: str = Query(default="strategy")):
    """Read a code file. kind determines which directory to look in."""
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
    """Delete a code file and unregister from registry."""
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")
    target_dir = _get_dir(kind)
    target = target_dir / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    target.unlink()
    # Clean up registry
    try:
        import sys
        stem = safe_name.replace(".py", "")
        if kind in ("portfolio_strategy", "cross_factor"):
            module_name = f"{target_dir.name}.{stem}"
            if kind == "portfolio_strategy":
                from ez.portfolio.portfolio_strategy import PortfolioStrategy
                old_keys = [k for k, v in PortfolioStrategy._registry.items() if v.__module__ == module_name]
                for k in old_keys:
                    del PortfolioStrategy._registry[k]
        else:
            module_name = f"strategies.{stem}"
            from ez.strategy.base import Strategy
            old_keys = [k for k, v in Strategy._registry.items() if v.__module__ == module_name]
            for k in old_keys:
                del Strategy._registry[k]
        if module_name in sys.modules:
            del sys.modules[module_name]
    except Exception:
        pass  # best-effort cleanup
    return {"deleted": safe_name}


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
