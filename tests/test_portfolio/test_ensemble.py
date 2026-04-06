"""V2.13 Phase 3: StrategyEnsemble unit + integration tests.

Tests for the composition-layer heuristic orchestrator. Covers:
- Skeleton / equal mode / manual mode / validation
- Hypothetical-return ledger
- return_weighted / inverse_vol modes
- Correlation warnings
- Nested ensembles
- End-to-end integration

**CI note**: scikit-learn is NOT required for StrategyEnsemble tests
(ensemble wraps PortfolioStrategy instances, not sklearn models).
However, some integration tests use MLAlpha which needs sklearn.
"""
from __future__ import annotations

from datetime import date, datetime
import json

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.portfolio_strategy import PortfolioStrategy


# ─── Test helpers ─────────────────────────────────────────────────

class _StaticStrategy(PortfolioStrategy):
    """A strategy that always returns the same weights."""

    def __init__(self, weights: dict[str, float], name: str = "static"):
        super().__init__()
        self._w = dict(weights)
        self._name = name

    def generate_weights(self, data, date, prev_w, prev_r):
        return dict(self._w)


class _FailingStrategy(PortfolioStrategy):
    """A strategy that always raises."""

    def generate_weights(self, data, date, prev_w, prev_r):
        raise RuntimeError("intentional failure")


def _make_universe(n_days: int = 100, symbols: list[str] | None = None):
    """Build a simple test universe with known close prices."""
    if symbols is None:
        symbols = ["A", "B", "C"]
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    rng = np.random.default_rng(42)
    data = {}
    for i, sym in enumerate(symbols):
        prices = 100 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.01, n_days))
        data[sym] = pd.DataFrame({
            "open": prices, "high": prices * 1.005, "low": prices * 0.995,
            "close": prices, "adj_close": prices,
            "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
        }, index=dates)
    return data, dates


# ─── Task 3.1: Skeleton tests ────────────────────────────────────

class TestEnsembleSkeleton:
    def test_is_portfolio_strategy_subclass(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        assert issubclass(StrategyEnsemble, PortfolioStrategy)

    def test_equal_weight_combines_two_strategies(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        s1 = _StaticStrategy({"A": 1.0, "B": 0.0})
        s2 = _StaticStrategy({"A": 0.0, "B": 1.0})
        ens = StrategyEnsemble(strategies=[s1, s2], mode="equal")

        combined = ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        assert abs(combined["A"] - 0.5) < 1e-6
        assert abs(combined["B"] - 0.5) < 1e-6

    def test_equal_weight_preserves_cash_intent(self):
        """If both subs hold 50% cash (sum=0.5), ensemble also ~50% cash."""
        from ez.portfolio.ensemble import StrategyEnsemble
        s1 = _StaticStrategy({"A": 0.5})  # 50% A, 50% cash
        s2 = _StaticStrategy({"B": 0.5})  # 50% B, 50% cash
        ens = StrategyEnsemble(strategies=[s1, s2], mode="equal")

        combined = ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        assert abs(combined.get("A", 0) - 0.25) < 1e-6
        assert abs(combined.get("B", 0) - 0.25) < 1e-6
        assert sum(combined.values()) < 0.51  # ~50% cash

    def test_manual_weight_normalizes(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        s1 = _StaticStrategy({"A": 1.0})
        s2 = _StaticStrategy({"B": 1.0})
        ens = StrategyEnsemble(
            strategies=[s1, s2], mode="manual",
            ensemble_weights=[3.0, 1.0],
        )
        combined = ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        assert abs(combined.get("A", 0) - 0.75) < 1e-6
        assert abs(combined.get("B", 0) - 0.25) < 1e-6

    def test_manual_requires_ensemble_weights(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        with pytest.raises(ValueError, match="manual.*requires"):
            StrategyEnsemble(
                strategies=[_StaticStrategy({"A": 1.0})],
                mode="manual",
            )

    def test_ensemble_weights_length_mismatch(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        with pytest.raises(ValueError, match="length"):
            StrategyEnsemble(
                strategies=[_StaticStrategy({"A": 1.0})],
                mode="manual",
                ensemble_weights=[1.0, 2.0],  # 2 weights for 1 strategy
            )

    def test_negative_weights_rejected(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        with pytest.raises(ValueError, match="non-negative"):
            StrategyEnsemble(
                strategies=[_StaticStrategy({"A": 1.0}), _StaticStrategy({"B": 1.0})],
                mode="manual",
                ensemble_weights=[1.0, -0.5],
            )

    def test_empty_strategies_rejected(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        with pytest.raises(ValueError, match="at least one"):
            StrategyEnsemble(strategies=[], mode="equal")

    def test_deepcopy_isolation(self):
        """Modifying original sub-strategy state after construction
        must not affect the ensemble's internal copy."""
        from ez.portfolio.ensemble import StrategyEnsemble
        original = _StaticStrategy({"A": 1.0})
        ens = StrategyEnsemble(strategies=[original], mode="equal")

        # Mutate the original
        original._w["A"] = 0.0
        original._w["Z"] = 1.0

        # Ensemble's copy should be unaffected
        combined = ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        assert combined.get("A", 0) == 1.0
        assert "Z" not in combined

    def test_pop_registry(self):
        """StrategyEnsemble must NOT be in the dropdown registry."""
        with pytest.raises(KeyError):
            PortfolioStrategy.resolve_class("StrategyEnsemble")
        assert "StrategyEnsemble" not in PortfolioStrategy._registry

    def test_lookback_days_max_of_subs_no_buffer(self):
        """Ensemble lookback = max(sub lookbacks). No buffer inflation."""
        from ez.portfolio.ensemble import StrategyEnsemble
        s1 = _StaticStrategy({"A": 1.0})
        s2 = _StaticStrategy({"B": 1.0})
        # Both inherit default lookback=252
        ens = StrategyEnsemble(strategies=[s1, s2], mode="equal")
        assert ens.lookback_days == 252  # max(252, 252), no +20

    def test_sub_exception_warns_and_continues(self):
        """A failing sub-strategy produces a warning and is treated as
        cash. Other subs still produce weights."""
        from ez.portfolio.ensemble import StrategyEnsemble
        import logging

        ok = _StaticStrategy({"A": 1.0})
        bad = _FailingStrategy()
        ens = StrategyEnsemble(strategies=[ok, bad], mode="equal")

        captured = []
        handler = logging.Handler()
        handler.emit = lambda r: captured.append(r.getMessage())
        logger = logging.getLogger("ez.portfolio.ensemble")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        try:
            combined = ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        finally:
            logger.removeHandler(handler)

        # ok sub's A=1.0 is the only contribution, re-normalized
        assert combined.get("A", 0) > 0
        # Warning about the failing sub
        assert any("sub-strategy #1" in m for m in captured)
        # Failure tracked in state
        assert ens.state["failure_counts"][1] >= 1

    def test_legitimate_empty_no_warning(self):
        """A sub that returns {} (no signal) is NOT treated as an error.
        No warning should be emitted."""
        from ez.portfolio.ensemble import StrategyEnsemble
        import logging

        empty = _StaticStrategy({})  # legitimate: "no position this period"
        full = _StaticStrategy({"A": 1.0})
        ens = StrategyEnsemble(strategies=[empty, full], mode="equal")

        captured = []
        handler = logging.Handler()
        handler.emit = lambda r: captured.append(r.getMessage())
        logger = logging.getLogger("ez.portfolio.ensemble")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        try:
            combined = ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        finally:
            logger.removeHandler(handler)

        # A=0.5 (full's 1.0 × 0.5 ensemble weight)
        assert abs(combined.get("A", 0) - 0.5) < 1e-6
        # No warning for the empty sub — it's legitimate
        sub_warnings = [m for m in captured if "sub-strategy" in m]
        assert len(sub_warnings) == 0


# ─── Task 3.2: Hypothetical-return ledger ─────────────────────────

class TestHypotheticalReturnLedger:
    def test_ledger_reconstruction_matches_close_to_close(self):
        """Two static subs with known weights, known close prices.
        Verify the hypothetical return matches hand-calculated values.

        Data layout (30 business days):
        - indices 0-7: close = 100 for all symbols (baseline)
        - indices 8+: A=110, B=105, C=120 (price jump)

        Rebalances:
        - dates[5]: first rebalance → prev_close = 100 (still in baseline)
        - dates[15]: second rebalance → close at dates[14] = 110/105/120
          (jumped at index 8, well before dates[14])

        Expected returns:
        - sub_ab: 0.5*(110/100-1) + 0.5*(105/100-1) = 0.075
        - sub_c: 1.0*(120/100-1) = 0.20
        """
        from ez.portfolio.ensemble import StrategyEnsemble

        dates = pd.date_range("2022-01-03", periods=30, freq="B")
        data = {
            "A": pd.DataFrame({
                "close": np.concatenate([np.full(8, 100.0), np.full(22, 110.0)]),
                "adj_close": np.concatenate([np.full(8, 100.0), np.full(22, 110.0)]),
            }, index=dates),
            "B": pd.DataFrame({
                "close": np.concatenate([np.full(8, 100.0), np.full(22, 105.0)]),
                "adj_close": np.concatenate([np.full(8, 100.0), np.full(22, 105.0)]),
            }, index=dates),
            "C": pd.DataFrame({
                "close": np.concatenate([np.full(8, 100.0), np.full(22, 120.0)]),
                "adj_close": np.concatenate([np.full(8, 100.0), np.full(22, 120.0)]),
            }, index=dates),
        }

        sub_ab = _StaticStrategy({"A": 0.5, "B": 0.5})
        sub_c = _StaticStrategy({"C": 1.0})
        ens = StrategyEnsemble(strategies=[sub_ab, sub_c], mode="equal")

        # First rebalance at dates[5] — seeds the ledger (close=100 baseline)
        ens.generate_weights(data, dates[5].to_pydatetime(), {}, {})
        # Second rebalance at dates[15] — triggers ledger update
        # prev_close at dates[5] = 100, current_close at dates[14] = 110/105/120
        ens.generate_weights(data, dates[15].to_pydatetime(), {}, {})

        r_ab = ens.state["sub_hypothetical_returns"][0]
        r_c = ens.state["sub_hypothetical_returns"][1]
        assert len(r_ab) == 1
        assert len(r_c) == 1
        # sub_ab: 0.5*(110/100-1) + 0.5*(105/100-1) = 0.075
        assert abs(r_ab[0] - 0.075) < 1e-6
        # sub_c: 1.0*(120/100-1) = 0.20
        assert abs(r_c[0] - 0.20) < 1e-6

    def test_zero_weight_sub_returns_zero(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        data, dates = _make_universe(n_days=30)
        empty = _StaticStrategy({})
        full = _StaticStrategy({"A": 1.0})
        ens = StrategyEnsemble(strategies=[empty, full], mode="equal")

        ens.generate_weights(data, dates[10].to_pydatetime(), {}, {})
        ens.generate_weights(data, dates[20].to_pydatetime(), {}, {})

        # Empty sub has 0.0 return (it held nothing)
        r_empty = ens.state["sub_hypothetical_returns"][0]
        assert len(r_empty) == 1
        assert r_empty[0] == 0.0

    def test_missing_symbol_graceful_skip(self):
        """If a sub holds a symbol not in universe_data, skip it."""
        from ez.portfolio.ensemble import StrategyEnsemble
        data, dates = _make_universe(n_days=30, symbols=["A", "B"])

        # Sub holds "Z" which is NOT in universe_data
        sub = _StaticStrategy({"Z": 0.5, "A": 0.5})
        ens = StrategyEnsemble(strategies=[sub], mode="equal")

        ens.generate_weights(data, dates[10].to_pydatetime(), {}, {})
        ens.generate_weights(data, dates[20].to_pydatetime(), {}, {})

        # Return only includes A's contribution (Z skipped)
        r = ens.state["sub_hypothetical_returns"][0]
        assert len(r) == 1
        assert isinstance(r[0], float)

    def test_ledger_state_is_json_serializable(self):
        """State must be pure dict/list/float — no pandas."""
        from ez.portfolio.ensemble import StrategyEnsemble
        data, dates = _make_universe(n_days=30)
        sub = _StaticStrategy({"A": 1.0})
        ens = StrategyEnsemble(strategies=[sub], mode="equal")

        ens.generate_weights(data, dates[10].to_pydatetime(), {}, {})
        ens.generate_weights(data, dates[20].to_pydatetime(), {}, {})

        # json.dumps on the entire state dict must not raise
        json_str = json.dumps(ens.state)
        assert len(json_str) > 10
