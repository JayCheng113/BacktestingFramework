"""Tests for AlphaCombiner (V2.11.1 F3): z-score, weighting, edge cases."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from ez.portfolio.alpha_combiner import AlphaCombiner
from ez.portfolio.cross_factor import CrossSectionalFactor


class MockFactor(CrossSectionalFactor):
    """Test factor returning fixed raw scores."""
    def __init__(self, factor_name: str, scores: dict[str, float]):
        self._name = factor_name
        self._scores = scores

    @property
    def name(self):
        return self._name

    def compute_raw(self, universe_data, date):
        return pd.Series(self._scores)

    def compute(self, universe_data, date):
        raw = self.compute_raw(universe_data, date)
        return raw.rank(pct=True) if len(raw) > 0 else raw

# Remove MockFactor from registry
CrossSectionalFactor._registry.pop("MockFactor", None)


DUMMY_UNIVERSE = {"A": pd.DataFrame(), "B": pd.DataFrame(), "C": pd.DataFrame(), "D": pd.DataFrame()}
DT = datetime(2024, 5, 1)


class TestEqualWeight:
    def test_basic_combination(self):
        f1 = MockFactor("f1", {"A": 10, "B": 20, "C": 30, "D": 40})
        f2 = MockFactor("f2", {"A": 40, "B": 30, "C": 20, "D": 10})
        combiner = AlphaCombiner(factors=[f1, f2])
        raw = combiner.compute_raw(DUMMY_UNIVERSE, DT)
        # f1 z-scores: [-1.34, -0.45, 0.45, 1.34], f2 z-scores: [1.34, 0.45, -0.45, -1.34]
        # Equal weight mean → all should be ~0
        assert len(raw) == 4
        assert abs(raw.mean()) < 1e-10  # opposing factors cancel out

    def test_ranked_output(self):
        f1 = MockFactor("f1", {"A": 10, "B": 20, "C": 30})
        combiner = AlphaCombiner(factors=[f1])
        ranked = combiner.compute(DUMMY_UNIVERSE, DT)
        assert ranked.min() >= 0 and ranked.max() <= 1.0


class TestWeightedCombination:
    def test_ic_weights(self):
        f1 = MockFactor("f1", {"A": 10, "B": 20, "C": 30})
        f2 = MockFactor("f2", {"A": 30, "B": 20, "C": 10})
        # f1 gets 3x weight
        combiner = AlphaCombiner(factors=[f1, f2], weights={"f1": 0.9, "f2": 0.3})
        raw = combiner.compute_raw(DUMMY_UNIVERSE, DT)
        # f1 dominates → C should rank highest (f1: C=30 is highest)
        assert raw["C"] > raw["A"]

    def test_zero_weight_ignored(self):
        f1 = MockFactor("f1", {"A": 10, "B": 20, "C": 30})
        f2 = MockFactor("f2", {"A": 99, "B": 1, "C": 50})
        combiner = AlphaCombiner(factors=[f1, f2], weights={"f1": 1.0, "f2": 0.0})
        raw = combiner.compute_raw(DUMMY_UNIVERSE, DT)
        # Only f1 matters → C > B > A
        assert raw["C"] > raw["B"] > raw["A"]


class TestDivisionByZero:
    """C2: std < 1e-10 should not crash."""

    def test_constant_factor(self):
        f1 = MockFactor("f1", {"A": 5.0, "B": 5.0, "C": 5.0})  # all same → std=0
        f2 = MockFactor("f2", {"A": 10, "B": 20, "C": 30})
        combiner = AlphaCombiner(factors=[f1, f2])
        raw = combiner.compute_raw(DUMMY_UNIVERSE, DT)
        assert len(raw) == 3  # should not crash
        # f1 contributes 0 → result driven by f2 only
        assert raw["C"] > raw["A"]

    def test_single_stock(self):
        f1 = MockFactor("f1", {"A": 10})
        combiner = AlphaCombiner(factors=[f1])
        raw = combiner.compute_raw({"A": pd.DataFrame()}, DT)
        assert len(raw) == 1


class TestMissingValues:
    """C3: partial missing data should re-normalize weights, not exclude stocks."""

    def test_partial_coverage(self):
        f1 = MockFactor("f1", {"A": 10, "B": 20, "C": 30, "D": 40})
        f2 = MockFactor("f2", {"A": 100, "B": 200})  # C, D missing
        combiner = AlphaCombiner(factors=[f1, f2])
        raw = combiner.compute_raw(DUMMY_UNIVERSE, DT)
        # A and B have both factors; C and D have only f1 → all 4 should have scores
        assert "C" in raw.index
        assert "D" in raw.index

    def test_no_overlap(self):
        f1 = MockFactor("f1", {"A": 10, "B": 20})
        f2 = MockFactor("f2", {"C": 30, "D": 40})
        combiner = AlphaCombiner(factors=[f1, f2])
        raw = combiner.compute_raw(DUMMY_UNIVERSE, DT)
        # Each stock only has 1 factor → still gets a score
        assert len(raw) == 4


class TestNotRegistered:
    def test_not_in_registry(self):
        reg = CrossSectionalFactor.get_registry()
        assert "AlphaCombiner" not in reg


class TestName:
    def test_composite_name(self):
        f1 = MockFactor("ep", {})
        f2 = MockFactor("roe", {})
        combiner = AlphaCombiner(factors=[f1, f2])
        assert combiner.name == "alpha(ep+roe)"


class TestBackwardCompat:
    """Legacy factor without compute_raw() should still work via default fallback."""

    def test_legacy_factor_in_combiner(self):
        class LegacyFactor(CrossSectionalFactor):
            @property
            def name(self):
                return "legacy"
            def compute(self, universe_data, date):
                return pd.Series({"A": 0.8, "B": 0.5, "C": 0.2})

        CrossSectionalFactor._registry.pop("LegacyFactor", None)

        f = LegacyFactor()
        combiner = AlphaCombiner(factors=[f])
        raw = combiner.compute_raw(DUMMY_UNIVERSE, DT)
        assert len(raw) == 3  # should work via default compute_raw() fallback
