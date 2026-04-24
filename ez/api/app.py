"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ez.api.deps import close_resources, get_tushare_provider
from ez.config import load_config
from ez.strategy.loader import load_all_strategies, load_user_factors


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    load_all_strategies()
    load_user_factors()
    # V2.9: load user portfolio strategies + cross factors
    from ez.portfolio.loader import load_portfolio_strategies, load_cross_factors, load_ml_alphas
    load_portfolio_strategies()
    load_cross_factors()
    load_ml_alphas()  # V2.13.1 Phase 5
    # V2.15: Resume running paper-trading deployments from DB
    auto_tick_task = None
    try:
        from ez.api.routes.live import get_scheduler
        scheduler = get_scheduler()
        restored = await scheduler.resume_all()
        if restored:
            logging.getLogger(__name__).info("Restored %d paper-trading deployments", restored)

        # V2.17 round 3: auto-tick loop for unattended operation.
        # Opt-in via EZ_LIVE_AUTO_TICK=1 env var. Scheduler's
        # idempotency (last_processed_date) + calendar check + future-
        # date guard make this safe to run frequently — re-ticking
        # today's date on an already-processed deployment is a no-op.
        import os as _os
        if _os.environ.get("EZ_LIVE_AUTO_TICK") == "1":
            interval_s = int(_os.environ.get("EZ_LIVE_AUTO_TICK_INTERVAL_S", "3600"))
            if interval_s <= 0:
                raise ValueError(
                    f"EZ_LIVE_AUTO_TICK_INTERVAL_S must be positive, got {interval_s}"
                )
            # V2.17 round 5: webhook alert dispatcher (optional)
            from ez.live.alert_dispatcher import from_env as _alerts_from_env
            from ez.api.routes.live import _get_monitor
            alert_dispatcher = _alerts_from_env()
            monitor = _get_monitor() if alert_dispatcher else None
            auto_tick_task = asyncio.create_task(
                _auto_tick_loop(
                    scheduler, interval_s,
                    alert_dispatcher=alert_dispatcher,
                    monitor=monitor,
                ),
                name="live_auto_tick",
            )
            logging.getLogger(__name__).info(
                "Live auto-tick enabled (interval=%ds, alerts=%s)",
                interval_s, "on" if alert_dispatcher else "off",
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("Live scheduler resume_all failed: %s", exc)
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
    # V2.17 round 3: stop auto-tick loop cleanly on shutdown
    if auto_tick_task is not None:
        auto_tick_task.cancel()
        try:
            await auto_tick_task
        except (asyncio.CancelledError, Exception):
            pass
    # Async cleanup of LLM provider (must happen before sync close_resources).
    # close_resources() also calls reset_provider_cache() → close(), but that is
    # a no-op because aclose() already sets _async_client = None.
    from ez.llm.factory import get_cached_provider
    provider = get_cached_provider()
    try:
        if provider is not None:
            await provider.aclose()
    finally:
        close_resources()


async def _auto_tick_loop(
    scheduler,
    interval_s: int,
    alert_dispatcher=None,
    monitor=None,
) -> None:
    """V2.17 round 3: background loop that ticks the live scheduler
    at a fixed interval for unattended paper-trading operation.

    Safety:
    - scheduler.tick() is idempotent per (deployment_id, snapshot_date)
      via `last_processed_date` — re-ticking today during an already-
      processed day is a no-op for that deployment.
    - Calendar check per-deployment: non-trading days skip naturally.
    - future-date guard in scheduler.tick() prevents pollution if the
      system clock drifts.
    - Each iteration logs errors but does NOT crash the loop. User can
      still trigger /api/live/tick manually.

    Enabled via env EZ_LIVE_AUTO_TICK=1. Default off — manual-only.
    Interval configurable via EZ_LIVE_AUTO_TICK_INTERVAL_S (default 1h).
    A short interval is cheap because of the idempotency guarantees.

    V2.17 round 5: if `alert_dispatcher` and `monitor` are supplied,
    after each iteration the loop checks monitor alerts and dispatches
    new ones to the configured webhook, even if tick() itself failed.
    Webhook failures are logged but never crash the loop.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be positive, got {interval_s}")
    log = logging.getLogger(__name__)
    while True:
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return
        tick_completed = False
        total_results = 0
        tick_batches = getattr(scheduler, "get_auto_tick_batches", None)
        if callable(tick_batches):
            batches = tick_batches()
        else:
            batches = [(date.today(), None)]
        for business_date, markets in batches:
            try:
                if markets is None:
                    results = await scheduler.tick(business_date)
                else:
                    results = await scheduler.tick(business_date, markets=markets)
                tick_completed = True
                total_results += len(results or [])
            except asyncio.CancelledError:
                return
            except ValueError as e:
                scope = (
                    f" for markets {list(markets)}"
                    if markets is not None
                    else ""
                )
                log.warning("Auto-tick skipped for %s%s: %s", business_date, scope, e)
            except Exception:
                scope = (
                    f" for markets {list(markets)}"
                    if markets is not None
                    else ""
                )
                log.exception(
                    "Auto-tick iteration failed for %s%s (loop continues)",
                    business_date,
                    scope,
                )
        if tick_completed and total_results:
            processed_dates = ", ".join(str(business_date) for business_date, _ in batches)
            log.info(
                "Auto-tick for %s: %d deployments processed",
                processed_dates,
                total_results,
            )
        if alert_dispatcher is not None and monitor is not None:
            try:
                alerts = monitor.check_alerts()
                if alerts:
                    await alert_dispatcher.dispatch_new(alerts)
            except asyncio.CancelledError:
                return
            except Exception:
                phase = "after tick" if tick_completed else "after tick failure"
                log.exception("Alert dispatch failed (%s, loop continues)", phase)


def _get_version() -> str:
    """Read version from pyproject.toml (single source of truth).

    Priority:
    1. pyproject.toml on disk (works in dev, editable install, CI)
    2. importlib.metadata (works in frozen/packaged mode where pyproject.toml is absent)
    3. Hardcoded fallback
    """
    try:
        import tomllib
        from pathlib import Path
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        if pyproject.exists():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return data["project"]["version"]
    except Exception:
        pass
    try:
        from importlib.metadata import version
        return version("ez-trading")
    except Exception:
        pass
    return "0.3.1"

_APP_VERSION = _get_version()

app = FastAPI(title="ez-trading", version=_APP_VERSION, lifespan=lifespan)

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


from ez.api.routes import market_data, backtest, factors, experiments, candidates, code, chat, settings, research, portfolio, fundamental, live, validation  # noqa: E402
app.include_router(market_data.router, prefix="/api/market-data", tags=["market-data"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
app.include_router(factors.router, prefix="/api/factors", tags=["factors"])
app.include_router(experiments.router, prefix="/api/experiments", tags=["experiments"])
app.include_router(candidates.router, prefix="/api/candidates", tags=["candidates"])
app.include_router(code.router, prefix="/api/code", tags=["code"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(research.router, prefix="/api/research", tags=["research"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(fundamental.router, prefix="/api/fundamental", tags=["fundamental"])
app.include_router(live.router, prefix="/api/live", tags=["live"])
app.include_router(validation.router, prefix="/api/validation", tags=["validation"])


@app.get("/api/health")
def health():
    from ez.strategy.base import Strategy
    return {
        "status": "ok",
        "version": _APP_VERSION,
        "strategies_registered": len(Strategy._registry),
    }


# Serve frontend static files (built React app)
import sys as _sys
if getattr(_sys, 'frozen', False):
    _FRONTEND_DIR = Path(_sys._MEIPASS) / "web" / "dist"
    if not _FRONTEND_DIR.exists():
        logging.getLogger(__name__).warning("Frontend assets not found at %s", _FRONTEND_DIR)
else:
    from ez.config import get_project_root as _get_root
    _FRONTEND_DIR = _get_root() / "web" / "dist"
if _FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve React SPA — non-API routes return index.html. API routes get 404."""
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": f"API endpoint not found: /{path}"})
        # Path traversal protection: resolve and verify containment
        file = (_FRONTEND_DIR / path).resolve()
        frontend_root = _FRONTEND_DIR.resolve()
        if file.is_relative_to(frontend_root) and file.exists() and file.is_file():
            return FileResponse(str(file))
        return FileResponse(str(_FRONTEND_DIR / "index.html"))
