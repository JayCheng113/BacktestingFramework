"""FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ez.config import load_config
from ez.strategy.loader import load_all_strategies

app = FastAPI(title="ez-trading", version="0.1.0")

config = load_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

load_all_strategies()

from ez.api.routes import market_data, backtest, factors
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
