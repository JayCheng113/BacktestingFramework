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


def _get_experiment_store() -> ExperimentStore:
    """Get or create ExperimentStore with its own DuckDB connection.

    Uses the same db file as the core store but opens an independent
    connection — avoids accessing DuckDBStore._conn (private).
    """
    global _exp_store
    if _exp_store is None:
        import duckdb
        from ez.config import load_config
        from pathlib import Path
        config = load_config()
        db_path = Path(config.database.path)
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parent.parent.parent.parent / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(db_path))
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

    # Idempotency: check if same spec already has a completed run
    existing = exp_store.find_by_spec_id(spec.spec_id)
    completed = [r for r in existing if r.get("status") == "completed"]
    if completed:
        return {
            "status": "duplicate",
            "message": f"Spec already has {len(completed)} completed run(s)",
            "existing_run_id": completed[0]["run_id"],
            "spec_id": spec.spec_id,
        }

    # Fetch data
    data = _fetch_data(req.symbol, req.market, req.period, req.start_date, req.end_date)

    # Run
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

    # Persist (atomic: transaction prevents concurrent duplicate completed runs)
    exp_store.save_spec(spec.to_dict())
    inserted = exp_store.save_run_if_new(spec.spec_id, report.to_dict())
    if not inserted:
        return {
            "status": "duplicate",
            "message": "Concurrent run completed while this one was executing",
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
