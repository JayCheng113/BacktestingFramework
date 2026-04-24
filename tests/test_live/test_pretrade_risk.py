from __future__ import annotations

from datetime import date

import pytest

from ez.live.capital_policy import (
    CapitalPolicyConfig,
    CapitalPolicyEngine,
    CapitalStage,
)
from ez.live.events import Order, make_client_order_id, make_order_id
from ez.live.risk import PreTradeRiskConfig, PreTradeRiskEngine


def _order(*, deployment_id: str, business_date: date, symbol: str, side: str, shares: int) -> Order:
    client_order_id = make_client_order_id(deployment_id, business_date, symbol, side)
    return Order(
        order_id=make_order_id(client_order_id),
        client_order_id=client_order_id,
        deployment_id=deployment_id,
        symbol=symbol,
        side=side,
        shares=shares,
        business_date=business_date,
    )


def test_max_position_weight_rejects_oversized_buy():
    engine = PreTradeRiskEngine(
        PreTradeRiskConfig(max_position_weight=0.30)
    )
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 13),
        orders=[
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="AAA",
                side="buy",
                shares=40_000,
            )
        ],
        holdings={},
        prices={"AAA": 10.0},
        equity=1_000_000.0,
    )

    assert decision.accepted_orders == []
    assert decision.rejected_orders[0].reason == "risk:max_position_weight"
    assert decision.risk_events[0]["rule"] == "max_position_weight"
    assert "Projected position weight" in decision.risk_events[0]["message"]
    assert decision.risk_events[0]["details"]["max_position_weight"] == 0.30


def test_max_daily_turnover_rejects_order_after_turnover_budget_used():
    engine = PreTradeRiskEngine(
        PreTradeRiskConfig(max_daily_turnover=0.50)
    )
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 13),
        orders=[
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="AAA",
                side="buy",
                shares=30_000,
            ),
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="BBB",
                side="buy",
                shares=25_000,
            ),
        ],
        holdings={},
        prices={"AAA": 10.0, "BBB": 10.0},
        equity=1_000_000.0,
    )

    assert [order.symbol for order in decision.accepted_orders] == ["AAA"]
    assert decision.rejected_orders[0].order.symbol == "BBB"
    assert decision.rejected_orders[0].reason == "risk:max_daily_turnover"
    assert decision.risk_events[0]["details"]["max_daily_turnover"] == 0.50


def test_max_concentration_rejects_projected_portfolio_overweight():
    engine = PreTradeRiskEngine(
        PreTradeRiskConfig(max_concentration=0.45)
    )
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 13),
        orders=[
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="AAA",
                side="buy",
                shares=10_000,
            )
        ],
        holdings={"BBB": 50_000},
        prices={"AAA": 10.0, "BBB": 10.0},
        equity=1_000_000.0,
    )

    assert decision.accepted_orders == []
    assert decision.rejected_orders[0].reason == "risk:max_concentration"
    assert decision.rejected_orders[0].details["max_weight_symbol"] == "BBB"
    assert decision.risk_events[0]["details"]["projected_max_weight"] == 0.5


def test_max_gross_exposure_rejects_buy_above_leverage_cap():
    engine = PreTradeRiskEngine(
        PreTradeRiskConfig(max_gross_exposure=0.75)
    )
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 13),
        orders=[
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="AAA",
                side="buy",
                shares=10_000,
            )
        ],
        holdings={"BBB": 70_000},
        prices={"AAA": 10.0, "BBB": 10.0},
        equity=1_000_000.0,
    )

    assert decision.accepted_orders == []
    assert decision.rejected_orders[0].reason == "risk:max_gross_exposure"
    assert decision.risk_events[0]["details"]["projected_gross_exposure"] == pytest.approx(0.8)


def test_kill_switch_blocks_sell_orders_too():
    """Kill switch must reject both sides, not just buys."""
    engine = PreTradeRiskEngine(PreTradeRiskConfig(kill_switch=True))
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 13),
        orders=[
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="AAA",
                side="sell",
                shares=5_000,
            )
        ],
        holdings={"AAA": 5_000},
        prices={"AAA": 10.0},
        equity=1_000_000.0,
    )

    assert decision.accepted_orders == []
    assert decision.rejected_orders[0].reason == "risk:kill_switch"


def test_max_concentration_also_rejects_sell_that_concentrates_other_holdings():
    """
    Selling one small position can push another position's share of total
    equity above max_concentration, because the smaller position's sale
    reduces the denominator-less-than-proportionally. The rule must
    therefore also evaluate sells against projected metrics.

    Setup:
    - holdings AAA=90k shares * 10 = 900k (90% of 1M equity)
    - holdings BBB=10k shares * 10 = 100k (10% of 1M equity)
    - sell BBB 10k -> projected BBB=0, AAA stays 900k but effective
      portfolio weight is still 900k/1M = 0.9 (equity is cash + positions
      and the risk engine computes weights against the *input* equity).
    - With max_concentration=0.5, the projected weight of AAA (0.9) must
      fail closed even though the order being traded is a BBB sell.
    """
    engine = PreTradeRiskEngine(PreTradeRiskConfig(max_concentration=0.5))
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 13),
        orders=[
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="BBB",
                side="sell",
                shares=10_000,
            )
        ],
        holdings={"AAA": 90_000, "BBB": 10_000},
        prices={"AAA": 10.0, "BBB": 10.0},
        equity=1_000_000.0,
    )

    assert decision.accepted_orders == []
    assert decision.rejected_orders[0].reason == "risk:max_concentration"
    details = decision.rejected_orders[0].details
    assert details["max_weight_symbol"] == "AAA"
    assert details["projected_max_weight"] == pytest.approx(0.9)


def test_max_daily_turnover_applies_to_sell_orders():
    """Sells consume the daily turnover budget just like buys."""
    engine = PreTradeRiskEngine(PreTradeRiskConfig(max_daily_turnover=0.10))
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 13),
        orders=[
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="AAA",
                side="sell",
                shares=15_000,
            )
        ],
        holdings={"AAA": 20_000},
        prices={"AAA": 10.0},
        equity=1_000_000.0,
    )

    assert decision.accepted_orders == []
    assert decision.rejected_orders[0].reason == "risk:max_daily_turnover"


def test_sell_does_not_trigger_max_position_weight_or_gross_exposure():
    """
    Sells can only reduce a symbol's weight and the total gross exposure,
    so they must never be rejected by per-symbol weight or leverage caps.
    """
    engine = PreTradeRiskEngine(
        PreTradeRiskConfig(
            max_position_weight=0.05,
            max_gross_exposure=0.30,
        )
    )
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 13),
        orders=[
            _order(
                deployment_id="dep-1",
                business_date=date(2026, 4, 13),
                symbol="AAA",
                side="sell",
                shares=50_000,
            )
        ],
        # Initially gross exposure = 80% and AAA weight = 80% — both are
        # way above the caps, but a sell can only shrink them, so the sell
        # must be accepted.
        holdings={"AAA": 80_000},
        prices={"AAA": 10.0},
        equity=1_000_000.0,
    )

    assert len(decision.accepted_orders) == 1
    assert decision.rejected_orders == []


# ---------------------------------------------------------------------------
# V3.3.46 capital policy integration
# ---------------------------------------------------------------------------
def _make_capital_engine(
    stage: CapitalStage,
    *,
    env_var: str,
) -> CapitalPolicyEngine:
    cfg = CapitalPolicyConfig.default_staircase()
    cfg.current_stage = stage
    cfg.kill_switch_env_var = env_var
    return CapitalPolicyEngine(cfg)


def test_integration_pretrade_risk_rejects_via_capital_policy(
    monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end: `PreTradeRiskEngine` must consult the capital policy
    BEFORE any other rule and surface the stage-scoped reject code."""
    env = "EZ_LIVE_QMT_KILL_SWITCH_PRETRADE_TEST"
    monkeypatch.delenv(env, raising=False)

    # 1. SMALL_WHITELIST stage rejects oversize per-symbol position:
    #    limit is 20k, we try 50k buy (50k shares * $1) and cumulative
    #    notional fits (< 50k cap per day), but per-symbol value is violated.
    policy = _make_capital_engine(CapitalStage.SMALL_WHITELIST, env_var=env)
    engine = PreTradeRiskEngine(
        PreTradeRiskConfig(),
        capital_policy=policy,
        broker_type="qmt",
    )

    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 16),
        orders=[
            _order(
                deployment_id="dep-v3346",
                business_date=date(2026, 4, 16),
                symbol="600000.SH",
                side="buy",
                shares=25_000,  # 25k * $1 = $25k > 20k per-symbol cap
            )
        ],
        holdings={},
        prices={"600000.SH": 1.0},
        equity=1_000_000.0,
    )

    assert decision.accepted_orders == []
    assert len(decision.rejected_orders) == 1
    rejected = decision.rejected_orders[0]
    assert rejected.reason == (
        "risk:capital_stage_small_whitelist_"
        "max_position_value_per_symbol_exceeded"
    )
    assert rejected.rule == "max_position_value_per_symbol_exceeded"

    event = decision.risk_events[0]
    assert event["rule"] == "max_position_value_per_symbol_exceeded"
    assert event["reason"].startswith("risk:capital_stage_small_whitelist_")
    assert event["details"]["max_position_value_per_symbol"] == pytest.approx(
        20_000.0
    )
    assert event["details"]["projected_position_value"] == pytest.approx(
        25_000.0
    )

    # 2. Kill-switch on: the SAME order gets the kill-switch downgrade rule
    #    (it now fires before per-symbol cap).
    monkeypatch.setenv(env, "true")
    kill_decision = engine.evaluate_orders(
        business_date=date(2026, 4, 16),
        orders=[
            _order(
                deployment_id="dep-v3346",
                business_date=date(2026, 4, 16),
                symbol="600000.SH",
                side="buy",
                shares=1_000,  # tiny order, still rejected because broker=qmt
            )
        ],
        holdings={},
        prices={"600000.SH": 1.0},
        equity=1_000_000.0,
    )
    assert kill_decision.accepted_orders == []
    kill_rejected = kill_decision.rejected_orders[0]
    assert kill_rejected.rule == "kill_switch_capital_downgrade"
    assert kill_rejected.reason == (
        "risk:capital_stage_paper_sim_kill_switch_capital_downgrade"
    )
    assert (
        kill_decision.risk_events[0]["details"]["broker_type"] == "qmt"
    )

    # 3. Clean env + paper broker + SMALL_WHITELIST should pass when order
    #    is inside all caps.
    monkeypatch.delenv(env, raising=False)
    paper_engine = PreTradeRiskEngine(
        PreTradeRiskConfig(),
        capital_policy=_make_capital_engine(
            CapitalStage.SMALL_WHITELIST, env_var=env
        ),
        broker_type="paper",
    )
    pass_decision = paper_engine.evaluate_orders(
        business_date=date(2026, 4, 16),
        orders=[
            _order(
                deployment_id="dep-v3346",
                business_date=date(2026, 4, 16),
                symbol="600000.SH",
                side="buy",
                shares=10_000,  # 10k * $1 = $10k well within caps
            )
        ],
        holdings={},
        prices={"600000.SH": 1.0},
        equity=1_000_000.0,
    )
    assert len(pass_decision.accepted_orders) == 1
    assert pass_decision.rejected_orders == []


def test_pretrade_risk_defaults_do_not_enforce_capital_policy():
    """Existing API surface: constructing PreTradeRiskEngine without a
    capital_policy must behave exactly like before (no extra rejects)."""
    engine = PreTradeRiskEngine()
    decision = engine.evaluate_orders(
        business_date=date(2026, 4, 16),
        orders=[
            _order(
                deployment_id="dep-legacy",
                business_date=date(2026, 4, 16),
                symbol="AAA",
                side="buy",
                shares=50_000,  # huge notional — would fail SMALL_WHITELIST
            )
        ],
        holdings={},
        prices={"AAA": 10.0},
        equity=10_000_000.0,
    )
    assert len(decision.accepted_orders) == 1
    assert decision.rejected_orders == []
