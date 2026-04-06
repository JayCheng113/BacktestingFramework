"""Tests for DeployGate — 10-check non-bypassable deployment gate."""
from __future__ import annotations

import json

import pytest

from ez.agent.gates import GateReason, GateVerdict
from ez.live.deploy_gate import DeployGate, DeployGateConfig
from ez.live.deployment_spec import DeploymentSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(**overrides) -> DeploymentSpec:
    defaults = dict(
        strategy_name="TopN",
        strategy_params={"top_n": 5},
        symbols=("A", "B", "C", "D", "E", "F"),
        market="cn_stock",
        freq="monthly",
    )
    defaults.update(overrides)
    return DeploymentSpec(**defaults)


_SENTINEL = object()


def _make_run(
    *,
    metrics: dict | None | object = _SENTINEL,
    dates: list | None | object = _SENTINEL,
    rebalance_weights: list | None | object = _SENTINEL,
) -> dict:
    """Build a fake portfolio_runs row (already JSON-parsed, like PortfolioStore.get_run())."""
    if metrics is _SENTINEL:
        metrics = {
            "sharpe_ratio": 1.2,
            "max_drawdown": -0.15,
            "trade_count": 50,
        }
    if dates is _SENTINEL:
        dates = [f"2022-01-{str(i).zfill(2)}" for i in range(4, 32)] \
              + [f"2022-02-{str(i).zfill(2)}" for i in range(1, 29)] \
              + [f"2022-{str(m).zfill(2)}-01" for m in range(3, 13)] * 42  # >504
    if rebalance_weights is _SENTINEL:
        rebalance_weights = [
            {"date": "2022-02-01", "weights": {"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.1, "F": 0.1}},
            {"date": "2022-03-01", "weights": {"A": 0.15, "B": 0.25, "C": 0.2, "D": 0.2, "E": 0.1, "F": 0.1}},
        ]
    return {
        "run_id": "run_001",
        "strategy_name": "TopN",
        "strategy_params": {"top_n": 5},
        "symbols": ["A", "B", "C", "D", "E", "F"],
        "start_date": "2022-01-04",
        "end_date": "2024-01-03",
        "freq": "monthly",
        "initial_cash": 1_000_000,
        "metrics": metrics,
        "equity_curve": [1e6, 1.01e6, 1.02e6],
        "trade_count": 50,
        "rebalance_count": 24,
        "created_at": "2024-01-04 00:00:00",
        "rebalance_weights": rebalance_weights,
        "trades": [],
        "config": {},
        "warnings": [],
        "dates": dates,
        "weights_history": [],
    }


def _good_wf_metrics() -> dict:
    return {"p_value": 0.01, "overfitting_score": 0.1}


class _MockPortfolioStore:
    """Mock that returns a dict just like PortfolioStore.get_run()."""

    def __init__(self, runs: dict[str, dict | None] | None = None):
        self._runs = runs or {}

    def get_run(self, run_id: str) -> dict | None:
        return self._runs.get(run_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeployGateConfig:
    def test_defaults(self):
        cfg = DeployGateConfig()
        assert cfg.min_sharpe == 0.5
        assert cfg.max_drawdown == 0.25
        assert cfg.min_trades == 20
        assert cfg.max_p_value == 0.05
        assert cfg.max_overfitting_score == 0.3
        assert cfg.min_backtest_days == 504
        assert cfg.require_wfo is True
        assert cfg.min_symbols == 5
        assert cfg.max_concentration == 0.4

    def test_custom_values(self):
        cfg = DeployGateConfig(min_sharpe=1.0, max_drawdown=0.1)
        assert cfg.min_sharpe == 1.0
        assert cfg.max_drawdown == 0.1


class TestSourceRunExists:
    def test_missing_run_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        store = _MockPortfolioStore()  # empty store
        verdict = gate.evaluate(spec, "nonexistent", store, _good_wf_metrics())
        assert not verdict.passed
        assert len(verdict.reasons) == 1
        assert verdict.reasons[0].rule == "source_run_exists"
        assert not verdict.reasons[0].passed

    def test_none_run_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        store = _MockPortfolioStore({"run_001": None})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        assert not verdict.passed
        assert verdict.reasons[0].rule == "source_run_exists"


class TestMinSharpe:
    def test_below_threshold_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"sharpe_ratio": 0.3, "max_drawdown": -0.1, "trade_count": 50})
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        sharpe_reason = next(r for r in verdict.reasons if r.rule == "min_sharpe")
        assert not sharpe_reason.passed
        assert sharpe_reason.value == 0.3
        assert sharpe_reason.threshold == 0.5

    def test_above_threshold_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"sharpe_ratio": 0.8, "max_drawdown": -0.1, "trade_count": 50})
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        sharpe_reason = next(r for r in verdict.reasons if r.rule == "min_sharpe")
        assert sharpe_reason.passed

    def test_missing_sharpe_defaults_zero(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"max_drawdown": -0.1, "trade_count": 50})
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        sharpe_reason = next(r for r in verdict.reasons if r.rule == "min_sharpe")
        assert not sharpe_reason.passed
        assert sharpe_reason.value == 0


class TestMaxDrawdown:
    def test_above_threshold_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"sharpe_ratio": 1.0, "max_drawdown": -0.35, "trade_count": 50})
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        dd_reason = next(r for r in verdict.reasons if r.rule == "max_drawdown")
        assert not dd_reason.passed
        assert dd_reason.value == pytest.approx(0.35)

    def test_positive_drawdown_value(self):
        """max_drawdown stored as positive should also work."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"sharpe_ratio": 1.0, "max_drawdown": 0.35, "trade_count": 50})
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        dd_reason = next(r for r in verdict.reasons if r.rule == "max_drawdown")
        assert not dd_reason.passed
        assert dd_reason.value == pytest.approx(0.35)

    def test_within_threshold_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"sharpe_ratio": 1.0, "max_drawdown": -0.2, "trade_count": 50})
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        dd_reason = next(r for r in verdict.reasons if r.rule == "max_drawdown")
        assert dd_reason.passed


class TestMinTrades:
    def test_below_threshold_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"sharpe_ratio": 1.0, "max_drawdown": -0.1, "trade_count": 5})
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        trades_reason = next(r for r in verdict.reasons if r.rule == "min_trades")
        assert not trades_reason.passed
        assert trades_reason.value == 5

    def test_above_threshold_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"sharpe_ratio": 1.0, "max_drawdown": -0.1, "trade_count": 25})
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        trades_reason = next(r for r in verdict.reasons if r.rule == "min_trades")
        assert trades_reason.passed


class TestMaxPValue:
    def test_above_threshold_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        wf = {"p_value": 0.2, "overfitting_score": 0.1}
        verdict = gate.evaluate(spec, "run_001", store, wf)
        p_reason = next(r for r in verdict.reasons if r.rule == "max_p_value")
        assert not p_reason.passed
        assert p_reason.value == 0.2

    def test_below_threshold_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        wf = {"p_value": 0.03, "overfitting_score": 0.1}
        verdict = gate.evaluate(spec, "run_001", store, wf)
        p_reason = next(r for r in verdict.reasons if r.rule == "max_p_value")
        assert p_reason.passed

    def test_missing_p_value_defaults_to_1(self):
        """Missing p_value in wf_metrics defaults to 1.0 -> fail."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        wf = {"overfitting_score": 0.1}
        verdict = gate.evaluate(spec, "run_001", store, wf)
        p_reason = next(r for r in verdict.reasons if r.rule == "max_p_value")
        assert not p_reason.passed
        assert p_reason.value == 1.0


class TestMaxOverfittingScore:
    def test_above_threshold_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        wf = {"p_value": 0.01, "overfitting_score": 0.6}
        verdict = gate.evaluate(spec, "run_001", store, wf)
        of_reason = next(r for r in verdict.reasons if r.rule == "max_overfitting_score")
        assert not of_reason.passed
        assert of_reason.value == 0.6

    def test_below_threshold_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        wf = {"p_value": 0.01, "overfitting_score": 0.2}
        verdict = gate.evaluate(spec, "run_001", store, wf)
        of_reason = next(r for r in verdict.reasons if r.rule == "max_overfitting_score")
        assert of_reason.passed


class TestMinBacktestDays:
    def test_too_short_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(dates=["2022-01-04", "2022-01-05", "2022-01-06"])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        days_reason = next(r for r in verdict.reasons if r.rule == "min_backtest_days")
        assert not days_reason.passed
        assert days_reason.value == 3

    def test_long_enough_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(dates=[f"d{i}" for i in range(510)])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        days_reason = next(r for r in verdict.reasons if r.rule == "min_backtest_days")
        assert days_reason.passed
        assert days_reason.value == 510

    def test_empty_dates_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(dates=[])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        days_reason = next(r for r in verdict.reasons if r.rule == "min_backtest_days")
        assert not days_reason.passed
        assert days_reason.value == 0

    def test_dates_as_json_string(self):
        """Defensive: dates stored as raw JSON string should be parsed."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(dates=[f"d{i}" for i in range(510)])
        # Simulate raw JSON string (pre-parse did not happen)
        run["dates"] = json.dumps(run["dates"])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        days_reason = next(r for r in verdict.reasons if r.rule == "min_backtest_days")
        assert days_reason.passed


class TestMinSymbols:
    def test_too_few_fails(self):
        gate = DeployGate()
        spec = _make_spec(symbols=("A", "B"))
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        sym_reason = next(r for r in verdict.reasons if r.rule == "min_symbols")
        assert not sym_reason.passed
        assert sym_reason.value == 2

    def test_enough_passes(self):
        gate = DeployGate()
        spec = _make_spec(symbols=("A", "B", "C", "D", "E"))
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        sym_reason = next(r for r in verdict.reasons if r.rule == "min_symbols")
        assert sym_reason.passed


class TestMaxConcentration:
    def test_too_high_fails(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(rebalance_weights=[
            {"date": "2022-02-01", "weights": {"A": 0.6, "B": 0.4}},
        ])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        conc_reason = next(r for r in verdict.reasons if r.rule == "max_concentration")
        assert not conc_reason.passed
        assert conc_reason.value == pytest.approx(0.6)

    def test_within_limit_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(rebalance_weights=[
            {"date": "2022-02-01", "weights": {"A": 0.3, "B": 0.3, "C": 0.4}},
            {"date": "2022-03-01", "weights": {"A": 0.25, "B": 0.35, "C": 0.4}},
        ])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        conc_reason = next(r for r in verdict.reasons if r.rule == "max_concentration")
        assert conc_reason.passed

    def test_empty_rebalance_weights_fails(self):
        """No rebalance data -> concentration defaults to 1.0 -> fail."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(rebalance_weights=[])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        conc_reason = next(r for r in verdict.reasons if r.rule == "max_concentration")
        assert not conc_reason.passed
        assert conc_reason.value == 1.0

    def test_plain_dict_fallback_format(self):
        """Defensive: entries without 'weights' key treated as raw weight dicts."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(rebalance_weights=[
            {"A": 0.3, "B": 0.3, "C": 0.4},
        ])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        conc_reason = next(r for r in verdict.reasons if r.rule == "max_concentration")
        assert conc_reason.passed
        assert conc_reason.value == pytest.approx(0.4)

    def test_rebalance_weights_as_json_string(self):
        """Defensive: raw JSON string should be parsed."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(rebalance_weights=[
            {"date": "2022-02-01", "weights": {"A": 0.6, "B": 0.4}},
        ])
        run["rebalance_weights"] = json.dumps(run["rebalance_weights"])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        conc_reason = next(r for r in verdict.reasons if r.rule == "max_concentration")
        assert not conc_reason.passed
        assert conc_reason.value == pytest.approx(0.6)

    def test_max_across_all_rebalances(self):
        """Gate should find the maximum weight across ALL rebalance periods."""
        gate = DeployGate(DeployGateConfig(max_concentration=0.5))
        spec = _make_spec()
        run = _make_run(rebalance_weights=[
            {"date": "2022-02-01", "weights": {"A": 0.3, "B": 0.3, "C": 0.4}},
            {"date": "2022-03-01", "weights": {"A": 0.55, "B": 0.25, "C": 0.2}},
        ])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        conc_reason = next(r for r in verdict.reasons if r.rule == "max_concentration")
        assert not conc_reason.passed
        assert conc_reason.value == pytest.approx(0.55)


class TestRequireWfo:
    def test_no_wf_data_fails(self):
        """Default wf_metrics (p=1, overfit=1) means WFO not done -> fail."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        wf = {"p_value": 1.0, "overfitting_score": 1.0}
        verdict = gate.evaluate(spec, "run_001", store, wf)
        wfo_reason = next(r for r in verdict.reasons if r.rule == "require_wfo")
        assert not wfo_reason.passed

    def test_with_wf_data_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        wfo_reason = next(r for r in verdict.reasons if r.rule == "require_wfo")
        assert wfo_reason.passed

    def test_require_wfo_disabled(self):
        """When require_wfo=False, no require_wfo reason should appear."""
        gate = DeployGate(DeployGateConfig(require_wfo=False))
        spec = _make_spec()
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        wf = {"p_value": 1.0, "overfitting_score": 1.0}
        verdict = gate.evaluate(spec, "run_001", store, wf)
        wfo_reasons = [r for r in verdict.reasons if r.rule == "require_wfo"]
        assert len(wfo_reasons) == 0


class TestFreqValid:
    def test_invalid_freq_fails(self):
        gate = DeployGate()
        spec = _make_spec(freq="tick")
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        freq_reason = next(r for r in verdict.reasons if r.rule == "freq_valid")
        assert not freq_reason.passed

    def test_daily_passes(self):
        gate = DeployGate()
        spec = _make_spec(freq="daily")
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        freq_reason = next(r for r in verdict.reasons if r.rule == "freq_valid")
        assert freq_reason.passed

    def test_weekly_passes(self):
        gate = DeployGate()
        spec = _make_spec(freq="weekly")
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        freq_reason = next(r for r in verdict.reasons if r.rule == "freq_valid")
        assert freq_reason.passed

    def test_monthly_passes(self):
        gate = DeployGate()
        spec = _make_spec(freq="monthly")
        run = _make_run()
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        freq_reason = next(r for r in verdict.reasons if r.rule == "freq_valid")
        assert freq_reason.passed


class TestAllPass:
    def test_everything_passes(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(
            metrics={"sharpe_ratio": 1.2, "max_drawdown": -0.15, "trade_count": 50},
            dates=[f"d{i}" for i in range(510)],
            rebalance_weights=[
                {"date": "2022-02-01", "weights": {"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.15, "E": 0.15, "F": 0.1}},
            ],
        )
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        assert verdict.passed, f"Failed reasons: {[r.rule for r in verdict.failed_reasons]}"
        assert len(verdict.reasons) >= 10  # at least 10 checks

    def test_verdict_summary(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(
            metrics={"sharpe_ratio": 1.2, "max_drawdown": -0.15, "trade_count": 50},
            dates=[f"d{i}" for i in range(510)],
            rebalance_weights=[
                {"date": "2022-02-01", "weights": {"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.15, "E": 0.15, "F": 0.1}},
            ],
        )
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        assert "PASS" in verdict.summary
        n = len(verdict.reasons)
        assert f"{n}/{n}" in verdict.summary

    def test_failed_reasons_property(self):
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(
            metrics={"sharpe_ratio": 0.1, "max_drawdown": -0.5, "trade_count": 5},
            dates=[f"d{i}" for i in range(10)],
        )
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        assert not verdict.passed
        assert len(verdict.failed_reasons) > 0
        for fr in verdict.failed_reasons:
            assert not fr.passed


class TestEdgeCases:
    def test_metrics_as_json_string(self):
        """Defensive: metrics stored as raw JSON string should be parsed."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run(metrics={"sharpe_ratio": 1.0, "max_drawdown": -0.1, "trade_count": 50})
        run["metrics"] = json.dumps(run["metrics"])
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        sharpe_reason = next(r for r in verdict.reasons if r.rule == "min_sharpe")
        assert sharpe_reason.passed

    def test_none_metrics_defaults(self):
        """None metrics should default to empty dict -> all zero."""
        gate = DeployGate()
        spec = _make_spec()
        run = _make_run()
        run["metrics"] = None
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        sharpe_reason = next(r for r in verdict.reasons if r.rule == "min_sharpe")
        assert not sharpe_reason.passed

    def test_custom_config_thresholds(self):
        cfg = DeployGateConfig(min_sharpe=2.0, max_drawdown=0.1, min_trades=100)
        gate = DeployGate(cfg)
        spec = _make_spec()
        run = _make_run(
            metrics={"sharpe_ratio": 1.5, "max_drawdown": -0.15, "trade_count": 50},
            dates=[f"d{i}" for i in range(510)],
        )
        store = _MockPortfolioStore({"run_001": run})
        verdict = gate.evaluate(spec, "run_001", store, _good_wf_metrics())
        assert not verdict.passed
        # All three custom thresholds should fail
        sharpe_reason = next(r for r in verdict.reasons if r.rule == "min_sharpe")
        assert not sharpe_reason.passed
        dd_reason = next(r for r in verdict.reasons if r.rule == "max_drawdown")
        assert not dd_reason.passed
        trades_reason = next(r for r in verdict.reasons if r.rule == "min_trades")
        assert not trades_reason.passed
