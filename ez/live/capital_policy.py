"""Capital expansion policy + rollback kill-switch for live trading.

V3.3.46 scope:
- Stage-based capital expansion: READ_ONLY -> PAPER_SIM -> SMALL_WHITELIST ->
  EXPANDED_WHITELIST -> FULL_CAPITAL.
- Env-var kill-switch that immediately downgrades any non-paper stage to
  PAPER_SIM. Non-paper (real-broker) orders under an active kill-switch are
  hard rejected with `rule="kill_switch_capital_downgrade"`.
- Per-stage limits: max daily notional, max per-symbol position value, max
  total gross exposure, and optional symbol whitelist.
- Stage transition entry gates: minimum consecutive drift-free days and
  minimum recent order success rate before a target stage becomes eligible.
- Structured audit events for stage transitions and kill-switch triggers.

This module is intentionally side-effect free: it only inspects the current
process environment for the kill-switch flag and exposes a pure
`check_order()` decision function. Integration with `PreTradeRiskEngine`
happens in `ez/live/risk.py`; scheduler-level wiring is intentionally
deferred to a later phase.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class CapitalStage(StrEnum):
    READ_ONLY = "read_only"              # No orders permitted at all
    PAPER_SIM = "paper_sim"              # Paper broker only
    SMALL_WHITELIST = "small_whitelist"  # Real capital + strict whitelist + small
    EXPANDED_WHITELIST = "expanded"      # Broader whitelist + mid-size caps
    FULL_CAPITAL = "full"                # Full capital, caps relaxed


# Stages that require PAPER broker (not QMT/real).
_PAPER_ONLY_STAGES: frozenset[CapitalStage] = frozenset(
    {CapitalStage.READ_ONLY, CapitalStage.PAPER_SIM}
)

# Env var truthy values that enable kill-switch.
_KILL_SWITCH_TRUTHY: frozenset[str] = frozenset(
    {"1", "true", "yes", "on"}
)


@dataclass(slots=True)
class StageLimits:
    """Hard per-stage caps.

    Attributes:
        max_capital_per_day: cumulative notional (sum of |buy|+|sell|) allowed
            in a single business date before additional orders are rejected.
        max_position_value_per_symbol: projected position value (shares * price)
            per symbol after the order is applied. Hard ceiling.
        max_total_gross_exposure: projected Σ|position_value| after the order
            is applied. Hard ceiling.
        allowed_symbols: if not None, only symbols in this list may trade.
            None means no whitelist at this stage.
    """

    max_capital_per_day: float
    max_position_value_per_symbol: float
    max_total_gross_exposure: float
    allowed_symbols: list[str] | None = None


@dataclass(slots=True)
class CapitalPolicyConfig:
    """Stage-based capital expansion config.

    `stage_limits` / `entry_gates` are keyed by `CapitalStage`. Missing keys
    are treated as "no limits / no gate", but for safety the default
    staircase fills every stage.
    """

    current_stage: CapitalStage = CapitalStage.READ_ONLY
    stage_limits: dict[CapitalStage, StageLimits] = field(default_factory=dict)
    # Entry gates define requirements to enter each target stage.
    # e.g. {SMALL_WHITELIST: {"min_days_no_drift": 5, "min_order_success_rate": 0.9}}
    entry_gates: dict[CapitalStage, dict[str, Any]] = field(default_factory=dict)
    kill_switch_env_var: str = "EZ_LIVE_QMT_KILL_SWITCH"

    @classmethod
    def from_params(cls, params: dict[str, Any] | None) -> "CapitalPolicyConfig | None":
        """Build a config from the ``risk_params["capital_policy"]`` bucket.

        Accepted shapes in ``params``:
        - ``None`` / missing / ``{"enabled": False}`` → returns ``None`` so the
          caller can fall back to "no capital policy" (fully backward compatible).
        - ``{"enabled": True}`` → returns ``default_staircase()``.
        - ``{"enabled": True, "stage": "small_whitelist", ...}`` →
          ``default_staircase()`` overridden by the provided fields.

        Recognized override keys: ``stage``, ``stage_limits`` (dict
        stage→{max_capital_per_day, max_position_value_per_symbol,
        max_total_gross_exposure, allowed_symbols}), ``entry_gates``,
        ``kill_switch_env_var``.
        """
        if not params:
            return None
        if not bool(params.get("enabled", False)):
            return None
        base = cls.default_staircase()
        stage_raw = params.get("stage")
        if stage_raw is not None:
            try:
                base.current_stage = CapitalStage(str(stage_raw))
            except ValueError:
                # Unknown stage — keep conservative READ_ONLY default.
                base.current_stage = CapitalStage.READ_ONLY
        raw_limits = params.get("stage_limits")
        if isinstance(raw_limits, dict):
            for key, limits in raw_limits.items():
                try:
                    stage_key = CapitalStage(str(key))
                except ValueError:
                    continue
                if not isinstance(limits, dict):
                    continue
                existing = base.stage_limits.get(stage_key)

                def _safe_float(val: Any, default: float) -> float:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        return default

                base.stage_limits[stage_key] = StageLimits(
                    max_capital_per_day=_safe_float(
                        limits.get("max_capital_per_day"),
                        getattr(existing, "max_capital_per_day", 0.0),
                    ),
                    max_position_value_per_symbol=_safe_float(
                        limits.get("max_position_value_per_symbol"),
                        getattr(existing, "max_position_value_per_symbol", 0.0),
                    ),
                    max_total_gross_exposure=_safe_float(
                        limits.get("max_total_gross_exposure"),
                        getattr(existing, "max_total_gross_exposure", 0.0),
                    ),
                    allowed_symbols=(
                        list(limits["allowed_symbols"])
                        if "allowed_symbols" in limits
                        and limits["allowed_symbols"] is not None
                        else getattr(existing, "allowed_symbols", None)
                    ),
                )
        raw_gates = params.get("entry_gates")
        if isinstance(raw_gates, dict):
            for key, gate in raw_gates.items():
                try:
                    stage_key = CapitalStage(str(key))
                except ValueError:
                    continue
                if isinstance(gate, dict):
                    base.entry_gates[stage_key] = dict(gate)
        env_name = params.get("kill_switch_env_var")
        if isinstance(env_name, str) and env_name.strip():
            base.kill_switch_env_var = env_name.strip()
        return base

    @classmethod
    def default_staircase(cls) -> "CapitalPolicyConfig":
        """Return a conservative default expansion staircase.

        Callers can override by passing their own `stage_limits` / `entry_gates`.
        """
        return cls(
            current_stage=CapitalStage.READ_ONLY,
            stage_limits={
                CapitalStage.READ_ONLY: StageLimits(
                    max_capital_per_day=0.0,
                    max_position_value_per_symbol=0.0,
                    max_total_gross_exposure=0.0,
                    allowed_symbols=[],
                ),
                CapitalStage.PAPER_SIM: StageLimits(
                    max_capital_per_day=1e9,
                    max_position_value_per_symbol=1e9,
                    max_total_gross_exposure=1e9,
                ),
                CapitalStage.SMALL_WHITELIST: StageLimits(
                    max_capital_per_day=50_000.0,
                    max_position_value_per_symbol=20_000.0,
                    max_total_gross_exposure=100_000.0,
                ),
                CapitalStage.EXPANDED_WHITELIST: StageLimits(
                    max_capital_per_day=500_000.0,
                    max_position_value_per_symbol=100_000.0,
                    max_total_gross_exposure=1_000_000.0,
                ),
                CapitalStage.FULL_CAPITAL: StageLimits(
                    max_capital_per_day=1e12,
                    max_position_value_per_symbol=1e12,
                    max_total_gross_exposure=1e12,
                ),
            },
            entry_gates={
                CapitalStage.SMALL_WHITELIST: {
                    "min_days_no_drift": 5,
                    "min_order_success_rate": 0.90,
                },
                CapitalStage.EXPANDED_WHITELIST: {
                    "min_days_no_drift": 10,
                    "min_order_success_rate": 0.95,
                },
                CapitalStage.FULL_CAPITAL: {
                    "min_days_no_drift": 20,
                    "min_order_success_rate": 0.98,
                },
            },
        )


@dataclass(slots=True)
class CapitalRejectReason:
    """Structured reject payload for a failed capital-policy check."""

    rule: str
    stage: CapitalStage
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class CapitalPolicyEngine:
    """Evaluate whether an order is permissible under the current capital stage.

    The engine is deterministic given its config and the environment. It does
    not mutate stage state on its own — stage transitions must go through
    `check_stage_transition_eligible()` + explicit config update at a higher
    layer (scheduler / ops tool), so every transition has an audit trail.
    """

    def __init__(self, config: CapitalPolicyConfig):
        self.config = config

    # ------------------------------------------------------------------
    # Kill-switch / effective stage
    # ------------------------------------------------------------------
    def is_kill_switch_active(self) -> bool:
        """Whether the configured env var currently requests emergency stop.

        Truthy values: {"1", "true", "yes", "on"} (case-insensitive).
        All other values (including unset) are treated as inactive.
        """
        raw = os.environ.get(self.config.kill_switch_env_var, "")
        return raw.strip().lower() in _KILL_SWITCH_TRUTHY

    def effective_stage(self) -> CapitalStage:
        """Return the stage we should actually act under.

        When the kill-switch is active, any non-paper stage is downgraded to
        PAPER_SIM. READ_ONLY is left at READ_ONLY because it is already
        stricter than PAPER_SIM. The paper engines can still run; only real
        broker submission gets blocked (by the broker-type check in
        `check_order`).
        """
        if self.is_kill_switch_active():
            if self.config.current_stage == CapitalStage.READ_ONLY:
                return CapitalStage.READ_ONLY
            return CapitalStage.PAPER_SIM
        return self.config.current_stage

    # ------------------------------------------------------------------
    # Order check
    # ------------------------------------------------------------------
    def check_order(
        self,
        *,
        symbol: str,
        side: str,
        notional: float,
        current_position_value: float,
        current_total_exposure: float,
        cumulative_day_notional: float,
        broker_type: str,
    ) -> CapitalRejectReason | None:
        """Return None when the order passes all capital-policy rules.

        Args:
            symbol: instrument symbol (e.g. "600000.SH").
            side: "buy" or "sell".
            notional: |shares| * price for this order.
            current_position_value: |shares * price| for this symbol BEFORE
                the order is applied (non-negative).
            current_total_exposure: Σ|position_value| across all symbols
                BEFORE the order is applied (non-negative).
            cumulative_day_notional: sum of already-accepted notional for
                the current business date BEFORE this order is applied.
            broker_type: adapter kind — currently "paper" or "qmt"
                (anything that is not "paper" is treated as a real/shadow
                broker and can be rejected by the kill-switch downgrade).
        """
        kill_active = self.is_kill_switch_active()
        configured_stage = self.config.current_stage
        effective = self.effective_stage()
        is_paper_broker = (broker_type == "paper")

        # Rule 1: kill-switch downgrade → block any non-paper broker order.
        # This check is intentionally the very first rule so operators can
        # always stop the real broker with one env flip.
        if kill_active and not is_paper_broker:
            return CapitalRejectReason(
                rule="kill_switch_capital_downgrade",
                stage=effective,
                message=(
                    "Kill-switch is active; real-broker orders are blocked "
                    "(stage downgraded to paper_sim)."
                ),
                details={
                    "kill_switch_env_var": self.config.kill_switch_env_var,
                    "configured_stage": str(configured_stage),
                    "effective_stage": str(effective),
                    "broker_type": broker_type,
                },
            )

        # Rule 2: READ_ONLY blocks EVERYTHING, including paper orders.
        if effective == CapitalStage.READ_ONLY:
            return CapitalRejectReason(
                rule="read_only_stage_blocks_orders",
                stage=effective,
                message="READ_ONLY stage does not permit any orders.",
                details={
                    "broker_type": broker_type,
                    "effective_stage": str(effective),
                },
            )

        # Rule 3: PAPER_SIM rejects real-broker deployments (stage/broker
        # mismatch). This is what the kill-switch ultimately leverages too.
        if effective == CapitalStage.PAPER_SIM and not is_paper_broker:
            return CapitalRejectReason(
                rule="paper_sim_stage_requires_paper_broker",
                stage=effective,
                message=(
                    "PAPER_SIM stage only permits paper-broker orders; "
                    f"broker_type={broker_type!r} is blocked."
                ),
                details={
                    "broker_type": broker_type,
                    "effective_stage": str(effective),
                },
            )

        limits = self.config.stage_limits.get(effective)
        if limits is None:
            # No limits configured for this stage: accept conservatively.
            return None

        # Rule 4: symbol whitelist (per-stage, optional).
        if limits.allowed_symbols is not None and symbol not in limits.allowed_symbols:
            return CapitalRejectReason(
                rule="symbol_not_in_stage_whitelist",
                stage=effective,
                message=(
                    f"Symbol {symbol!r} is not in the {effective} whitelist."
                ),
                details={
                    "symbol": symbol,
                    "allowed_symbols": list(limits.allowed_symbols),
                    "effective_stage": str(effective),
                },
            )

        # Rule 5: daily notional cap (cumulative after this order).
        projected_day_notional = float(cumulative_day_notional) + float(notional)
        if projected_day_notional > limits.max_capital_per_day + 1e-9:
            return CapitalRejectReason(
                rule="max_capital_per_day_exceeded",
                stage=effective,
                message=(
                    "Projected daily notional would exceed the stage cap."
                ),
                details={
                    "projected_day_notional": projected_day_notional,
                    "max_capital_per_day": limits.max_capital_per_day,
                    "order_notional": float(notional),
                    "cumulative_day_notional": float(cumulative_day_notional),
                    "effective_stage": str(effective),
                },
            )

        # Rule 6: per-symbol position value cap (buy side grows the
        # position; sell side shrinks it, so we project accordingly).
        side_lower = side.lower()
        if side_lower == "buy":
            projected_position_value = float(current_position_value) + float(notional)
        else:
            # Sell can only reduce the position value (bounded below by 0).
            projected_position_value = max(
                float(current_position_value) - float(notional), 0.0
            )
        # Only enforce on buy: selling cannot push the per-symbol value up.
        if (
            side_lower == "buy"
            and projected_position_value > limits.max_position_value_per_symbol + 1e-9
        ):
            return CapitalRejectReason(
                rule="max_position_value_per_symbol_exceeded",
                stage=effective,
                message=(
                    "Projected per-symbol position value exceeds the stage cap."
                ),
                details={
                    "symbol": symbol,
                    "projected_position_value": projected_position_value,
                    "max_position_value_per_symbol": (
                        limits.max_position_value_per_symbol
                    ),
                    "current_position_value": float(current_position_value),
                    "order_notional": float(notional),
                    "effective_stage": str(effective),
                },
            )

        # Rule 7: total gross exposure cap. For a buy we add notional to
        # exposure; for a sell we either reduce the symbol's position
        # (still tracked in exposure) or, if it overshoots, the residual
        # would reverse the sign — which A-share long-only deployments do
        # not support, so we treat residual sell notional as additional
        # exposure too. In practice `PreTradeRiskEngine` and OMS bounds-
        # check shares long before this call, so the sell branch here is
        # mainly a safety net; we use the projected per-symbol value we
        # already computed.
        if side_lower == "buy":
            projected_total_exposure = (
                float(current_total_exposure) + float(notional)
            )
        else:
            # Sell: the symbol shrinks by min(notional, current_position_value),
            # remainder (if any) does not add exposure for long-only.
            shrink = min(float(notional), float(current_position_value))
            projected_total_exposure = max(
                float(current_total_exposure) - shrink, 0.0
            )
        if (
            side_lower == "buy"
            and projected_total_exposure > limits.max_total_gross_exposure + 1e-9
        ):
            return CapitalRejectReason(
                rule="max_total_gross_exposure_exceeded",
                stage=effective,
                message=(
                    "Projected total gross exposure exceeds the stage cap."
                ),
                details={
                    "projected_total_exposure": projected_total_exposure,
                    "max_total_gross_exposure": (
                        limits.max_total_gross_exposure
                    ),
                    "current_total_exposure": float(current_total_exposure),
                    "order_notional": float(notional),
                    "effective_stage": str(effective),
                },
            )

        return None

    # ------------------------------------------------------------------
    # Stage transition eligibility
    # ------------------------------------------------------------------
    def check_stage_transition_eligible(
        self,
        *,
        target: CapitalStage,
        recent_drift_days: int,
        recent_order_success_rate: float,
    ) -> tuple[bool, list[str]]:
        """Evaluate whether `target` stage is enterable.

        Args:
            target: the stage we want to enter.
            recent_drift_days: consecutive business days with no reconcile
                drift (higher = healthier).
            recent_order_success_rate: recent-window successful order ratio
                (0.0–1.0).

        Returns:
            (eligible, blockers). When `eligible=False`, `blockers` explains
            which gate failed; when `eligible=True`, `blockers` is empty.
        """
        gate = self.config.entry_gates.get(target)
        if not gate:
            # No gate configured for this target stage → allowed.
            return True, []

        blockers: list[str] = []

        min_days = gate.get("min_days_no_drift")
        if min_days is not None:
            try:
                min_days_int = int(min_days)
            except (TypeError, ValueError):
                min_days_int = 0
            if int(recent_drift_days) < min_days_int:
                blockers.append(
                    f"min_days_no_drift not met: "
                    f"have={int(recent_drift_days)} need>={min_days_int}"
                )

        min_success = gate.get("min_order_success_rate")
        if min_success is not None:
            try:
                min_success_f = float(min_success)
            except (TypeError, ValueError):
                min_success_f = 0.0
            if float(recent_order_success_rate) + 1e-12 < min_success_f:
                blockers.append(
                    f"min_order_success_rate not met: "
                    f"have={float(recent_order_success_rate):.4f} "
                    f"need>={min_success_f:.4f}"
                )

        return (len(blockers) == 0), blockers

    # ------------------------------------------------------------------
    # Audit event
    # ------------------------------------------------------------------
    def audit_event(self, event_type: str, **extra: Any) -> dict[str, Any]:
        """Build a structured audit record.

        Always contains: `event`, `timestamp` (UTC ISO-8601), `configured_stage`,
        `effective_stage`, `kill_switch_active`, `kill_switch_env_var`.
        Additional caller-supplied kwargs are merged into the record
        (they must be JSON-safe for the caller's persistence layer).
        """
        return {
            "event": str(event_type),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "configured_stage": str(self.config.current_stage),
            "effective_stage": str(self.effective_stage()),
            "kill_switch_active": self.is_kill_switch_active(),
            "kill_switch_env_var": self.config.kill_switch_env_var,
            **extra,
        }


__all__ = [
    "CapitalStage",
    "StageLimits",
    "CapitalPolicyConfig",
    "CapitalRejectReason",
    "CapitalPolicyEngine",
]
