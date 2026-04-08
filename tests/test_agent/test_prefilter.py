"""Tests for prefilter — F3."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ez.agent.candidate_search import ParamRange, SearchConfig, grid_search
from ez.agent.prefilter import PrefilterConfig, prefilter

import ez.strategy.builtin.ma_cross  # noqa: F401


@pytest.fixture
def sample_data():
    rng = np.random.default_rng(42)
    n = 500
    prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
    dates = pd.date_range("2020-01-03", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices * (1 + rng.normal(0, 0.002, n)),
        "high": prices * (1 + abs(rng.normal(0, 0.005, n))),
        "low": prices * (1 - abs(rng.normal(0, 0.005, n))),
        "close": prices, "adj_close": prices,
        "volume": rng.integers(100_000, 5_000_000, n),
    }, index=dates)


@pytest.fixture
def specs():
    config = SearchConfig(
        strategy_name="MACrossStrategy",
        param_ranges=[
            ParamRange("short_period", [3, 5]),
            ParamRange("long_period", [15, 20]),
        ],
        symbol="000001.SZ",
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
        run_wfo=True,
    )
    return grid_search(config)


class TestPrefilter:
    def test_returns_results_for_all_specs(self, specs, sample_data):
        results = prefilter(specs, sample_data)
        assert len(results) == len(specs)

    def test_each_result_has_metrics(self, specs, sample_data):
        results = prefilter(specs, sample_data)
        for r in results:
            assert r.sharpe is not None or not r.passed
            assert r.spec in specs

    def test_strict_config_filters_more(self, specs, sample_data):
        lenient = PrefilterConfig(min_sharpe=-10, max_drawdown=1.0, min_trades=0)
        strict = PrefilterConfig(min_sharpe=5.0, max_drawdown=0.01, min_trades=1000)
        lenient_results = prefilter(specs, sample_data, lenient)
        strict_results = prefilter(specs, sample_data, strict)
        lenient_pass = sum(1 for r in lenient_results if r.passed)
        strict_pass = sum(1 for r in strict_results if r.passed)
        assert lenient_pass >= strict_pass

    def test_failed_backtest_does_not_pass(self, sample_data):
        from ez.agent.run_spec import RunSpec
        spec = RunSpec(
            strategy_name="NonExistentStrategy",
            strategy_params={},
            symbol="000001.SZ", market="cn_stock",
            start_date=date(2020, 1, 1), end_date=date(2024, 12, 31),
        )
        results = prefilter([spec], sample_data)
        assert len(results) == 1
        assert results[0].passed is False
        assert "failed" in results[0].reason

    def test_prefilter_preserves_original_spec(self, specs, sample_data):
        """Pre-filter should preserve the original spec (with WFO settings)."""
        results = prefilter(specs, sample_data)
        for r in results:
            assert r.spec.run_wfo is True  # original spec had WFO enabled

    def test_nan_metrics_do_not_pass(self, sample_data):
        """NaN sharpe/drawdown/trades must fail pre-filter, not bypass it."""
        from unittest.mock import patch, MagicMock
        from ez.agent.run_spec import RunSpec

        spec = RunSpec(
            strategy_name="MACrossStrategy",
            strategy_params={"short_period": 5, "long_period": 20},
            symbol="000001.SZ", market="cn_stock",
            start_date=date(2020, 1, 1), end_date=date(2024, 12, 31),
        )

        # Mock a RunResult with NaN metrics
        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.backtest.metrics = {
            "sharpe_ratio": float("nan"),
            "max_drawdown": float("nan"),
            "trade_count": float("nan"),
        }
        mock_result.error = None

        with patch("ez.agent.prefilter.Runner") as MockRunner:
            MockRunner.return_value.run.return_value = mock_result
            results = prefilter([spec], sample_data, PrefilterConfig(min_sharpe=0.0))

        assert len(results) == 1
        assert results[0].passed is False

    def test_market_rules_params_preserved_in_quick_spec(self, sample_data):
        """Regression test for codex finding: prefilter previously rebuilt
        RunSpec with a hand-picked subset of fields, silently DROPPING
        use_market_rules / t_plus_1 / price_limit_pct / lot_size. A candidate
        could pass prefilter with no A-share rules, then fail the full run
        once rules were enforced — making gate verdicts inconsistent.

        Fix: uses dataclasses.replace() so every RunSpec field is preserved.
        """
        from unittest.mock import patch, MagicMock
        from ez.agent.run_spec import RunSpec

        # Spec with CUSTOM market-rule settings (not the dataclass defaults)
        original_spec = RunSpec(
            strategy_name="MACrossStrategy",
            strategy_params={"short_period": 5, "long_period": 20},
            symbol="000001.SZ", market="cn_stock",
            start_date=date(2020, 1, 1), end_date=date(2024, 12, 31),
            use_market_rules=True,  # ← was silently dropped before fix
            t_plus_1=True,
            price_limit_pct=0.20,  # ChiNext — NOT the default 0.10
            lot_size=200,          # NOT the default 100
            initial_capital=500_000.0,  # NOT the default 100_000
            commission_rate=0.0005,     # NOT the default 0.00008
            slippage_rate=0.001,        # matches the default 0.001
            tags=["market-rules-test"],
            description="verify prefilter propagates all fields",
        )

        captured_spec = {}

        def _capture_run(spec, data):
            captured_spec["value"] = spec
            mock_result = MagicMock()
            mock_result.status = "completed"
            mock_result.backtest.metrics = {
                "sharpe_ratio": 1.0, "max_drawdown": 0.1, "trade_count": 20,
            }
            mock_result.error = None
            return mock_result

        with patch("ez.agent.prefilter.Runner") as MockRunner:
            MockRunner.return_value.run.side_effect = _capture_run
            prefilter([original_spec], sample_data)

        quick_spec = captured_spec["value"]
        # Contract: every market-rule parameter must match the original spec
        assert quick_spec.use_market_rules is True, "use_market_rules dropped by prefilter"
        assert quick_spec.t_plus_1 is True, "t_plus_1 dropped"
        assert quick_spec.price_limit_pct == 0.20, f"price_limit_pct={quick_spec.price_limit_pct}, expected 0.20"
        assert quick_spec.lot_size == 200, f"lot_size={quick_spec.lot_size}, expected 200"
        # Contract: other custom params also preserved
        assert quick_spec.initial_capital == 500_000.0
        assert quick_spec.commission_rate == 0.0005
        assert quick_spec.slippage_rate == 0.001
        # Contract: only run-mode flags should differ from original
        assert quick_spec.run_backtest is True
        assert quick_spec.run_wfo is False
        # Original spec had run_wfo=True (default), quick overrides to False
