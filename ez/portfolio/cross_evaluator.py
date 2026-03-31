"""V2.10 F1: CrossSectionalEvaluator — evaluate cross-sectional factor effectiveness.

Computes:
- IC (Pearson correlation between factor scores and forward returns)
- Rank IC (Spearman rank correlation)
- ICIR (IC mean / IC std, measures consistency)
- IC decay (IC at different forward horizons)
- Quintile returns (factor-sorted portfolio returns by group)
"""
from __future__ import annotations

import bisect
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from scipy import stats

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import CrossSectionalFactor
from ez.portfolio.universe import slice_universe_data


@dataclass
class CrossSectionalEvalResult:
    """Result of cross-sectional factor evaluation."""
    factor_name: str = ""
    # Per-date IC series
    ic_series: list[float] = field(default_factory=list)
    rank_ic_series: list[float] = field(default_factory=list)
    eval_dates: list[str] = field(default_factory=list)  # ISO dates
    # Aggregated
    mean_ic: float = 0.0
    mean_rank_ic: float = 0.0
    ic_std: float = 0.0
    rank_ic_std: float = 0.0
    icir: float = 0.0         # mean_ic / ic_std
    rank_icir: float = 0.0    # mean_rank_ic / rank_ic_std
    # IC decay: {lag_days: mean_ic}
    ic_decay: dict[int, float] = field(default_factory=dict)
    # Quintile returns: {quintile(1-5): mean_forward_return}
    quintile_returns: dict[int, float] = field(default_factory=dict)
    quintile_count: dict[int, int] = field(default_factory=dict)
    # Coverage
    n_eval_dates: int = 0
    avg_stocks_per_date: float = 0.0


def _build_price_index(universe_data: dict[str, pd.DataFrame]) -> dict[str, tuple[list[date], list[float]]]:
    """Pre-build sorted (date, adj_close) arrays for O(log n) forward return lookup."""
    index: dict[str, tuple[list[date], list[float]]] = {}
    for sym, df in universe_data.items():
        col = "adj_close" if "adj_close" in df.columns else "close"
        dates_list = []
        prices_list = []
        for i in range(len(df)):
            d = df.index[i].date() if isinstance(df.index, pd.DatetimeIndex) else df.index[i]
            p = float(df.iloc[i][col])
            if not np.isnan(p):
                dates_list.append(d)
                prices_list.append(p)
        # Ensure sorted
        if dates_list and dates_list != sorted(dates_list):
            order = sorted(range(len(dates_list)), key=lambda k: dates_list[k])
            dates_list = [dates_list[k] for k in order]
            prices_list = [prices_list[k] for k in order]
        index[sym] = (dates_list, prices_list)
    return index


def _get_price_at(index: dict[str, tuple[list[date], list[float]]], sym: str, d: date) -> float | None:
    """Get price at or before date d via bisect."""
    if sym not in index:
        return None
    dates, prices = index[sym]
    idx = bisect.bisect_right(dates, d) - 1
    if idx < 0:
        return None
    return prices[idx]


def _get_forward_return(index: dict[str, tuple[list[date], list[float]]],
                        sym: str, d: date, forward_days: int) -> float | None:
    """Compute forward N-trading-day return from date d."""
    if sym not in index:
        return None
    dates, prices = index[sym]
    # Find d in the pre-indexed dates (or closest prior date)
    idx0 = bisect.bisect_left(dates, d)
    if idx0 >= len(dates) or dates[idx0] != d:
        idx0 = bisect.bisect_right(dates, d) - 1
        if idx0 < 0:
            return None
    p0 = prices[idx0]
    if p0 <= 0:
        return None
    target_idx = idx0 + forward_days
    if target_idx >= len(dates):
        return None
    p1 = prices[target_idx]
    if p1 <= 0:
        return None
    return p1 / p0 - 1


def evaluate_cross_sectional_factor(
    factor: CrossSectionalFactor,
    universe_data: dict[str, pd.DataFrame],
    calendar: TradingCalendar,
    start: date,
    end: date,
    forward_days: int = 5,
    eval_freq: str = "weekly",
    n_quantiles: int = 5,
    lookback_days: int = 252,
) -> CrossSectionalEvalResult:
    """Evaluate a cross-sectional factor's predictive power.

    Args:
        factor: CrossSectionalFactor to evaluate.
        universe_data: {symbol: DataFrame} with OHLCV + adj_close.
        calendar: Trading calendar.
        start, end: Evaluation period.
        forward_days: Forward return horizon in trading days.
        eval_freq: How often to evaluate ("daily", "weekly", "monthly").
        n_quantiles: Number of quantile groups for layered returns.
        lookback_days: Data lookback for factor computation.
    """
    result = CrossSectionalEvalResult(factor_name=factor.name)

    # Get evaluation dates
    eval_dates = calendar.rebalance_dates(start, end, eval_freq)
    if not eval_dates:
        return result

    # Pre-build price index for forward returns
    price_index = _build_price_index(universe_data)

    ic_list: list[float] = []
    rank_ic_list: list[float] = []
    all_quintile_returns: dict[int, list[float]] = {q: [] for q in range(1, n_quantiles + 1)}
    stock_counts: list[int] = []

    for eval_date in eval_dates:
        # Slice data for factor (anti-lookahead: up to eval_date - 1)
        sliced = slice_universe_data(universe_data, eval_date, lookback_days)
        if not sliced:
            continue

        # Compute factor scores
        dt = datetime.combine(eval_date, datetime.min.time())
        scores = factor.compute(sliced, dt)
        if scores.empty or len(scores) < 5:
            continue

        # Compute forward returns for each stock
        fwd_returns = {}
        for sym in scores.index:
            ret = _get_forward_return(price_index, sym, eval_date, forward_days)
            if ret is not None:
                fwd_returns[sym] = ret

        # Align: only stocks with both score and forward return
        common = sorted(set(scores.index) & set(fwd_returns.keys()))
        if len(common) < 5:
            continue

        s = scores[common].values.astype(float)
        r = np.array([fwd_returns[sym] for sym in common])

        # Remove NaN
        mask = ~(np.isnan(s) | np.isnan(r))
        s, r = s[mask], r[mask]
        if len(s) < 5:
            continue

        # IC (Pearson) — suppress ConstantInputWarning for zero-variance inputs
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ic = float(np.corrcoef(s, r)[0, 1]) if np.std(s) > 0 and np.std(r) > 0 else np.nan
            rank_ic = float(stats.spearmanr(s, r).statistic) if len(s) >= 5 else np.nan

        # NaN for invalid dates — both series and aggregation use NaN consistently.
        # Frontend renders NaN as null (ECharts skips null points in line charts).
        ic_val = ic if not np.isnan(ic) else np.nan
        rank_ic_val = rank_ic if not np.isnan(rank_ic) else np.nan
        ic_list.append(ic_val)
        rank_ic_list.append(rank_ic_val)
        result.ic_series.append(None if np.isnan(ic_val) else float(ic_val))
        result.rank_ic_series.append(None if np.isnan(rank_ic_val) else float(rank_ic_val))
        result.eval_dates.append(eval_date.isoformat())
        stock_counts.append(len(common))

        # Quintile returns
        score_series = pd.Series(s, index=range(len(s)))
        return_series = pd.Series(r, index=range(len(s)))
        try:
            quintile_labels = pd.qcut(score_series, n_quantiles, labels=False, duplicates="drop") + 1
            for q in range(1, n_quantiles + 1):
                q_mask = quintile_labels == q
                if q_mask.sum() > 0:
                    all_quintile_returns[q].append(float(return_series[q_mask].mean()))
        except (ValueError, IndexError):
            pass  # too few unique values for qcut

    # Aggregate (nanmean/nanstd: skip NaN dates to avoid biasing IC downward)
    if ic_list:
        result.mean_ic = float(np.nanmean(ic_list))
        result.ic_std = float(np.nanstd(ic_list))
        result.icir = result.mean_ic / result.ic_std if result.ic_std > 0 else 0.0
    if rank_ic_list:
        result.mean_rank_ic = float(np.nanmean(rank_ic_list))
        result.rank_ic_std = float(np.nanstd(rank_ic_list))
        result.rank_icir = result.mean_rank_ic / result.rank_ic_std if result.rank_ic_std > 0 else 0.0

    result.n_eval_dates = len(ic_list)
    result.avg_stocks_per_date = float(np.mean(stock_counts)) if stock_counts else 0.0

    for q, rets in all_quintile_returns.items():
        if rets:
            result.quintile_returns[q] = float(np.mean(rets))
            result.quintile_count[q] = len(rets)

    return result


def evaluate_ic_decay(
    factor: CrossSectionalFactor,
    universe_data: dict[str, pd.DataFrame],
    calendar: TradingCalendar,
    start: date,
    end: date,
    lags: list[int] | None = None,
    eval_freq: str = "weekly",
    lookback_days: int = 252,
) -> dict[int, float]:
    """Compute IC at different forward horizons (IC decay curve).

    Returns: {lag_days: mean_rank_ic}
    """
    if lags is None:
        lags = [1, 5, 10, 20]
    decay: dict[int, float] = {}
    for lag in lags:
        r = evaluate_cross_sectional_factor(
            factor, universe_data, calendar, start, end,
            forward_days=lag, eval_freq=eval_freq, lookback_days=lookback_days,
        )
        decay[lag] = r.mean_rank_ic
    return decay


def compute_factor_correlation(
    factors: list[CrossSectionalFactor],
    universe_data: dict[str, pd.DataFrame],
    calendar: TradingCalendar,
    start: date,
    end: date,
    eval_freq: str = "monthly",
    lookback_days: int = 252,
) -> pd.DataFrame:
    """Compute pairwise Spearman rank correlation between factors.

    Returns: DataFrame (n_factors × n_factors) with factor names as index/columns.
    """
    eval_dates = calendar.rebalance_dates(start, end, eval_freq)
    if not eval_dates:
        names = [f.name for f in factors]
        return pd.DataFrame(np.eye(len(factors)), index=names, columns=names)


    # Collect factor scores keyed by (eval_date, factor_name) → aligned Series
    # Only store dates where ALL factors have data on common symbols
    date_aligned: list[dict[str, pd.Series]] = []

    for eval_date in eval_dates:
        sliced = slice_universe_data(universe_data, eval_date, lookback_days)
        if not sliced:
            continue
        dt = datetime.combine(eval_date, datetime.min.time())
        date_scores: dict[str, pd.Series] = {}
        for f in factors:
            s = f.compute(sliced, dt)
            if not s.empty:
                date_scores[f.name] = s

        if len(date_scores) < 2:
            continue

        # Align all available factors to common symbols
        common = set.intersection(*[set(s.index) for s in date_scores.values()])
        if len(common) < 5:
            continue

        common_sorted = sorted(common)
        aligned = {fname: s[common_sorted] for fname, s in date_scores.items()}
        date_aligned.append(aligned)

    # Compute pairwise rank correlations using date-aligned data
    names = [f.name for f in factors]
    n = len(names)
    corr_matrix = np.eye(n)

    for i in range(n):
        for j in range(i + 1, n):
            corrs = []
            for aligned in date_aligned:
                # Only use dates where BOTH factors i and j have data
                if names[i] in aligned and names[j] in aligned:
                    si = aligned[names[i]]
                    sj = aligned[names[j]]
                    if len(si) >= 5:
                        c = stats.spearmanr(si.values, sj.values).statistic
                        if not np.isnan(c):
                            corrs.append(c)
            avg_corr = float(np.mean(corrs)) if corrs else 0.0
            corr_matrix[i, j] = avg_corr
            corr_matrix[j, i] = avg_corr

    return pd.DataFrame(corr_matrix, index=names, columns=names)
