"""V2.7: Code editor API — template, validate, save, list strategies."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ez.agent.sandbox import (
    _safe_filename,
    check_syntax,
    get_template,
    list_user_strategies,
    save_and_validate_strategy,
)

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_STRATEGIES_DIR = _PROJECT_ROOT / "strategies"


class TemplateRequest(BaseModel):
    kind: str = "strategy"  # "strategy" | "factor"
    class_name: str = ""
    description: str = ""


class ValidateRequest(BaseModel):
    code: str


class SaveRequest(BaseModel):
    filename: str
    code: str
    overwrite: bool = False


@router.post("/template")
def generate_template(req: TemplateRequest):
    """Generate a Python template for a strategy or factor."""
    if req.kind not in ("strategy", "factor"):
        raise HTTPException(status_code=422, detail="kind must be 'strategy' or 'factor'")
    code = get_template(kind=req.kind, class_name=req.class_name, description=req.description)
    return {"code": code, "kind": req.kind}


@router.post("/validate")
def validate_code(req: ValidateRequest):
    """Check syntax and forbidden imports without saving."""
    errors = check_syntax(req.code)
    return {"valid": len(errors) == 0, "errors": errors}


@router.post("/save")
def save_code(req: SaveRequest):
    """Save code to strategies/ and run contract test."""
    result = save_and_validate_strategy(req.filename, req.code, overwrite=req.overwrite)
    if not result["success"]:
        raise HTTPException(status_code=422, detail=result)
    return result


@router.get("/files")
def list_files():
    """List user strategy files."""
    return list_user_strategies()


@router.get("/files/{filename}")
def read_file(filename: str):
    """Read a strategy file."""
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")
    target = _STRATEGIES_DIR / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return {"filename": safe_name, "code": target.read_text(encoding="utf-8")}


@router.delete("/files/{filename}")
def delete_file(filename: str):
    """Delete a user strategy file and unregister from Strategy._registry."""
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")
    target = _STRATEGIES_DIR / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    target.unlink()
    # Clean up registry so deleted strategy doesn't appear in lists
    try:
        import sys
        from ez.strategy.base import Strategy
        stem = safe_name.replace(".py", "")
        module_name = f"strategies.{stem}"
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

    # New filename: remove research_ prefix
    dst = src.replace("research_", "", 1)
    code = src_path.read_text(encoding="utf-8")

    # Also rename class: ResearchXxx → Xxx
    import re
    code = re.sub(r'class Research(\w+)\(', r'class \1(', code)
    code = re.sub(r'return "Research', r'return "', code)

    result = save_and_validate_strategy(dst, code, overwrite=False)
    if not result["success"]:
        return {"success": False, "errors": result["errors"]}
    return {"success": True, "filename": dst, "path": result.get("path", "")}
