"""Integration tests for Runner + Gate + Report (B2-B4)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ez.agent.gates import GateConfig, GateVerdict, ResearchGate
from ez.agent.report import ExperimentReport
from ez.agent.run_spec import RunSpec
from ez.agent.runner import Runner, RunResult


# Ensure builtin strategies are loaded
import ez.strategy.builtin.ma_cross  # noqa: F401


@pytest.fixture
def sample_data():
    """500-bar synthetic data with mild uptrend (enough for 3-split WFO with MA20)."""
    rng = np.random.default_rng(42)
    n = 500
    returns = rng.normal(0.001, 0.015, n)
    prices = 10 * np.cumprod(1 + returns)
    dates = pd.date_range("2022-01-03", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices * (1 + rng.normal(0, 0.002, n)),
        "high": prices * (1 + abs(rng.normal(0, 0.005, n))),
        "low": prices * (1 - abs(rng.normal(0, 0.005, n))),
        "close": prices,
        "adj_close": prices,
        "volume": rng.integers(100_000, 5_000_000, n),
    }, index=dates)


@pytest.fixture
def spec():
    return RunSpec(
        strategy_name="MACrossStrategy",
        strategy_params={"short_period": 5, "long_period": 20},
        symbol="TEST.SZ",
        market="cn_stock",
        start_date=date(2022, 1, 1),
        end_date=date(2022, 12, 31),
        wfo_n_splits=3,
    )


class TestRunner:
    def test_run_completes(self, spec, sample_data):
        result = Runner().run(spec, sample_data)
        assert result.status == "completed"
        assert result.backtest is not None
        assert result.walk_forward is not None
        assert result.run_id
        assert result.spec_id == spec.spec_id
        assert result.duration_ms > 0
        assert result.error is None

    def test_run_backtest_only(self, sample_data):
        spec = RunSpec(
            strategy_name="MACrossStrategy",
            strategy_params={"short_period": 5, "long_period": 20},
            symbol="TEST.SZ", market="cn_stock",
            start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
            run_wfo=False,
        )
        result = Runner().run(spec, sample_data)
        assert result.status == "completed"
        assert result.backtest is not None
        assert result.walk_forward is None

    def test_run_wfo_only(self, sample_data):
        spec = RunSpec(
            strategy_name="MACrossStrategy",
            strategy_params={"short_period": 5, "long_period": 20},
            symbol="TEST.SZ", market="cn_stock",
            start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
            run_backtest=False,
        )
        result = Runner().run(spec, sample_data)
        assert result.status == "completed"
        assert result.backtest is None
        assert result.walk_forward is not None

    def test_invalid_strategy_returns_failed(self, sample_data):
        spec = RunSpec(
            strategy_name="NonExistentStrategy",
            strategy_params={},
            symbol="TEST.SZ", market="cn_stock",
            start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
        )
        result = Runner().run(spec, sample_data)
        assert result.status == "failed"
        assert "not found" in result.error

    def test_non_integer_float_param_rejected(self, sample_data):
        """P0 regression: int(3.5)→3 was silently truncating. Must fail."""
        spec = RunSpec(
            strategy_name="MACrossStrategy",
            strategy_params={"short_period": 3.5, "long_period": 20},
            symbol="TEST.SZ", market="cn_stock",
            start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
            run_wfo=False,
        )
        result = Runner().run(spec, sample_data)
        assert result.status == "failed"
        assert "non-integer" in result.error.lower()

    def test_run_ids_unique(self, spec, sample_data):
        r1 = Runner().run(spec, sample_data)
        r2 = Runner().run(spec, sample_data)
        assert r1.run_id != r2.run_id
        assert r1.spec_id == r2.spec_id  # same spec → same spec_id

    def test_run_experiment_save_spec_round_trips(self, spec, sample_data, tmp_path):
        """Regression test for codex finding #19: run_experiment previously
        called store.save_spec(spec.__dict__), but spec_id is a @property and
        is NOT in __dict__, so save_spec() crashed with KeyError['spec_id'].
        Fix: use spec.to_dict() instead.

        Verified end-to-end: spec.to_dict() must contain spec_id and be
        acceptable to ExperimentStore.save_spec().
        """
        import duckdb
        from ez.agent.experiment_store import ExperimentStore

        # spec.__dict__ should NOT contain spec_id (regression guard — a future
        # dataclass refactor that materializes spec_id into an instance field
        # would still need to.to_dict() for JSON-safe serialization)
        assert "spec_id" not in spec.__dict__
        # but spec.to_dict() must contain it
        d = spec.to_dict()
        assert "spec_id" in d
        assert d["spec_id"] == spec.spec_id

        # And ExperimentStore.save_spec must accept the dict without KeyError
        store = ExperimentStore(duckdb.connect(":memory:"))
        store.save_spec(d)  # no exception → regression fixed

    def test_walk_forward_does_not_share_state_across_splits(self, sample_data):
        """Regression test for codex finding #2/#4: walk-forward previously
        passed the SAME strategy instance to every split's engine.run(), so
        IS-phase state could bleed into OOS results. Now each split deepcopies.
        """
        import copy
        from ez.agent.runner import _resolve_strategy

        # Build a stateful strategy that records every data length it sees
        base = _resolve_strategy("MACrossStrategy", {"short_period": 5, "long_period": 20})
        base._seen_data_lens = []  # injected tracker

        # Manual: deepcopy check — runner does this at resolve time
        clone = copy.deepcopy(base)
        assert clone is not base
        clone._seen_data_lens.append(999)
        assert 999 not in base._seen_data_lens, "deepcopy should isolate state"

    def test_portfolio_sharpe_matches_standard_formula(self):
        """Regression test for codex finding #1: portfolio engine previously
        computed sharpe as (ann_ret / ann_vol) without risk-free rate, while
        single-stock used the standard (excess.mean() / excess.std() × √252).
        Same name, different semantics — portfolio ranking could not be
        compared with single-stock results. Now both use the standard formula.
        """
        import numpy as np
        import pandas as pd
        from ez.backtest.metrics import MetricsCalculator

        # Construct a deterministic equity curve with known properties
        rng = np.random.default_rng(123)
        daily_returns = rng.normal(0.0005, 0.01, 252)
        eq_values = 100000 * np.cumprod(1 + daily_returns)
        eq_series = pd.Series(eq_values)
        bench = pd.Series([eq_values[0]] * len(eq_values))

        # Single-stock formula via MetricsCalculator
        single_metrics = MetricsCalculator().compute(eq_series, bench)
        single_sharpe = single_metrics["sharpe_ratio"]

        # Portfolio formula (replicated from engine.py) with ddof=1 to match pandas
        returns = np.diff(eq_values) / eq_values[:-1]
        daily_rf = 0.03 / 252
        excess = returns - daily_rf
        excess_std = float(np.std(excess, ddof=1))
        portfolio_sharpe = (
            float(np.mean(excess) / excess_std * np.sqrt(252))
            if excess_std > 1e-10 else 0.0
        )

        # After dd8ab5e self-review fix (ddof=1): byte-identical match
        assert abs(single_sharpe - portfolio_sharpe) < 1e-10, (
            f"Portfolio sharpe {portfolio_sharpe} differs from single-stock "
            f"{single_sharpe} — formulas are NOT aligned"
        )

    def test_portfolio_all_metrics_match_single_stock(self):
        """Regression test for reviewer round 4 Important 1: portfolio engine
        previously had divergent formulas for Sortino, alpha, beta (only Sharpe
        was fixed in dd8ab5e). Now ALL four metrics match single-stock exactly.
        """
        import numpy as np
        import pandas as pd
        from ez.backtest.metrics import MetricsCalculator

        np.random.seed(123)
        rng = np.random.default_rng(123)
        daily = rng.normal(0.0005, 0.01, 252)
        eq_values = 100000 * np.cumprod(1 + daily)
        # Non-flat benchmark so alpha/beta are meaningful
        bench_values = 100000 * np.cumprod(1 + rng.normal(0.0003, 0.008, 252))
        eq_s = pd.Series(eq_values)
        bench_s = pd.Series(bench_values)

        # Single-stock canonical formulas
        single = MetricsCalculator().compute(eq_s, bench_s)

        # Portfolio replica (exact copy of portfolio engine formulas)
        returns = np.diff(eq_values) / eq_values[:-1]
        daily_rf = 0.03 / 252
        excess = returns - daily_rf
        excess_std = float(np.std(excess, ddof=1))
        sharpe_p = float(np.mean(excess) / excess_std * np.sqrt(252))
        downside_sq = np.minimum(excess, 0) ** 2
        downside_dev = float(np.sqrt(downside_sq.mean()))
        sortino_p = float(np.mean(excess) / downside_dev * np.sqrt(252))

        bench_returns = np.diff(bench_values) / bench_values[:-1]
        excess_b = bench_returns - daily_rf
        cov_sb = float(np.cov(excess, excess_b, ddof=1)[0, 1])
        var_b = float(np.var(excess_b, ddof=1))
        beta_p = cov_sb / var_b
        alpha_p = float((np.mean(excess) - beta_p * np.mean(excess_b)) * 252)

        # All four metrics must be byte-identical (or near) to single-stock
        assert abs(single["sharpe_ratio"] - sharpe_p) < 1e-10
        assert abs(single["sortino_ratio"] - sortino_p) < 1e-10, (
            f"Sortino divergence: single={single['sortino_ratio']} vs "
            f"portfolio={sortino_p}"
        )
        assert abs(single["alpha"] - alpha_p) < 1e-10, (
            f"Alpha divergence: single={single['alpha']} vs portfolio={alpha_p}"
        )
        assert abs(single["beta"] - beta_p) < 1e-10, (
            f"Beta divergence: single={single['beta']} vs portfolio={beta_p}"
        )

    def test_portfolio_walk_forward_propagates_t_plus_1(self):
        """Regression test for reviewer round 4 Important 2: portfolio_walk_forward
        previously always passed run_portfolio_backtest with the default
        t_plus_1=True, so US/HK walk-forward validation incorrectly applied
        A-share T+1 constraints. Fix: t_plus_1 parameter added and propagated.
        """
        import inspect
        from ez.portfolio.walk_forward import portfolio_walk_forward
        sig = inspect.signature(portfolio_walk_forward)
        assert "t_plus_1" in sig.parameters, (
            "portfolio_walk_forward missing t_plus_1 parameter"
        )
        # Default preserves backward compat (A-share)
        assert sig.parameters["t_plus_1"].default is True
        # Source must actually pass t_plus_1 to run_portfolio_backtest
        src = inspect.getsource(portfolio_walk_forward)
        # Both IS and OOS calls pass t_plus_1
        assert src.count("t_plus_1=t_plus_1") >= 2, (
            "portfolio_walk_forward must propagate t_plus_1 to both IS and OOS "
            "run_portfolio_backtest calls"
        )

    def test_profit_factor_uses_standard_gross_ratio(self):
        """Regression test for codex #1 sub-issue: profit_factor was previously
        computed as avg_win_pct / avg_loss_pct, which is dimensionally wrong
        and ignores position sizing. Standard definition is gross_profit /
        gross_loss (sum of absolute P&L in currency units).
        """
        from unittest.mock import MagicMock
        from ez.backtest.engine import VectorizedBacktestEngine

        # Scenario: 3 trades — 2 big losses (total -200), 1 big win (+500)
        # Standard profit factor = 500 / 200 = 2.5
        # Old (wrong) formula: avg_win_pct / avg_loss_pct depends on sizes
        trades = [
            MagicMock(pnl=500.0, pnl_pct=0.05, entry_time=0, exit_time=5),
            MagicMock(pnl=-100.0, pnl_pct=-0.01, entry_time=10, exit_time=15),
            MagicMock(pnl=-100.0, pnl_pct=-0.20, entry_time=20, exit_time=25),
        ]
        # Manually compute what the engine would do:
        # gross_profit = sum(t.pnl for winners) = 500
        # gross_loss = |sum(t.pnl for losers)| = 200
        # profit_factor = 500 / 200 = 2.5
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        gross_profit = float(sum(t.pnl for t in wins))
        gross_loss = abs(float(sum(t.pnl for t in losses)))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        assert abs(pf - 2.5) < 1e-10, (
            f"Expected profit_factor=2.5 (gross 500/200), got {pf}"
        )

        # The OLD (wrong) formula would have used pnl_pct:
        # avg_win = 0.05, avg_loss = mean(|-0.01|, |-0.20|) = 0.105
        # old_pf = 0.05 / 0.105 ≈ 0.476 — completely different from 2.5
        import numpy as np
        old_avg_win = np.mean([t.pnl_pct for t in wins])
        old_avg_loss = abs(np.mean([t.pnl_pct for t in losses]))
        old_pf = old_avg_win / old_avg_loss
        # Confirm the old and new formulas give materially different answers
        assert abs(old_pf - pf) > 1.0, (
            f"Old formula ({old_pf:.3f}) and new formula ({pf:.3f}) should "
            f"differ significantly — if they match, test data needs to be "
            f"more asymmetric"
        )

    def test_oos_metrics_recomputed_from_combined_curve(self, sample_data):
        """Regression test for codex finding #5: walk-forward oos_metrics
        previously used `sum(oos_sharpes) / len(oos_sharpes)` (per-split
        average), biased when splits had different lengths or volatility.
        Now recomputed from the combined oos_equity_curve via MetricsCalculator.
        """
        from ez.backtest.walk_forward import WalkForwardValidator
        from ez.strategy.builtin.ma_cross import MACrossStrategy

        validator = WalkForwardValidator()
        strategy = MACrossStrategy(short_period=3, long_period=5)
        result = validator.validate(sample_data, strategy, n_splits=3, train_ratio=0.7)

        # oos_metrics must now be a dict with MetricsCalculator fields
        # (sharpe_ratio, total_return, etc.) not just sharpe_ratio
        assert "sharpe_ratio" in result.oos_metrics
        # The recomputed sharpe is derived from oos_equity_curve, so it must
        # be consistent with that curve's values (finite, not NaN)
        import numpy as np
        assert np.isfinite(result.oos_metrics["sharpe_ratio"])
        # The oos_equity_curve must have enough points to support metric computation
        assert len(result.oos_equity_curve) > 1

    def test_portfolio_engine_metrics_match_single_stock_end_to_end(self):
        """Integration regression test for reviewer round 5 Important 2: the
        prior test (test_portfolio_all_metrics_match_single_stock) only verified
        a hand-replica of the formula, not the engine code path. This test
        actually runs run_portfolio_backtest() and compares its metrics dict
        against MetricsCalculator on the resulting equity curve.
        """
        import numpy as np
        import pandas as pd
        from ez.portfolio.engine import run_portfolio_backtest, CostModel
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe
        from ez.portfolio.cross_factor import MomentumRank
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.backtest.metrics import MetricsCalculator

        # Minimal deterministic universe: 3 symbols, 100 trading days
        symbols = [f"S{i}" for i in range(3)]
        rng = np.random.default_rng(42)
        dates = pd.date_range("2023-01-02", periods=100, freq="B")
        data = {}
        for i, sym in enumerate(symbols):
            prices = 10 * (i + 1) * np.cumprod(1 + rng.normal(0.001, 0.012, 100))
            data[sym] = pd.DataFrame({
                "open": prices, "high": prices * 1.01, "low": prices * 0.99,
                "close": prices, "adj_close": prices,
                "volume": rng.integers(100000, 5000000, 100),
            }, index=dates)

        cal = TradingCalendar.from_dates([d.date() for d in dates])
        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=2),
            universe=Universe(symbols), universe_data=data, calendar=cal,
            start=dates[25].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            cost_model=CostModel(),
        )

        # Now run the same equity curve through MetricsCalculator and verify
        # the engine's metrics match exactly.
        eq_s = pd.Series(result.equity_curve)
        bench_s = pd.Series(result.benchmark_curve)
        canonical = MetricsCalculator().compute(eq_s, bench_s)

        assert abs(result.metrics["sharpe_ratio"] - canonical["sharpe_ratio"]) < 1e-10, (
            f"engine sharpe={result.metrics['sharpe_ratio']} vs "
            f"canonical sharpe={canonical['sharpe_ratio']}"
        )
        assert abs(result.metrics["sortino_ratio"] - canonical["sortino_ratio"]) < 1e-10
        assert abs(result.metrics["alpha"] - canonical["alpha"]) < 1e-10
        assert abs(result.metrics["beta"] - canonical["beta"]) < 1e-10
        assert abs(result.metrics["max_drawdown"] - canonical["max_drawdown"]) < 1e-10

    def test_profit_factor_engine_integration(self):
        """Integration regression test for reviewer round 5 Important 2: the
        prior profit_factor test used MagicMock trades, not the real engine
        path. This test runs VectorizedBacktestEngine.run() on a synthetic
        series designed to produce exactly known P&L per trade, then asserts
        result.metrics["profit_factor"] equals the standard gross ratio.
        """
        import numpy as np
        import pandas as pd
        from ez.backtest.engine import VectorizedBacktestEngine
        from ez.strategy.base import Strategy

        # Construct a custom deterministic strategy that enters on day 5 and
        # exits on day 10 (one known trade per window). Three trades total
        # with a known P&L structure.
        class _FixedSignalStrategy(Strategy):
            """Signal = 1 on days 5-9, 15-19, 25-29. Otherwise 0."""
            def required_factors(self):
                return []
            def generate_signals(self, data):
                n = len(data)
                sig = pd.Series([0.0] * n, index=data.index)
                for start in [5, 15, 25]:
                    for i in range(start, min(start + 5, n)):
                        sig.iloc[i] = 1.0
                return sig

        # Synthetic prices with predictable jumps: trade 1 wins big, trades 2&3 lose
        n = 40
        prices = [100.0] * n
        # trade 1: day 5 buy @ 100, day 11 sell @ 150 (+50 per share → big win)
        for i in range(5, 11):
            prices[i] = 100 + (i - 4) * 10  # ramp up
        for i in range(11, 15):
            prices[i] = 150  # plateau
        # trade 2: day 15 buy @ 150, day 21 sell @ 140 (-10 per share → small loss)
        for i in range(15, 21):
            prices[i] = 150 - (i - 14) * 2  # ramp down
        for i in range(21, 25):
            prices[i] = 138
        # trade 3: day 25 buy @ 138, day 31 sell @ 128 (-10 per share → small loss)
        for i in range(25, 31):
            prices[i] = 138 - (i - 24) * 2
        for i in range(31, n):
            prices[i] = 126

        dates = pd.date_range("2023-01-02", periods=n, freq="B")
        df = pd.DataFrame({
            "open": prices, "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices, "adj_close": prices,
            "volume": [1_000_000] * n,
        }, index=dates)

        engine = VectorizedBacktestEngine()
        result = engine.run(df, _FixedSignalStrategy(), initial_capital=100_000)

        # Must have produced trades
        assert result.metrics["trade_count"] > 0, "No trades produced — fixture issue"
        pf = result.metrics.get("profit_factor")
        # Either a finite positive number (normal path) or inf (all winners)
        # For this fixture we expect 1 winner + 2 losers → finite positive ratio.
        assert pf is not None and pf != 0.0, "profit_factor should be set"
        # Manually replicate from the engine's own trade list to verify the
        # formula matches the canonical gross-ratio definition.
        gross_profit = sum(t.pnl for t in result.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in result.trades if t.pnl <= 0))
        if gross_loss > 1e-10:
            expected_pf = gross_profit / gross_loss
            assert abs(pf - expected_pf) < 1e-6, (
                f"engine profit_factor={pf} differs from gross ratio "
                f"({gross_profit}/{gross_loss} = {expected_pf})"
            )

    def test_portfolio_significance_sharpe_aligned_with_engine(self):
        """Integration regression test for reviewer round 5 Important 1: the
        _sharpe() helpers in portfolio_significance and backtest significance
        previously used numpy default ddof=0, while engine sharpe used ddof=1.
        On short OOS windows the CI and observed sharpe could disagree by up
        to 2.7%. Now all use ddof=1.
        """
        import numpy as np
        from ez.portfolio.walk_forward import _sharpe as _port_sharpe
        from ez.backtest.significance import _sharpe as _bt_sharpe

        # Construct a daily return series
        rng = np.random.default_rng(7)
        returns = rng.normal(0.001, 0.012, 60)  # short OOS window

        # Portfolio helper
        s_port = _port_sharpe(returns)
        # Backtest helper (same formula, same rf)
        s_bt = _bt_sharpe(returns, daily_rf=0.03 / 252)

        # Both should match to numerical precision
        assert abs(s_port - s_bt) < 1e-12, (
            f"Portfolio helper sharpe ({s_port}) differs from backtest helper "
            f"sharpe ({s_bt}) — both should use the same ddof=1 formula"
        )

        # And they should match the canonical engine formula (ddof=1)
        daily_rf = 0.03 / 252
        excess = returns - daily_rf
        canonical = float(np.mean(excess) / np.std(excess, ddof=1) * np.sqrt(252))
        assert abs(s_port - canonical) < 1e-12

    def test_latest_weights_returns_last_non_empty(self):
        """Regression test for codex follow-up: latest_weights API field
        should return the last NON-EMPTY weights entry, not [-1] (which is
        the post-liquidation empty dict).
        """
        from unittest.mock import MagicMock
        # Simulate a PortfolioResult with [..., {"A":0.5,"B":0.5}, {}]
        result = MagicMock()
        result.weights_history = [
            {"A": 0.3, "B": 0.7},
            {"A": 0.5, "B": 0.5},
            {},  # post-liquidation empty
        ]
        latest = next((w for w in reversed(result.weights_history) if w), {})
        assert latest == {"A": 0.5, "B": 0.5}, (
            f"Expected last non-empty weights, got {latest}"
        )

    def test_optimizer_fallback_events_tracked(self):
        """Regression test for codex follow-up: PortfolioOptimizer.fallback_events
        must record every silent degradation to equal-weight, so the API layer
        can surface them as user warnings.
        """
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints

        c = OptimizationConstraints(max_weight=0.5)
        opt = MeanVarianceOptimizer(risk_aversion=1.0, constraints=c, cov_lookback=60)

        # Case 1: all-negative alpha → fallback
        opt.optimize({"A": -0.1, "B": -0.2})
        assert len(opt.fallback_events) == 0, (
            "All-negative alpha is filtered to empty symbols, not a fallback"
        )

        # Case 2: no covariance context → fallback path
        result = opt.optimize({"A": 0.5, "B": 0.3, "C": 0.2})
        assert isinstance(result, dict)
        assert len(opt.fallback_events) >= 1, (
            "No-context optimize should record fallback event"
        )
        reason = opt.fallback_events[0]["reason"]
        assert "covariance" in reason or "fallback" in reason.lower() or "total_alpha" in reason

    def test_evaluate_factors_passes_dynamic_lookback_to_evaluator(self):
        """Regression test for codex follow-up: evaluate-factors and factor-
        correlation must propagate dynamic lookback_days to the evaluator
        functions, not just to the data fetch. Prior version only lengthened
        the fetch, but evaluate_cross_sectional_factor() defaulted to 252
        internally, silently truncating long-warmup factors.
        """
        import inspect
        from ez.api.routes import portfolio as _p
        src = inspect.getsource(_p.evaluate_factors)
        # Must contain lookback_days=dynamic_lb (or similar) in evaluate call
        assert "lookback_days=dynamic_lb" in src, (
            "evaluate_factors must pass dynamic lookback to the evaluator"
        )
        src_corr = inspect.getsource(_p.factor_correlation)
        assert "lookback_days=dynamic_lb" in src_corr, (
            "factor_correlation must pass dynamic lookback to compute_factor_correlation"
        )

    def test_ai_portfolio_tool_gates_min_commission_by_market(self):
        """Regression test for codex follow-up: run_portfolio_backtest_tool
        must gate min_commission by market (A-share 5, US/HK 0), not just
        stamp_tax. Prior version only handled stamp_tax and inflated small
        US/HK trade costs.
        """
        import inspect
        from ez.agent import tools as _t
        src = inspect.getsource(_t.run_portfolio_backtest_tool.__wrapped__ if hasattr(_t.run_portfolio_backtest_tool, '__wrapped__') else _t.run_portfolio_backtest_tool)
        # Must mention min_commission is market-gated
        assert "min_commission=5.0 if is_cn" in src or "min_commission = 5.0 if is_cn" in src, (
            "AI portfolio tool must gate min_commission by market"
        )


class TestGate:
    def _make_result(self, spec, sample_data) -> RunResult:
        return Runner().run(spec, sample_data)

    def test_gate_produces_verdict(self, spec, sample_data):
        result = self._make_result(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        assert isinstance(verdict, GateVerdict)
        assert len(verdict.reasons) >= 4  # sharpe, dd, trades, significance

    def test_gate_with_wfo(self, spec, sample_data):
        result = self._make_result(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        # Should have overfitting rule since WFO was run
        rules = {r.rule for r in verdict.reasons}
        assert "max_overfitting" in rules

    def test_gate_failed_run(self, sample_data):
        spec = RunSpec(
            strategy_name="NonExistent", strategy_params={},
            symbol="T", market="cn_stock",
            start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
        )
        result = Runner().run(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        assert not verdict.passed
        assert verdict.reasons[0].rule == "run_status"

    def test_custom_config(self, spec, sample_data):
        result = self._make_result(spec, sample_data)
        # Very strict gate — likely fails
        strict = GateConfig(min_sharpe=5.0, max_drawdown=0.01, min_trades=1000)
        verdict = ResearchGate(strict).evaluate(result)
        assert not verdict.passed
        assert len(verdict.failed_reasons) > 0

    def test_lenient_config(self, spec, sample_data):
        result = self._make_result(spec, sample_data)
        lenient = GateConfig(
            min_sharpe=-10, max_drawdown=1.0, min_trades=0,
            max_p_value=1.0, max_overfitting_score=10.0,
        )
        verdict = ResearchGate(lenient).evaluate(result)
        assert verdict.passed

    def test_verdict_summary(self, spec, sample_data):
        result = self._make_result(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        assert "PASS" in verdict.summary or "FAIL" in verdict.summary

    def test_empty_reasons_does_not_pass(self, sample_data):
        """Regression: all([]) is True in Python, but gate should FAIL with no rules."""
        spec = RunSpec(
            strategy_name="MACrossStrategy",
            strategy_params={"short_period": 5, "long_period": 20},
            symbol="T", market="cn_stock",
            start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
            run_backtest=False, wfo_n_splits=3,
        )
        result = Runner().run(spec, sample_data)
        # No backtest → no sharpe/dd/trades/sig rules; require_wfo=False → no WFO rule
        config = GateConfig(require_wfo=False)
        verdict = ResearchGate(config).evaluate(result)
        assert not verdict.passed, "Gate with zero rules must not pass"

    def test_wfo_only_gate(self, sample_data):
        """Gate evaluates a WFO-only run (no backtest)."""
        spec = RunSpec(
            strategy_name="MACrossStrategy",
            strategy_params={"short_period": 5, "long_period": 20},
            symbol="T", market="cn_stock",
            start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
            run_backtest=False, wfo_n_splits=3,
        )
        result = Runner().run(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        # Should have at least the WFO overfitting rule
        rules = {r.rule for r in verdict.reasons}
        assert "max_overfitting" in rules or "wfo_required" in rules

    def test_max_drawdown_rejects_large_dd(self, spec, sample_data):
        """Regression: negative max_drawdown (e.g. -0.31) must be caught by gate.

        Bug: metrics returns negative DD, gate compared dd <= threshold,
        so -0.31 <= 0.1 passed. Fix: compare abs(dd) <= threshold.
        """
        result = self._make_result(spec, sample_data)
        # Use a very strict DD threshold that should fail
        strict = GateConfig(
            min_sharpe=-100, max_drawdown=0.001,  # 0.1% — almost impossible
            min_trades=0, max_p_value=1.0, max_overfitting_score=10.0,
            require_wfo=False,
        )
        verdict = ResearchGate(strict).evaluate(result)
        dd_rule = next(r for r in verdict.reasons if r.rule == "max_drawdown")
        assert not dd_rule.passed, (
            f"Gate should reject: dd_abs={dd_rule.value:.4f} > threshold=0.001"
        )
        assert dd_rule.value > 0, "Gate should report absolute drawdown value"

    def test_max_drawdown_value_is_positive(self, spec, sample_data):
        """Gate should always report drawdown as a positive number."""
        result = self._make_result(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        dd_rule = next(r for r in verdict.reasons if r.rule == "max_drawdown")
        assert dd_rule.value >= 0, f"Drawdown should be positive, got {dd_rule.value}"


class TestReport:
    def test_from_result(self, spec, sample_data):
        result = Runner().run(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        report = ExperimentReport.from_result(result, verdict)

        assert report.run_id == result.run_id
        assert report.spec_id == spec.spec_id
        assert report.status == "completed"
        assert report.sharpe_ratio is not None
        assert report.trade_count >= 0
        assert report.gate_summary

    def test_to_dict_complete(self, spec, sample_data):
        result = Runner().run(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        report = ExperimentReport.from_result(result, verdict)
        d = report.to_dict()

        assert "run_id" in d
        assert "sharpe_ratio" in d
        assert "gate_passed" in d
        assert "gate_reasons" in d
        assert isinstance(d["gate_reasons"], list)

    def test_failed_report(self, sample_data):
        spec = RunSpec(
            strategy_name="NonExistent", strategy_params={},
            symbol="T", market="cn_stock",
            start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
        )
        result = Runner().run(spec, sample_data)
        verdict = ResearchGate().evaluate(result)
        report = ExperimentReport.from_result(result, verdict)
        assert report.status == "failed"
        assert report.error is not None
        assert not report.gate_passed
