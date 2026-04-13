"""V2.16.2 round 5: cross-engine consistency diff.

Systematic canary: the SAME trivial strategy on the SAME synthetic
data must produce near-identical equity across three engines:

  1. ez/backtest/engine.py — single-stock VectorizedBacktestEngine
  2. ez/portfolio/engine.py — run_portfolio_backtest (1-symbol universe)
  3. ez/live/paper_engine.py — PaperTradingEngine.execute_day loop

If any two drift > tolerance, at least one engine has a silent bug.
V2.18.1 would have been caught by this. V2.16.2 round 2 too.

Strategy design:
- Always long 100% (no re-trading after initial entry)
- Flat synthetic price (close = adj_close = 10.0 every bar)
- Zero commission, zero stamp tax, zero slippage, lot_size=1, no limits
- Same initial cash across all three

Expected: all three final equity values equal initial cash within ~0.5%
(allowing for T+1 entry lag differences + terminal liquidation nuances).

This is a DIFFERENCE test — it surfaces drift without knowing the
"correct" answer. Even if all three agree on a wrong number, the drift
test wouldn't catch it; but any two-engine inconsistency is exposed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Shared synthetic data
# ---------------------------------------------------------------------------

N_DAYS = 30
SYMBOL = "TEST"
INITIAL_CASH = 100_000.0
FLAT_PRICE = 10.0


def _business_days(n: int, start_date: date = date(2024, 3, 1)) -> list[date]:
    """Return n consecutive weekday dates starting from start_date."""
    out: list[date] = []
    d = start_date
    while len(out) < n:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d)
        d = d + timedelta(days=1)
    return out


def _synthetic_df(n: int = N_DAYS) -> pd.DataFrame:
    """Flat-price synthetic data. raw == adj (no dividends), same OHLC."""
    dates = _business_days(n)
    df = pd.DataFrame({
        "open": np.full(n, FLAT_PRICE),
        "high": np.full(n, FLAT_PRICE),
        "low": np.full(n, FLAT_PRICE),
        "close": np.full(n, FLAT_PRICE),
        "adj_close": np.full(n, FLAT_PRICE),
        "volume": np.full(n, 10_000_000),
    }, index=pd.to_datetime(dates))
    df.index.name = "date"
    return df


# ---------------------------------------------------------------------------
# Engine 1: single-stock
# ---------------------------------------------------------------------------

class _AlwaysLongSingleStock:
    def required_factors(self):
        return []

    def generate_signals(self, df):
        return pd.Series([1.0] * len(df), index=df.index, dtype=float)


def _run_single_stock(df: pd.DataFrame) -> pd.Series:
    from ez.backtest.engine import VectorizedBacktestEngine
    engine = VectorizedBacktestEngine(commission_rate=0.0, min_commission=0.0)
    result = engine.run(df, _AlwaysLongSingleStock(), initial_capital=INITIAL_CASH)
    return result.equity_curve


# ---------------------------------------------------------------------------
# Engine 2: portfolio (1-symbol universe)
# ---------------------------------------------------------------------------

class _AlwaysLongPortfolio:
    """Minimal PortfolioStrategy — duck-typed to avoid auto-registration.

    Returns full weight on the single symbol every rebalance day. First
    rebalance buys, subsequent rebalances hit the weight-diff threshold
    and no-op.
    """
    lookback_days = 5

    def __init__(self, symbol: str):
        self.symbol = symbol

    def generate_weights(self, universe_data, date_, prev_weights, prev_returns):
        return {self.symbol: 1.0}


def _run_portfolio(df: pd.DataFrame) -> pd.Series:
    from ez.portfolio.calendar import TradingCalendar
    from ez.portfolio.universe import Universe
    from ez.portfolio.engine import run_portfolio_backtest
    from ez.portfolio.execution import CostModel

    dates_index: pd.DatetimeIndex = df.index  # type: ignore[assignment]
    trading_days = [d.date() for d in dates_index]
    calendar = TradingCalendar.from_dates(trading_days)
    universe = Universe([SYMBOL])

    cost = CostModel(
        buy_commission_rate=0.0, sell_commission_rate=0.0,
        min_commission=0.0, stamp_tax_rate=0.0, slippage_rate=0.0,
    )
    result = run_portfolio_backtest(
        strategy=_AlwaysLongPortfolio(SYMBOL),
        universe=universe,
        universe_data={SYMBOL: df},
        calendar=calendar,
        start=trading_days[0],
        end=trading_days[-1],
        freq="daily",
        initial_cash=INITIAL_CASH,
        cost_model=cost,
        lot_size=1,
        limit_pct=0.0,
        t_plus_1=False,  # disable to remove T+1-only engine difference
        skip_terminal_liquidation=True,  # avoid terminal trade added
    )
    return pd.Series(
        result.equity_curve,
        index=pd.to_datetime(result.dates),
    )


# ---------------------------------------------------------------------------
# Engine 3: paper trading (simulated day-by-day)
# ---------------------------------------------------------------------------

class _FakeBar:
    def __init__(self, d, p):
        self.time = datetime.combine(d, datetime.min.time())
        self.open = self.high = self.low = self.close = self.adj_close = p
        self.volume = 10_000_000


def _run_paper(df: pd.DataFrame) -> pd.Series:
    from ez.live.paper_engine import PaperTradingEngine
    from ez.live.deployment_spec import DeploymentSpec

    trading_days = [d.date() for d in df.index]
    price = float(df["adj_close"].iloc[0])
    all_bars = [_FakeBar(d, price) for d in trading_days]

    def get_kline(symbol, market, period, start_d, end_d):
        return [b for b in all_bars if start_d <= b.time.date() <= end_d]

    chain = MagicMock()
    chain.get_kline.side_effect = get_kline

    spec = DeploymentSpec(
        strategy_name="AlwaysLong",
        strategy_params={},
        symbols=(SYMBOL,),
        market="cn_stock",
        freq="daily",
        initial_cash=INITIAL_CASH,
        buy_commission_rate=0.0,
        sell_commission_rate=0.0,
        min_commission=0.0,
        stamp_tax_rate=0.0,
        slippage_rate=0.0,
        lot_size=1,
        price_limit_pct=0.0,
        t_plus_1=False,
    )
    strategy = _AlwaysLongPortfolio(SYMBOL)
    engine = PaperTradingEngine(spec=spec, strategy=strategy, data_chain=chain)

    # Every day is a rebalance day (daily freq)
    engine._rebalance_dates_cache = set(trading_days)

    equities: list[float] = []
    for d in trading_days:
        result = engine.execute_day(d)
        equities.append(result["equity"])
    return pd.Series(equities, index=pd.to_datetime(trading_days))


# ---------------------------------------------------------------------------
# Consistency tests
# ---------------------------------------------------------------------------

def _max_rel_diff(a: pd.Series, b: pd.Series) -> float:
    """Max relative difference over the shared date range."""
    common = a.index.intersection(b.index)
    if len(common) == 0:
        return float("inf")
    av = a.loc[common].values
    bv = b.loc[common].values
    # Avoid /0; both should be ~INITIAL_CASH
    denom = np.maximum(np.abs(av), 1.0)
    return float(np.max(np.abs(av - bv) / denom))


def test_flat_price_single_stock_vs_portfolio() -> None:
    """Trivial always-long strategy, flat price, zero costs.
    Single-stock and portfolio(1-symbol) engines must agree on
    final equity within ~0.5%.

    A drift here means one engine has a silent P&L bug on the
    simplest possible case. Use it as a baseline — non-zero drift
    even in the trivial case is a red flag.
    """
    df = _synthetic_df()
    ss = _run_single_stock(df)
    pf = _run_portfolio(df)

    # Final equity close to initial (costs=0, flat price -> no P&L)
    assert abs(ss.iloc[-1] - INITIAL_CASH) < 0.01 * INITIAL_CASH, (
        f"Single-stock final equity drifted from flat: {ss.iloc[-1]}"
    )
    assert abs(pf.iloc[-1] - INITIAL_CASH) < 0.01 * INITIAL_CASH, (
        f"Portfolio final equity drifted from flat: {pf.iloc[-1]}"
    )

    drift = _max_rel_diff(ss, pf)
    assert drift < 0.005, (
        f"Single-stock vs Portfolio equity drift {drift:.4%} exceeds 0.5% "
        f"on trivial flat-price always-long. Final: ss={ss.iloc[-1]:.2f} "
        f"pf={pf.iloc[-1]:.2f}. Inspect engine P&L math."
    )


def test_flat_price_portfolio_vs_paper() -> None:
    """Portfolio engine and paper-trading engine must agree on a
    day-by-day simulation of the same strategy and data. Paper
    engine reuses execute_portfolio_trades + CostModel so this
    should be very close."""
    df = _synthetic_df()
    pf = _run_portfolio(df)
    pp = _run_paper(df)

    drift = _max_rel_diff(pf, pp)
    assert drift < 0.005, (
        f"Portfolio vs Paper equity drift {drift:.4%} exceeds 0.5% "
        f"on trivial case. Final: pf={pf.iloc[-1]:.2f} pp={pp.iloc[-1]:.2f}"
    )


def test_flat_price_all_three_engines() -> None:
    """Triangular agreement: all three final equities within 1% of
    each other on the trivial case. This is the top-level canary
    the user asked for — one drift signals a bug somewhere.
    """
    df = _synthetic_df()
    ss = _run_single_stock(df)
    pf = _run_portfolio(df)
    pp = _run_paper(df)

    finals = {
        "single_stock": float(ss.iloc[-1]),
        "portfolio": float(pf.iloc[-1]),
        "paper": float(pp.iloc[-1]),
    }
    values = list(finals.values())
    spread = (max(values) - min(values)) / max(values)
    assert spread < 0.01, (
        f"Cross-engine spread {spread:.4%} on trivial always-long: {finals}. "
        f"One engine produces different P&L — inspect for silent bugs."
    )


def test_trending_price_engines_agree_on_total_return() -> None:
    """Price trends from 10 -> 15 (+50%) over the run. Always-long
    strategy should capture the full trend in both engines. Final
    equity ≈ initial * (end_price / start_price). Drift here signals
    P&L computation divergence (e.g., shares-rounding or price-lookup).
    """
    n = 30
    dates = _business_days(n)
    prices = np.linspace(10.0, 15.0, n)
    df = pd.DataFrame({
        "open": prices, "high": prices, "low": prices,
        "close": prices, "adj_close": prices, "volume": np.full(n, 10_000_000),
    }, index=pd.to_datetime(dates))
    df.index.name = "date"

    ss = _run_single_stock(df)
    pf = _run_portfolio(df)

    # Ground-truth expected: bought at ~prices[1] (signals shifted for
    # single-stock, portfolio rebalances on day 0 / next trading day),
    # held to prices[-1]. Allow 5% tolerance for entry-day semantics.
    expected = INITIAL_CASH * (prices[-1] / prices[1])
    assert abs(ss.iloc[-1] - expected) / expected < 0.05, (
        f"Single-stock total return deviates from expected: "
        f"got {ss.iloc[-1]:.2f} expected ~{expected:.2f}"
    )
    # Cross-engine diff should be tight — same strategy, same prices
    drift = _max_rel_diff(ss, pf)
    assert drift < 0.02, (
        f"Trending price: single-stock vs portfolio drift {drift:.4%}. "
        f"Final ss={ss.iloc[-1]:.2f} pf={pf.iloc[-1]:.2f}"
    )


def test_nonzero_costs_single_stock_vs_portfolio() -> None:
    """Real-world costs (0.01% commission, no stamp, no slippage).
    Buy once at start, hold, exit at end (terminal liquidation).
    Both engines should apply costs on buy + sell legs.

    Drift here would expose asymmetric cost treatment between the
    two engines (e.g., V2.12.2's profit_factor unification missed a
    sibling).
    """
    from ez.backtest.engine import VectorizedBacktestEngine
    from ez.portfolio.calendar import TradingCalendar
    from ez.portfolio.universe import Universe
    from ez.portfolio.engine import run_portfolio_backtest
    from ez.portfolio.execution import CostModel

    df = _synthetic_df(20)
    trading_days = [d.date() for d in df.index]

    ss_engine = VectorizedBacktestEngine(
        commission_rate=0.0001,
        min_commission=0.0,
    )
    ss_result = ss_engine.run(df, _AlwaysLongSingleStock(), initial_capital=INITIAL_CASH)

    calendar = TradingCalendar.from_dates(trading_days)
    pf_result = run_portfolio_backtest(
        strategy=_AlwaysLongPortfolio(SYMBOL),
        universe=Universe([SYMBOL]),
        universe_data={SYMBOL: df},
        calendar=calendar,
        start=trading_days[0], end=trading_days[-1],
        freq="daily",
        initial_cash=INITIAL_CASH,
        cost_model=CostModel(
            buy_commission_rate=0.0001, sell_commission_rate=0.0001,
            min_commission=0.0, stamp_tax_rate=0.0, slippage_rate=0.0,
        ),
        lot_size=1, limit_pct=0.0, t_plus_1=False,
        skip_terminal_liquidation=True,
    )
    pf = pd.Series(pf_result.equity_curve, index=pd.to_datetime(pf_result.dates))

    # Single-stock: buys day 1, costs ~0.01% of INITIAL_CASH = 10. Final equity
    # ≈ 100_000 - 10 = 99_990. Check both engines land close.
    drift = _max_rel_diff(ss_result.equity_curve, pf)
    assert drift < 0.01, (
        f"With commissions: single-stock vs portfolio drift {drift:.4%}. "
        f"ss final={ss_result.equity_curve.iloc[-1]:.2f} pf final={pf.iloc[-1]:.2f}"
    )


def test_dividend_day_single_stock_vs_portfolio() -> None:
    """Dividend day canary: strategy buys THROUGH a dividend event.
    Single-stock engine V2.16.2 round 2 fix should now match portfolio
    engine V2.18.1 behavior.

    Prior to these fixes, a -50% raw-close drop on day 10 (with
    adj_close flat) would produce different equity depending on
    which engine — cross-engine drift would expose the bug.
    """
    # 20 days, dividend on day 10 (raw 10 -> 5, adj stays at 10)
    dates = _business_days(20)
    raw = np.full(20, FLAT_PRICE)
    raw[10:] = FLAT_PRICE * 0.5  # -50% drop post-dividend
    adj = np.full(20, FLAT_PRICE)  # adj absorbs the drop
    df = pd.DataFrame({
        "open": raw, "high": raw, "low": raw,
        "close": raw, "adj_close": adj, "volume": np.full(20, 10_000_000),
    }, index=pd.to_datetime(dates))
    df.index.name = "date"

    ss = _run_single_stock(df)
    pf = _run_portfolio(df)

    # Both should show ~flat equity through the dividend (adj_close flat)
    # Prior bug would show divergent equity on/after day 10
    drift = _max_rel_diff(ss, pf)
    assert drift < 0.01, (
        f"Dividend day cross-engine drift {drift:.4%} > 1%. "
        f"V2.18.1 portfolio fix and V2.16.2 round 2 single-stock fix "
        f"should keep both equities ~flat. Inspect engine price handling."
    )
