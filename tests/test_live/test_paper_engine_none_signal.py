"""V2.17 round 6 regression: paper_engine handles strategy returning None.

Real-world bug exposed by first production deployment (A+Bond 50/50):
PaperTradingEngine.execute_day crashed with
    AttributeError: 'NoneType' object has no attribute 'items'
when the wrapped strategy (ARotateBondBlend -> EtfRotateCombo) returned
None on a non-rebalance day. ez/portfolio/engine.py has handled this
since V2.17 — paper_engine was the parity gap.

Contract: strategy.generate_weights() returning None means "skip
rebalancing today, hold prior positions, still record equity/dates".
Engine must:
1. Not crash
2. Mark-to-market at current prices
3. Append equity/dates
4. Set rebalanced=False
5. Return a valid result dict
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd

from ez.live.deployment_spec import DeploymentSpec
from ez.live.paper_engine import PaperTradingEngine


class _FakeBar:
    def __init__(self, d, p):
        self.time = datetime.combine(d, datetime.min.time())
        self.open = self.high = self.low = self.close = self.adj_close = p
        self.volume = 10_000_000


class _NoneOnNonRebalStrategy:
    """Rebalances on first day only; returns None thereafter."""
    lookback_days = 3

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._calls = 0

    def generate_weights(self, universe_data, date_, prev_weights, prev_returns):
        self._calls += 1
        if self._calls == 1:
            return {self.symbol: 1.0}
        return None  # non-rebalance day


def _spec() -> DeploymentSpec:
    return DeploymentSpec(
        strategy_name="T",
        strategy_params={},
        symbols=("AAA",),
        market="cn_stock",
        freq="daily",
        initial_cash=100_000.0,
        buy_commission_rate=0.0, sell_commission_rate=0.0,
        min_commission=0.0, stamp_tax_rate=0.0, slippage_rate=0.0,
        lot_size=1, price_limit_pct=0.0, t_plus_1=False,
    )


def _run_3_days(strategy, price: float = 10.0):
    """Helper: 3 daily bars, daily freq (every day is rebalance day)."""
    dates = [date(2024, 3, i) for i in (1, 4, 5)]  # weekdays
    bars = [_FakeBar(d, price) for d in dates]

    def get_kline(sym, m, p, sd, ed):
        return [b for b in bars if sd <= b.time.date() <= ed]

    chain = MagicMock()
    chain.get_kline.side_effect = get_kline

    engine = PaperTradingEngine(spec=_spec(), strategy=strategy, data_chain=chain)
    engine._rebalance_dates_cache = set(dates)

    results = [engine.execute_day(d) for d in dates]
    return engine, results


def test_none_signal_does_not_crash() -> None:
    """Strategy returns None on days 2-3. Engine must keep running."""
    strategy = _NoneOnNonRebalStrategy("AAA")
    engine, results = _run_3_days(strategy)
    # All 3 days executed without exception
    assert len(results) == 3
    assert len(engine.equity_curve) == 3


def test_none_signal_skips_rebalance_flag() -> None:
    """Day 1 rebalances (buys); days 2-3 hold (rebalanced=False)."""
    strategy = _NoneOnNonRebalStrategy("AAA")
    _, results = _run_3_days(strategy)
    assert results[0]["rebalanced"] is True
    assert results[1]["rebalanced"] is False
    assert results[2]["rebalanced"] is False
    # No trades on None days
    assert results[1]["trades"] == []
    assert results[2]["trades"] == []


def test_none_signal_preserves_holdings() -> None:
    """Holdings bought on day 1 remain through days 2-3 with None."""
    strategy = _NoneOnNonRebalStrategy("AAA")
    engine, results = _run_3_days(strategy, price=10.0)
    # Day 1: buys AAA with ~all cash
    day1_shares = results[0]["holdings"].get("AAA", 0)
    assert day1_shares > 0
    # Days 2-3: same holdings preserved
    assert results[1]["holdings"].get("AAA", 0) == day1_shares
    assert results[2]["holdings"].get("AAA", 0) == day1_shares


def test_none_signal_marks_to_market_at_current_prices() -> None:
    """On None days, equity must reflect current prices (mark-to-market),
    not stale day-1 equity. Simulated with a price bump on day 2."""
    strategy = _NoneOnNonRebalStrategy("AAA")

    dates = [date(2024, 3, i) for i in (1, 4, 5)]
    # Day 1: price 10, Day 2: price 11 (+10%), Day 3: price 10
    prices = [10.0, 11.0, 10.0]
    bars = [_FakeBar(d, p) for d, p in zip(dates, prices)]

    def get_kline(sym, m, p, sd, ed):
        return [b for b in bars if sd <= b.time.date() <= ed]
    chain = MagicMock()
    chain.get_kline.side_effect = get_kline

    engine = PaperTradingEngine(spec=_spec(), strategy=strategy, data_chain=chain)
    engine._rebalance_dates_cache = set(dates)

    r = [engine.execute_day(d) for d in dates]
    # Day 2 (None signal, +10% price): equity should be up ~10% from day 1
    assert r[1]["equity"] > r[0]["equity"]
    # Day 3 (None signal, back to 10): equity should drop back near day 1
    assert abs(r[2]["equity"] - r[0]["equity"]) < 0.02 * r[0]["equity"]


def test_none_signal_still_emits_market_snapshot_and_bars() -> None:
    """Hold-only days still need market context for audit/replay."""
    strategy = _NoneOnNonRebalStrategy("AAA")
    _, results = _run_3_days(strategy, price=10.0)

    for result in results[1:]:
        assert result["_market_snapshot"]["source"] == "live"
        assert result["_market_snapshot"]["prices"] == {"AAA": 10.0}
        assert result["_market_snapshot"]["has_bar_symbols"] == ["AAA"]
        assert result["_market_bars"] == [{
            "symbol": "AAA",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "adj_close": 10.0,
            "volume": 10000000.0,
            "source": "live",
        }]
