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

    def test_nan_in_ensemble_weights_rejected(self):
        """Codex: NaN in ensemble_weights must be caught at construction."""
        from ez.portfolio.ensemble import StrategyEnsemble
        with pytest.raises(ValueError, match="not finite"):
            StrategyEnsemble(
                strategies=[_StaticStrategy({"A": 1.0}), _StaticStrategy({"B": 1.0})],
                mode="manual",
                ensemble_weights=[1.0, float("nan")],
            )

    def test_inf_in_ensemble_weights_rejected(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        with pytest.raises(ValueError, match="not finite"):
            StrategyEnsemble(
                strategies=[_StaticStrategy({"A": 1.0}), _StaticStrategy({"B": 1.0})],
                mode="manual",
                ensemble_weights=[float("inf"), 1.0],
            )

    def test_ensemble_weights_defensive_copy(self):
        """Codex: external mutation of weights list must NOT affect ensemble."""
        from ez.portfolio.ensemble import StrategyEnsemble
        weights = [3.0, 1.0]
        ens = StrategyEnsemble(
            strategies=[_StaticStrategy({"A": 1.0}), _StaticStrategy({"B": 1.0})],
            mode="manual",
            ensemble_weights=weights,
        )
        # Mutate the original list
        weights[0] = 0.0
        weights[1] = 100.0

        # Ensemble should still use the original [3, 1] → [0.75, 0.25]
        combined = ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        assert abs(combined.get("A", 0) - 0.75) < 1e-6

    def test_correlation_threshold_out_of_range_rejected(self):
        """Codex: correlation_threshold must be in (0, 1]."""
        from ez.portfolio.ensemble import StrategyEnsemble
        for bad in [-0.1, 0.0, 1.1, float("nan")]:
            with pytest.raises(ValueError, match="correlation_threshold"):
                StrategyEnsemble(
                    strategies=[_StaticStrategy({"A": 1.0})],
                    mode="equal",
                    correlation_threshold=bad,
                )

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


# ─── Task 3.3: return_weighted + inverse_vol ──────────────────────

class TestReturnWeightedMode:
    def _build_and_warm(self, sub_a_ret: float, sub_b_ret: float, n_warmup: int = 10):
        """Build an ensemble with two subs and manually fill the ledger
        with known hypothetical returns to bypass the actual price-based
        reconstruction."""
        from ez.portfolio.ensemble import StrategyEnsemble
        sub_a = _StaticStrategy({"X": 1.0}, name="sub_a")
        sub_b = _StaticStrategy({"Y": 1.0}, name="sub_b")
        ens = StrategyEnsemble(
            strategies=[sub_a, sub_b],
            mode="return_weighted",
            warmup_rebalances=n_warmup,
        )
        # Manually fill ledger to simulate n_warmup rebalances
        for i in range(n_warmup):
            ens.state["sub_hypothetical_returns"][0].append(sub_a_ret)
            ens.state["sub_hypothetical_returns"][1].append(sub_b_ret)
            ens.state["sub_target_weights"][0].append({
                "date": f"2022-01-{10+i:02d}", "weights": {"X": 1.0},
            })
            ens.state["sub_target_weights"][1].append({
                "date": f"2022-01-{10+i:02d}", "weights": {"Y": 1.0},
            })
        ens.state["first_rebalance_date"] = "2022-01-10"
        # Ensure elapsed days > _min_warmup_days (= n_warmup * 7)
        # Set to 3 months later to comfortably pass the date gate
        ens.state["last_rebalance_date"] = "2022-04-10"
        return ens

    def test_prefers_higher_return_sub(self):
        ens = self._build_and_warm(sub_a_ret=0.05, sub_b_ret=0.01, n_warmup=10)
        # return_weighted: weight ∝ max(0, mean). A=0.05, B=0.01
        weights = ens._compute_ensemble_weights([True, True])
        assert weights[0] > weights[1], (
            f"Expected sub_a (mean=0.05) > sub_b (mean=0.01), got {weights}"
        )

    def test_all_negative_falls_back_to_equal(self):
        ens = self._build_and_warm(sub_a_ret=-0.02, sub_b_ret=-0.03, n_warmup=10)
        weights = ens._compute_ensemble_weights([True, True])
        # All negative → equal fallback
        assert abs(weights[0] - 0.5) < 1e-6
        assert abs(weights[1] - 0.5) < 1e-6

    def test_falls_back_during_warmup(self):
        """Before warmup_rebalances entries, use equal weights."""
        from ez.portfolio.ensemble import StrategyEnsemble
        sub_a = _StaticStrategy({"X": 1.0})
        sub_b = _StaticStrategy({"Y": 1.0})
        ens = StrategyEnsemble(
            strategies=[sub_a, sub_b],
            mode="return_weighted",
            warmup_rebalances=8,
        )
        # Only 3 entries — below warmup threshold
        for _ in range(3):
            ens.state["sub_hypothetical_returns"][0].append(0.10)
            ens.state["sub_hypothetical_returns"][1].append(0.01)
        ens.state["first_rebalance_date"] = "2022-01-10"
        ens.state["last_rebalance_date"] = "2022-01-20"

        weights = ens._compute_ensemble_weights([True, True])
        # During warmup → equal
        assert abs(weights[0] - 0.5) < 1e-6


class TestInverseVolMode:
    def test_assigns_more_weight_to_lower_vol_sub(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        sub_a = _StaticStrategy({"X": 1.0})
        sub_b = _StaticStrategy({"Y": 1.0})
        ens = StrategyEnsemble(
            strategies=[sub_a, sub_b],
            mode="inverse_vol",
            warmup_rebalances=8,
        )
        # Sub A: low vol returns
        rng = np.random.default_rng(42)
        for _ in range(12):
            ens.state["sub_hypothetical_returns"][0].append(0.01 + rng.normal(0, 0.001))
            ens.state["sub_hypothetical_returns"][1].append(0.01 + rng.normal(0, 0.05))
            ens.state["sub_target_weights"][0].append({"date": "2022-01-10", "weights": {}})
            ens.state["sub_target_weights"][1].append({"date": "2022-01-10", "weights": {}})
        ens.state["first_rebalance_date"] = "2022-01-10"
        ens.state["last_rebalance_date"] = "2022-04-10"

        weights = ens._compute_ensemble_weights([True, True])
        # Sub A has lower vol → should get higher weight
        assert weights[0] > weights[1], (
            f"Expected low-vol sub_a > high-vol sub_b, got {weights}"
        )

    def test_falls_back_during_warmup(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        sub_a = _StaticStrategy({"X": 1.0})
        sub_b = _StaticStrategy({"Y": 1.0})
        ens = StrategyEnsemble(
            strategies=[sub_a, sub_b],
            mode="inverse_vol",
            warmup_rebalances=8,
        )
        # Only 3 entries — warmup
        for _ in range(3):
            ens.state["sub_hypothetical_returns"][0].append(0.01)
            ens.state["sub_hypothetical_returns"][1].append(0.05)

        weights = ens._compute_ensemble_weights([True, True])
        assert abs(weights[0] - 0.5) < 1e-6


# ─── Task 3.4: Correlation warnings ──────────────────────────────

    def test_warmup_requires_both_count_and_days(self):
        """Plan Task 3.3: warmup gate must check BOTH rebalance count
        AND elapsed days. Count alone is not enough (weekly vs monthly
        frequency changes the meaning of '8 rebalances')."""
        from ez.portfolio.ensemble import StrategyEnsemble
        sub_a = _StaticStrategy({"X": 1.0})
        sub_b = _StaticStrategy({"Y": 1.0})
        ens = StrategyEnsemble(
            strategies=[sub_a, sub_b],
            mode="return_weighted",
            warmup_rebalances=8,
        )
        # Fill 10 entries (>= 8) BUT only 10 calendar days elapsed
        # (< min_warmup_days = 8 * 7 = 56)
        for i in range(10):
            ens.state["sub_hypothetical_returns"][0].append(0.10)
            ens.state["sub_hypothetical_returns"][1].append(0.01)
            ens.state["sub_target_weights"][0].append({"date": "2022-01-10", "weights": {}})
            ens.state["sub_target_weights"][1].append({"date": "2022-01-10", "weights": {}})
        ens.state["first_rebalance_date"] = "2022-01-10"
        ens.state["last_rebalance_date"] = "2022-01-20"  # only 10 days!

        # Despite having 10 entries (>= 8), elapsed = 10 days < 56
        # → warmup NOT complete → equal weight fallback
        weights = ens._compute_ensemble_weights([True, True])
        assert abs(weights[0] - 0.5) < 1e-6, (
            f"Expected equal weight (warmup not complete due to days), "
            f"got {weights}"
        )


class TestAllSubsEmpty:
    """Plan Combination Rule #5: all subs return {} → combined = {} →
    engine holds 100% cash. NOT an error."""

    def test_all_subs_return_empty_gives_empty_combined(self):
        from ez.portfolio.ensemble import StrategyEnsemble
        empty_a = _StaticStrategy({})
        empty_b = _StaticStrategy({})
        ens = StrategyEnsemble(strategies=[empty_a, empty_b], mode="equal")

        combined = ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        assert combined == {}

    def test_all_subs_return_empty_no_warning(self):
        """All-empty is a legitimate 'go to cash' signal, not an error."""
        from ez.portfolio.ensemble import StrategyEnsemble
        import logging

        empty_a = _StaticStrategy({})
        empty_b = _StaticStrategy({})
        ens = StrategyEnsemble(strategies=[empty_a, empty_b], mode="equal")

        captured = []
        handler = logging.Handler()
        handler.emit = lambda r: captured.append(r.getMessage())
        logger = logging.getLogger("ez.portfolio.ensemble")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        try:
            ens.generate_weights({}, datetime(2024, 1, 1), {}, {})
        finally:
            logger.removeHandler(handler)

        # No sub-strategy warnings (empty is legitimate, not failure)
        sub_warnings = [m for m in captured if "sub-strategy" in m]
        assert len(sub_warnings) == 0


class TestCorrelationWarnings:
    def _build_warmed_ensemble(self, returns_a, returns_b, warmup=8):
        from ez.portfolio.ensemble import StrategyEnsemble
        sub_a = _StaticStrategy({"X": 1.0})
        sub_b = _StaticStrategy({"Y": 1.0})
        ens = StrategyEnsemble(
            strategies=[sub_a, sub_b],
            mode="equal",
            warmup_rebalances=warmup,
            correlation_threshold=0.9,
        )
        for ra, rb in zip(returns_a, returns_b):
            ens.state["sub_hypothetical_returns"][0].append(ra)
            ens.state["sub_hypothetical_returns"][1].append(rb)
            ens.state["sub_target_weights"][0].append({"date": "2022-01-10", "weights": {}})
            ens.state["sub_target_weights"][1].append({"date": "2022-01-10", "weights": {}})
        ens.state["first_rebalance_date"] = "2022-01-10"
        ens.state["last_rebalance_date"] = "2022-06-10"
        return ens

    def test_identical_strategies_trigger_warning(self):
        """Two subs with identical return series → corr=1.0 → warning."""
        returns = [0.01, -0.02, 0.03, -0.01, 0.02, 0.015, -0.005, 0.01, 0.02, -0.01]
        ens = self._build_warmed_ensemble(returns, returns, warmup=8)

        # Trigger the check
        ens._check_correlation_warnings()

        warnings = ens.state["correlation_warnings"]
        assert len(warnings) == 1
        w = warnings[0]
        assert w["sub_i"] == 0
        assert w["sub_j"] == 1
        assert w["correlation"] > 0.99
        assert w["n_samples"] == 10
        assert isinstance(w["correlation"], float)

    def test_uncorrelated_no_warning(self):
        """Two subs with uncorrelated returns → no warning."""
        rng = np.random.default_rng(123)
        returns_a = rng.normal(0, 0.01, 12).tolist()
        returns_b = rng.normal(0, 0.01, 12).tolist()
        ens = self._build_warmed_ensemble(returns_a, returns_b, warmup=8)

        ens._check_correlation_warnings()

        # Correlation should be low (random) → no warning
        warnings = ens.state["correlation_warnings"]
        assert len(warnings) == 0

    def test_short_series_skipped(self):
        """Below min_overlap → no correlation computed (avoid false positives)."""
        from ez.portfolio.ensemble import StrategyEnsemble
        sub_a = _StaticStrategy({"X": 1.0})
        sub_b = _StaticStrategy({"Y": 1.0})
        ens = StrategyEnsemble(
            strategies=[sub_a, sub_b],
            mode="equal",
            warmup_rebalances=8,
        )
        # Only 3 entries — below min_overlap
        for _ in range(3):
            ens.state["sub_hypothetical_returns"][0].append(0.01)
            ens.state["sub_hypothetical_returns"][1].append(0.01)

        ens._check_correlation_warnings()
        assert len(ens.state["correlation_warnings"]) == 0


# ─── Task 3.5: Nested ensembles + e2e ────────────────────────────

class TestNestedEnsembles:
    def test_nested_ensemble_works_recursively(self):
        """StrategyEnsemble([EnsembleA, EnsembleB, Static]) works."""
        from ez.portfolio.ensemble import StrategyEnsemble

        inner_a = StrategyEnsemble(
            strategies=[_StaticStrategy({"X": 1.0}), _StaticStrategy({"Y": 1.0})],
            mode="equal",
        )
        inner_b = StrategyEnsemble(
            strategies=[_StaticStrategy({"Z": 1.0})],
            mode="equal",
        )
        outer = StrategyEnsemble(
            strategies=[inner_a, inner_b, _StaticStrategy({"W": 1.0})],
            mode="equal",
        )
        combined = outer.generate_weights({}, datetime(2024, 1, 1), {}, {})
        # 3 subs at outer level, each gets 1/3 weight
        # inner_a: {X:0.5, Y:0.5} × 1/3 → X:1/6, Y:1/6
        # inner_b: {Z:1.0} × 1/3 → Z:1/3
        # static: {W:1.0} × 1/3 → W:1/3
        assert abs(combined.get("X", 0) - 1/6) < 1e-6
        assert abs(combined.get("Y", 0) - 1/6) < 1e-6
        assert abs(combined.get("Z", 0) - 1/3) < 1e-6
        assert abs(combined.get("W", 0) - 1/3) < 1e-6

    def test_inner_state_isolated_from_outer(self):
        """Inner ensemble's self.state must not pollute outer's."""
        from ez.portfolio.ensemble import StrategyEnsemble

        inner = StrategyEnsemble(
            strategies=[_StaticStrategy({"A": 1.0})],
            mode="equal",
        )
        outer = StrategyEnsemble(
            strategies=[inner, _StaticStrategy({"B": 1.0})],
            mode="equal",
        )

        data, dates = _make_universe(n_days=30, symbols=["A", "B"])
        outer.generate_weights(data, dates[5].to_pydatetime(), {}, {})
        outer.generate_weights(data, dates[15].to_pydatetime(), {}, {})

        # Outer has its own ledger entries
        assert len(outer.state["sub_target_weights"]) == 2
        # Inner (deepcopied inside outer) has its own separate ledger
        inner_in_outer = outer._strategies[0]
        assert inner_in_outer is not inner  # deepcopy
        # Inner's state is independent (has its own ledger from its own generate_weights calls)
        assert id(inner_in_outer.state) != id(outer.state)

    def test_nested_lookback_no_double_buffer(self):
        """Inner ensemble lookback=300. Outer should NOT add another buffer.
        Outer lookback = max(inner=300, other_sub=252) = 300."""
        from ez.portfolio.ensemble import StrategyEnsemble

        # Create a sub-strategy with high lookback
        class _HighLookbackStrategy(PortfolioStrategy):
            @property
            def lookback_days(self) -> int:
                return 300
            def generate_weights(self, data, date, pw, pr):
                return {"A": 1.0}

        inner = StrategyEnsemble(
            strategies=[_HighLookbackStrategy()],
            mode="equal",
        )
        assert inner.lookback_days == 300  # max of subs, no buffer

        outer = StrategyEnsemble(
            strategies=[inner, _StaticStrategy({"B": 1.0})],
            mode="equal",
        )
        # outer.lookback = max(inner=300, static=252) = 300
        # NOT 300 + 20 + 20 (double buffer inflation)
        assert outer.lookback_days == 300


class TestEndToEndIntegration:
    def test_ensemble_through_run_portfolio_backtest(self):
        """StrategyEnsemble wrapping two TopNRotation subs through a
        real run_portfolio_backtest produces valid results."""
        from ez.portfolio.ensemble import StrategyEnsemble
        from ez.portfolio.cross_factor import MomentumRank, VolumeRank
        from ez.portfolio.portfolio_strategy import TopNRotation
        from ez.portfolio.engine import run_portfolio_backtest
        from ez.portfolio.calendar import TradingCalendar
        from ez.portfolio.universe import Universe

        rng = np.random.default_rng(42)
        n_days = 300
        n_stocks = 6
        dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
        data = {}
        for i in range(n_stocks):
            prices = 100 * np.cumprod(1 + rng.normal(0.0003 * (i + 1), 0.012, n_days))
            data[f"S{i:02d}"] = pd.DataFrame({
                "open": prices, "high": prices * 1.005, "low": prices * 0.995,
                "close": prices, "adj_close": prices,
                "volume": rng.integers(100_000, 1_000_000, n_days).astype(float),
            }, index=dates)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe([f"S{i:02d}" for i in range(n_stocks)])

        sub_mom = TopNRotation(factor=MomentumRank(period=20), top_n=3)
        sub_vol = TopNRotation(factor=VolumeRank(period=10), top_n=3)

        ensemble = StrategyEnsemble(
            strategies=[sub_mom, sub_vol],
            mode="equal",
        )

        result = run_portfolio_backtest(
            strategy=ensemble,
            universe=universe,
            universe_data=data,
            calendar=cal,
            start=dates[60].date(),
            end=dates[-1].date(),
            freq="weekly",
            initial_cash=1_000_000,
        )

        assert result is not None
        assert len(result.equity_curve) > 0
        assert result.equity_curve[-1] > 0  # not bankrupt
        # Ensemble had at least some rebalances
        assert len(result.rebalance_dates) > 5
