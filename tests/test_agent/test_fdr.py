"""Tests for FDR (False Discovery Rate) correction."""
from __future__ import annotations

import math

import pytest

from ez.agent.fdr import FDRResult, apply_fdr, benjamini_hochberg, bonferroni


class TestBonferroni:
    def test_empty(self):
        assert bonferroni([]) == []

    def test_single(self):
        results = bonferroni([("s1", 0.03)], alpha=0.05)
        assert len(results) == 1
        assert results[0].adjusted_p_value == 0.03  # 0.03 * 1 = 0.03
        assert results[0].is_significant is True

    def test_multiple(self):
        pvals = [("s1", 0.01), ("s2", 0.03), ("s3", 0.04)]
        results = bonferroni(pvals, alpha=0.05)
        # Adjusted: 0.03, 0.09, 0.12
        assert results[0].adjusted_p_value == pytest.approx(0.03)
        assert results[0].is_significant is True
        assert results[1].adjusted_p_value == pytest.approx(0.09)
        assert results[1].is_significant is False
        assert results[2].adjusted_p_value == pytest.approx(0.12)
        assert results[2].is_significant is False

    def test_capped_at_one(self):
        results = bonferroni([("s1", 0.5), ("s2", 0.8)], alpha=0.05)
        assert results[0].adjusted_p_value == 1.0
        assert results[1].adjusted_p_value == 1.0

    def test_nan_p_value(self):
        results = bonferroni([("s1", 0.01), ("s2", float("nan"))], alpha=0.05)
        assert results[0].is_significant is True
        assert math.isnan(results[1].adjusted_p_value)
        assert results[1].is_significant is False


class TestBenjaminiHochberg:
    def test_empty(self):
        assert benjamini_hochberg([]) == []

    def test_single(self):
        results = benjamini_hochberg([("s1", 0.03)], alpha=0.05)
        assert results[0].adjusted_p_value == 0.03
        assert results[0].is_significant is True

    def test_classic_example(self):
        # 5 tests with p-values
        pvals = [
            ("s1", 0.005),
            ("s2", 0.009),
            ("s3", 0.025),
            ("s4", 0.050),
            ("s5", 0.400),
        ]
        results = benjamini_hochberg(pvals, alpha=0.05)
        # BH: rank 1: 0.005*5/1=0.025, rank 2: 0.009*5/2=0.0225,
        #     rank 3: 0.025*5/3=0.0417, rank 4: 0.050*5/4=0.0625,
        #     rank 5: 0.400*5/5=0.4
        # Monotonicity: [0.0225, 0.0225, 0.0417, 0.0625, 0.4]
        assert results[0].is_significant is True  # 0.005 → adj ≈ 0.0225
        assert results[1].is_significant is True  # 0.009 → adj ≈ 0.0225
        assert results[2].is_significant is True  # 0.025 → adj ≈ 0.0417
        assert results[3].is_significant is False  # 0.050 → adj ≈ 0.0625
        assert results[4].is_significant is False  # 0.400 → adj = 0.4

    def test_preserves_order(self):
        # Input not sorted by p-value
        pvals = [("s3", 0.04), ("s1", 0.01), ("s2", 0.02)]
        results = benjamini_hochberg(pvals, alpha=0.05)
        assert results[0].spec_id == "s3"
        assert results[1].spec_id == "s1"
        assert results[2].spec_id == "s2"

    def test_nan_excluded(self):
        pvals = [("s1", 0.01), ("s2", float("nan")), ("s3", 0.03)]
        results = benjamini_hochberg(pvals, alpha=0.05)
        assert math.isnan(results[1].adjusted_p_value)
        assert results[1].is_significant is False
        # s1 and s3 should be adjusted with m=2 (only valid p-values)
        assert results[0].is_significant is True

    def test_all_nan(self):
        pvals = [("s1", float("nan")), ("s2", float("inf"))]
        results = benjamini_hochberg(pvals, alpha=0.05)
        assert all(not r.is_significant for r in results)


class TestApplyFDR:
    def test_apply_bh(self):
        results = [
            {"spec_id": "s1", "p_value": 0.01},
            {"spec_id": "s2", "p_value": 0.04},
            {"spec_id": "s3", "p_value": 0.10},
        ]
        apply_fdr(results, method="bh", alpha=0.05)
        assert "fdr_adjusted_p" in results[0]
        assert "fdr_significant" in results[0]
        assert results[0]["fdr_method"] == "bh"

    def test_apply_bonferroni(self):
        results = [
            {"spec_id": "s1", "p_value": 0.01},
            {"spec_id": "s2", "p_value": 0.04},
        ]
        apply_fdr(results, method="bonferroni", alpha=0.05)
        assert results[0]["fdr_method"] == "bonferroni"
        assert results[0]["fdr_adjusted_p"] == pytest.approx(0.02)

    def test_missing_p_value(self):
        results = [{"spec_id": "s1"}]
        apply_fdr(results, method="bh")
        assert math.isnan(results[0]["fdr_adjusted_p"])
