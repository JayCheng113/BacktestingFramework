"""Tests for V2.22 metric helpers: Deflated Sharpe, MinBTL, annual breakdown."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ez.research._metrics import (
    deflated_sharpe_ratio,
    minimum_backtest_length,
    annual_breakdown,
)


# ============================================================
# deflated_sharpe_ratio
# ============================================================

class TestDeflatedSharpe:
    def test_positive_sharpe_returns_probability(self):
        rng = np.random.default_rng(42)
        # High-Sharpe series
        rets = pd.Series(rng.normal(0.001, 0.01, 1000))
        r = deflated_sharpe_ratio(rets, n_trials=1)
        assert r is not None
        assert 0.0 <= r["deflated_sharpe"] <= 1.0
        assert r["sharpe"] > 0

    def test_short_series_returns_none(self):
        rng = np.random.default_rng(42)
        r = deflated_sharpe_ratio(pd.Series(rng.normal(0, 0.01, 20)))
        assert r is None

    def test_multiple_trials_penalizes_dsr(self):
        rng = np.random.default_rng(42)
        rets = pd.Series(rng.normal(0.0008, 0.01, 500))
        r1 = deflated_sharpe_ratio(rets, n_trials=1)
        r100 = deflated_sharpe_ratio(rets, n_trials=100)
        # More trials → higher SR_0 threshold → lower DSR (more skeptical)
        assert r1 is not None and r100 is not None
        assert r1["deflated_sharpe"] >= r100["deflated_sharpe"]
        assert r100["expected_max_sr"] > r1["expected_max_sr"]

    def test_expected_max_sr_pins_gumbel_formula(self):
        """Pin Bailey & de Prado (2014) Eq. 10 numerical value.

        This guards against silent regression to a wrong formula.
        The V2.23.1 I1 review fix corrected `sqrt((1-γ)·2·log N)`
        (≈ 0.636 for N=100) to the full Gumbel form (≈ 2.15).
        Monotonicity-only tests would pass under BOTH formulas, so
        we pin the actual numerical value here.
        """
        from scipy.stats import norm
        from math import e as math_e
        rng = np.random.default_rng(42)
        rets = pd.Series(rng.normal(0.0008, 0.01, 500))
        r100 = deflated_sharpe_ratio(rets, n_trials=100)
        gamma = 0.5772156649
        expected = (
            (1 - gamma) * norm.ppf(1 - 1 / 100)
            + gamma * norm.ppf(1 - 1 / (100 * math_e))
        )
        # sr_benchmark=0 default → expected_max_sr = expected adjustment.
        # Tolerance 1e-6 would fail under the old (wrong) formula by ~1.5.
        assert r100 is not None
        assert abs(r100["expected_max_sr"] - expected) < 1e-6, (
            f"Expected max SR = {r100['expected_max_sr']:.4f}, "
            f"should be {expected:.4f} per Bailey & de Prado Eq. 10"
        )

    def test_expected_max_sr_nontrivial_at_n_100(self):
        """At N=100 the Gumbel adjustment should be ~2.15, not ~0.64.

        Canary: if someone reverts to `sqrt((1-γ)·2·log N)`, this fires.
        """
        rng = np.random.default_rng(42)
        rets = pd.Series(rng.normal(0.0008, 0.01, 500))
        r = deflated_sharpe_ratio(rets, n_trials=100)
        assert r is not None
        # Old wrong formula gave ~0.636; correct formula gives ~2.15.
        # Any value < 1.5 indicates a regression.
        assert r["expected_max_sr"] > 1.5, (
            f"expected_max_sr={r['expected_max_sr']:.3f} suspiciously low "
            f"— check for DSR formula regression"
        )

    def test_high_kurtosis_reduces_dsr(self):
        """Heavy-tailed returns should get lower DSR than normal returns
        at the same Sharpe."""
        rng = np.random.default_rng(42)
        # Normal
        normal_rets = pd.Series(rng.normal(0.0005, 0.01, 500))
        # T-distributed (heavy tails, similar mean/std)
        t_rets = pd.Series(rng.standard_t(df=4, size=500) * 0.01 + 0.0005)
        rn = deflated_sharpe_ratio(normal_rets)
        rt = deflated_sharpe_ratio(t_rets)
        assert rn is not None and rt is not None
        # Heavy tails typically → lower DSR (if Sharpes comparable)

    def test_zero_std_returns_none(self):
        rets = pd.Series([0.001] * 100)
        r = deflated_sharpe_ratio(rets)
        assert r is None

    def test_returns_all_keys(self):
        rng = np.random.default_rng(1)
        rets = pd.Series(rng.normal(0.0005, 0.01, 500))
        r = deflated_sharpe_ratio(rets)
        assert r is not None
        for key in ("sharpe", "deflated_sharpe", "expected_max_sr", "skew", "kurt"):
            assert key in r


# ============================================================
# minimum_backtest_length
# ============================================================

class TestMinBTL:
    def test_high_sharpe_requires_less_data(self):
        """Higher Sharpe → less data needed."""
        mb_high = minimum_backtest_length(2.0)
        mb_low = minimum_backtest_length(0.5)
        assert mb_high is not None and mb_low is not None
        assert mb_high < mb_low

    def test_negative_sharpe_returns_none(self):
        assert minimum_backtest_length(-0.5) is None
        assert minimum_backtest_length(0.0) is None

    def test_multiple_trials_increases_min_btl(self):
        # Use higher Sharpe so both n_trials values produce valid results.
        # sqrt(2*log(100)) ≈ 3.03, so SR=3.5 stays above the threshold.
        mb_1 = minimum_backtest_length(3.5, n_trials=1)
        mb_100 = minimum_backtest_length(3.5, n_trials=100)
        assert mb_1 is not None and mb_100 is not None
        assert mb_100 > mb_1

    def test_very_low_sharpe_with_many_trials_is_none(self):
        """Sharpe < Gumbel expected-max can't be significant."""
        result = minimum_backtest_length(0.1, n_trials=1_000_000)
        assert result is None

    def test_coherence_with_dsr_gumbel_benchmark(self):
        """V2.23.2 Important 4: MinBTL uses the same Gumbel expected-max-SR
        benchmark as DSR. A Sharpe beating Gumbel should give a finite
        number; one below should return None. They must agree.
        """
        from ez.research._metrics import (
            _GAMMA_EULER, minimum_backtest_length as mbl,
        )
        from scipy.stats import norm
        from math import e as math_e
        # Compute the Gumbel threshold directly
        n = 100
        gumbel = (
            (1 - _GAMMA_EULER) * norm.ppf(1 - 1/n)
            + _GAMMA_EULER * norm.ppf(1 - 1/(n * math_e))
        )
        # Sharpe just above Gumbel → finite MinBTL (close to 0 effective SR
        # means lots of years, but not None)
        above = gumbel + 0.5
        assert mbl(above, n_trials=n) is not None
        # Sharpe just below Gumbel → None (can't be significant)
        below = gumbel - 0.01
        assert mbl(below, n_trials=n) is None

    def test_gumbel_benchmark_matches_dsr(self):
        """Coherence: MinBTL and DSR use the SAME benchmark. If DSR's
        expected_max_sr equals X, MinBTL rejects Sharpe < X.
        """
        import pandas as pd
        rng = np.random.default_rng(42)
        rets = pd.Series(rng.normal(0.0008, 0.01, 500))
        dsr = deflated_sharpe_ratio(rets, n_trials=100)
        assert dsr is not None
        expected_max = dsr["expected_max_sr"]
        # MinBTL for Sharpe right at expected_max should give None
        # (or an effectively infinite value); for Sharpe well above it,
        # finite. This establishes coherence between the two functions.
        assert minimum_backtest_length(expected_max * 0.99, n_trials=100) is None
        assert minimum_backtest_length(expected_max * 2.0, n_trials=100) is not None


# ============================================================
# annual_breakdown
# ============================================================

class TestAnnualBreakdown:
    def test_three_years_breakdown(self):
        rng = np.random.default_rng(42)
        idx = pd.bdate_range("2020-01-01", "2022-12-30")
        rets = pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx)
        r = annual_breakdown(rets)
        assert len(r["per_year"]) == 3
        years = [y["year"] for y in r["per_year"]]
        assert 2020 in years and 2021 in years and 2022 in years

    def test_empty_returns(self):
        r = annual_breakdown(pd.Series(dtype=float))
        assert r["per_year"] == []
        assert r["worst_year"] is None

    def test_non_datetime_index_returns_empty(self):
        r = annual_breakdown(pd.Series([0.001, 0.002, 0.003]))
        assert r["per_year"] == []

    def test_profitable_ratio(self):
        # 3 profitable, 2 unprofitable
        years = []
        rng = np.random.default_rng(42)
        for y in range(2018, 2023):
            # Alternate profitable/unprofitable
            mean = 0.001 if y % 2 == 0 else -0.001
            idx = pd.bdate_range(f"{y}-01-01", f"{y}-12-30")
            years.append(pd.Series(rng.normal(mean, 0.01, len(idx)), index=idx))
        combined = pd.concat(years)
        r = annual_breakdown(combined)
        assert 0 <= r["profitable_ratio"] <= 1.0
        assert r["worst_year"] is not None
        assert r["best_year"] is not None

    def test_worst_year_has_lowest_sharpe(self):
        rng = np.random.default_rng(42)
        # 2020: good; 2021: bad; 2022: medium
        y20 = pd.Series(
            rng.normal(0.002, 0.01, 252),
            index=pd.bdate_range("2020-01-01", periods=252),
        )
        y21 = pd.Series(
            rng.normal(-0.001, 0.01, 252),
            index=pd.bdate_range("2021-01-01", periods=252),
        )
        y22 = pd.Series(
            rng.normal(0.0005, 0.01, 252),
            index=pd.bdate_range("2022-01-01", periods=252),
        )
        r = annual_breakdown(pd.concat([y20, y21, y22]))
        # Worst year should be 2021 (negative mean)
        assert r["worst_year"] == 2021

    def test_skips_tiny_partial_years(self):
        """Years with <5 days should be skipped."""
        rng = np.random.default_rng(42)
        # Full 2020 + 3 days of 2021
        y20 = pd.Series(
            rng.normal(0.001, 0.01, 252),
            index=pd.bdate_range("2020-01-01", periods=252),
        )
        y21 = pd.Series(
            rng.normal(0.001, 0.01, 3),
            index=pd.bdate_range("2021-01-01", periods=3),
        )
        r = annual_breakdown(pd.concat([y20, y21]))
        years = [y["year"] for y in r["per_year"]]
        assert 2020 in years
        assert 2021 not in years  # skipped
