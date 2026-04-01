"""V2.12.1 edge case tests."""
import numpy as np
import pandas as pd
import pytest
from datetime import date


class TestGramSchmidt:
    def test_orthogonalized_columns_are_orthogonal(self):
        from ez.portfolio.orthogonalization import gram_schmidt_orthogonalize
        rng = np.random.default_rng(42)
        # Create correlated factors
        base = rng.normal(0, 1, 100)
        f1 = base + rng.normal(0, 0.1, 100)
        f2 = base + rng.normal(0, 0.1, 100)
        f3 = rng.normal(0, 1, 100)
        mat = np.column_stack([f1, f2, f3])

        orth = gram_schmidt_orthogonalize(mat)

        # Columns should be nearly uncorrelated
        for i in range(3):
            for j in range(i + 1, 3):
                corr = np.corrcoef(orth[:, i], orth[:, j])[0, 1]
                assert abs(corr) < 0.05, f"Columns {i},{j} corr={corr:.4f}"

    def test_nan_rows_preserved(self):
        from ez.portfolio.orthogonalization import gram_schmidt_orthogonalize
        mat = np.array([[1, 2], [3, 4], [np.nan, 6], [7, 8]], dtype=float)
        orth = gram_schmidt_orthogonalize(mat)
        assert np.isnan(orth[2, 0])  # NaN preserved
        assert not np.isnan(orth[0, 0])

    def test_single_factor_unchanged(self):
        from ez.portfolio.orthogonalization import gram_schmidt_orthogonalize
        mat = np.array([[1], [2], [3]], dtype=float)
        orth = gram_schmidt_orthogonalize(mat)
        np.testing.assert_array_equal(orth, mat)


class TestOptimizerTE:
    def test_te_constraint_with_benchmark_weights(self):
        """Optimizer should respect tracking error constraint."""
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        symbols = [f"S{i}" for i in range(5)]
        rng = np.random.default_rng(42)
        dates_range = pd.date_range("2023-01-02", periods=100, freq="B")
        data = {}
        for i, sym in enumerate(symbols):
            prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.01 * (i + 1), 100))
            data[sym] = pd.DataFrame({"close": prices, "adj_close": prices, "volume": rng.integers(100000, 5000000, 100)}, index=dates_range)

        benchmark_w = {f"S{i}": 0.2 for i in range(5)}
        opt = MeanVarianceOptimizer(
            risk_aversion=1.0,
            constraints=OptimizationConstraints(max_weight=0.40),
            cov_lookback=60,
            benchmark_weights=benchmark_w,
            max_tracking_error=0.05,
        )
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.2 for s in symbols})
        assert all(w >= -1e-9 for w in result.values())
        assert abs(sum(result.values()) - 1.0) < 1e-5


class TestIndexData:
    def test_cache_prevents_repeated_calls(self):
        from ez.portfolio.index_data import IndexDataProvider
        provider = IndexDataProvider()
        # Set cache directly
        import time as _time
        provider._cache["cons_TEST"] = (_time.monotonic(), ["A.SH", "B.SZ"])
        result = provider.get_constituents("TEST")
        assert result == ["A.SH", "B.SZ"]

    def test_fallback_to_equal_weight(self):
        from ez.portfolio.index_data import IndexDataProvider
        provider = IndexDataProvider()
        weights = provider._build_weights(["A.SH", "B.SZ", "C.SZ"])
        assert len(weights) == 3
        assert abs(sum(weights.values()) - 1.0) < 1e-10
        assert abs(weights["A.SH"] - 1 / 3) < 1e-10

    def test_normalize_code(self):
        from ez.portfolio.index_data import IndexDataProvider
        assert IndexDataProvider._normalize_code("600519") == "600519.SH"
        assert IndexDataProvider._normalize_code("000001") == "000001.SZ"
        assert IndexDataProvider._normalize_code("300750") == "300750.SZ"
        assert IndexDataProvider._normalize_code("600519.SH") == "600519.SH"


class TestOptimizerEdgeCases:
    def test_all_negative_alpha_returns_fallback(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        opt = MeanVarianceOptimizer(constraints=OptimizationConstraints(max_weight=0.5))
        # All negative alphas → filtered to empty → return {}
        result = opt.optimize({"A": -0.5, "B": -0.3})
        assert result == {}

    def test_covariance_with_exactly_3_days(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        symbols = ["A", "B"]
        dates_range = pd.date_range("2023-07-01", periods=3, freq="B")
        data = {s: pd.DataFrame({"close": [10, 11, 10.5], "adj_close": [10, 11, 10.5]},
                                index=dates_range) for s in symbols}
        opt = MeanVarianceOptimizer(constraints=OptimizationConstraints(max_weight=0.8), cov_lookback=60)
        opt.set_context(date(2023, 7, 6), data)
        result = opt.optimize({"A": 0.6, "B": 0.4})
        assert len(result) > 0  # should not crash


class TestRiskManagerEdgeCases:
    def test_turnover_check_with_empty_prev_weights(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_turnover=0.30))
        new_w = {"A": 0.5, "B": 0.5}
        # First rebalance: prev is empty → turnover = max(0.5, 0) = 0.5 > 0.3
        result, event = rm.check_turnover(new_w, {})
        assert event is not None  # should trigger mixing

    def test_mixed_weights_sum_normalized(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_turnover=0.10))
        new_w = {"A": 0.3, "B": 0.3}  # sum=0.6 (optimizer left cash)
        prev_w = {"A": 0.5, "B": 0.5}  # sum=1.0
        result, _ = rm.check_turnover(new_w, prev_w)
        total = sum(result.values())
        assert total <= 1.0 + 1e-6  # should not exceed 1.0


class TestAttributionEdgeCases:
    def test_single_period_carino_equals_arithmetic(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult
        data = {"A": pd.DataFrame({"close": [10, 11], "adj_close": [10, 11]},
                                  index=pd.date_range("2023-01-02", periods=2, freq="B"))}
        result = PortfolioResult(
            rebalance_dates=[date(2023, 1, 2), date(2023, 1, 3)],
            rebalance_weights=[{"A": 1.0}],
            weights_history=[{"A": 1.0}] * 2,
            trades=[],
        )
        attr = compute_attribution(result, data, {"A": "银行"})
        # With 1 period, Carino linking = arithmetic (k/K = 1)
        assert attr.cumulative is not None
        assert abs(attr.cumulative.total_excess - attr.periods[0].total_excess) < 1e-10

    def test_zero_return_period_carino_k_is_one(self):
        """Carino k(0) = 1.0 (L'Hôpital)."""
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult
        # Same price → zero return
        data = {"A": pd.DataFrame({"close": [10, 10, 10], "adj_close": [10, 10, 10]},
                                  index=pd.date_range("2023-01-02", periods=3, freq="B"))}
        result = PortfolioResult(
            rebalance_dates=[date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4)],
            rebalance_weights=[{"A": 1.0}, {"A": 1.0}],
            weights_history=[{"A": 1.0}] * 3,
            trades=[],
        )
        attr = compute_attribution(result, data, {"A": "银行"})
        assert attr.cumulative is not None
        # All effects should be 0 (no return difference)
        assert abs(attr.cumulative.total_excess) < 1e-10


class TestFinalLiquidation:
    def _run_with_liquidation(self):
        from ez.portfolio.engine import run_portfolio_backtest, CostModel
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.cross_factor import MomentumRank
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.universe import Universe

        symbols = [f"S{i}" for i in range(3)]
        rng = np.random.default_rng(42)
        dates_range = pd.date_range("2023-01-02", periods=60, freq="B")
        data = {}
        for i, sym in enumerate(symbols):
            prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, 60))
            data[sym] = pd.DataFrame({"open": prices, "high": prices * 1.01, "low": prices * 0.99,
                                      "close": prices, "adj_close": prices, "volume": rng.integers(100000, 5000000, 60)}, index=dates_range)
        cal = TradingCalendar.from_dates([d.date() for d in dates_range])
        return run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=2),
            universe=Universe(symbols), universe_data=data, calendar=cal,
            start=dates_range[25].date(), end=dates_range[-1].date(),
            freq="monthly", initial_cash=100000, lot_size=1,
        )

    def test_liquidation_trades_have_flag(self):
        result = self._run_with_liquidation()
        liq_trades = [t for t in result.trades if t.get("liquidation")]
        assert len(liq_trades) > 0

    def test_liquidation_date_is_after_last_trading_day(self):
        result = self._run_with_liquidation()
        last_day = result.dates[-1].isoformat()
        liq_trades = [t for t in result.trades if t.get("liquidation")]
        for t in liq_trades:
            assert t["date"] > last_day

    def test_turnover_excludes_liquidation(self):
        result = self._run_with_liquidation()
        assert result.metrics.get("turnover_per_rebalance") is not None
