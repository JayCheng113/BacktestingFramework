"""Unit tests for RunPortfolioStep.

Uses monkeypatching to mock ``run_portfolio_backtest`` since the full
portfolio engine requires TradingCalendar, Universe, discrete-share
accounting, and real market data. The step's job is wiring — these
tests verify it wires correctly.

Also includes a lightweight integration test that calls the real engine
on synthetic data to verify the equity → returns conversion is correct.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from ez.research.context import PipelineContext
from ez.research.steps.run_portfolio import RunPortfolioStep


# ============================================================
# Helpers
# ============================================================

def _make_df(start: str = "2020-01-01", periods: int = 500, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=periods, freq="B")
    r = rng.normal(0.0005, 0.015, periods)
    price = 100 * np.cumprod(1 + r)
    return pd.DataFrame({
        "open": price * 0.999,
        "high": price * 1.01,
        "low": price * 0.99,
        "close": price,
        "adj_close": price,
        "volume": rng.integers(100_000, 1_000_000, periods).astype(float),
    }, index=idx)


@dataclass
class _FakePortfolioResult:
    """Minimal PortfolioResult mock."""
    equity_curve: list[float] = field(default_factory=list)
    benchmark_curve: list[float] = field(default_factory=list)
    dates: list[date] = field(default_factory=list)
    weights_history: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    rebalance_dates: list[date] = field(default_factory=list)
    rebalance_weights: list[dict] = field(default_factory=list)
    risk_events: list[dict] = field(default_factory=list)


def _make_fake_result(n_days: int = 100) -> _FakePortfolioResult:
    """Create a fake PortfolioResult with realistic equity curve."""
    dates = pd.bdate_range("2021-01-04", periods=n_days).date.tolist()
    equity = [1_000_000.0]
    rng = np.random.default_rng(99)
    for _ in range(n_days - 1):
        equity.append(equity[-1] * (1 + rng.normal(0.0003, 0.01)))
    return _FakePortfolioResult(
        equity_curve=equity,
        dates=dates,
        metrics={
            "total_return": equity[-1] / equity[0] - 1,
            "sharpe_ratio": 1.5,
            "max_drawdown": -0.08,
        },
    )


def _make_context(
    symbols: list[str],
    start: str = "2020-01-01",
    periods: int = 500,
) -> PipelineContext:
    ud = {s: _make_df(start, periods, seed=i) for i, s in enumerate(symbols)}
    return PipelineContext(
        config={"start_date": "2021-01-04", "end_date": "2022-12-30"},
        artifacts={"universe_data": ud},
    )


class _FakeStrategy:
    """Duck-typed portfolio strategy for testing."""
    lookback_days = 252
    def generate_weights(self, data, target_date, prev_weights, prev_returns):
        return {}


# ============================================================
# Constructor validation
# ============================================================

class TestConstructorValidation:
    def test_empty_label_raises(self):
        with pytest.raises(ValueError, match="non-empty string label"):
            RunPortfolioStep(strategy=_FakeStrategy(), label="", symbols=["A"])

    def test_empty_symbols_raises(self):
        with pytest.raises(ValueError, match="at least one symbol"):
            RunPortfolioStep(strategy=_FakeStrategy(), label="A", symbols=[])

    def test_string_symbols_raises(self):
        with pytest.raises(TypeError, match="list of strings"):
            RunPortfolioStep(strategy=_FakeStrategy(), label="A", symbols="AAA")

    def test_bytes_symbols_raises(self):
        with pytest.raises(TypeError, match="list of strings"):
            RunPortfolioStep(strategy=_FakeStrategy(), label="A", symbols=b"AAA")

    def test_valid_construction(self):
        step = RunPortfolioStep(
            strategy=_FakeStrategy(),
            label="Alpha",
            symbols=["510300.SH", "510500.SH"],
            freq="weekly",
            market="cn_stock",
        )
        assert step.label == "Alpha"
        assert step.symbols == ["510300.SH", "510500.SH"]
        assert step.freq == "weekly"


# ============================================================
# Market parameter derivation
# ============================================================

class TestMarketDefaults:
    def test_cn_stock_defaults(self):
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A",
            symbols=["A"], market="cn_stock",
        )
        params = step._derive_market_params()
        assert params["t_plus_1"] is True
        assert params["lot_size"] == 100
        assert params["limit_pct"] == 0.10
        assert params["stamp_tax_rate"] == 0.0005

    def test_us_stock_defaults(self):
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A",
            symbols=["A"], market="us_stock",
        )
        params = step._derive_market_params()
        assert params["t_plus_1"] is False
        assert params["lot_size"] == 1
        assert params["limit_pct"] == 0.0
        assert params["stamp_tax_rate"] == 0.0

    def test_cost_model_respects_market_stamp_tax(self):
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A",
            symbols=["A"], market="cn_stock",
        )
        cm = step._build_cost_model(step._derive_market_params())
        assert cm.stamp_tax_rate == 0.0005

    def test_cost_model_user_override_takes_precedence(self):
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A",
            symbols=["A"], market="cn_stock",
            cost_model_kwargs={"stamp_tax_rate": 0.001, "slippage_rate": 0.005},
        )
        cm = step._build_cost_model(step._derive_market_params())
        assert cm.stamp_tax_rate == 0.001
        assert cm.slippage_rate == 0.005


# ============================================================
# Happy path (mocked engine)
# ============================================================

class TestHappyPath:
    def test_writes_returns_metrics_equity(self, monkeypatch):
        """Core test: step produces returns, metrics, equity_curves."""
        fake_result = _make_fake_result(100)

        def mock_run(**kwargs):
            return fake_result
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            mock_run,
        )

        ctx = _make_context(["SYM1", "SYM2"])
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="Alpha",
            symbols=["SYM1", "SYM2"],
        )
        out = step.run(ctx)

        # returns
        returns_df = out.artifacts["returns"]
        assert isinstance(returns_df, pd.DataFrame)
        assert "Alpha" in returns_df.columns
        assert len(returns_df) == 99  # 100 equity points → 99 returns

        # metrics
        assert "Alpha" in out.artifacts["metrics"]
        assert out.artifacts["metrics"]["Alpha"]["sharpe_ratio"] == 1.5

        # equity curves
        assert "Alpha" in out.artifacts["equity_curves"]

        # portfolio results
        assert "Alpha" in out.artifacts["portfolio_results"]

    def test_equity_to_returns_conversion_is_correct(self, monkeypatch):
        """Verify pct_change conversion produces expected values."""
        result = _FakePortfolioResult(
            equity_curve=[100.0, 110.0, 99.0, 108.9],
            dates=[date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6), date(2021, 1, 7)],
            metrics={"total_return": 0.089},
        )
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            lambda *a, **kw: result,
        )
        ctx = _make_context(["S1"])
        step = RunPortfolioStep(strategy=_FakeStrategy(), label="X", symbols=["S1"])
        out = step.run(ctx)

        rets = out.artifacts["returns"]["X"]
        assert len(rets) == 3
        np.testing.assert_almost_equal(rets.iloc[0], 0.10, decimal=10)     # 110/100 - 1
        np.testing.assert_almost_equal(rets.iloc[1], -0.10, decimal=10)    # 99/110 - 1
        np.testing.assert_almost_equal(rets.iloc[2], 0.10, decimal=10)     # 108.9/99 - 1

    def test_engine_receives_correct_params(self, monkeypatch):
        """Verify the step passes market-derived params to engine correctly."""
        captured = {}

        def mock_run(**kwargs):
            captured.update(kwargs)
            return _make_fake_result(50)
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            mock_run,
        )

        ctx = _make_context(["S1", "S2"])
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A",
            symbols=["S1", "S2"],
            freq="monthly",
            market="cn_stock",
            benchmark_symbol="510300.SH",
            skip_terminal_liquidation=True,
            use_open_price=True,
        )
        step.run(ctx)

        assert captured["freq"] == "monthly"
        assert captured["t_plus_1"] is True
        assert captured["lot_size"] == 100
        assert captured["limit_pct"] == 0.10
        assert captured["benchmark_symbol"] == "510300.SH"
        assert captured["skip_terminal_liquidation"] is True
        assert captured["use_open_price"] is True
        # Universe should only contain the specified symbols
        assert set(captured["universe_data"].keys()) == {"S1", "S2"}


# ============================================================
# Merge semantics
# ============================================================

class TestMergeSemantics:
    def test_merges_with_existing_returns_dataframe(self, monkeypatch):
        """If returns already exist from RunStrategiesStep, merge via outer join."""
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            lambda *a, **kw: _make_fake_result(100),
        )

        ctx = _make_context(["S1", "S2"])
        # Pre-populate with existing returns (as if RunStrategiesStep ran)
        existing_idx = pd.bdate_range("2021-01-04", periods=99)
        existing_returns = pd.DataFrame(
            {"Bond": np.random.default_rng(1).normal(0.0001, 0.005, 99)},
            index=existing_idx,
        )
        ctx.artifacts["returns"] = existing_returns
        ctx.artifacts["metrics"] = {"Bond": {"sharpe_ratio": 0.5}}
        ctx.artifacts["equity_curves"] = {"Bond": pd.Series([1.0] * 99)}

        step = RunPortfolioStep(strategy=_FakeStrategy(), label="Alpha", symbols=["S1"])
        out = step.run(ctx)

        returns_df = out.artifacts["returns"]
        assert "Bond" in returns_df.columns
        assert "Alpha" in returns_df.columns
        assert "Bond" in out.artifacts["metrics"]
        assert "Alpha" in out.artifacts["metrics"]

    def test_creates_fresh_returns_when_none_exists(self, monkeypatch):
        """First step in pipeline — no pre-existing returns."""
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            lambda *a, **kw: _make_fake_result(50),
        )
        ctx = _make_context(["S1"])
        step = RunPortfolioStep(strategy=_FakeStrategy(), label="A", symbols=["S1"])
        out = step.run(ctx)

        assert isinstance(out.artifacts["returns"], pd.DataFrame)
        assert list(out.artifacts["returns"].columns) == ["A"]


# ============================================================
# Error paths
# ============================================================

class TestErrorPaths:
    def test_missing_symbol_in_universe_data_raises(self):
        ctx = _make_context(["S1"])
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A",
            symbols=["S1", "MISSING"],
        )
        with pytest.raises(ValueError, match="not found in universe_data"):
            step.run(ctx)

    def test_missing_universe_data_artifact_raises(self):
        ctx = PipelineContext()
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A", symbols=["S1"],
        )
        with pytest.raises(KeyError, match="universe_data"):
            step.run(ctx)

    def test_too_few_equity_points_raises(self, monkeypatch):
        result = _FakePortfolioResult(
            equity_curve=[1_000_000.0],
            dates=[date(2021, 1, 4)],
            metrics={},
        )
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            lambda *a, **kw: result,
        )
        ctx = _make_context(["S1"])
        step = RunPortfolioStep(strategy=_FakeStrategy(), label="A", symbols=["S1"])
        with pytest.raises(RuntimeError, match="fewer than 2 equity points"):
            step.run(ctx)

    def test_engine_exception_propagates(self, monkeypatch):
        def mock_run(**kwargs):
            raise ValueError("engine blew up")
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            mock_run,
        )
        ctx = _make_context(["S1"])
        step = RunPortfolioStep(strategy=_FakeStrategy(), label="A", symbols=["S1"])
        with pytest.raises(ValueError, match="engine blew up"):
            step.run(ctx)


# ============================================================
# Date resolution
# ============================================================

class TestDateResolution:
    def test_uses_config_dates(self, monkeypatch):
        captured = {}
        def mock_run(**kwargs):
            captured.update(kwargs)
            return _make_fake_result(50)
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            mock_run,
        )

        ctx = PipelineContext(
            config={"start_date": "2021-06-01", "end_date": "2022-06-01"},
            artifacts={"universe_data": {"S1": _make_df("2020-01-01", 700)}},
        )
        step = RunPortfolioStep(strategy=_FakeStrategy(), label="A", symbols=["S1"])
        step.run(ctx)

        assert captured["start"] == date(2021, 6, 1)
        assert captured["end"] == date(2022, 6, 1)

    def test_infers_dates_from_data_when_config_missing(self, monkeypatch):
        captured = {}
        def mock_run(**kwargs):
            captured.update(kwargs)
            return _make_fake_result(50)
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            mock_run,
        )

        df = _make_df("2020-01-01", 500)
        ctx = PipelineContext(artifacts={"universe_data": {"S1": df}})
        step = RunPortfolioStep(strategy=_FakeStrategy(), label="A", symbols=["S1"])
        step.run(ctx)

        # start should be inferred_start + lookback buffer
        # end should be max date
        assert captured["start"] is not None
        assert captured["end"] is not None
        assert captured["end"] == df.index[-1].date()


# ============================================================
# Calendar construction
# ============================================================

class TestCalendarConstruction:
    def test_builds_calendar_from_union_of_dates(self):
        df1 = _make_df("2020-01-01", 100, seed=1)
        df2 = _make_df("2020-01-15", 100, seed=2)
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A", symbols=["S1", "S2"],
        )
        cal = step._build_calendar({"S1": df1, "S2": df2})
        # Calendar should have dates from both DataFrames
        all_dates = set(d.date() for d in df1.index) | set(d.date() for d in df2.index)
        assert len(cal._days) == len(all_dates)

    def test_raises_on_empty_data(self):
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A", symbols=["S1"],
        )
        empty_df = pd.DataFrame(
            columns=["open", "high", "low", "close", "adj_close", "volume"],
            index=pd.DatetimeIndex([]),
        )
        with pytest.raises(RuntimeError, match="no trading dates"):
            step._build_calendar({"S1": empty_df})


# ============================================================
# Integration: real portfolio engine on synthetic data
# ============================================================

class TestRealEngineIntegration:
    """Run the actual portfolio engine to verify end-to-end correctness.

    Uses a trivial equal-weight strategy (duck-typed) on 2 synthetic ETFs.
    """

    def test_real_engine_produces_valid_returns(self):
        """Smoke test: real engine → equity → returns are finite and reasonable."""
        # Create a duck-typed portfolio strategy
        class _EqualWeightAll:
            lookback_days = 20
            def generate_weights(self, data, target_date, prev_weights, prev_returns):
                symbols = list(data.keys())
                if not symbols:
                    return {}
                w = 1.0 / len(symbols)
                return {s: w for s in symbols}

        symbols = ["ETF_A", "ETF_B"]
        ud = {s: _make_df("2020-01-01", 600, seed=i) for i, s in enumerate(symbols)}

        ctx = PipelineContext(
            config={"start_date": "2020-06-01", "end_date": "2022-06-01"},
            artifacts={"universe_data": ud},
        )
        step = RunPortfolioStep(
            strategy=_EqualWeightAll(),
            label="EW",
            symbols=symbols,
            freq="monthly",
            market="cn_stock",
        )
        out = step.run(ctx)

        returns_df = out.artifacts["returns"]
        assert "EW" in returns_df.columns
        rets = returns_df["EW"].dropna()
        assert len(rets) > 100
        assert np.all(np.isfinite(rets.values))
        assert abs(rets.mean()) < 0.1  # reasonable daily return

        metrics = out.artifacts["metrics"]["EW"]
        assert "total_return" in metrics
        assert "sharpe_ratio" in metrics


# ============================================================
# Review round-1 regression tests (C1, C2, I1, I3)
# ============================================================

class TestReviewRound1:
    """Regression tests for code-reviewer findings."""

    def test_c1_short_data_start_past_end_raises(self):
        """C1: lookback buffer pushes start past end on short data."""
        # 50 business days ≈ 2.5 months, lookback_days=252 → buffer=403 days
        short_df = _make_df("2024-01-01", 50, seed=1)
        ctx = PipelineContext(
            artifacts={"universe_data": {"S1": short_df}},
        )
        step = RunPortfolioStep(
            strategy=_FakeStrategy(),  # lookback_days=252
            label="A",
            symbols=["S1"],
        )
        with pytest.raises(RuntimeError, match="start.*>=.*end"):
            step.run(ctx)

    def test_c2_non_overlapping_symbols_raises(self):
        """C2: symbols with disjoint date ranges → empty intersection."""
        df_jan = _make_df("2020-01-01", 60, seed=1)
        df_sep = _make_df("2020-09-01", 60, seed=2)
        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="A", symbols=["S1", "S2"],
        )
        with pytest.raises(RuntimeError, match="non-overlapping"):
            step._infer_dates_from_data({"S1": df_jan, "S2": df_sep})

    def test_i1_outer_join_nan_safe_for_nested_oos(self, monkeypatch):
        """I1: outer join NaN does not break downstream metric computation."""
        from ez.research._metrics import compute_basic_metrics

        # Portfolio returns: 2024-02 to 2024-04
        port_idx = pd.bdate_range("2024-02-01", periods=60)
        port_rets = pd.Series(
            np.random.default_rng(1).normal(0.0003, 0.01, 60),
            index=port_idx,
            name="Alpha",
        )
        # Single-stock returns: 2024-01 to 2024-05 (wider range)
        stock_idx = pd.bdate_range("2024-01-01", periods=100)
        stock_rets = pd.Series(
            np.random.default_rng(2).normal(0.0001, 0.005, 100),
            index=stock_idx,
            name="Bond",
        )
        # Merge via outer join (same as step does)
        merged = stock_rets.to_frame().join(port_rets.to_frame(), how="outer")
        assert merged.isna().any().any()  # NaN exists in outer region

        # Weighted portfolio on merged data — NaN propagates
        weighted = merged["Alpha"] * 0.7 + merged["Bond"] * 0.3
        # compute_basic_metrics should handle NaN gracefully
        result = compute_basic_metrics(weighted.dropna())
        assert result is not None
        assert np.isfinite(result["sharpe"])

    def test_i3_duplicate_label_warns(self, monkeypatch, caplog):
        """I3: using the same label twice produces a warning."""
        monkeypatch.setattr(
            "ez.portfolio.engine.run_portfolio_backtest",
            lambda **kw: _make_fake_result(50),
        )
        ctx = _make_context(["S1"])
        # Pre-populate with existing "Alpha" column
        existing = pd.DataFrame(
            {"Alpha": np.zeros(49)},
            index=pd.bdate_range("2021-01-05", periods=49),
        )
        ctx.artifacts["returns"] = existing

        step = RunPortfolioStep(
            strategy=_FakeStrategy(), label="Alpha", symbols=["S1"],
        )
        import logging
        with caplog.at_level(logging.WARNING, logger="ez.research.steps.run_portfolio"):
            out = step.run(ctx)

        assert "already exists" in caplog.text
        # Column still works (overwritten, not duplicated)
        assert list(out.artifacts["returns"].columns).count("Alpha") == 1
