"""V2.16.2 regression: paper_engine price-fetch NaN fallback.

Parity with `ez/portfolio/engine.py` V2.18.1 fix. Three guards:

1. adj_close=NaN + raw close finite → use raw close (equity still priced)
2. adj_close=NaN + raw close NaN → symbol omitted from prices → execute
   skips the trade (no ValueError from _lot_round(NaN))
3. Previously-held symbol with missing today's bar → _mark_to_market
   uses _last_prices cache (never reports zero equity for held stock)

Prior bug: `float(df["adj_close"].iloc[-1])` returned NaN, which then
flowed into execute_portfolio_trades where `raw_shares = amount/NaN`
→ `_lot_round(NaN)` → `int(NaN)` → ValueError, crashing tick().
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from ez.live.paper_engine import PaperTradingEngine
from ez.live.deployment_spec import DeploymentSpec


def _spec() -> DeploymentSpec:
    return DeploymentSpec(
        strategy_name="T",
        strategy_params={},
        symbols=("AAA", "BBB"),
        market="cn_stock",
        freq="weekly",
        initial_cash=1_000_000.0,
    )


def _engine() -> PaperTradingEngine:
    # data_chain / strategy not exercised here; helpers are pure.
    return PaperTradingEngine(spec=_spec(), strategy=object(), data_chain=object())


def _df(rows: list[tuple[float, float]]) -> pd.DataFrame:
    """rows = list of (close, adj_close) tuples, one per day."""
    dates = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(
        {"close": [r[0] for r in rows], "adj_close": [r[1] for r in rows]},
        index=dates,
    )


def test_adj_close_nan_falls_back_to_raw_close() -> None:
    """Provider returned adj_close=NaN (lag / computation pending) but
    raw close is valid. Paper engine must use raw close to avoid
    inserting NaN into prices, which would crash _lot_round downstream."""
    data = {"AAA": _df([(10.0, 10.0), (11.0, float("nan"))])}
    prices = _engine()._get_latest_prices(data)
    assert "AAA" in prices
    assert prices["AAA"] == 11.0
    assert math.isfinite(prices["AAA"])


def test_adj_close_finite_preferred_over_raw() -> None:
    """Normal case: adj_close takes priority (correct post-dividend valuation)."""
    # Dividend day: raw_close=5 (post-div drop), adj_close=10 (adjusted)
    data = {"AAA": _df([(10.0, 10.0), (5.0, 10.0)])}
    prices = _engine()._get_latest_prices(data)
    # Must pick adj_close=10, not raw=5 — exact V2.18.1 semantics
    assert prices["AAA"] == 10.0


def test_both_nan_omits_symbol_from_prices() -> None:
    """Both adj_close and raw close NaN on the latest bar. Symbol must
    NOT appear in prices dict — otherwise execute_portfolio_trades
    would hit NaN math. Mark-to-market falls back to _last_prices."""
    data = {"AAA": _df([(10.0, 10.0), (float("nan"), float("nan"))])}
    prices = _engine()._get_latest_prices(data)
    assert "AAA" not in prices


def test_raw_close_getter_guards_nan() -> None:
    """_get_raw_closes must not return NaN — limit_pct math would break
    (NaN compared to threshold is always False, hiding limit checks)."""
    data = {"AAA": _df([(10.0, 10.0), (float("nan"), 10.0)])}
    raw = _engine()._get_raw_closes(data)
    assert "AAA" not in raw

    data_ok = {"BBB": _df([(10.0, 10.0), (11.0, 11.0)])}
    raw_ok = _engine()._get_raw_closes(data_ok)
    assert raw_ok["BBB"] == 11.0


def test_prev_raw_close_guards_nan() -> None:
    """Previous-day raw close NaN: omit symbol from prev_raw (prevents
    division by zero in limit % change)."""
    data = {"AAA": _df([(float("nan"), 10.0), (11.0, 11.0)])}
    prev = _engine()._get_prev_raw_closes(data)
    assert "AAA" not in prev


def test_mark_to_market_uses_last_prices_when_symbol_missing() -> None:
    """Held symbol with no price today (e.g. suspension / fetch gap)
    must be valued at last known price, not at zero. Zero would crash
    equity and trigger spurious drawdown risk events."""
    eng = _engine()
    eng.cash = 100_000.0
    eng.holdings = {"AAA": 1000}
    eng._last_prices = {"AAA": 20.0}
    # prices dict empty — AAA missing today
    equity = eng._mark_to_market({})
    # 100_000 cash + 1000 * 20.0 = 120_000
    assert equity == 120_000.0


def test_mark_to_market_updates_last_prices_cache() -> None:
    """When price IS available, _last_prices should be updated so next
    suspension day uses the freshest known value."""
    eng = _engine()
    eng.cash = 0.0
    eng.holdings = {"AAA": 100}
    equity1 = eng._mark_to_market({"AAA": 50.0})
    assert equity1 == 5000.0
    assert eng._last_prices["AAA"] == 50.0
    # Next day: price missing → falls back to 50.0
    equity2 = eng._mark_to_market({})
    assert equity2 == 5000.0


def test_numpy_nan_also_handled() -> None:
    """pd/numpy sometimes yields np.float64(nan) not Python float("nan").
    Both must be detected by math.isfinite."""
    data = {"AAA": _df([(10.0, 10.0), (11.0, np.float64(np.nan))])}
    prices = _engine()._get_latest_prices(data)
    # adj NaN → fallback to raw 11.0
    assert prices["AAA"] == 11.0
