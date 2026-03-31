"""Tests for industry neutralization (V2.11.1 F2)."""
import pandas as pd
import pytest

from ez.portfolio.neutralization import neutralize_by_industry


@pytest.fixture
def industry_map():
    return {
        "A": "银行", "B": "银行", "C": "银行",
        "D": "食品", "E": "食品",
        "F": "科技",  # only 1 stock in industry
    }


class TestNeutralizeBasic:
    def test_demeaning(self, industry_map):
        scores = pd.Series({"A": 10.0, "B": 8.0, "C": 6.0, "D": 3.0, "E": 5.0, "F": 7.0})
        result, warnings = neutralize_by_industry(scores, industry_map)
        # 银行均值=8, A中性化=2, B=0, C=-2; 食品均值=4, D=-1, E=1; F单独→dropped
        assert abs(result["A"] - 2.0) < 1e-10
        assert abs(result["B"] - 0.0) < 1e-10
        assert abs(result["C"] - (-2.0)) < 1e-10
        assert abs(result["D"] - (-1.0)) < 1e-10
        assert abs(result["E"] - 1.0) < 1e-10
        assert "F" not in result  # single-stock industry dropped

    def test_single_stock_industry_dropped(self, industry_map):
        scores = pd.Series({"A": 10.0, "B": 8.0, "F": 7.0})
        result, _ = neutralize_by_industry(scores, industry_map)
        assert "F" not in result

    def test_no_industry_keeps_original(self):
        industry_map = {"A": "银行", "B": "银行"}
        scores = pd.Series({"A": 10.0, "B": 8.0, "C": 5.0})  # C has no industry
        result, warnings = neutralize_by_industry(scores, industry_map)
        assert result["C"] == 5.0  # kept original
        assert any("无行业标签" in w for w in warnings)


class TestCoverageThreshold:
    def test_low_coverage_skips(self):
        industry_map = {"A": "银行"}  # only 1 of 5 stocks
        scores = pd.Series({"A": 10.0, "B": 8.0, "C": 6.0, "D": 3.0, "E": 5.0})
        result, warnings = neutralize_by_industry(scores, industry_map, min_coverage=0.5)
        # Coverage = 20% < 50% → skip
        assert result.equals(scores)
        assert any("跳过" in w for w in warnings)

    def test_sufficient_coverage_proceeds(self, industry_map):
        scores = pd.Series({"A": 10.0, "B": 8.0, "C": 6.0, "D": 3.0, "E": 5.0})
        result, warnings = neutralize_by_industry(scores, industry_map, min_coverage=0.5)
        # 5/5 = 100% coverage (F not in scores) → proceeds
        assert not result.equals(scores)  # should be neutralized


class TestEdgeCases:
    def test_empty_scores(self):
        result, warnings = neutralize_by_industry(pd.Series(dtype=float), {})
        assert len(result) == 0

    def test_all_same_industry(self):
        industry_map = {"A": "银行", "B": "银行", "C": "银行"}
        scores = pd.Series({"A": 10.0, "B": 8.0, "C": 6.0})
        result, _ = neutralize_by_industry(scores, industry_map)
        # Mean=8, so A=2, B=0, C=-2
        assert abs(result.sum()) < 1e-10  # sum of neutralized = 0

    def test_neutralize_raw_vs_rank_differs(self):
        """C1 validation: neutralizing raw values vs ranks gives different results."""
        industry_map = {"A": "银行", "B": "银行", "C": "食品", "D": "食品"}
        raw = pd.Series({"A": 0.20, "B": 0.18, "C": 0.05, "D": 0.04})
        ranked = raw.rank(pct=True)

        n_raw, _ = neutralize_by_industry(raw, industry_map)
        n_ranked, _ = neutralize_by_industry(ranked, industry_map)

        # Re-rank both and compare — they should differ
        rank_from_raw = n_raw.rank(pct=True)
        rank_from_ranked = n_ranked.rank(pct=True)
        # In this case both give same within-group ordering, but values differ
        assert not n_raw.equals(n_ranked)
