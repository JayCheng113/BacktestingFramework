"""Tests for V2.22 verdict engine."""
from __future__ import annotations

import pytest

from ez.research.verdict import (
    VerdictThresholds,
    compute_verdict,
    CheckResult,
)


class TestVerdictThresholds:
    def test_default_thresholds_are_reasonable(self):
        t = VerdictThresholds()
        assert 0 < t.max_degradation < t.max_degradation_fail
        assert 0 < t.max_p_value < t.max_p_value_fail
        assert 0 < t.min_deflated_sharpe_fail < t.min_deflated_sharpe


class TestComputeVerdict:
    def test_all_pass_produces_pass_verdict(self):
        v = compute_verdict(
            wf_aggregate={"degradation": 0.1, "overfitting_score": 0.05},
            bootstrap={"ci_lower": 0.3, "ci_upper": 1.5, "p_value": 0.01},
            deflated={"deflated_sharpe": 0.85, "sharpe": 1.5},
            min_btl_result={"actual_years": 5.0, "min_btl_years": 2.0},
            annual={
                "per_year": [{"year": 2020, "sharpe": 1.0, "ret": 0.1, "mdd": -0.05}],
                "profitable_ratio": 0.8,
            },
        )
        assert v.result == "pass"
        assert v.failed == 0
        assert len(v.checks) > 0

    def test_failing_p_value_produces_fail(self):
        v = compute_verdict(
            bootstrap={"ci_lower": -0.5, "ci_upper": 0.5, "p_value": 0.8},
        )
        assert v.result == "fail"
        assert v.failed >= 1
        # At least one check should mention p-value or CI
        assert any("p" in c.reason.lower() or "ci" in c.reason.lower() for c in v.checks)

    def test_degradation_warn_threshold(self):
        """Degradation between warn and fail thresholds → warn."""
        v = compute_verdict(
            wf_aggregate={"degradation": 0.5},
        )
        degr_check = next(c for c in v.checks if "degradation" in c.name.lower())
        assert degr_check.status == "warn"

    def test_degradation_fail_threshold(self):
        v = compute_verdict(
            wf_aggregate={"degradation": 0.9},
        )
        degr_check = next(c for c in v.checks if "degradation" in c.name.lower())
        assert degr_check.status == "fail"

    def test_ci_including_zero_fails(self):
        v = compute_verdict(
            bootstrap={"ci_lower": -0.1, "ci_upper": 0.5, "p_value": 0.1},
        )
        ci_check = next(c for c in v.checks if "ci" in c.name.lower())
        assert ci_check.status == "fail"

    def test_low_deflated_sharpe_warns(self):
        v = compute_verdict(
            deflated={"deflated_sharpe": 0.45, "sharpe": 1.0},
        )
        dsr_check = next(c for c in v.checks if "deflated" in c.name.lower())
        assert dsr_check.status == "warn"

    def test_min_btl_fail_when_actual_insufficient(self):
        v = compute_verdict(
            min_btl_result={"actual_years": 1.0, "min_btl_years": 3.0},
        )
        btl_check = next(c for c in v.checks if "backtest length" in c.name.lower())
        assert btl_check.status in ("fail", "warn")

    def test_annual_breakdown_profitable_ratio(self):
        v = compute_verdict(
            annual={
                "per_year": [{"year": y, "sharpe": 0, "ret": 0.05, "mdd": -0.1} for y in range(5)],
                "profitable_ratio": 0.2,  # Below fail threshold (0.4)
            },
        )
        ann_check = next(c for c in v.checks if "annual" in c.name.lower())
        assert ann_check.status == "fail"

    def test_summary_is_chinese(self):
        v = compute_verdict(
            bootstrap={"ci_lower": 0.3, "ci_upper": 1.5, "p_value": 0.01},
        )
        # Should contain Chinese characters
        assert any("\u4e00" <= ch <= "\u9fff" for ch in v.summary)

    def test_to_dict_is_json_safe(self):
        import json
        v = compute_verdict(
            bootstrap={"ci_lower": 0.3, "ci_upper": 1.5, "p_value": 0.01},
        )
        d = v.to_dict()
        json.dumps(d)  # should not raise

    def test_verdict_counts_match(self):
        v = compute_verdict(
            wf_aggregate={"degradation": 0.1},  # pass
            bootstrap={"ci_lower": -0.1, "ci_upper": 0.5, "p_value": 0.08},  # fail, warn
            deflated={"deflated_sharpe": 0.7, "sharpe": 1.0},  # pass
        )
        assert v.passed + v.warned + v.failed == v.total

    def test_custom_thresholds_applied(self):
        # Strict thresholds
        strict = VerdictThresholds(max_p_value=0.01, max_p_value_fail=0.02)
        v = compute_verdict(
            bootstrap={"ci_lower": 0.3, "ci_upper": 1.5, "p_value": 0.03},
            thresholds=strict,
        )
        p_check = next(c for c in v.checks if "p-value" in c.name.lower())
        assert p_check.status == "fail"
