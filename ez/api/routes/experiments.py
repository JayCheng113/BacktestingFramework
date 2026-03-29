"""B6: Experiment API endpoints.

POST /experiments       — submit and run an experiment
GET  /experiments       — list recent experiments
GET  /experiments/{id}  — get single experiment detail
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ez.agent.experiment_store import ExperimentStore
from ez.agent.gates import GateConfig, ResearchGate
from ez.agent.report import ExperimentReport
from ez.agent.run_spec import RunSpec
from ez.agent.runner import Runner
from ez.api.deps import get_chain

router = APIRouter()


# ---- Request / Response models ----

class ExperimentRequest(BaseModel):
    strategy_name: str
    strategy_params: dict = {}
    symbol: str
    market: str = "cn_stock"
    period: str = "daily"
    start_date: date
    end_date: date
    initial_capital: float = 100_000.0
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    slippage_rate: float = Field(default=0.0, ge=0, le=0.1)
    run_backtest: bool = True
    run_wfo: bool = True
    wfo_n_splits: int = Field(default=5, ge=2, le=20)
    wfo_train_ratio: float = Field(default=0.7, ge=0.5, le=0.9)
    tags: list[str] = []
    description: str = ""

    # Gate config (optional overrides)
    gate_min_sharpe: float = 0.5
    gate_max_drawdown: float = 0.3
    gate_min_trades: int = 10
    gate_max_p_value: float = 0.05


# ---- Helpers ----

_exp_store: ExperimentStore | None = None


def close_experiment_store() -> None:
    """Shut down the experiment store connection. Called by deps.close_resources()."""
    global _exp_store
    if _exp_store is not None:
        _exp_store.close()
        _exp_store = None


def _resolve_db_path() -> str:
    """Resolve DB path using same logic as core DuckDBStore (respects EZ_DATA_DIR)."""
    import os
    from pathlib import Path
    from ez.config import load_config
    data_dir = os.environ.get("EZ_DATA_DIR")
    if data_dir:
        p = Path(data_dir) / "ez_trading.db"
    else:
        config = load_config()
        p = Path(config.database.path)
        if not p.is_absolute():
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            p = project_root / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


def _get_experiment_store() -> ExperimentStore:
    """Get or create ExperimentStore with its own DuckDB connection."""
    global _exp_store
    if _exp_store is None:
        import duckdb
        conn = duckdb.connect(_resolve_db_path())
        _exp_store = ExperimentStore(conn)
    return _exp_store


def _fetch_data(symbol: str, market: str, period: str, start: date, end: date):
    import pandas as pd
    chain = get_chain()
    bars = chain.get_kline(symbol, market, period, start, end)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")
    return pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")


# ---- Endpoints ----

@router.post("")
def submit_experiment(req: ExperimentRequest):
    """Submit and execute an experiment, return report."""
    try:
        spec = RunSpec(
            strategy_name=req.strategy_name,
            strategy_params=req.strategy_params,
            symbol=req.symbol,
            market=req.market,
            period=req.period,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.initial_capital,
            commission_rate=req.commission_rate,
            min_commission=req.min_commission,
            slippage_rate=req.slippage_rate,
            run_backtest=req.run_backtest,
            run_wfo=req.run_wfo,
            wfo_n_splits=req.wfo_n_splits,
            wfo_train_ratio=req.wfo_train_ratio,
            tags=req.tags,
            description=req.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    exp_store = _get_experiment_store()

    # Pre-check: fast duplicate detection (avoids expensive computation)
    existing_run_id = exp_store.get_completed_run_id(spec.spec_id)
    if existing_run_id:
        return {
            "status": "duplicate",
            "message": "Spec already has a completed run",
            "existing_run_id": existing_run_id,
            "spec_id": spec.spec_id,
        }

    # Fetch data and run (expensive — only after pre-check passes)
    data = _fetch_data(req.symbol, req.market, req.period, req.start_date, req.end_date)
    result = Runner().run(spec, data)

    # Gate
    gate_config = GateConfig(
        min_sharpe=req.gate_min_sharpe,
        max_drawdown=req.gate_max_drawdown,
        min_trades=req.gate_min_trades,
        max_p_value=req.gate_max_p_value,
    )
    verdict = ResearchGate(gate_config).evaluate(result)
    report = ExperimentReport.from_result(result, verdict)

    # Persist (PK constraint on completed_specs is the atomic lock)
    exp_store.save_spec(spec.to_dict())
    inserted = exp_store.save_completed_run(report.to_dict())
    if not inserted:
        winner = exp_store.get_completed_run_id(spec.spec_id)
        return {
            "status": "duplicate",
            "message": "Another run completed while this one was executing",
            "existing_run_id": winner,
            "spec_id": spec.spec_id,
        }

    return report.to_dict()


@router.get("")
def list_experiments(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List recent experiment runs."""
    exp_store = _get_experiment_store()
    return exp_store.list_runs(limit=limit, offset=offset)


@router.get("/{run_id}")
def get_experiment(run_id: str):
    """Get a single experiment by run_id."""
    exp_store = _get_experiment_store()
    run = exp_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


@router.delete("/{run_id}")
def delete_experiment(run_id: str):
    """Delete a single experiment run."""
    exp_store = _get_experiment_store()
    if not exp_store.delete_run(run_id):
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {"status": "deleted", "run_id": run_id}


@router.post("/cleanup")
def cleanup_experiments(keep_last: int = Query(default=200, ge=1, le=10000)):
    """Delete oldest runs beyond keep_last threshold."""
    exp_store = _get_experiment_store()
    deleted = exp_store.cleanup_old_runs(keep_last)
    return {"status": "ok", "deleted": deleted}
