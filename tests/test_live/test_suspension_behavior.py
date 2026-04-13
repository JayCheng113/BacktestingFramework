"""V2.17 round 8: suspension (停牌) behavior verification.

Stock/ETF suspension: the exchange halts trading for some days
(corporate event, regulatory, etc). Data provider has NO bar for
those days. Paper trading must:

1. Not crash
2. Hold existing suspended positions (can't trade)
3. Mark-to-market at last known price (V2.17 _last_prices cache)
4. NOT buy a suspended symbol even if strategy targets it
5. Trade normally in other (non-suspended) symbols

These tests document the current behavior — any regression that changes
these semantics must deliberately update this contract.
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ez.live.deployment_spec import DeploymentSpec
from ez.live.paper_engine import PaperTradingEngine


class _FakeBar:
    def __init__(self, d, p):
        self.time = datetime.combine(d, datetime.min.time())
        self.open = self.high = self.low = self.close = self.adj_close = p
        self.volume = 10_000_000


class _BuyABStrategy:
    """Always target 50% A + 50% B."""
    lookback_days = 3

    def generate_weights(self, universe_data, date_, prev_weights, prev_returns):
        return {"A": 0.5, "B": 0.5}


def _make_engine(trading_days: list[date], bars_by_sym: dict) -> PaperTradingEngine:
    """bars_by_sym: {symbol: list of (date, price) tuples — missing date = suspension}."""
    def get_kline(sym, market, period, start_d, end_d):
        if sym not in bars_by_sym:
            return []
        return [_FakeBar(d, p) for d, p in bars_by_sym[sym]
                if start_d <= d <= end_d]

    chain = MagicMock()
    chain.get_kline.side_effect = get_kline

    spec = DeploymentSpec(
        strategy_name="T", strategy_params={},
        symbols=("A", "B"), market="cn_stock", freq="daily",
        initial_cash=100_000.0,
        buy_commission_rate=0.0, sell_commission_rate=0.0,
        min_commission=0.0, stamp_tax_rate=0.0, slippage_rate=0.0,
        lot_size=1, price_limit_pct=0.0, t_plus_1=False,
    )
    engine = PaperTradingEngine(
        spec=spec, strategy=_BuyABStrategy(), data_chain=chain,
    )
    engine._rebalance_dates_cache = set(trading_days)
    return engine


# ---------------------------------------------------------------------------
# Scenario: B is suspended on day 2
# ---------------------------------------------------------------------------

def test_suspended_symbol_on_rebalance_day_is_not_bought() -> None:
    """Day 1: normal, buy A + B. Day 2: B suspended (no bar). Strategy
    still targets 50/50, but execute must not trade B on day 2."""
    days = [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 5)]
    # B missing on day 2 (suspension)
    engine = _make_engine(days, {
        "A": [(d, 10.0) for d in days],
        "B": [(days[0], 20.0), (days[2], 20.0)],  # missing days[1]
    })

    r1 = engine.execute_day(days[0])
    # Day 1: both bought
    assert r1["holdings"].get("A", 0) > 0
    assert r1["holdings"].get("B", 0) > 0

    r2 = engine.execute_day(days[1])
    # Day 2: B suspended. Strategy wanted B but no bar → must not trade
    b_trades = [t for t in r2["trades"] if t["symbol"] == "B"]
    assert b_trades == [], (
        f"B is suspended (no bar today) but engine attempted to trade: {b_trades}"
    )


def test_held_suspended_position_retains_last_price() -> None:
    """Position held while suspended: mark-to-market uses last known
    price, not zero. Otherwise equity falsely crashes."""
    days = [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 5)]
    engine = _make_engine(days, {
        "A": [(d, 10.0) for d in days],
        "B": [(days[0], 20.0), (days[2], 22.0)],  # suspended day 2
    })
    r1 = engine.execute_day(days[0])
    b_shares = r1["holdings"].get("B", 0)
    b_value_day1 = b_shares * 20.0

    r2 = engine.execute_day(days[1])  # B suspended
    # Mark-to-market: B's shares held but valued at last known price.
    # If engine naively used 0 for missing price, equity would drop by
    # ~50% of initial.
    # Equity should be close to day 1 (only A can move, A didn't change)
    assert r2["equity"] > 90_000, (
        f"Equity dropped unexpectedly on suspension day: {r2['equity']}. "
        f"Suspended position should be valued at last known price."
    )


def test_both_symbols_non_suspended_trades_normally() -> None:
    """Baseline: when no suspensions, weights converge to target."""
    days = [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 5)]
    engine = _make_engine(days, {
        "A": [(d, 10.0) for d in days],
        "B": [(d, 20.0) for d in days],
    })
    for d in days:
        engine.execute_day(d)
    # Final weights should be ~50/50
    final_weights = engine.prev_weights
    assert 0.45 < final_weights.get("A", 0) < 0.55
    assert 0.45 < final_weights.get("B", 0) < 0.55


def test_resumption_after_suspension() -> None:
    """B resumes on day 3 — engine should be able to trade again."""
    days = [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 5)]
    # B price moves higher during suspension
    engine = _make_engine(days, {
        "A": [(d, 10.0) for d in days],
        "B": [(days[0], 20.0), (days[2], 25.0)],  # suspended day 2
    })
    engine.execute_day(days[0])
    engine.execute_day(days[1])  # suspension
    r3 = engine.execute_day(days[2])
    # Engine should successfully re-rebalance with both symbols
    # (weights may not hit 50/50 exactly due to lot rounding, but no crash)
    assert r3["equity"] > 0
    # B is tradeable again; engine can adjust (though may choose not to
    # if weight still within threshold)
    assert r3["holdings"].get("B", 0) > 0  # still holding B


def test_full_universe_suspension_produces_no_trade_but_no_crash() -> None:
    """Edge case: entire universe suspended. Engine should not crash,
    just skip the rebalance and carry forward last equity."""
    days = [date(2024, 3, 1), date(2024, 3, 4)]
    engine = _make_engine(days, {
        "A": [(days[0], 10.0)],  # suspended day 2
        "B": [(days[0], 20.0)],  # suspended day 2
    })
    engine.execute_day(days[0])  # normal
    r2 = engine.execute_day(days[1])  # universe fully suspended

    # No trades on day 2
    assert r2["trades"] == []
    # But equity recorded (mark-to-market with last prices)
    assert len(engine.equity_curve) == 2
    assert r2["equity"] > 0
