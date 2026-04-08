"""B6: Experiment API endpoints.

POST /experiments       — submit and run an experiment
GET  /experiments       — list recent experiments
GET  /experiments/{id}  — get single experiment detail

V2.7.1: Uses shared ExperimentStore singleton from data_access (was duplicate).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ez.agent.data_access import get_experiment_store
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
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.00008
    min_commission: float = 0.0
    slippage_rate: float = Field(default=0.001, ge=0, le=0.1)
    run_backtest: bool = True
    run_wfo: bool = True
    wfo_n_splits: int = Field(default=5, ge=2, le=20)
    wfo_train_ratio: float = Field(default=0.7, ge=0.5, le=0.9)
    tags: list[str] = []
    description: str = ""

    # Market rules (V2.6)
    use_market_rules: bool = False
    t_plus_1: bool = True
    price_limit_pct: float = Field(default=0.1, ge=0, le=0.5)
    lot_size: int = Field(default=100, ge=0)

    # Gate config (optional overrides)
    gate_min_sharpe: float = 0.5
    gate_max_drawdown: float = 0.3
    gate_min_trades: int = 10
    gate_max_p_value: float = 0.05


# ---- Helpers ----

# V2.7.1: ExperimentStore singleton moved to ez/agent/data_access.py
# (was duplicated here — two connections to the same DB)


def _fetch_data(symbol: str, market: str, period: str, start: date, end: date):
    from ez.api.deps import fetch_kline_df
    return fetch_kline_df(symbol, market, period, start, end)


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
            use_market_rules=req.use_market_rules,
            t_plus_1=req.t_plus_1,
            price_limit_pct=req.price_limit_pct,
            lot_size=req.lot_size,
            tags=req.tags,
            description=req.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    exp_store = get_experiment_store()

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
    exp_store = get_experiment_store()
    return exp_store.list_runs(limit=limit, offset=offset)


@router.get("/{run_id}")
def get_experiment(run_id: str):
    """Get a single experiment by run_id."""
    exp_store = get_experiment_store()
    run = exp_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


@router.delete("/{run_id}")
def delete_experiment(run_id: str):
    """Delete a single experiment run."""
    exp_store = get_experiment_store()
    if not exp_store.delete_run(run_id):
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {"status": "deleted", "run_id": run_id}


@router.post("/cleanup")
def cleanup_experiments(keep_last: int = Query(default=200, ge=1, le=10000)):
    """Delete oldest runs beyond keep_last threshold."""
    exp_store = get_experiment_store()
    deleted = exp_store.cleanup_old_runs(keep_last)
    return {"status": "ok", "deleted": deleted}
