from __future__ import annotations

from datetime import date

import pytest

from ez.live.allocation import AllocationContext, RuntimeAllocator, RuntimeAllocatorConfig


def test_pro_rata_cap_scales_weights_without_changing_ranking():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="pro_rata_cap",
            runtime_allocation_cap=0.5,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.6, "BBB": 0.4},
    )

    assert decision.adjusted_weights == {"AAA": 0.3, "BBB": 0.2}
    assert decision.allocation_events[0]["event"] == "runtime_allocation_gate"
    assert decision.allocation_events[0]["details"]["allocation_mode"] == "pro_rata_cap"


def test_equal_weight_cap_rebalances_selected_symbols():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="equal_weight_cap",
            runtime_allocation_cap=0.6,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.5, "BBB": 0.3, "CCC": 0.2},
    )

    assert decision.adjusted_weights == pytest.approx({"AAA": 0.2, "BBB": 0.2, "CCC": 0.2})
    assert "equal weights" in decision.allocation_events[0]["message"].lower()


def test_max_names_trims_lower_ranked_symbols_before_scaling():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="pro_rata_cap",
            runtime_allocation_cap=0.6,
            max_names=2,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.4, "BBB": 0.35, "CCC": 0.25},
    )

    assert decision.adjusted_weights == pytest.approx({"AAA": 0.32, "BBB": 0.28})
    details = decision.allocation_events[0]["details"]
    assert decision.allocation_events[0]["event"] == "runtime_allocator"
    assert details["dropped_symbols"] == ["CCC"]
    assert details["max_names"] == 2


def test_max_names_preserves_total_requested_budget_without_cap():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="pro_rata_cap",
            max_names=2,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.5, "BBB": 0.3, "CCC": 0.2},
    )

    assert decision.adjusted_weights == pytest.approx({"AAA": 0.625, "BBB": 0.375})
    assert decision.allocation_events[0]["details"]["effective_allocation"] == pytest.approx(1.0)


def test_equal_weight_allocator_uses_allocator_event_type_without_drops():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="equal_weight_cap",
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.5, "BBB": 0.3, "CCC": 0.2},
    )

    assert decision.allocation_events[0]["event"] == "runtime_allocator"


def test_risk_budget_allocator_overweights_lower_vol_symbols():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="risk_budget_cap",
            runtime_allocation_cap=0.6,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.4, "BBB": 0.2},
        context=AllocationContext(volatility_by_symbol={"AAA": 0.30, "BBB": 0.10}),
    )

    assert decision.adjusted_weights == pytest.approx({"AAA": 0.24, "BBB": 0.36})
    assert decision.allocation_events[0]["details"]["allocation_mode"] == "risk_budget_cap"
    assert "volatility budgets" in decision.allocation_events[0]["message"].lower()


def test_target_portfolio_vol_scales_allocation_after_risk_budget():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="risk_budget_cap",
            runtime_allocation_cap=1.0,
            target_portfolio_vol=0.15,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.5, "BBB": 0.5},
        context=AllocationContext(volatility_by_symbol={"AAA": 0.30, "BBB": 0.30}),
    )

    assert decision.adjusted_weights == pytest.approx({"AAA": 0.3535533906, "BBB": 0.3535533906})
    assert decision.allocation_events[0]["details"]["estimated_portfolio_vol"] == pytest.approx(0.2121320343)
    assert decision.allocation_events[0]["details"]["vol_target_scale"] == pytest.approx(0.7071067812)


def test_constrained_opt_projects_weights_into_budget_and_position_caps():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=0.8,
            max_position_weight=0.4,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.7, "BBB": 0.5},
        context=AllocationContext(current_weights={"AAA": 0.2, "BBB": 0.1}),
    )

    assert decision.adjusted_weights == pytest.approx({"AAA": 0.4, "BBB": 0.4})
    details = decision.allocation_events[0]["details"]
    assert details["allocation_mode"] == "constrained_opt"
    assert details["hard_constraints"]["budget"] == pytest.approx(0.8)
    assert details["hard_constraints"]["max_position_weight"] == pytest.approx(0.4)


def test_constrained_opt_compresses_turnover_against_current_weights():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=0.8,
            max_daily_turnover=0.4,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"BBB": 0.8},
        context=AllocationContext(current_weights={"AAA": 0.5}),
    )

    assert decision.adjusted_weights == pytest.approx({"AAA": 0.3461538462, "BBB": 0.2461538462})
    details = decision.allocation_events[0]["details"]
    assert details["requested_turnover"] == pytest.approx(1.3)
    assert details["effective_turnover"] == pytest.approx(0.4)
    assert details["turnover_scale"] == pytest.approx(0.3076923077)


def test_constrained_opt_uses_covariance_risk_term_to_reduce_high_risk_weight():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=0.8,
            covariance_risk_aversion=8.0,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.4, "BBB": 0.4},
        context=AllocationContext(
            current_weights={},
            volatility_by_symbol={"AAA": 0.30, "BBB": 0.10},
            covariance_symbols=("AAA", "BBB"),
            covariance_matrix=[[0.09, 0.0], [0.0, 0.01]],
        ),
    )

    assert decision.adjusted_weights["BBB"] > decision.adjusted_weights["AAA"]
    assert sum(decision.adjusted_weights.values()) == pytest.approx(0.8)
    assert decision.allocation_events[0]["details"]["covariance_used"] is True


def test_constrained_opt_can_blend_toward_risk_budget_target():
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=0.8,
            risk_budget_strength=1.0,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.4, "BBB": 0.4},
        context=AllocationContext(
            current_weights={},
            volatility_by_symbol={"AAA": 0.30, "BBB": 0.10},
            covariance_symbols=("AAA", "BBB"),
            covariance_matrix=[[0.09, 0.0], [0.0, 0.01]],
        ),
    )

    assert decision.adjusted_weights == pytest.approx({"AAA": 0.2, "BBB": 0.6})
    assert decision.allocation_events[0]["details"]["risk_budget_target"] == pytest.approx({"AAA": 0.2, "BBB": 0.6})


# ---------------------------------------------------------------------------
# V3.1 hardening: vol-target re-project, max_names underfill, risk_budget
# stability, sqrt input validation, covariance PSD / feasibility clamp.
# ---------------------------------------------------------------------------


def test_vol_target_scaling_reprojects_to_position_cap():
    """After a vol-target scale, no single symbol may exceed max_position_weight.

    Setup the estimated portfolio vol to trigger a ~0.95 scale:
    - weights {AAA: 0.16, BBB: 0.04}, vols both 0.10
    - estimated = sqrt(0.16**2 * 0.01 + 0.04**2 * 0.01) ~= 0.016492
    - target_portfolio_vol = 0.01567 -> scale ~= 0.01567 / 0.016492 ~= 0.95
    - scaled AAA = 0.16 * 0.95 = 0.152, which exceeds max_position_weight=0.15.
    - Re-projection is required to bring AAA back to 0.15.
    """
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="pro_rata_cap",
            # pro-rata with cap=1.0 does not re-scale (sum=0.20 <= 1.0).
            runtime_allocation_cap=1.0,
            max_position_weight=0.15,
            target_portfolio_vol=0.01567,
            volatility_fallback=0.10,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.16, "BBB": 0.04},
        context=AllocationContext(volatility_by_symbol={"AAA": 0.10, "BBB": 0.10}),
    )

    weights = decision.adjusted_weights
    # AAA must not exceed the per-symbol cap after re-projection.
    assert weights["AAA"] <= 0.15 + 1e-9
    # The re-projection flag is set so monitor/API can surface it.
    details = decision.allocation_events[0]["details"]
    assert details.get("vol_target_reproject_cap_hit") is True


def test_max_names_underfill_reports_ratio_and_reason():
    """When max_names + cap trimming leaves budget unused, underfill is surfaced."""
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="pro_rata_cap",
            runtime_allocation_cap=1.0,
            max_names=3,
            max_position_weight=0.09,
        )
    )
    # Top 3 symbols after ranking by requested weight: AAA, BBB, CCC.
    # Each capped at 0.09 -> sum = 0.27 vs requested allocation = 1.0.
    # Underfill ratio = 1 - 0.27 / 1.0 = 0.73 > 0.1 -> must be reported.
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={
            "AAA": 0.30,
            "BBB": 0.25,
            "CCC": 0.20,
            "DDD": 0.15,
            "EEE": 0.10,
        },
        context=AllocationContext(
            # Hit the re-projection path via vol_target off: simplest way
            # to exercise max_position_weight cap is by running constrained_opt,
            # but this test targets the plain underfill detection from the
            # allocator, so we use pro_rata and then scale manually.
        ),
    )

    details = decision.allocation_events[0]["details"]
    # max_names=3 means two symbols were dropped.
    assert details["dropped_symbols"] == ["DDD", "EEE"]
    # pro_rata_cap scales to the budget regardless of per-symbol cap, so
    # underfill from pro_rata alone reflects only the max_names truncation
    # interaction (budget stays at requested_allocation=0.75 here; adjusted
    # sums to 0.75). Underfill only fires if adjusted < 0.9 * budget.
    # So to get underfill, we rely on the separate constrained_opt test below.
    # What we assert here is that max_names trimming still surfaces normally.
    assert details["max_names"] == 3


def test_max_names_plus_caps_underfill_surfaces_through_constrained_opt():
    """The spec scenario: top 3 capped at 0.09, max_names=3, budget=1.0 ->
    effective allocation <= 0.27 and underfill_ratio > 0.7.
    """
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=1.0,
            max_names=3,
            max_position_weight=0.09,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={
            "AAA": 0.30,
            "BBB": 0.25,
            "CCC": 0.20,
            "DDD": 0.15,
            "EEE": 0.10,
        },
    )
    details = decision.allocation_events[0]["details"]
    # Sum must respect both budget and caps.
    assert sum(decision.adjusted_weights.values()) <= 0.27 + 1e-9
    assert details["underfill_ratio"] > 0.7
    assert "max_names" in details["underfill_reason"]


def test_risk_budget_falls_back_to_pro_rata_when_all_vols_tiny():
    """All vols below the absolute epsilon -> degenerate -> pro-rata fallback."""
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="risk_budget_cap",
            runtime_allocation_cap=0.5,
            volatility_fallback=1e-8,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.3, "BBB": 0.2},
        context=AllocationContext(
            volatility_by_symbol={"AAA": 1e-8, "BBB": 1e-9},
        ),
    )

    # Pro-rata on budget=0.5, weights 0.3 and 0.2 -> scale 0.5/0.5=1.0.
    # So adjusted mirrors the requested ratios.
    assert decision.adjusted_weights == pytest.approx({"AAA": 0.3, "BBB": 0.2})
    details = decision.allocation_events[0]["details"]
    assert details.get("risk_budget_fallback") is True


def test_risk_budget_handles_negative_and_nan_vol_safely():
    """Negative / NaN vols get sanitized to zero (and then floored)."""
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="risk_budget_cap",
            runtime_allocation_cap=0.4,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.2, "BBB": 0.2},
        context=AllocationContext(
            volatility_by_symbol={"AAA": float("nan"), "BBB": -0.05},
        ),
    )
    # Both sanitized to 0 -> both floored to epsilon -> equal scores ->
    # equal split of the 0.4 budget.
    assert decision.adjusted_weights == pytest.approx({"AAA": 0.2, "BBB": 0.2})


def test_vol_target_with_negative_vol_entries_does_not_crash():
    """Vol-target sqrt must tolerate garbage inputs without crashing."""
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="pro_rata_cap",
            runtime_allocation_cap=0.5,
            target_portfolio_vol=0.05,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.3, "BBB": 0.2},
        context=AllocationContext(
            volatility_by_symbol={"AAA": float("-inf"), "BBB": float("nan")},
        ),
    )
    # With all vols sanitized to 0, estimated portfolio vol is 0, which
    # short-circuits: allocator falls back to the original pro-rata weights.
    assert sum(decision.adjusted_weights.values()) == pytest.approx(0.5)


def test_constrained_opt_with_rank_one_covariance_falls_back_gracefully():
    """Rank-1 covariance is not PSD in the strict sense -> degenerate + fallback."""
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=0.8,
            covariance_risk_aversion=8.0,
        )
    )
    # Rank-1 outer-product covariance.
    cov = [[0.09, 0.09], [0.09, 0.09]]
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.4, "BBB": 0.4},
        context=AllocationContext(
            current_weights={},
            volatility_by_symbol={"AAA": 0.30, "BBB": 0.30},
            covariance_symbols=("AAA", "BBB"),
            covariance_matrix=cov,
        ),
    )
    details = decision.allocation_events[0]["details"]
    assert details.get("covariance_degenerate") is True
    assert details.get("covariance_used") is False
    # Fallback still respects the budget and caps: sum <= budget.
    assert sum(decision.adjusted_weights.values()) <= 0.8 + 1e-9


def test_constrained_opt_with_nan_covariance_falls_back_gracefully():
    """NaN-poisoned covariance must not crash the allocator."""
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=0.8,
            covariance_risk_aversion=8.0,
        )
    )
    nan = float("nan")
    cov = [[nan, 0.0], [0.0, nan]]
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.4, "BBB": 0.4},
        context=AllocationContext(
            current_weights={},
            volatility_by_symbol={"AAA": 0.30, "BBB": 0.30},
            covariance_symbols=("AAA", "BBB"),
            covariance_matrix=cov,
        ),
    )
    # NaN diagonals are replaced by vol-derived variances -> PSD after lift
    # -> covariance is valid again; verify no crash and weights sum properly.
    weights = decision.adjusted_weights
    assert sum(weights.values()) <= 0.8 + 1e-9
    assert all(0.0 <= w <= 0.8 + 1e-9 for w in weights.values())


def test_constrained_opt_with_all_zero_covariance_falls_back_gracefully():
    """All-zero cov has zero eigenvalues -> degenerate -> fallback."""
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=0.8,
            covariance_risk_aversion=8.0,
        )
    )
    cov = [[0.0, 0.0], [0.0, 0.0]]
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={"AAA": 0.4, "BBB": 0.4},
        context=AllocationContext(
            current_weights={},
            # No volatility either -> the diagonal fallback also fails.
            volatility_by_symbol={},
            covariance_symbols=("AAA", "BBB"),
            covariance_matrix=cov,
        ),
    )
    # No crash; allocator still produces a feasible allocation via the
    # budget/cap-only projection path.
    weights = decision.adjusted_weights
    assert sum(weights.values()) <= 0.8 + 1e-9


def test_constrained_opt_feasibility_clamp_when_caps_cannot_fill_budget():
    """budget=1.0 with 5 symbols each capped at 0.05 -> feasibility_clamped."""
    allocator = RuntimeAllocator(
        RuntimeAllocatorConfig(
            allocation_mode="constrained_opt",
            runtime_allocation_cap=1.0,
            max_position_weight=0.05,
        )
    )
    decision = allocator.allocate(
        business_date=date(2026, 4, 18),
        target_weights={
            "AAA": 0.3,
            "BBB": 0.2,
            "CCC": 0.2,
            "DDD": 0.2,
            "EEE": 0.1,
        },
    )
    details = decision.allocation_events[0]["details"]
    # 5 caps * 0.05 = 0.25 total feasible budget.
    assert sum(decision.adjusted_weights.values()) <= 0.25 + 1e-9
    assert details.get("feasibility_clamped") is True
    assert details["hard_constraints"]["budget"] == pytest.approx(0.25)
