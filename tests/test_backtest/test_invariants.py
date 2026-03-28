"""A1: Accounting invariant tests -- V2.3 correctness hardening.

Core invariant: at every bar i,
    abs(cash + shares * close[i] - equity[i]) <= EPS_FUND

Verification approach: a shadow simulator independently computes per-bar
(cash, shares, equity), then compared with the engine's equity curve.
"""

import numpy as np
import pandas as pd
import pytest

from ez.backtest.engine import VectorizedBacktestEngine
from ez.core.matcher import Matcher, SimpleMatcher, SlippageMatcher
from ez.factor.base import Factor
from ez.strategy.base import Strategy

# Split tolerances: fund-level vs rate-level
EPS_FUND = 0.01     # cash/equity comparison (1 cent per $100K)
EPS_RATE = 1e-10    # daily return comparison (floating-point precision)
EPS_PNL = 0.01      # trade PnL vs equity change


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_data(
    closes: list[float],
    opens: list[float] | None = None,
) -> pd.DataFrame:
    n = len(closes)
    if opens is None:
        opens = closes
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) * 1.005 for o, c in zip(opens, closes)],
            "low": [min(o, c) * 0.995 for o, c in zip(opens, closes)],
            "close": closes,
            "adj_close": closes,
            "volume": [1_000_000] * n,
        },
        index=dates,
    )


class FixedSignalStrategy(Strategy):
    """Strategy emitting predetermined signals. No factors -> zero warmup."""

    def __init__(self, signals: list[float]):
        self._signals = signals

    def required_factors(self) -> list[Factor]:
        return []

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        n = len(df)
        sig = (self._signals + [0.0] * n)[:n]
        return pd.Series(sig, index=df.index)


# Remove test utility from Strategy auto-registry
_key = f"{FixedSignalStrategy.__module__}.{FixedSignalStrategy.__name__}"
if _key in Strategy._registry:
    del Strategy._registry[_key]


def shadow_simulate(
    closes: np.ndarray,
    opens: np.ndarray,
    weights: np.ndarray,
    capital: float,
    matcher: Matcher,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Independent per-bar simulation returning (equity, cash, shares).

    Mirrors VectorizedBacktestEngine._simulate logic as an oracle
    for accounting invariant checks.
    """
    n = len(closes)
    equity = np.zeros(n)
    cash_hist = np.zeros(n)
    shares_hist = np.zeros(n)

    cash = capital
    shares = 0.0
    prev_weight = 0.0

    equity[0] = capital
    cash_hist[0] = cash
    shares_hist[0] = shares

    for i in range(1, n):
        target_weight = weights[i] if i < len(weights) else 0.0
        exec_price = opens[i]

        if abs(target_weight - prev_weight) > 1e-6:
            current_equity = cash + shares * exec_price
            target_value = current_equity * target_weight
            current_value = shares * exec_price

            if target_value < current_value and shares > 0:
                sell_shares = shares if target_weight == 0 else (current_value - target_value) / exec_price
                fill = matcher.fill_sell(exec_price, sell_shares)
                cash += fill.net_amount
                shares -= fill.shares
                if shares < 1e-10:
                    shares = 0.0

            elif target_value > current_value:
                additional = min(target_value - current_value, cash)
                if additional > 0:
                    fill = matcher.fill_buy(exec_price, additional)
                    if fill.shares > 0:
                        shares += fill.shares
                        cash += fill.net_amount

            prev_weight = target_weight

        equity[i] = cash + shares * closes[i]
        cash_hist[i] = cash
        shares_hist[i] = shares

    return equity, cash_hist, shares_hist


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

PRICES = [100, 102, 98, 105, 110, 95, 100, 108, 112, 106,
          103, 107, 111, 104, 99, 101, 105, 109, 113, 108]
OPENS = [99, 101, 101, 97, 104, 111, 96, 99, 107, 113,
         107, 102, 106, 112, 105, 98, 100, 104, 108, 114]

SIGNAL_SCENARIOS = [
    pytest.param([0, 1, 1, 1, 1, 1, 1, 0, 0, 0,
                  0, 1, 1, 1, 0, 0, 0, 0, 0, 0], id="buy-hold-sell"),
    pytest.param([0, 0.3, 0.5, 0.8, 1.0, 1.0, 0.5, 0.2, 0, 0,
                  0, 0, 0.5, 1.0, 1.0, 0.5, 0, 0, 0, 0], id="scale-in-out"),
    pytest.param([0, 1, 0, 1, 0, 1, 0, 1, 0, 1,
                  0, 1, 0, 1, 0, 1, 0, 1, 0, 0], id="rapid-switch"),
    pytest.param([1.0] * 20, id="always-in"),
    pytest.param([0.0] * 20, id="never-trade"),
]

MATCHERS = [
    pytest.param(SimpleMatcher(0.001, 0.0), id="simple-no-min"),
    pytest.param(SimpleMatcher(0.001, 5.0), id="simple-min5"),
    pytest.param(SlippageMatcher(0.001, 0.001, 5.0), id="slip-0.1pct"),
    pytest.param(SlippageMatcher(0.002, 0.0003, 0.0), id="slip-0.2pct-no-min"),
]

CAPITAL = 100_000.0


def _shifted_weights(raw_signals: list[float], n: int) -> np.ndarray:
    """Replicate engine signal processing: shift(1).fillna(0).clip(0,1)."""
    sig = (raw_signals + [0.0] * n)[:n]
    s = pd.Series(sig)
    return s.shift(1).fillna(0.0).clip(0.0, 1.0).values


# ---------------------------------------------------------------------------
# A1 Test Suite
# ---------------------------------------------------------------------------

class TestAccountingInvariants:

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", SIGNAL_SCENARIOS)
    def test_shadow_equity_matches_engine(self, matcher, raw_signals):
        """Shadow simulator equity matches engine equity at every bar."""
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(matcher=matcher)
        result = engine.run(data, FixedSignalStrategy(raw_signals), CAPITAL)

        weights = _shifted_weights(raw_signals, len(data))
        shadow_eq, shadow_cash, shadow_shares = shadow_simulate(
            data["adj_close"].values, data["open"].values,
            weights, CAPITAL, matcher,
        )

        engine_eq = result.equity_curve.values
        assert len(shadow_eq) == len(engine_eq)
        for i in range(len(engine_eq)):
            assert abs(shadow_eq[i] - engine_eq[i]) <= EPS_FUND, (
                f"Bar {i}: shadow={shadow_eq[i]:.4f} engine={engine_eq[i]:.4f} "
                f"diff={abs(shadow_eq[i] - engine_eq[i]):.6f}"
            )

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", SIGNAL_SCENARIOS)
    def test_per_bar_cash_plus_position_equals_equity(self, matcher, raw_signals):
        """cash + shares * close[i] == equity[i] at every bar."""
        data = make_data(PRICES, OPENS)
        weights = _shifted_weights(raw_signals, len(data))
        eq, cash, shares = shadow_simulate(
            data["adj_close"].values, data["open"].values,
            weights, CAPITAL, matcher,
        )
        for i in range(len(eq)):
            expected = cash[i] + shares[i] * PRICES[i]
            assert abs(expected - eq[i]) <= EPS_FUND, (
                f"Bar {i}: cash({cash[i]:.2f})+pos({shares[i]:.4f}*{PRICES[i]})="
                f"{expected:.2f} != equity={eq[i]:.2f}"
            )

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", SIGNAL_SCENARIOS)
    def test_equity_starts_at_capital(self, matcher, raw_signals):
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(matcher=matcher)
        result = engine.run(data, FixedSignalStrategy(raw_signals), CAPITAL)
        assert result.equity_curve.iloc[0] == CAPITAL

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", SIGNAL_SCENARIOS)
    def test_no_nan_in_equity(self, matcher, raw_signals):
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(matcher=matcher)
        result = engine.run(data, FixedSignalStrategy(raw_signals), CAPITAL)
        assert not result.equity_curve.isna().any(), "NaN in equity curve"

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", SIGNAL_SCENARIOS)
    def test_equity_always_positive(self, matcher, raw_signals):
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(matcher=matcher)
        result = engine.run(data, FixedSignalStrategy(raw_signals), CAPITAL)
        assert (result.equity_curve > 0).all(), "Non-positive equity found"

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", SIGNAL_SCENARIOS)
    def test_daily_returns_consistent(self, matcher, raw_signals):
        """daily_return[i] == equity[i]/equity[i-1] - 1."""
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(matcher=matcher)
        result = engine.run(data, FixedSignalStrategy(raw_signals), CAPITAL)
        eq = result.equity_curve.values
        dr = result.daily_returns.values
        assert dr[0] == 0.0
        for i in range(1, len(eq)):
            if eq[i - 1] > 0:
                expected = eq[i] / eq[i - 1] - 1
                assert abs(dr[i] - expected) <= EPS_RATE, (
                    f"Bar {i}: return={dr[i]:.10f} expected={expected:.10f}"
                )

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", [
        pytest.param([0, 1, 1, 1, 1, 1, 1, 0, 0, 0,
                      0, 1, 1, 1, 0, 0, 0, 0, 0, 0], id="buy-hold-sell"),
        pytest.param([0, 0.3, 0.5, 0.8, 1.0, 1.0, 0.5, 0.2, 0, 0,
                      0, 0, 0.5, 1.0, 1.0, 0.5, 0, 0, 0, 0], id="scale-in-out"),
        pytest.param([0, 1, 0, 1, 0, 1, 0, 1, 0, 1,
                      0, 1, 0, 1, 0, 1, 0, 1, 0, 0], id="rapid-switch"),
    ])
    def test_closed_trades_pnl_equals_equity_change(self, matcher, raw_signals):
        """When all positions close, sum(trade.pnl) ~ equity[-1] - capital."""
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(matcher=matcher)
        result = engine.run(data, FixedSignalStrategy(raw_signals), CAPITAL)
        if result.trades:
            total_pnl = sum(t.pnl for t in result.trades)
            equity_change = result.equity_curve.iloc[-1] - CAPITAL
            assert abs(total_pnl - equity_change) <= EPS_PNL, (
                f"PnL sum={total_pnl:.4f} != equity change={equity_change:.4f}"
            )

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", SIGNAL_SCENARIOS)
    def test_trade_commission_non_negative(self, matcher, raw_signals):
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(matcher=matcher)
        result = engine.run(data, FixedSignalStrategy(raw_signals), CAPITAL)
        for t in result.trades:
            assert t.commission >= 0, f"Negative commission: {t}"

    @pytest.mark.parametrize("matcher", MATCHERS)
    @pytest.mark.parametrize("raw_signals", SIGNAL_SCENARIOS)
    def test_equity_from_returns_reconstruction(self, matcher, raw_signals):
        """Equity reconstructed from daily_returns matches reported equity.

        This is an independent oracle that uses only BacktestResult fields
        (equity_curve, daily_returns) without replicating simulation logic.
        """
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(matcher=matcher)
        result = engine.run(data, FixedSignalStrategy(raw_signals), CAPITAL)
        eq = result.equity_curve.values
        dr = result.daily_returns.values
        reconstructed = np.zeros(len(eq))
        reconstructed[0] = eq[0]
        for i in range(1, len(eq)):
            reconstructed[i] = reconstructed[i - 1] * (1 + dr[i])
        for i in range(len(eq)):
            assert abs(reconstructed[i] - eq[i]) <= EPS_FUND, (
                f"Bar {i}: reconstructed={reconstructed[i]:.4f} reported={eq[i]:.4f}"
            )

    def test_high_min_commission_prevents_trading(self):
        """min_commission > capital -> no trades, equity stays flat."""
        data = make_data(PRICES, OPENS)
        engine = VectorizedBacktestEngine(
            matcher=SimpleMatcher(0.001, 200_000.0)
        )
        result = engine.run(data, FixedSignalStrategy([1.0] * 20), CAPITAL)
        assert (result.equity_curve == CAPITAL).all()
        assert result.metrics["trade_count"] == 0
