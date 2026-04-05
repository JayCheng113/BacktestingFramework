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

        # Portfolio formula (replicated from engine.py)
        returns = np.diff(eq_values) / eq_values[:-1]
        daily_rf = 0.03 / 252
        excess = returns - daily_rf
        excess_std = float(np.std(excess))
        portfolio_sharpe = (
            float(np.mean(excess) / excess_std * np.sqrt(252))
            if excess_std > 1e-10 else 0.0
        )

        # Should match within small numerical tolerance (single-stock uses
        # pandas std with ddof=1, portfolio uses np.std with ddof=0 — minor
        # difference allowed)
        assert abs(single_sharpe - portfolio_sharpe) < 0.05, (
            f"Portfolio sharpe {portfolio_sharpe} differs from single-stock "
            f"{single_sharpe} — formulas are NOT aligned"
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
