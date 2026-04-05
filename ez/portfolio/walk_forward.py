"""V2.10 WF1+WF2: Portfolio Walk-Forward validation + statistical significance.

Adapts the single-stock WalkForwardValidator pattern to the portfolio engine.
Splits the backtest period into N non-overlapping folds, runs IS and OOS
on each fold, measures degradation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy import stats

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
    # V2.12.1 reviewer round 6 I1+I2: aggregate events from all folds so WF
    # users see optimizer fallbacks and risk events just like /run users do.
    optimizer_fallback_events: list[dict] = field(default_factory=list)
    risk_events: list[dict] = field(default_factory=list)


@dataclass
class PortfolioSignificanceResult:
    """Bootstrap CI + Monte Carlo for portfolio equity curve."""
    sharpe_ci_lower: float = 0.0
    sharpe_ci_upper: float = 0.0
    monte_carlo_p_value: float = 1.0
    is_significant: bool = False
    observed_sharpe: float = 0.0


def _sharpe(returns: np.ndarray, rf_daily: float = 0.03 / 252) -> float:
    # V2.12.1 reviewer round 5: ddof=1 to match ez/backtest/metrics.py and
    # ez/portfolio/engine.py. Prior version used numpy default ddof=0, causing
    # observed_sharpe and CI bounds computed here to disagree with the engine's
    # own Sharpe for the SAME equity curve — on short OOS windows (30-60 days)
    # the displayed Sharpe could fall outside its own CI by up to 2.7%.
    excess = returns - rf_daily
    std = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
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
    t_plus_1: bool = True,  # V2.12.1 codex reviewer round 4: propagate to run_portfolio_backtest
    optimizer_factory=None,  # V2.12.1 codex follow-up: propagate to run_portfolio_backtest
    risk_manager_factory=None,  # V2.12.1 codex follow-up
) -> PortfolioWFResult:
    """Run walk-forward validation on a portfolio strategy.

    Args:
        strategy_factory: Callable that returns a fresh PortfolioStrategy instance.
            Must be a factory (not instance) to reset state between folds.
        n_splits: Number of non-overlapping folds.
        train_ratio: Fraction of each fold used for in-sample.
        optimizer_factory: Optional callable returning a fresh optimizer per fold
            (factory, not instance, because optimizers hold context state).
        risk_manager_factory: Optional callable returning a fresh RiskManager per fold
            (factory, not instance, because RiskManager tracks drawdown across days).
    """
    if n_splits < 2:
        raise ValueError(f"n_splits must be >= 2, got {n_splits}")
    if not (0.0 < train_ratio < 1.0):
        raise ValueError(f"train_ratio must be in (0, 1), got {train_ratio}")

    trading_days = calendar.trading_days_between(start, end)
    n_days = len(trading_days)

    # V2.12.2 codex: use integer-interval arithmetic so the last window
    # absorbs the remainder n_days % n_splits. Previously
    # `window_size = n_days // n_splits` silently dropped the tail — for 503
    # days and 7 splits, the final 6 trading days were invisible to both IS
    # and OOS. Validation below uses the smallest possible window as a
    # conservative lower bound for the test size.
    min_window = n_days // n_splits
    min_test = 20
    min_test_size = min_window - int(min_window * train_ratio)
    if min_test_size < min_test:
        raise ValueError(
            f"OOS window too short: {min_test_size} days for {n_splits} splits. "
            f"Need >= {min_test}. Try fewer splits or longer date range."
        )

    result = PortfolioWFResult(n_splits=n_splits)
    # Chain OOS equity: each fold continues from where the previous ended
    chain_value = initial_cash

    for i in range(n_splits):
        # Integer-interval boundaries: last fold absorbs the remainder.
        win_start = i * n_days // n_splits
        win_end = (i + 1) * n_days // n_splits
        cur_window = win_end - win_start
        train_end_idx = win_start + int(cur_window * train_ratio)
        test_end_idx = win_end

        if train_end_idx >= n_days:
            continue  # not enough data for this fold

        train_start_date = trading_days[win_start]
        train_end_date = trading_days[train_end_idx - 1]
        test_start_date = trading_days[train_end_idx]
        test_end_date = trading_days[test_end_idx - 1]

        # Run IS — fresh optimizer/risk_manager per fold (they hold state)
        is_opt = optimizer_factory() if optimizer_factory else None
        is_rm = risk_manager_factory() if risk_manager_factory else None
        is_result = run_portfolio_backtest(
            strategy=strategy_factory(), universe=universe,
            universe_data=universe_data, calendar=calendar,
            start=train_start_date, end=train_end_date,
            freq=freq, initial_cash=initial_cash,
            cost_model=cost_model, lot_size=lot_size, limit_pct=limit_pct,
            benchmark_symbol=benchmark_symbol,
            t_plus_1=t_plus_1,
            optimizer=is_opt,
            risk_manager=is_rm,
        )
        # V2.12.1 reviewer round 6 I1+I2: aggregate events from this fold
        if is_opt is not None and is_opt.fallback_events:
            for ev in is_opt.fallback_events:
                result.optimizer_fallback_events.append({**ev, "phase": "IS", "fold": i})
        for ev in is_result.risk_events:
            result.risk_events.append({**ev, "phase": "IS", "fold": i})

        # Run OOS — fresh instances again
        oos_opt = optimizer_factory() if optimizer_factory else None
        oos_rm = risk_manager_factory() if risk_manager_factory else None
        oos_result = run_portfolio_backtest(
            strategy=strategy_factory(), universe=universe,
            universe_data=universe_data, calendar=calendar,
            start=test_start_date, end=test_end_date,
            freq=freq, initial_cash=initial_cash,
            cost_model=cost_model, lot_size=lot_size, limit_pct=limit_pct,
            benchmark_symbol=benchmark_symbol,
            t_plus_1=t_plus_1,
            optimizer=oos_opt,
            risk_manager=oos_rm,
        )
        if oos_opt is not None and oos_opt.fallback_events:
            for ev in oos_opt.fallback_events:
                result.optimizer_fallback_events.append({**ev, "phase": "OOS", "fold": i})
        for ev in oos_result.risk_events:
            result.risk_events.append({**ev, "phase": "OOS", "fold": i})

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

    # Bootstrap CI — BCa (Bias-Corrected and Accelerated) when possible
    boot = np.array([
        _sharpe(rng.choice(returns, size=len(returns), replace=True), daily_rf)
        for _ in range(n_bootstrap)
    ])
    # BCa bias correction: proportion of bootstrap < observed
    # Clamp fraction to [0.5/B, 1-0.5/B] to avoid ppf(0)=-inf / ppf(1)=inf
    boot_frac = float(np.mean(boot < observed))
    boot_frac = max(0.5 / n_bootstrap, min(boot_frac, 1 - 0.5 / n_bootstrap))
    z0 = float(stats.norm.ppf(boot_frac)) if np.std(boot) > 0 else 0.0
    # BCa acceleration: jackknife estimate
    n = len(returns)
    jack_sharpes = np.array([
        _sharpe(np.delete(returns, j), daily_rf) for j in range(min(n, 200))  # cap at 200 for speed
    ])
    jack_mean = np.mean(jack_sharpes)
    jack_diff = jack_mean - jack_sharpes
    a = float(np.sum(jack_diff ** 3) / (6 * (np.sum(jack_diff ** 2) ** 1.5 + 1e-20)))
    # Adjusted percentiles
    alpha_lo, alpha_hi = 0.025, 0.975
    z_lo, z_hi = stats.norm.ppf(alpha_lo), stats.norm.ppf(alpha_hi)
    adj_lo = stats.norm.cdf(z0 + (z0 + z_lo) / (1 - a * (z0 + z_lo) + 1e-20))
    adj_hi = stats.norm.cdf(z0 + (z0 + z_hi) / (1 - a * (z0 + z_hi) + 1e-20))
    ci_lower = float(np.percentile(boot, 100 * max(0.001, min(adj_lo, 0.999))))
    ci_upper = float(np.percentile(boot, 100 * max(0.001, min(adj_hi, 0.999))))

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
    """Sharpe from already-excess returns (no rf subtraction).

    V2.12.1 reviewer round 5: ddof=1 to match the canonical Sharpe formula
    across all modules.
    """
    std = float(np.std(excess_returns, ddof=1)) if len(excess_returns) > 1 else 0.0
    if std < 1e-10:
        return 0.0
    return float(np.mean(excess_returns) / std * np.sqrt(252))
