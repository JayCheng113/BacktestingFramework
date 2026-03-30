"""V2.10 WF1+WF2: Portfolio Walk-Forward validation + statistical significance.

Adapts the single-stock WalkForwardValidator pattern to the portfolio engine.
Splits the backtest period into N non-overlapping folds, runs IS and OOS
on each fold, measures degradation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.engine import CostModel, run_portfolio_backtest, PortfolioResult
from ez.portfolio.portfolio_strategy import PortfolioStrategy
from ez.portfolio.universe import Universe


@dataclass
class PortfolioWFResult:
    """Walk-Forward result for portfolio backtesting."""
    n_splits: int = 0
    is_sharpes: list[float] = field(default_factory=list)
    oos_sharpes: list[float] = field(default_factory=list)
    is_returns: list[float] = field(default_factory=list)
    oos_returns: list[float] = field(default_factory=list)
    oos_equity_curve: list[float] = field(default_factory=list)
    oos_dates: list[str] = field(default_factory=list)
    degradation: float = 0.0       # (IS_sharpe - OOS_sharpe) / |IS_sharpe|
    overfitting_score: float = 0.0  # max(0, degradation)
    oos_metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class PortfolioSignificanceResult:
    """Bootstrap CI + Monte Carlo for portfolio equity curve."""
    sharpe_ci_lower: float = 0.0
    sharpe_ci_upper: float = 0.0
    monte_carlo_p_value: float = 1.0
    is_significant: bool = False
    observed_sharpe: float = 0.0


def _sharpe(returns: np.ndarray, rf_daily: float = 0.03 / 252) -> float:
    excess = returns - rf_daily
    std = float(np.std(excess))
    if std < 1e-10:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(252))


def portfolio_walk_forward(
    strategy_factory,
    universe: Universe,
    universe_data: dict,
    calendar: TradingCalendar,
    start: date,
    end: date,
    n_splits: int = 5,
    train_ratio: float = 0.7,
    freq: str = "monthly",
    initial_cash: float = 1_000_000.0,
    cost_model: CostModel | None = None,
    lot_size: int = 100,
    limit_pct: float = 0.10,
    benchmark_symbol: str = "",
) -> PortfolioWFResult:
    """Run walk-forward validation on a portfolio strategy.

    Args:
        strategy_factory: Callable that returns a fresh PortfolioStrategy instance.
            Must be a factory (not instance) to reset state between folds.
        n_splits: Number of non-overlapping folds.
        train_ratio: Fraction of each fold used for in-sample.
    """
    if n_splits < 2:
        raise ValueError(f"n_splits must be >= 2, got {n_splits}")
    if not (0.0 < train_ratio < 1.0):
        raise ValueError(f"train_ratio must be in (0, 1), got {train_ratio}")

    trading_days = calendar.trading_days_between(start, end)
    n_days = len(trading_days)
    window_size = n_days // n_splits

    min_test = 20
    test_size = window_size - int(window_size * train_ratio)
    if test_size < min_test:
        raise ValueError(
            f"OOS window too short: {test_size} days for {n_splits} splits. "
            f"Need >= {min_test}. Try fewer splits or longer date range."
        )

    result = PortfolioWFResult(n_splits=n_splits)
    # Chain OOS equity: each fold continues from where the previous ended
    chain_value = initial_cash

    for i in range(n_splits):
        win_start = i * window_size
        train_end_idx = win_start + int(window_size * train_ratio)
        test_end_idx = min(win_start + window_size, n_days)

        if train_end_idx >= n_days or test_end_idx > n_days:
            continue

        train_start_date = trading_days[win_start]
        train_end_date = trading_days[train_end_idx - 1]
        test_start_date = trading_days[train_end_idx]
        test_end_date = trading_days[test_end_idx - 1]

        # Run IS
        is_result = run_portfolio_backtest(
            strategy=strategy_factory(), universe=universe,
            universe_data=universe_data, calendar=calendar,
            start=train_start_date, end=train_end_date,
            freq=freq, initial_cash=initial_cash,
            cost_model=cost_model, lot_size=lot_size, limit_pct=limit_pct,
            benchmark_symbol=benchmark_symbol,
        )

        # Run OOS
        oos_result = run_portfolio_backtest(
            strategy=strategy_factory(), universe=universe,
            universe_data=universe_data, calendar=calendar,
            start=test_start_date, end=test_end_date,
            freq=freq, initial_cash=initial_cash,
            cost_model=cost_model, lot_size=lot_size, limit_pct=limit_pct,
            benchmark_symbol=benchmark_symbol,
        )

        # Extract sharpe
        is_sharpe = is_result.metrics.get("sharpe_ratio", 0.0)
        oos_sharpe = oos_result.metrics.get("sharpe_ratio", 0.0)
        is_ret = is_result.metrics.get("total_return", 0.0)
        oos_ret = oos_result.metrics.get("total_return", 0.0)

        result.is_sharpes.append(is_sharpe)
        result.oos_sharpes.append(oos_sharpe)
        result.is_returns.append(is_ret)
        result.oos_returns.append(oos_ret)

        # Chain OOS equity curve: normalize fold equity to continue from chain_value
        if oos_result.equity_curve:
            fold_start = oos_result.equity_curve[0]
            if fold_start > 0:
                for eq in oos_result.equity_curve:
                    result.oos_equity_curve.append(chain_value * eq / fold_start)
                chain_value = result.oos_equity_curve[-1]  # next fold starts here
            result.oos_dates.extend(d.isoformat() for d in oos_result.dates)

    # Aggregate
    if result.is_sharpes:
        is_mean = np.mean(result.is_sharpes)
        oos_mean = np.mean(result.oos_sharpes)
        result.degradation = float((is_mean - oos_mean) / abs(is_mean)) if abs(is_mean) > 1e-10 else 0.0
        result.overfitting_score = max(0.0, result.degradation)
        # Compound OOS total return: product of (1 + fold_return) - 1
        compound_ret = 1.0
        for r in result.oos_returns:
            compound_ret *= (1 + r)
        result.oos_metrics = {
            "sharpe_ratio": float(oos_mean),
            "total_return": float(compound_ret - 1),
        }

    return result


def portfolio_significance(
    equity_curve: list[float],
    risk_free_rate: float = 0.03,
    n_bootstrap: int = 1000,
    n_permutations: int = 1000,
    seed: int | None = None,
) -> PortfolioSignificanceResult:
    """Bootstrap CI + Monte Carlo hypothesis test for portfolio equity curve.

    Null hypothesis: strategy has no alpha (expected excess return = 0).
    Method: Bootstrap resampling from mean-centered excess returns.
    """
    eq = np.array(equity_curve)
    if len(eq) < 20:
        return PortfolioSignificanceResult()

    returns = np.diff(eq) / eq[:-1]
    returns = returns[~np.isnan(returns)]
    if len(returns) < 20:
        return PortfolioSignificanceResult()

    daily_rf = risk_free_rate / 252
    observed = _sharpe(returns, daily_rf)
    rng = np.random.default_rng(seed)

    # Bootstrap CI (resample WITH replacement — changes mean/std)
    boot = np.array([
        _sharpe(rng.choice(returns, size=len(returns), replace=True), daily_rf)
        for _ in range(n_bootstrap)
    ])
    ci_lower = float(np.percentile(boot, 2.5))
    ci_upper = float(np.percentile(boot, 97.5))

    # Monte Carlo under null: excess returns centered at 0 (no alpha)
    excess = returns - daily_rf
    centered = excess - np.mean(excess)  # mean = 0 under null
    null_sharpes = np.array([
        _sharpe_raw(rng.choice(centered, size=len(centered), replace=True))
        for _ in range(n_permutations)
    ])
    p_value = float(np.mean(null_sharpes >= observed))

    return PortfolioSignificanceResult(
        sharpe_ci_lower=ci_lower,
        sharpe_ci_upper=ci_upper,
        monte_carlo_p_value=p_value,
        is_significant=p_value < 0.05,
        observed_sharpe=observed,
    )


def _sharpe_raw(excess_returns: np.ndarray) -> float:
    """Sharpe from already-excess returns (no rf subtraction)."""
    std = float(np.std(excess_returns))
    if std < 1e-10:
        return 0.0
    return float(np.mean(excess_returns) / std * np.sqrt(252))
