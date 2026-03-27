"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ez.api.deps import close_resources, get_tushare_provider
from ez.config import load_config
from ez.strategy.loader import load_all_strategies


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    load_all_strategies()
    # Pre-warm symbol cache so first search is fast
    tushare = get_tushare_provider()
    if tushare:
        try:
            tushare._ensure_symbol_cache()
        except Exception:
            pass
    yield
    close_resources()


app = FastAPI(title="ez-trading", version="0.1.0", lifespan=lifespan)

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


from ez.api.routes import market_data, backtest, factors  # noqa: E402
app.include_router(market_data.router, prefix="/api/market-data", tags=["market-data"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
app.include_router(factors.router, prefix="/api/factors", tags=["factors"])


@app.get("/api/health")
def health():
    from ez.strategy.base import Strategy
    return {
        "status": "ok",
        "version": "0.1.0",
        "strategies_registered": len(Strategy._registry),
    }
