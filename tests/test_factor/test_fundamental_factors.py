"""Tests for fundamental CrossSectionalFactors (V2.11): correctness, direction, PIT."""
from datetime import date, datetime

import duckdb
import pandas as pd
import numpy as np
import pytest

from ez.portfolio.cross_factor import CrossSectionalFactor


@pytest.fixture
def fund_store():
    """FundamentalStore with synthetic fundamental data for 5 stocks."""
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            ts_code VARCHAR PRIMARY KEY, name VARCHAR, area VARCHAR,
            industry VARCHAR, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    stocks = [
        ("000001.SZ", "平安银行", "深圳", "银行"),
        ("600519.SH", "贵州茅台", "贵州", "食品饮料"),
        ("000858.SZ", "五粮液", "四川", "食品饮料"),
        ("601318.SH", "中国平安", "深圳", "保险"),
        ("000333.SZ", "美的集团", "广东", "家电"),
    ]
    for code, name, area, ind in stocks:
        conn.execute("INSERT INTO symbols VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)", [code, name, area, ind])

    from ez.data.fundamental import FundamentalStore
    store = FundamentalStore(conn)

    # Insert daily basic data: different PE/PB/MV for each stock
    daily_data = [
        ("000001.SZ", 5.0, 0.5, 10.0, 2.0, 5000, 300000, 200000, 0.8),
        ("600519.SH", 35.0, 12.0, 15.0, 1.5, 2000, 2000000, 1800000, 0.3),
        ("000858.SZ", 25.0, 8.0, 12.0, 2.5, 3000, 500000, 400000, 0.5),
        ("601318.SH", 10.0, 1.5, 8.0, 3.0, 4000, 800000, 600000, 0.6),
        ("000333.SZ", 15.0, 3.0, 10.0, 1.0, 6000, 400000, 350000, 0.7),
    ]
    for sym, pe, pb, ps, dv, turnover, mv, cmv, vol_ratio in daily_data:
        for d in range(2, 25):
            store.save_daily_basic([{
                "symbol": sym, "trade_date": date(2024, 1, d),
                "pe_ttm": pe, "pb": pb, "ps_ttm": ps, "dv_ratio": dv,
                "turnover_rate": turnover / 100, "total_mv": mv, "circ_mv": cmv,
                "volume_ratio": vol_ratio,
            }])

    # Insert fina_indicator data
    fina_data = [
        ("000001.SZ", date(2024, 4, 28), date(2024, 3, 31), 12.5, 1.2, 45.0, 30.0, 88.0, 1.1, 15.0, 20.0, 5.0),
        ("600519.SH", date(2024, 4, 25), date(2024, 3, 31), 30.0, 15.0, 90.0, 60.0, 25.0, 3.5, 18.0, 22.0, -2.0),
        ("000858.SZ", date(2024, 4, 29), date(2024, 3, 31), 22.0, 10.0, 70.0, 40.0, 40.0, 2.0, 12.0, 15.0, 3.0),
        ("601318.SH", date(2024, 4, 26), date(2024, 3, 31), 18.0, 5.0, 55.0, 25.0, 75.0, 1.5, 10.0, 12.0, 1.0),
        ("000333.SZ", date(2024, 4, 27), date(2024, 3, 31), 25.0, 12.0, 35.0, 20.0, 50.0, 2.5, 25.0, 30.0, 8.0),
    ]
    for sym, ann, end, roe, roa, gm, npm, dta, cr, rev_yoy, prof_yoy, roe_yoy in fina_data:
        store.save_fina_indicator([{
            "symbol": sym, "ann_date": ann, "end_date": end,
            "roe": roe, "roe_waa": roe, "roa": roa,
            "grossprofit_margin": gm, "netprofit_margin": npm,
            "debt_to_assets": dta, "current_ratio": cr,
            "revenue_yoy": rev_yoy, "profit_yoy": prof_yoy, "roe_yoy": roe_yoy,
        }])

    syms = [s[0] for s in stocks]
    store.preload(syms, date(2024, 1, 1), date(2024, 12, 31))
    return store


@pytest.fixture
def universe_data():
    """Synthetic OHLCV universe data for 5 stocks."""
    syms = ["000001.SZ", "600519.SH", "000858.SZ", "601318.SH", "000333.SZ"]
    dates = pd.date_range("2024-01-02", periods=23, freq="B")
    rng = np.random.default_rng(42)
    data = {}
    for i, sym in enumerate(syms):
        base = 10 * (i + 1)
        prices = base * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.01, 23))
        data[sym] = pd.DataFrame({
            "open": prices, "high": prices * 1.01, "low": prices * 0.99,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100000, 1000000, 23).astype(float),
        }, index=dates)
    return data


class TestFundamentalFactorRegistration:
    def test_all_fundamental_factors_registered(self):
        from ez.factor.builtin.fundamental import get_fundamental_factors, FACTOR_CATEGORIES
        factors = get_fundamental_factors()
        expected_names = set()
        for cat_factors in FACTOR_CATEGORIES.values():
            expected_names.update(cat_factors)
        for name in expected_names:
            assert name in factors, f"Factor {name} not registered"

    def test_fundamental_factors_count(self):
        from ez.factor.builtin.fundamental import get_fundamental_factors
        factors = get_fundamental_factors()
        assert len(factors) == 18, f"Expected 18 fundamental factors, got {len(factors)}"

    def test_abstract_base_not_registered(self):
        from ez.factor.builtin.fundamental import get_fundamental_factors
        factors = get_fundamental_factors()
        assert "FundamentalCrossFactor" not in factors


class TestValueFactors:
    def test_ep_higher_for_low_pe(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import EP
        factor = EP(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 1, 15))

        # 000001.SZ has PE=5 (lowest) → EP=1/5=0.2 (highest) → should rank highest
        assert scores["000001.SZ"] > scores["600519.SH"], "Low PE stock should rank higher on EP"

    def test_bp_higher_for_low_pb(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import BP
        factor = BP(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 1, 15))
        assert scores["000001.SZ"] > scores["600519.SH"], "Low PB stock should rank higher on BP"

    def test_dp_higher_for_high_dividend(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import DP
        factor = DP(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 1, 15))
        # 601318.SH has dv_ratio=3.0 (highest)
        assert scores["601318.SH"] == scores.max(), "Highest dividend stock should rank highest"


class TestQualityFactors:
    def test_roe_pit_timing(self, fund_store, universe_data):
        """ROE should only be available after announcement date."""
        from ez.factor.builtin.fundamental import ROE
        factor = ROE(store=fund_store)

        # Before any announcement (before April 25)
        scores_before = factor.compute(universe_data, datetime(2024, 4, 1))
        assert len(scores_before) == 0, "No fina data should be available before announcement"

        # After all announcements (after April 29)
        scores_after = factor.compute(universe_data, datetime(2024, 5, 1))
        assert len(scores_after) == 5, "All 5 stocks should have scores after announcements"

    def test_roe_direction(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import ROE
        factor = ROE(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 5, 15))
        # 600519.SH has ROE=30 (highest) → should rank highest
        assert scores["600519.SH"] == scores.max(), "Highest ROE should rank highest"


class TestGrowthFactors:
    def test_revenue_growth_direction(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import RevenueGrowthYoY
        factor = RevenueGrowthYoY(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 5, 15))
        # 000333.SZ has revenue_yoy=25 (highest)
        assert scores["000333.SZ"] == scores.max()


class TestSizeFactors:
    def test_small_cap_gets_higher_score(self, fund_store, universe_data):
        """Size factor: small cap should get higher score (small-cap premium)."""
        from ez.factor.builtin.fundamental import LnMarketCap
        factor = LnMarketCap(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 1, 15))
        # 000001.SZ has MV=300000 (smallest) → should rank highest (higher_is_better=False → negated)
        assert scores["000001.SZ"] > scores["600519.SH"], "Smaller cap should rank higher"


class TestLiquidityFactors:
    def test_turnover_direction(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import TurnoverRate
        factor = TurnoverRate(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 1, 15))
        assert len(scores) > 0

    def test_amihud_uses_price_data(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import AmihudIlliquidity
        factor = AmihudIlliquidity(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 1, 24))
        assert len(scores) > 0  # Should compute from price data


class TestLeverageFactors:
    def test_low_debt_gets_higher_score(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import DebtToAssets
        factor = DebtToAssets(store=fund_store)
        scores = factor.compute(universe_data, datetime(2024, 5, 15))
        # 600519.SH has debt_to_assets=25 (lowest) → higher_is_better=False → highest rank
        assert scores["600519.SH"] == scores.max(), "Lowest debt should rank highest"


class TestIndustryMomentum:
    def test_industry_momentum_uses_industry(self, fund_store, universe_data):
        from ez.factor.builtin.fundamental import IndustryMomentum
        factor = IndustryMomentum(store=fund_store, period=10)
        scores = factor.compute(universe_data, datetime(2024, 1, 24))
        # Should have scores for stocks with industry labels
        assert len(scores) >= 3  # at least the ones with industry in symbols table


class TestFactorOutputProperties:
    def test_scores_between_0_and_1(self, fund_store, universe_data):
        """All factor scores should be percentile ranks in [0, 1]."""
        from ez.factor.builtin.fundamental import get_fundamental_factors
        factors = get_fundamental_factors()
        dt = datetime(2024, 5, 15)

        for name, cls in factors.items():
            factor = cls(store=fund_store)
            scores = factor.compute(universe_data, dt)
            if len(scores) == 0:
                continue
            assert scores.min() >= 0, f"{name}: min score {scores.min()} < 0"
            assert scores.max() <= 1.0, f"{name}: max score {scores.max()} > 1"

    def test_no_store_returns_empty(self, universe_data):
        """Factor without store should return empty series gracefully."""
        from ez.factor.builtin.fundamental import EP
        factor = EP()  # no store
        scores = factor.compute(universe_data, datetime(2024, 1, 15))
        assert len(scores) == 0


class TestFundamentalContractInvariants:
    """Contract invariants that ALL 18 FundamentalCrossFactor subclasses must satisfy.
    Mirrors TestCrossSectionalFactorContract in test_cross_factor_contract.py but with
    fund_store injection. Ensures new fundamental factors conform to CrossSectionalFactor ABC.
    """

    @pytest.fixture
    def all_fundamental_instances(self, fund_store):
        from ez.factor.builtin.fundamental import get_fundamental_factors
        return [(name, cls(store=fund_store)) for name, cls in get_fundamental_factors().items()]

    def test_all_have_name(self, all_fundamental_instances):
        for name, factor in all_fundamental_instances:
            assert isinstance(factor.name, str), f"{name} has non-string name"
            assert len(factor.name) > 0, f"{name} has empty name"

    def test_all_have_warmup_period(self, all_fundamental_instances):
        for name, factor in all_fundamental_instances:
            assert isinstance(factor.warmup_period, int), f"{name} warmup is not int"
            assert factor.warmup_period >= 0, f"{name} warmup < 0"

    def test_all_have_category(self, all_fundamental_instances):
        for name, factor in all_fundamental_instances:
            assert isinstance(factor.category, str), f"{name} category is not str"
            assert factor.category in ("value", "quality", "growth", "size", "liquidity", "leverage", "industry"), (
                f"{name} has unknown category: {factor.category}"
            )

    def test_all_have_description(self, all_fundamental_instances):
        for name, factor in all_fundamental_instances:
            assert isinstance(factor.description, str), f"{name} description is not str"

    def test_compute_returns_series(self, all_fundamental_instances, universe_data):
        for name, factor in all_fundamental_instances:
            result = factor.compute(universe_data, datetime(2024, 5, 15))
            assert isinstance(result, pd.Series), f"{name} compute() did not return Series"

    def test_compute_raw_returns_series(self, all_fundamental_instances, universe_data):
        for name, factor in all_fundamental_instances:
            result = factor.compute_raw(universe_data, datetime(2024, 5, 15))
            assert isinstance(result, pd.Series), f"{name} compute_raw() did not return Series"

    def test_compute_no_nan(self, all_fundamental_instances, universe_data):
        """compute() must drop NaN (contract requirement)."""
        for name, factor in all_fundamental_instances:
            result = factor.compute(universe_data, datetime(2024, 5, 15))
            if len(result) > 0:
                assert not result.isna().any(), f"{name} compute() contains NaN"

    def test_compute_raw_no_nan(self, all_fundamental_instances, universe_data):
        """compute_raw() must drop NaN (V2.11.1 contract)."""
        for name, factor in all_fundamental_instances:
            result = factor.compute_raw(universe_data, datetime(2024, 5, 15))
            if len(result) > 0:
                assert not result.isna().any(), f"{name} compute_raw() contains NaN"

    def test_compute_values_in_0_1(self, all_fundamental_instances, universe_data):
        """Percentile ranks must be in [0, 1]."""
        for name, factor in all_fundamental_instances:
            result = factor.compute(universe_data, datetime(2024, 5, 15))
            if len(result) > 0:
                assert result.min() >= -1e-9, f"{name}: min {result.min()} < 0"
                assert result.max() <= 1.0 + 1e-9, f"{name}: max {result.max()} > 1"

    def test_empty_universe_returns_empty(self, all_fundamental_instances):
        """Empty universe should yield empty series, not crash."""
        for name, factor in all_fundamental_instances:
            result = factor.compute({}, datetime(2024, 5, 15))
            assert len(result) == 0, f"{name} did not return empty for empty universe"

    def test_compute_and_raw_same_index(self, all_fundamental_instances, universe_data):
        """compute() and compute_raw() must cover the same symbols (after dropna)."""
        for name, factor in all_fundamental_instances:
            ranked = factor.compute(universe_data, datetime(2024, 5, 15))
            raw = factor.compute_raw(universe_data, datetime(2024, 5, 15))
            assert set(ranked.index) == set(raw.index), (
                f"{name}: compute={set(ranked.index)} vs compute_raw={set(raw.index)}"
            )
