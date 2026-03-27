"""Factor evaluation: IC, ICIR, decay, turnover.

[CORE] — append-only. New metrics can be added, existing must not change.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from ez.types import FactorAnalysis


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
        ic_series = self._rolling_corr(fv, fr, window, method="pearson")
        rank_ic_series = self._rolling_corr(fv, fr, window, method="spearman")

        ic_mean = float(ic_series.mean())
        rank_ic_mean = float(rank_ic_series.mean())
        ic_std = float(ic_series.std())
        rank_ic_std = float(rank_ic_series.std())
        icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
        rank_icir = rank_ic_mean / rank_ic_std if rank_ic_std > 1e-10 else 0.0

        ic_decay = {}
        for p in periods:
            shifted_returns = forward_returns.shift(-p).reindex(common_idx).dropna()
            overlap = fv.index.intersection(shifted_returns.index)
            if len(overlap) > 10:
                corr, _ = stats.spearmanr(fv.loc[overlap], shifted_returns.loc[overlap])
                ic_decay[p] = float(corr)
            else:
                ic_decay[p] = 0.0

        rank = fv.rank()
        turnover = float(rank.autocorr(lag=1)) if len(rank) > 1 else 0.0

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
        for i in range(window, len(a) + 1):
            chunk_a = a.iloc[i - window : i]
            chunk_b = b.iloc[i - window : i]
            if method == "spearman":
                corr, _ = stats.spearmanr(chunk_a, chunk_b)
            else:
                corr = chunk_a.corr(chunk_b)
            results.append(corr)
        idx = a.index[window - 1 :]
        return pd.Series(results, index=idx[: len(results)], name=f"{method}_ic")
