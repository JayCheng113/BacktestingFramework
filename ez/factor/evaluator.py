"""Factor evaluation: IC, ICIR, decay, turnover.

[CORE] — append-only. New metrics can be added, existing must not change.

Degenerate-input contract (V2.12.1 post-review hardening):
- Constant factor (zero variance) → ic_mean/rank_ic_mean/icir/turnover all return 0.0 (not NaN)
- Empty/insufficient data → all metrics return 0.0
- NaN guards are applied at every output point to prevent JSON serialization corruption
  and downstream ranking comparison issues (e.g., V2.13 ML training with constant features)
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy import stats

from ez.types import FactorAnalysis


def _nan_safe(x: float) -> float:
    """Return x if finite, else 0.0. Guards against NaN/inf from degenerate statistical inputs."""
    return float(x) if (x == x and not np.isinf(x)) else 0.0


class FactorEvaluator:
    """Evaluate factor predictive power via time-series IC analysis.

    Note: V1 is single-stock time-series IC (not cross-sectional).
    """

    def evaluate(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series,
        periods: list[int] | None = None,
    ) -> FactorAnalysis:
        if periods is None:
            periods = [1, 5, 10, 20]

        factor_values = factor_values.dropna()
        forward_returns = forward_returns.reindex(factor_values.index).dropna()
        common_idx = factor_values.index.intersection(forward_returns.index)
        fv = factor_values.loc[common_idx]
        fr = forward_returns.loc[common_idx]

        window = min(30, len(fv) // 3)
        if window < 2:
            # Not enough data for meaningful rolling correlation
            return FactorAnalysis(
                ic_series=pd.Series(dtype=float), rank_ic_series=pd.Series(dtype=float),
                ic_mean=0.0, rank_ic_mean=0.0, icir=0.0, rank_icir=0.0,
                ic_decay={p: 0.0 for p in periods}, turnover=0.0,
                quintile_returns=pd.DataFrame(),
            )
        ic_series = self._rolling_corr(fv, fr, window, method="pearson")
        rank_ic_series = self._rolling_corr(fv, fr, window, method="spearman")

        # NaN guard: constant-input rolling correlations produce NaN series → .mean() is NaN
        ic_mean = _nan_safe(ic_series.mean())
        rank_ic_mean = _nan_safe(rank_ic_series.mean())
        ic_std = _nan_safe(ic_series.std())
        rank_ic_std = _nan_safe(rank_ic_series.std())
        icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
        rank_icir = rank_ic_mean / rank_ic_std if rank_ic_std > 1e-10 else 0.0

        ic_decay = {}
        for p in periods:
            shifted_returns = forward_returns.shift(-p).reindex(common_idx).dropna()
            overlap = fv.index.intersection(shifted_returns.index)
            if len(overlap) > 10:
                # np.errstate narrows divide/invalid suppression to the spearmanr call only,
                # leaving unrelated warnings intact
                with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
                    warnings.simplefilter("ignore", category=stats.ConstantInputWarning)
                    corr, _ = stats.spearmanr(fv.loc[overlap], shifted_returns.loc[overlap])
                ic_decay[p] = _nan_safe(corr)
            else:
                ic_decay[p] = 0.0

        # Turnover: rank autocorr. Constant rank → zero variance → autocorr is NaN + emits
        # "invalid value encountered in divide" from numpy. Guard both the warning and the NaN.
        rank = fv.rank()
        if len(rank) > 1:
            with np.errstate(invalid="ignore", divide="ignore"):
                turnover = _nan_safe(rank.autocorr(lag=1))
        else:
            turnover = 0.0

        return FactorAnalysis(
            ic_series=ic_series,
            rank_ic_series=rank_ic_series,
            ic_mean=ic_mean,
            rank_ic_mean=rank_ic_mean,
            icir=icir,
            rank_icir=rank_icir,
            ic_decay=ic_decay,
            turnover=turnover,
            quintile_returns=pd.DataFrame(),
        )

    @staticmethod
    def _rolling_corr(
        a: pd.Series, b: pd.Series, window: int, method: str = "pearson",
    ) -> pd.Series:
        results = []
        # Sanitize inputs per-window rather than suppressing warnings globally:
        # zero-variance chunks produce NaN correlations (handled by caller's NaN guard).
        _CONST_TOL = 1e-12
        with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
            warnings.simplefilter("ignore", category=stats.ConstantInputWarning)
            for i in range(window, len(a) + 1):
                chunk_a = a.iloc[i - window : i]
                chunk_b = b.iloc[i - window : i]
                # Fast-path: constant chunk (within float tolerance) → correlation undefined
                if (chunk_a.max() - chunk_a.min()) < _CONST_TOL or (chunk_b.max() - chunk_b.min()) < _CONST_TOL:
                    results.append(np.nan)
                    continue
                if method == "spearman":
                    corr, _ = stats.spearmanr(chunk_a, chunk_b)
                else:
                    corr = chunk_a.corr(chunk_b)
                results.append(corr)
        idx = a.index[window - 1 :]
        return pd.Series(results, index=idx[: len(results)], name=f"{method}_ic")
