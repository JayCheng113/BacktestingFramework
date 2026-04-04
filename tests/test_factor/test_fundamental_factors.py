"""Tests for fundamental CrossSectionalFactors (V2.11): correctness, direction, PIT."""
from datetime import date, datetime

import duckdb
import pandas as pd
import numpy as np
import pytest

from ez.portfolio.cross_factor import CrossSectionalFactor


@pytest.fixture(scope="module")
def fund_store():
    """FundamentalStore with synthetic fundamental data for 5 stocks.

    Module-scoped: 198 parameterized contract tests share one fixture to avoid
    rebuilding the 1825-row daily_basic snapshot per test. The fixture is read-only
    w.r.t. test state (no mutation in contract tests).
    """
    from datetime import timedelta
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
    # Full-year coverage (2024-01 to 2024-12) so contract tests on 2024-05-15 find data.
    # Batch insert: collect all rows first, then single save_daily_basic() call.
    daily_data = [
        ("000001.SZ", 5.0, 0.5, 10.0, 2.0, 5000, 300000, 200000, 0.8),
        ("600519.SH", 35.0, 12.0, 15.0, 1.5, 2000, 2000000, 1800000, 0.3),
        ("000858.SZ", 25.0, 8.0, 12.0, 2.5, 3000, 500000, 400000, 0.5),
        ("601318.SH", 10.0, 1.5, 8.0, 3.0, 4000, 800000, 600000, 0.6),
        ("000333.SZ", 15.0, 3.0, 10.0, 1.0, 6000, 400000, 350000, 0.7),
    ]
    daily_rows = []
    for sym, pe, pb, ps, dv, turnover, mv, cmv, vol_ratio in daily_data:
        d0 = date(2024, 1, 2)
        for offset in range(0, 365):
            trade_d = d0 + timedelta(days=offset)
            if trade_d.weekday() >= 5:
                continue
            daily_rows.append({
                "symbol": sym, "trade_date": trade_d,
                "pe_ttm": pe, "pb": pb, "ps_ttm": ps, "dv_ratio": dv,
                "turnover_rate": turnover / 100, "total_mv": mv, "circ_mv": cmv,
                "volume_ratio": vol_ratio,
            })
    store.save_daily_basic(daily_rows)  # single batch call

    # Insert fina_indicator data (also batched)
    fina_data = [
        ("000001.SZ", date(2024, 4, 28), date(2024, 3, 31), 12.5, 1.2, 45.0, 30.0, 88.0, 1.1, 15.0, 20.0, 5.0),
        ("600519.SH", date(2024, 4, 25), date(2024, 3, 31), 30.0, 15.0, 90.0, 60.0, 25.0, 3.5, 18.0, 22.0, -2.0),
        ("000858.SZ", date(2024, 4, 29), date(2024, 3, 31), 22.0, 10.0, 70.0, 40.0, 40.0, 2.0, 12.0, 15.0, 3.0),
        ("601318.SH", date(2024, 4, 26), date(2024, 3, 31), 18.0, 5.0, 55.0, 25.0, 75.0, 1.5, 10.0, 12.0, 1.0),
        ("000333.SZ", date(2024, 4, 27), date(2024, 3, 31), 25.0, 12.0, 35.0, 20.0, 50.0, 2.5, 25.0, 30.0, 8.0),
    ]
    fina_rows = []
    for sym, ann, end, roe, roa, gm, npm, dta, cr, rev_yoy, prof_yoy, roe_yoy in fina_data:
        fina_rows.append({
            "symbol": sym, "ann_date": ann, "end_date": end,
            "roe": roe, "roe_waa": roe, "roa": roa,
            "grossprofit_margin": gm, "netprofit_margin": npm,
            "debt_to_assets": dta, "current_ratio": cr,
            "revenue_yoy": rev_yoy, "profit_yoy": prof_yoy, "roe_yoy": roe_yoy,
        })
    store.save_fina_indicator(fina_rows)

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


_FUNDAMENTAL_NAMES = [
    "EP", "BP", "SP", "DP",
    "ROE", "ROA", "GrossMargin", "NetProfitMargin",
    "RevenueGrowthYoY", "ProfitGrowthYoY", "ROEChange",
    "LnMarketCap", "LnCircMV",
    "TurnoverRate", "AmihudIlliquidity",
    "DebtToAssets", "CurrentRatio",
    "IndustryMomentum",
]


@pytest.fixture
def fundamental_factory(fund_store):
    """Map of factor name → constructor bound to fund_store."""
    from ez.factor.builtin.fundamental import get_fundamental_factors
    registry = get_fundamental_factors()
    return {name: (lambda cls=cls: cls(store=fund_store)) for name, cls in registry.items()}


@pytest.fixture
def make_factor(fundamental_factory):
    def _make(name: str):
        return fundamental_factory[name]()
    return _make


class TestFundamentalContractInvariants:
    """Contract invariants that ALL 18 FundamentalCrossFactor subclasses must satisfy.

    Truly parameterized: each factor × each invariant is a separate pytest test case.
    Failures report as `test_compute_no_nan[ROE]` — selective rerun + clear attribution.

    This catches regressions when a new fundamental factor is added but forgets to
    conform to the CrossSectionalFactor ABC contract.
    """

    def test_registry_has_all_18(self):
        """The 18 canonical fundamental factors must all be in the registry."""
        from ez.factor.builtin.fundamental import get_fundamental_factors
        registry = get_fundamental_factors()
        missing = set(_FUNDAMENTAL_NAMES) - set(registry.keys())
        extra = set(registry.keys()) - set(_FUNDAMENTAL_NAMES)
        assert not missing, f"Missing fundamental factors from registry: {missing}"
        assert len(registry) == 18, f"Expected 18 factors, got {len(registry)}: {extra}"

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_has_name(self, factor_name, make_factor):
        f = make_factor(factor_name)
        assert isinstance(f.name, str)
        assert len(f.name) > 0

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_has_warmup_period(self, factor_name, make_factor):
        f = make_factor(factor_name)
        assert isinstance(f.warmup_period, int)
        assert f.warmup_period >= 0

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_has_category(self, factor_name, make_factor):
        f = make_factor(factor_name)
        assert isinstance(f.category, str)
        assert f.category in (
            "value", "quality", "growth", "size",
            "liquidity", "leverage", "industry",
        ), f"{factor_name} has unknown category: {f.category}"

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_has_non_empty_description(self, factor_name, make_factor):
        f = make_factor(factor_name)
        assert isinstance(f.description, str)
        assert len(f.description) > 0, f"{factor_name} has empty description"

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_compute_returns_series(self, factor_name, make_factor, universe_data):
        f = make_factor(factor_name)
        result = f.compute(universe_data, datetime(2024, 5, 15))
        assert isinstance(result, pd.Series)

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_compute_raw_returns_series(self, factor_name, make_factor, universe_data):
        f = make_factor(factor_name)
        result = f.compute_raw(universe_data, datetime(2024, 5, 15))
        assert isinstance(result, pd.Series)

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_compute_returns_non_empty(self, factor_name, make_factor, universe_data):
        """After the fund_store fixture preloads data for all 5 stocks, every fundamental
        factor should produce a non-empty result on 2024-05-15 (after all ann_dates)."""
        f = make_factor(factor_name)
        result = f.compute(universe_data, datetime(2024, 5, 15))
        assert len(result) > 0, (
            f"{factor_name} returned empty — fixture data may be missing. "
            f"This prevents vacuous invariant assertions."
        )

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_compute_no_nan(self, factor_name, make_factor, universe_data):
        """compute() must drop NaN (contract requirement)."""
        f = make_factor(factor_name)
        result = f.compute(universe_data, datetime(2024, 5, 15))
        assert not result.isna().any(), f"{factor_name} compute() contains NaN"

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_compute_raw_no_nan(self, factor_name, make_factor, universe_data):
        """compute_raw() must drop NaN (V2.11.1 contract)."""
        f = make_factor(factor_name)
        result = f.compute_raw(universe_data, datetime(2024, 5, 15))
        assert not result.isna().any(), f"{factor_name} compute_raw() contains NaN"

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_compute_values_in_0_1(self, factor_name, make_factor, universe_data):
        """Percentile ranks must be in [0, 1]."""
        f = make_factor(factor_name)
        result = f.compute(universe_data, datetime(2024, 5, 15))
        assert result.min() >= -1e-9, f"{factor_name}: min {result.min()} < 0"
        assert result.max() <= 1.0 + 1e-9, f"{factor_name}: max {result.max()} > 1"

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_empty_universe_returns_empty(self, factor_name, make_factor):
        """Empty universe should yield empty series, not crash."""
        f = make_factor(factor_name)
        result = f.compute({}, datetime(2024, 5, 15))
        assert len(result) == 0

    @pytest.mark.parametrize("factor_name", _FUNDAMENTAL_NAMES)
    def test_compute_and_raw_same_index(self, factor_name, make_factor, universe_data):
        """compute() and compute_raw() must cover the same symbols (after dropna)."""
        f = make_factor(factor_name)
        ranked = f.compute(universe_data, datetime(2024, 5, 15))
        raw = f.compute_raw(universe_data, datetime(2024, 5, 15))
        assert set(ranked.index) == set(raw.index), (
            f"{factor_name}: compute={set(ranked.index)} vs compute_raw={set(raw.index)}"
        )
