"""Unit tests for V3.3.46 capital expansion + kill-switch policy."""
from __future__ import annotations

import pytest

from ez.live.capital_policy import (
    CapitalPolicyConfig,
    CapitalPolicyEngine,
    CapitalRejectReason,
    CapitalStage,
    StageLimits,
)


KILL_ENV = "EZ_LIVE_QMT_KILL_SWITCH_TEST"


def _default_config(stage: CapitalStage = CapitalStage.SMALL_WHITELIST) -> CapitalPolicyConfig:
    """Build a deterministic config for tests.

    Uses a dedicated env var name so we do not clash with any real runtime
    `EZ_LIVE_QMT_KILL_SWITCH` that might be present in the shell.
    """
    cfg = CapitalPolicyConfig.default_staircase()
    cfg.current_stage = stage
    cfg.kill_switch_env_var = KILL_ENV
    return cfg


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KILL_ENV, raising=False)


# ---------------------------------------------------------------------------
# READ_ONLY
# ---------------------------------------------------------------------------
def test_read_only_blocks_all_orders(monkeypatch: pytest.MonkeyPatch):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.READ_ONLY))

    reject = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=1_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="paper",
    )

    assert isinstance(reject, CapitalRejectReason)
    assert reject.rule == "read_only_stage_blocks_orders"
    assert reject.stage == CapitalStage.READ_ONLY

    # READ_ONLY blocks real brokers too.
    reject_qmt = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=1_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    assert reject_qmt is not None
    # Either read_only or kill-switch downgrade — kill-switch is not
    # active here, so read_only wins.
    assert reject_qmt.rule == "read_only_stage_blocks_orders"


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------
def test_kill_switch_env_downgrades_to_paper_sim(monkeypatch: pytest.MonkeyPatch):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.SMALL_WHITELIST))
    assert engine.effective_stage() == CapitalStage.SMALL_WHITELIST
    assert engine.is_kill_switch_active() is False

    monkeypatch.setenv(KILL_ENV, "1")
    assert engine.is_kill_switch_active() is True
    assert engine.effective_stage() == CapitalStage.PAPER_SIM

    # Case-insensitive truthy values.
    for val in ("true", "YES", "On", "TRUE"):
        monkeypatch.setenv(KILL_ENV, val)
        assert engine.is_kill_switch_active() is True

    # Falsy values keep the configured stage.
    for val in ("0", "false", "off", "", "nope"):
        monkeypatch.setenv(KILL_ENV, val)
        assert engine.is_kill_switch_active() is False
        assert engine.effective_stage() == CapitalStage.SMALL_WHITELIST


def test_kill_switch_blocks_real_broker_orders(monkeypatch: pytest.MonkeyPatch):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.SMALL_WHITELIST))

    monkeypatch.setenv(KILL_ENV, "true")

    reject = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=1_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    assert reject is not None
    assert reject.rule == "kill_switch_capital_downgrade"
    assert reject.stage == CapitalStage.PAPER_SIM
    assert reject.details["broker_type"] == "qmt"
    assert reject.details["configured_stage"] == str(CapitalStage.SMALL_WHITELIST)
    assert reject.details["effective_stage"] == str(CapitalStage.PAPER_SIM)
    assert reject.details["kill_switch_env_var"] == KILL_ENV

    # Paper orders still flow through the downgraded paper stage (which has
    # 1e9 caps in the default staircase) — they should be accepted.
    paper_reject = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=1_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="paper",
    )
    assert paper_reject is None


# ---------------------------------------------------------------------------
# SMALL_WHITELIST limits
# ---------------------------------------------------------------------------
def test_small_whitelist_allows_within_limit(monkeypatch: pytest.MonkeyPatch):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.SMALL_WHITELIST))

    # Small order well within caps — 10k notional, 5k exposure, etc.
    reject = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=10_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    assert reject is None


def test_small_whitelist_rejects_beyond_daily_notional(monkeypatch: pytest.MonkeyPatch):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.SMALL_WHITELIST))

    # Default SMALL_WHITELIST.max_capital_per_day = 50_000
    # Already spent 45_000, try another 10_000 -> projected 55_000 > cap.
    reject = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=10_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=45_000.0,
        broker_type="qmt",
    )
    assert reject is not None
    assert reject.rule == "max_capital_per_day_exceeded"
    assert reject.details["projected_day_notional"] == pytest.approx(55_000.0)
    assert reject.details["max_capital_per_day"] == pytest.approx(50_000.0)


def test_small_whitelist_rejects_beyond_per_symbol_position(
    monkeypatch: pytest.MonkeyPatch,
):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.SMALL_WHITELIST))

    # max_position_value_per_symbol = 20_000; already 15k in symbol,
    # buy 10k more -> projected 25k.
    reject = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=10_000.0,
        current_position_value=15_000.0,
        current_total_exposure=15_000.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    assert reject is not None
    assert reject.rule == "max_position_value_per_symbol_exceeded"
    assert reject.details["projected_position_value"] == pytest.approx(25_000.0)
    assert reject.details["max_position_value_per_symbol"] == pytest.approx(20_000.0)
    assert reject.details["symbol"] == "600000.SH"


def test_small_whitelist_rejects_beyond_total_gross_exposure(
    monkeypatch: pytest.MonkeyPatch,
):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.SMALL_WHITELIST))

    # max_total_gross_exposure = 100_000. Already at 95k, +10k -> 105k.
    # Also stay under the per-symbol cap (20k) — different symbol at 0.
    reject = engine.check_order(
        symbol="000001.SZ",
        side="buy",
        notional=10_000.0,
        current_position_value=0.0,
        current_total_exposure=95_000.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    assert reject is not None
    assert reject.rule == "max_total_gross_exposure_exceeded"
    assert reject.details["projected_total_exposure"] == pytest.approx(105_000.0)
    assert reject.details["max_total_gross_exposure"] == pytest.approx(100_000.0)


def test_symbol_whitelist_enforcement_when_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    _clean_env(monkeypatch)
    cfg = _default_config(CapitalStage.SMALL_WHITELIST)
    # Override the SMALL_WHITELIST stage to require a whitelist.
    cfg.stage_limits[CapitalStage.SMALL_WHITELIST] = StageLimits(
        max_capital_per_day=50_000.0,
        max_position_value_per_symbol=20_000.0,
        max_total_gross_exposure=100_000.0,
        allowed_symbols=["600000.SH", "601398.SH"],
    )
    engine = CapitalPolicyEngine(cfg)

    # Whitelisted symbol ok.
    ok = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=5_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    assert ok is None

    # Non-whitelisted symbol rejected.
    reject = engine.check_order(
        symbol="000001.SZ",
        side="buy",
        notional=5_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    assert reject is not None
    assert reject.rule == "symbol_not_in_stage_whitelist"
    assert reject.details["symbol"] == "000001.SZ"
    assert reject.details["allowed_symbols"] == ["600000.SH", "601398.SH"]


# ---------------------------------------------------------------------------
# Stage transition gate
# ---------------------------------------------------------------------------
def test_stage_transition_gate_passes_when_criteria_met(
    monkeypatch: pytest.MonkeyPatch,
):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.PAPER_SIM))

    eligible, blockers = engine.check_stage_transition_eligible(
        target=CapitalStage.SMALL_WHITELIST,
        recent_drift_days=7,
        recent_order_success_rate=0.95,
    )
    assert eligible is True
    assert blockers == []


def test_stage_transition_gate_blocks_when_insufficient_history(
    monkeypatch: pytest.MonkeyPatch,
):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.PAPER_SIM))

    eligible, blockers = engine.check_stage_transition_eligible(
        target=CapitalStage.SMALL_WHITELIST,
        recent_drift_days=2,
        recent_order_success_rate=0.80,
    )
    assert eligible is False
    # Both criteria should fail.
    assert len(blockers) == 2
    joined = "\n".join(blockers)
    assert "min_days_no_drift" in joined
    assert "min_order_success_rate" in joined

    # Partial pass: days ok, success rate too low.
    eligible2, blockers2 = engine.check_stage_transition_eligible(
        target=CapitalStage.SMALL_WHITELIST,
        recent_drift_days=10,
        recent_order_success_rate=0.50,
    )
    assert eligible2 is False
    assert len(blockers2) == 1
    assert "min_order_success_rate" in blockers2[0]

    # Transition to a stage with no gate (PAPER_SIM -> PAPER_SIM in default
    # staircase has no gate entry) → always eligible.
    eligible_paper, blockers_paper = engine.check_stage_transition_eligible(
        target=CapitalStage.PAPER_SIM,
        recent_drift_days=0,
        recent_order_success_rate=0.0,
    )
    assert eligible_paper is True
    assert blockers_paper == []


# ---------------------------------------------------------------------------
# Audit event
# ---------------------------------------------------------------------------
def test_audit_event_structure_has_required_fields(
    monkeypatch: pytest.MonkeyPatch,
):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.SMALL_WHITELIST))

    event = engine.audit_event(
        "stage_transition",
        from_stage=str(CapitalStage.PAPER_SIM),
        to_stage=str(CapitalStage.SMALL_WHITELIST),
        actor="ops-user@example.com",
    )
    assert event["event"] == "stage_transition"
    assert "timestamp" in event and isinstance(event["timestamp"], str)
    assert event["configured_stage"] == str(CapitalStage.SMALL_WHITELIST)
    assert event["effective_stage"] == str(CapitalStage.SMALL_WHITELIST)
    assert event["kill_switch_active"] is False
    assert event["kill_switch_env_var"] == KILL_ENV
    assert event["from_stage"] == str(CapitalStage.PAPER_SIM)
    assert event["to_stage"] == str(CapitalStage.SMALL_WHITELIST)
    assert event["actor"] == "ops-user@example.com"

    # Kill-switch path: event reflects downgraded effective stage.
    monkeypatch.setenv(KILL_ENV, "1")
    killed = engine.audit_event(
        "kill_switch_triggered",
        reason="manual_stop",
    )
    assert killed["event"] == "kill_switch_triggered"
    assert killed["configured_stage"] == str(CapitalStage.SMALL_WHITELIST)
    assert killed["effective_stage"] == str(CapitalStage.PAPER_SIM)
    assert killed["kill_switch_active"] is True
    assert killed["reason"] == "manual_stop"


# ---------------------------------------------------------------------------
# Extra hardening: PAPER_SIM rejects QMT deployments even without kill-switch
# ---------------------------------------------------------------------------
def test_paper_sim_stage_blocks_qmt_broker(monkeypatch: pytest.MonkeyPatch):
    _clean_env(monkeypatch)
    engine = CapitalPolicyEngine(_default_config(CapitalStage.PAPER_SIM))

    reject = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=1_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    assert reject is not None
    assert reject.rule == "paper_sim_stage_requires_paper_broker"

    ok = engine.check_order(
        symbol="600000.SH",
        side="buy",
        notional=1_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="paper",
    )
    assert ok is None


def test_expanded_whitelist_has_higher_caps(monkeypatch: pytest.MonkeyPatch):
    """Quick smoke that the staircase actually expands limits."""
    _clean_env(monkeypatch)
    engine_small = CapitalPolicyEngine(_default_config(CapitalStage.SMALL_WHITELIST))
    engine_expanded = CapitalPolicyEngine(
        _default_config(CapitalStage.EXPANDED_WHITELIST)
    )

    # An order that would be rejected under SMALL_WHITELIST should pass
    # under EXPANDED_WHITELIST because daily cap jumps 50k -> 500k.
    order_kwargs = dict(
        symbol="600000.SH",
        side="buy",
        notional=60_000.0,
        current_position_value=0.0,
        current_total_exposure=0.0,
        cumulative_day_notional=0.0,
        broker_type="qmt",
    )
    small_reject = engine_small.check_order(**order_kwargs)
    assert small_reject is not None
    assert small_reject.rule == "max_capital_per_day_exceeded"

    expanded_reject = engine_expanded.check_order(**order_kwargs)
    assert expanded_reject is None
