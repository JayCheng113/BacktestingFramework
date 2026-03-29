"""F1-F4: Candidate search API endpoints.

POST /candidates/search — run batch parameter search
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ez.agent.batch_runner import BatchConfig, run_batch
from ez.agent.candidate_search import ParamRange, SearchConfig, grid_search, random_search
from ez.agent.gates import GateConfig
from ez.agent.prefilter import PrefilterConfig

router = APIRouter()


class ParamRangeRequest(BaseModel):
    name: str
    values: list[int | float]


class SearchRequest(BaseModel):
    strategy_name: str
    param_ranges: list[ParamRangeRequest]
    symbol: str
    market: str = "cn_stock"
    period: str = "daily"
    start_date: date
    end_date: date
    run_wfo: bool = False
    wfo_n_splits: int = Field(default=3, ge=2, le=20)
    mode: str = Field(default="grid", pattern="^(grid|random)$")
    n_samples: int = Field(default=20, ge=1, le=1000)
    seed: int | None = None

    # Pre-filter
    prefilter_min_sharpe: float = 0.0
    prefilter_max_drawdown: float = 0.5
    prefilter_min_trades: int = 5
    skip_prefilter: bool = False

    # Gate
    gate_min_sharpe: float = 0.5
    gate_max_drawdown: float = 0.3
    gate_min_trades: int = 10
    gate_max_p_value: float = 0.05


def _build_search_config(req: SearchRequest) -> SearchConfig:
    return SearchConfig(
        strategy_name=req.strategy_name,
        param_ranges=[ParamRange(pr.name, pr.values) for pr in req.param_ranges],
        symbol=req.symbol,
        market=req.market,
        period=req.period,
        start_date=req.start_date,
        end_date=req.end_date,
        run_wfo=req.run_wfo,
        wfo_n_splits=req.wfo_n_splits,
    )


@router.post("/search")
def search_candidates(req: SearchRequest):
    """Run batch parameter search: grid or random."""
    from ez.api.routes.experiments import _fetch_data, _get_experiment_store

    # Pre-check: reject oversized grids before materializing
    if req.mode == "grid":
        total = 1
        for pr in req.param_ranges:
            total *= max(len(pr.values), 1)
        if total > 1000:
            raise HTTPException(status_code=400, detail=f"Too many candidates ({total}), max 1000")

    try:
        search_config = _build_search_config(req)
        if req.mode == "grid":
            specs = grid_search(search_config)
        else:
            specs = random_search(search_config, req.n_samples, req.seed)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    data = _fetch_data(req.symbol, req.market, req.period, req.start_date, req.end_date)
    store = _get_experiment_store()

    batch_config = BatchConfig(
        gate_config=GateConfig(
            min_sharpe=req.gate_min_sharpe,
            max_drawdown=req.gate_max_drawdown,
            min_trades=req.gate_min_trades,
            max_p_value=req.gate_max_p_value,
        ),
        prefilter_config=PrefilterConfig(
            min_sharpe=req.prefilter_min_sharpe,
            max_drawdown=req.prefilter_max_drawdown,
            min_trades=req.prefilter_min_trades,
        ),
        skip_prefilter=req.skip_prefilter,
    )

    result = run_batch(specs, data, batch_config, store=store)

    return {
        "total_specs": result.total_specs,
        "prefiltered": result.prefiltered,
        "executed": result.executed,
        "duplicates": result.duplicates,
        "passed_count": len(result.passed),
        "ranked": [
            {
                "spec_id": c.spec.spec_id,
                "params": c.spec.strategy_params,
                "sharpe": c.report.sharpe_ratio if c.report else None,
                "total_return": c.report.total_return if c.report else None,
                "max_drawdown": c.report.max_drawdown if c.report else None,
                "trade_count": c.report.trade_count if c.report else 0,
                "gate_passed": c.gate_passed,
                "run_id": c.report.run_id if c.report else None,
            }
            for c in result.ranked
        ],
    }
