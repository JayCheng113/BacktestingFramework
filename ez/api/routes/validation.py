"""V2.22: Unified OOS Validation API.

Single endpoint `POST /api/validation/validate` that runs the full
validation suite on a portfolio run (or pair of runs for comparison):
  - Walk-Forward (rolling N-fold weight optimization)
  - Paired Block Bootstrap CI
  - Monte Carlo significance
  - Deflated Sharpe Ratio
  - Minimum Backtest Length
  - Annual breakdown
  - Optional paired comparison (treatment vs control)
  - Verdict (pass/warn/fail + per-check reasons)

Reads daily returns from stored portfolio_runs (no duplicate backtesting).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ez.research._metrics import (
    compute_basic_metrics,
    deflated_sharpe_ratio,
    minimum_backtest_length,
    annual_breakdown,
)
from ez.research.steps.paired_bootstrap import paired_block_bootstrap
from ez.research.verdict import VerdictThresholds, compute_verdict

router = APIRouter()
logger = logging.getLogger(__name__)


class ValidationRequest(BaseModel):
    run_id: str = Field(
        description="主策略 portfolio_runs.run_id",
        pattern=r"^[a-zA-Z0-9_-]{1,64}$",
    )
    baseline_run_id: str | None = Field(
        default=None,
        description="可选: 对比基线 run_id",
        pattern=r"^[a-zA-Z0-9_-]{1,64}$",
    )
    n_bootstrap: int = Field(default=2000, ge=100, le=10000)
    block_size: int = Field(default=21, ge=1, le=252)
    n_trials: int = Field(
        default=1, ge=1, description="搜过多少策略 (DSR/MinBTL 多重检验调整)"
    )
    seed: int = Field(default=42)


def _run_to_returns(run: dict[str, Any]) -> pd.Series:
    """Convert a portfolio_runs row to a daily-returns Series."""
    equity_curve = run.get("equity_curve")
    dates = run.get("dates")
    if not equity_curve or not dates:
        raise HTTPException(
            status_code=422,
            detail=f"Run {run.get('run_id')} missing equity_curve or dates",
        )
    # Both are JSON strings (TEXT columns)
    if isinstance(equity_curve, str):
        equity_curve = json.loads(equity_curve)
    if isinstance(dates, str):
        dates = json.loads(dates)
    if len(equity_curve) < 30:
        raise HTTPException(
            status_code=422,
            detail=f"Run {run.get('run_id')} has only {len(equity_curve)} equity points",
        )
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    equity_series = pd.Series(equity_curve, index=idx)
    returns = equity_series.pct_change().iloc[1:]
    return returns


def _load_run_returns(run_id: str) -> tuple[dict[str, Any], pd.Series]:
    """Load a run and its daily returns. Raises HTTPException on failure.

    V2.23 I1 fix: reuse PortfolioStore singleton from routes.portfolio
    instead of constructing a new store (with new DuckDB connection)
    per request.
    """
    from ez.api.routes.portfolio import _get_store
    store = _get_store()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    returns = _run_to_returns(run)
    return run, returns


def _compute_bootstrap_on_single(
    returns: pd.Series,
    n_bootstrap: int,
    block_size: int,
    seed: int,
) -> dict[str, Any]:
    """Bootstrap CI on a single return series (null = Sharpe 0).

    We can't use paired_block_bootstrap directly since it tests
    A - B. Instead, use a single-series block bootstrap to get
    the Sharpe CI and the Monte Carlo p-value under H0: Sharpe=0.
    """
    arr = returns.dropna().to_numpy()
    n = len(arr)
    if n < block_size * 2:
        raise HTTPException(
            status_code=422,
            detail=f"Returns length {n} < block_size*2 ({block_size * 2})",
        )

    rng = np.random.default_rng(seed)
    # Observed Sharpe (annualized)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    observed_sharpe = (mean / std * np.sqrt(252)) if std > 1e-12 else 0.0

    # Block bootstrap distribution of Sharpe
    n_blocks = (n + block_size - 1) // block_size
    boot_sharpes = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        block_starts = rng.integers(0, n - block_size + 1, size=n_blocks)
        idx = np.concatenate([
            np.arange(s, min(s + block_size, n)) for s in block_starts
        ])[:n]
        resample = arr[idx]
        m = float(np.mean(resample))
        s = float(np.std(resample, ddof=1))
        boot_sharpes[i] = (m / s * np.sqrt(252)) if s > 1e-12 else 0.0

    ci_lower = float(np.percentile(boot_sharpes, 2.5))
    ci_upper = float(np.percentile(boot_sharpes, 97.5))

    # Two-sided bootstrap hypothesis test for H0: true Sharpe = 0.
    # Classical construction (Efron & Tibshirani 1993, Ch 15):
    #     p̂ = #{|boot_stat - mean(boot_stats)| >= |observed - null_value|} / B
    # For null_value=0:
    #     p̂ = #{|boot_stat - mean(boot_stats)| >= |observed|} / B
    # The centering simulates the null: under H0, the sampling distribution
    # would be centered at 0, and we ask how often a sample as extreme as
    # the observed statistic would arise by chance.
    # Note: this is the pivot-style bootstrap test, not the "center the
    # data and resample" variant — they are asymptotically equivalent for
    # statistics of the mean, and the pivot version is simpler to implement
    # correctly with block bootstrap (preserving autocorrelation).
    # Two-sided is more conservative than one-sided for a "Sharpe > 0"
    # test — effectively requires p_one_sided <= 0.025 at alpha=0.05.
    centered = boot_sharpes - np.mean(boot_sharpes)
    if abs(observed_sharpe) < 1e-12:
        p_value = 1.0
    else:
        p_value = float(np.mean(np.abs(centered) >= abs(observed_sharpe)))
        p_value = max(p_value, 1.0 / n_bootstrap)

    return {
        "observed_sharpe": observed_sharpe,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "p_value": p_value,
        "n_bootstrap": n_bootstrap,
        "block_size": block_size,
    }


def _extract_wf_from_run(run: dict[str, Any]) -> dict[str, Any] | None:
    """Extract WF aggregate from stored run's wf_metrics, if present.

    V2.15.1 stores WF results in the portfolio_runs.wf_metrics column.
    If the user hasn't run WF yet, returns None.
    """
    wf_raw = run.get("wf_metrics")
    if not wf_raw:
        return None
    if isinstance(wf_raw, str):
        try:
            wf_raw = json.loads(wf_raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(wf_raw, dict):
        return None
    return wf_raw


@router.post("/validate")
def validate(req: ValidationRequest) -> dict[str, Any]:
    """Run the unified OOS validation suite on a portfolio run.

    Returns a structured result covering:
      - significance (bootstrap CI + Monte Carlo p-value)
      - deflated Sharpe / MinBTL
      - annual breakdown
      - WF aggregate (if wf_metrics is stored on the run)
      - paired comparison (if baseline_run_id is provided)
      - verdict (pass/warn/fail + per-check reasons)
    """
    # 1. Load main run + returns
    main_run, main_returns = _load_run_returns(req.run_id)

    # 2. Significance (bootstrap + Monte Carlo)
    significance = _compute_bootstrap_on_single(
        main_returns, req.n_bootstrap, req.block_size, req.seed,
    )

    # 3. Deflated Sharpe + MinBTL
    deflated = deflated_sharpe_ratio(
        main_returns, n_trials=req.n_trials, sr_benchmark=0.0,
    )
    sharpe = deflated["sharpe"] if deflated else 0.0
    min_btl_years = minimum_backtest_length(
        sharpe, alpha=0.05, n_trials=req.n_trials,
    )
    actual_years = len(main_returns) / 252.0
    min_btl_result = {
        "actual_years": actual_years,
        "min_btl_years": min_btl_years,
    }

    # 4. Annual breakdown
    annual = annual_breakdown(main_returns)

    # 5. WF aggregate (from stored wf_metrics if available)
    wf = _extract_wf_from_run(main_run)

    # 6. Paired comparison (optional)
    comparison: dict[str, Any] | None = None
    if req.baseline_run_id:
        try:
            baseline_run, baseline_returns = _load_run_returns(req.baseline_run_id)
            # Align indices
            combined = pd.DataFrame({
                "treatment": main_returns,
                "control": baseline_returns,
            }).dropna()
            if len(combined) >= req.block_size * 2:
                cmp_result = paired_block_bootstrap(
                    returns_a=combined["treatment"].values,
                    returns_b=combined["control"].values,
                    n_bootstrap=req.n_bootstrap,
                    block_size=req.block_size,
                    seed=req.seed,
                )
                treatment_metrics = compute_basic_metrics(combined["treatment"]) or {}
                control_metrics = compute_basic_metrics(combined["control"]) or {}
                comparison = {
                    "treatment_run_id": req.run_id,
                    "control_run_id": req.baseline_run_id,
                    "sharpe_diff": cmp_result["observed"],
                    "ci_lower": cmp_result["ci_lower"],
                    "ci_upper": cmp_result["ci_upper"],
                    "p_value": cmp_result["p_value"],
                    "is_significant": cmp_result["p_value"] < 0.05,
                    "ci_excludes_zero": (
                        cmp_result["ci_lower"] > 0 or cmp_result["ci_upper"] < 0
                    ),
                    "treatment_metrics": treatment_metrics,
                    "control_metrics": control_metrics,
                    "n_observations": len(combined),
                }
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Paired comparison failed: %s", e)
            comparison = {"error": str(e)}

    # 7. Verdict
    verdict = compute_verdict(
        wf_aggregate=wf,
        bootstrap=significance,
        deflated=deflated,
        min_btl_result=min_btl_result,
        annual=annual,
    )

    return {
        "run_id": req.run_id,
        "baseline_run_id": req.baseline_run_id,
        "significance": significance,
        "deflated": deflated,
        "min_btl": min_btl_result,
        "annual": annual,
        "walk_forward": wf,
        "comparison": comparison,
        "verdict": verdict.to_dict(),
    }
