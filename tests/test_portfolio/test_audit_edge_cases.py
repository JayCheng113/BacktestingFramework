"""Edge case tests from V2.11.1 full audit (M3-M6)."""
from datetime import date, datetime

import duckdb
import numpy as np
import pandas as pd
import pytest

from ez.portfolio.neutralization import neutralize_by_industry


class TestNeutralizationBoundary:
    """M4: Coverage exactly 50% boundary."""

    def test_coverage_exactly_50_percent(self):
        """50% coverage should pass (>= threshold, not > threshold)."""
        scores = pd.Series({"A": 10, "B": 8, "C": 6, "D": 4})
        industry_map = {"A": "银行", "B": "银行"}  # 2/4 = 50%
        result, warnings = neutralize_by_industry(scores, industry_map, min_coverage=0.5)
        # 50% == 50% → should proceed (not skip)
        assert not any("跳过" in w for w in warnings)

    def test_coverage_just_below_50(self):
        """49% coverage should skip."""
        scores = pd.Series({f"S{i}": float(i) for i in range(100)})
        industry_map = {f"S{i}": "银行" for i in range(49)}  # 49/100
        result, warnings = neutralize_by_industry(scores, industry_map, min_coverage=0.5)
        assert any("跳过" in w for w in warnings)


class TestICAllNaN:
    """M5: IC evaluation with all NaN input."""

    def test_all_nan_factor_returns_empty(self):
        from ez.portfolio.cross_factor import CrossSectionalFactor

        class NaNFactor(CrossSectionalFactor):
            @property
            def name(self):
                return "nan_factor"
            def compute_raw(self, universe_data, date):
                return pd.Series(dtype=float)  # empty
            def compute(self, universe_data, date):
                return pd.Series(dtype=float)

        CrossSectionalFactor._registry.pop("NaNFactor", None)

        from ez.portfolio.cross_evaluator import evaluate_cross_sectional_factor
        from ez.portfolio.calendar import TradingCalendar

        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        data = {"A": pd.DataFrame({
            "open": np.ones(30) * 10, "high": np.ones(30) * 10,
            "low": np.ones(30) * 10, "close": np.ones(30) * 10,
            "adj_close": np.ones(30) * 10, "volume": np.ones(30) * 100000,
        }, index=dates)}
        cal = TradingCalendar.from_dates([d.date() for d in dates])

        result = evaluate_cross_sectional_factor(
            factor=NaNFactor(), universe_data=data, calendar=cal,
            start=dates[5].date(), end=dates[-1].date(),
        )
        # Should not crash; IC should be 0 or NaN
        assert result.n_eval_dates == 0 or np.isnan(result.mean_ic) or result.mean_ic == 0


class TestPITRestatement:
    """M6: PIT query with restatement (同一 end_date 不同 ann_date)."""

    def test_restatement_returns_latest_end_date(self):
        conn = duckdb.connect(":memory:")
        conn.execute("""CREATE TABLE symbols (ts_code VARCHAR PRIMARY KEY, name VARCHAR, area VARCHAR, industry VARCHAR, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        from ez.data.fundamental import FundamentalStore
        store = FundamentalStore(conn)

        # Q4 2022 original announcement
        store.save_fina_indicator([{
            "symbol": "000001.SZ", "ann_date": date(2023, 4, 20), "end_date": date(2022, 12, 31),
            "roe": 12.0, "roa": 1.0,
        }])
        # Q4 2022 restatement with LATER ann_date (corrected financials)
        store.save_fina_indicator([{
            "symbol": "000001.SZ", "ann_date": date(2023, 8, 15), "end_date": date(2022, 12, 31),
            "roe": 10.5, "roa": 0.9,  # restated lower
        }])

        store.preload(["000001.SZ"], date(2023, 1, 1), date(2023, 12, 31))

        # After restatement: ann_date preserved as original (2023-04-20), values updated to 10.5
        # PIT at 2023-05-01 should find it (ann_date=04-20 <= 05-01)
        val = store.get_fina_pit("000001.SZ", date(2023, 5, 1))
        assert val is not None
        assert val["roe"] == 10.5  # values updated, ann_date preserved

        # Before original announcement: should not be visible
        val_early = store.get_fina_pit("000001.SZ", date(2023, 4, 1))
        assert val_early is None  # ann_date=04-20 > 04-01


class TestSPFactorNaN:
    """B7: SP factor NaN handling after fix."""

    def test_sp_nan_ps_ttm_excluded(self):
        conn = duckdb.connect(":memory:")
        conn.execute("""CREATE TABLE symbols (ts_code VARCHAR PRIMARY KEY, name VARCHAR, area VARCHAR, industry VARCHAR, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        from ez.data.fundamental import FundamentalStore
        store = FundamentalStore(conn)

        # Stock with NaN ps_ttm
        store.save_daily_basic([
            {"symbol": "A", "trade_date": date(2024, 1, 2), "ps_ttm": float('nan')},
            {"symbol": "B", "trade_date": date(2024, 1, 2), "ps_ttm": 10.0},
        ])
        store.preload(["A", "B"], date(2024, 1, 1), date(2024, 1, 3))

        from ez.factor.builtin.fundamental import SP
        factor = SP(store=store)
        universe = {"A": pd.DataFrame(), "B": pd.DataFrame()}
        scores = factor.compute(universe, datetime(2024, 1, 2))
        # A should be excluded (NaN ps_ttm), B should have valid score
        assert "A" not in scores or not np.isnan(scores.get("A", 0))
        assert "B" in scores
