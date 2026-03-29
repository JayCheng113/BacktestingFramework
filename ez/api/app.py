"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ez.api.deps import close_resources, get_tushare_provider
from ez.config import load_config
from ez.strategy.loader import load_all_strategies


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    load_all_strategies()
    # Pre-warm symbol cache in background (don't block startup)
    tushare = get_tushare_provider()
    if tushare:
        import threading
        def _warm():
            try:
                tushare._ensure_symbol_cache()
            except Exception as exc:
                logging.getLogger(__name__).warning("Symbol cache pre-warm failed: %s", exc)
        threading.Thread(target=_warm, daemon=True).start()
    yield
    close_resources()


app = FastAPI(title="ez-trading", version="0.2.7", lifespan=lifespan)

config = load_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from ez.errors import BacktestError, EzTradingError, ProviderError, ValidationError  # noqa: E402


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(BacktestError)
async def backtest_error_handler(request: Request, exc: BacktestError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(EzTradingError)
async def ez_error_handler(request: Request, exc: EzTradingError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


from ez.api.routes import market_data, backtest, factors, experiments, candidates, code, chat  # noqa: E402
app.include_router(market_data.router, prefix="/api/market-data", tags=["market-data"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
app.include_router(factors.router, prefix="/api/factors", tags=["factors"])
app.include_router(experiments.router, prefix="/api/experiments", tags=["experiments"])
app.include_router(candidates.router, prefix="/api/candidates", tags=["candidates"])
app.include_router(code.router, prefix="/api/code", tags=["code"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])


@app.get("/api/health")
def health():
    from ez.strategy.base import Strategy
    return {
        "status": "ok",
        "version": "0.2.7",
        "strategies_registered": len(Strategy._registry),
    }


# Serve frontend static files (built React app)
import sys as _sys
if getattr(_sys, 'frozen', False):
    _FRONTEND_DIR = Path(_sys._MEIPASS) / "web" / "dist"
else:
    _FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
if _FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve React SPA — any non-API route returns index.html."""
        file = _FRONTEND_DIR / path
        if file.exists() and file.is_file():
            return FileResponse(str(file))
        return FileResponse(str(_FRONTEND_DIR / "index.html"))
