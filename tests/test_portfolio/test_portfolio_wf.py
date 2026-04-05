"""Tests for V2.10 WF1+WF2: Portfolio Walk-Forward + Significance."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.calendar import TradingCalendar
from ez.portfolio.cross_factor import MomentumRank
from ez.portfolio.engine import CostModel
from ez.portfolio.portfolio_strategy import TopNRotation, PortfolioStrategy
from ez.portfolio.universe import Universe
from ez.portfolio.walk_forward import (
    portfolio_walk_forward,
    portfolio_significance,
    PortfolioWFResult,
    PortfolioSignificanceResult,
)


def _make_data(n_stocks=10, n_days=500, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    data = {}
    for i in range(n_stocks):
        prices = 10 * np.cumprod(1 + rng.normal(0.0005 * (i + 1), 0.015, n_days))
        data[f"S{i:02d}"] = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
        }, index=dates)
    cal = TradingCalendar.from_dates([d.date() for d in dates])
    universe = Universe([f"S{i:02d}" for i in range(n_stocks)])
    return data, cal, universe, dates


class TestPortfolioWalkForward:

    def test_basic_wf(self):
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, train_ratio=0.7, freq="monthly",
        )
        assert isinstance(result, PortfolioWFResult)
        assert result.n_splits == 3
        assert len(result.is_sharpes) == 3
        assert len(result.oos_sharpes) == 3
        assert len(result.oos_equity_curve) > 0

    def test_degradation_computed(self):
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3,
        )
        assert np.isfinite(result.degradation)
        assert result.overfitting_score >= 0

    def test_oos_metrics(self):
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3,
        )
        assert "sharpe_ratio" in result.oos_metrics
        assert "total_return" in result.oos_metrics

    def test_too_few_splits_raises(self):
        data, cal, universe, dates = _make_data()
        with pytest.raises(ValueError, match="n_splits must be >= 2"):
            portfolio_walk_forward(
                strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
                universe=universe, universe_data=data, calendar=cal,
                start=dates[60].date(), end=dates[-1].date(),
                n_splits=1,
            )

    def test_invalid_train_ratio(self):
        data, cal, universe, dates = _make_data()
        with pytest.raises(ValueError, match="train_ratio"):
            portfolio_walk_forward(
                strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
                universe=universe, universe_data=data, calendar=cal,
                start=dates[60].date(), end=dates[-1].date(),
                train_ratio=0.0,
            )

    def test_too_many_splits_raises(self):
        data, cal, universe, dates = _make_data(n_days=100)
        with pytest.raises(ValueError, match="OOS window too short"):
            portfolio_walk_forward(
                strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
                universe=universe, universe_data=data, calendar=cal,
                start=dates[30].date(), end=dates[-1].date(),
                n_splits=20,
            )

    def test_fresh_strategy_per_fold(self):
        """Each fold must get a fresh strategy (no state leakage)."""
        call_count = [0]
        data, cal, universe, dates = _make_data()

        def factory():
            call_count[0] += 1
            return TopNRotation(MomentumRank(20), top_n=3)

        portfolio_walk_forward(
            strategy_factory=factory,
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3,
        )
        # 3 splits × 2 (IS + OOS) = 6 calls
        assert call_count[0] == 6

    def test_oos_sharpe_matches_chained_curve_not_fold_mean(self):
        """V2.12.2 codex round 3: oos_metrics['sharpe_ratio'] must be
        computed from the chained OOS equity curve via MetricsCalculator,
        not the mean of per-fold Sharpes. Mean-of-folds diverges from the
        true Sharpe of the concatenated curve when folds have different
        lengths or volatility structures.
        """
        import pandas as pd
        from ez.backtest.metrics import MetricsCalculator
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, freq="monthly",
        )
        assert "sharpe_ratio" in result.oos_metrics
        # Compute the "reference" sharpe directly from the chained curve
        # using the same MetricsCalculator path the fix uses.
        if len(result.oos_equity_curve) > 1:
            calc = MetricsCalculator()
            eq = pd.Series(result.oos_equity_curve)
            flat_bench = pd.Series([float(eq.iloc[0])] * len(eq))
            ref_metrics = calc.compute(eq, flat_bench)
            ref_sharpe = float(ref_metrics.get("sharpe_ratio", 0.0))
            reported = float(result.oos_metrics["sharpe_ratio"])
            # The two should be identical (same computation path)
            assert abs(reported - ref_sharpe) < 1e-10, (
                f"oos_metrics sharpe {reported} does not match chained-curve "
                f"sharpe {ref_sharpe} — fallback to fold-mean detected"
            )
            # And must differ from the naive fold-mean (unless folds happen
            # to align — which is rare but possible for tiny random seed).
            fold_mean = float(np.mean(result.oos_sharpes)) if result.oos_sharpes else 0.0
            # Not asserting they're different (folds may coincidentally match);
            # just verify the chained computation is what's reported.
            _ = fold_mean

    def test_tail_days_not_silently_dropped(self):
        """V2.12.2 codex: window_size = n_days // n_splits silently drops the
        remainder n_days % n_splits at the tail. Use daily rebalance so
        oos_dates captures every trading day, then assert the last OOS date
        is within 2 trading days of the final input date."""
        data, cal, universe, dates = _make_data(n_days=510)  # 510 % 7 = 6
        # Call portfolio_walk_forward direct, not portfolio_significance helper
        # so we get a PortfolioWFResult with oos_dates list.
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            n_splits=7, train_ratio=0.7, freq="daily",
        )
        assert result.oos_dates, "no OOS dates"
        from datetime import date as _date
        last_d = _date.fromisoformat(result.oos_dates[-1])
        inp_d = dates[-1].date()
        # Fix: last OOS covers rows up to inp_d → delta = 0 or 1.
        # Bug: window_size=72, last window ends at row 504, dropping rows
        # 504..510 → delta = 6 business days = 8 calendar days.
        delta_days = (inp_d - last_d).days
        assert delta_days <= 4, (
            f"Last OOS ends {delta_days} days before input — "
            f"tail days silently dropped by n_days // n_splits"
        )


class TestPortfolioSignificance:

    def test_basic_significance(self):
        data, cal, universe, dates = _make_data()
        from ez.portfolio.engine import run_portfolio_backtest
        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            freq="monthly",
        )
        sig = portfolio_significance(result.equity_curve, seed=42)
        assert isinstance(sig, PortfolioSignificanceResult)
        assert sig.sharpe_ci_lower <= sig.sharpe_ci_upper
        assert 0 <= sig.monte_carlo_p_value <= 1
        assert np.isfinite(sig.observed_sharpe)

    def test_short_equity_curve(self):
        sig = portfolio_significance([100000, 100100, 100050], seed=42)
        assert sig.monte_carlo_p_value == 1.0
        assert not sig.is_significant

    def test_deterministic_with_seed(self):
        eq = list(np.cumprod(1 + np.random.default_rng(42).normal(0.001, 0.01, 200)) * 100000)
        s1 = portfolio_significance(eq, seed=99)
        s2 = portfolio_significance(eq, seed=99)
        assert s1.sharpe_ci_lower == s2.sharpe_ci_lower
        assert s1.monte_carlo_p_value == s2.monte_carlo_p_value

    def test_strong_drift_has_low_pvalue(self):
        """Strong positive drift should produce p < 0.1 (strategy has real alpha)."""
        eq = list(np.cumprod(1 + np.random.default_rng(42).normal(0.003, 0.01, 500)) * 100000)
        sig = portfolio_significance(eq, seed=42, n_permutations=500)
        assert sig.observed_sharpe > 1.0, "Expected strong Sharpe"
        assert sig.monte_carlo_p_value < 0.1, f"p={sig.monte_carlo_p_value} too high for strong drift"


class TestWFRegressions:
    """Regression tests for WF fixes (OOS continuity, compound return)."""

    def test_oos_equity_continuous(self):
        """OOS equity curve must not have non-trading jumps at fold boundaries."""
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, freq="monthly",
        )
        if len(result.oos_equity_curve) > 1:
            # No single-day jump > 20% (would indicate fold boundary discontinuity)
            for i in range(1, len(result.oos_equity_curve)):
                prev = result.oos_equity_curve[i - 1]
                cur = result.oos_equity_curve[i]
                if prev > 0:
                    change = abs(cur - prev) / prev
                    assert change < 0.20, f"Jump of {change:.1%} at index {i} (fold boundary?)"

    def test_oos_dates_equity_aligned(self):
        """oos_dates and oos_equity_curve must have same length."""
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, freq="monthly",
        )
        assert len(result.oos_dates) == len(result.oos_equity_curve)

    def test_compound_total_return(self):
        """OOS total_return must be compound, not mean."""
        data, cal, universe, dates = _make_data()
        result = portfolio_walk_forward(
            strategy_factory=lambda: TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[60].date(), end=dates[-1].date(),
            n_splits=3, freq="monthly",
        )
        if result.oos_returns:
            compound = 1.0
            for r in result.oos_returns:
                compound *= (1 + r)
            expected = compound - 1
            actual = result.oos_metrics.get("total_return", 0)
            assert abs(actual - expected) < 1e-10, f"Expected compound {expected}, got {actual}"
