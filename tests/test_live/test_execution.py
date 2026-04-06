"""Tests for shared execute_portfolio_trades function (V2.15 A1)."""
from ez.portfolio.execution import (
    CostModel,
    TradeResult,
    execute_portfolio_trades,
)


def _default_cost() -> CostModel:
    return CostModel(
        buy_commission_rate=0.0003,
        sell_commission_rate=0.0003,
        min_commission=5.0,
        stamp_tax_rate=0.0005,
        slippage_rate=0.0,
    )


def test_basic_buy():
    """Empty holdings + 100% weight on AAPL -> buy shares."""
    trades, holdings, cash, trade_volume = execute_portfolio_trades(
        target_weights={"AAPL": 1.0},
        holdings={},
        equity=100_000,
        cash=100_000,
        prices={"AAPL": 100.0},
        raw_close_today={"AAPL": 100.0},
        prev_raw_close={"AAPL": 95.0},
        has_bar_today={"AAPL"},
        cost_model=_default_cost(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
    )
    assert len(trades) == 1
    assert trades[0].side == "buy"
    assert holdings["AAPL"] > 0
    assert cash < 100_000
    assert trade_volume > 0


def test_sell_before_buy():
    """Sells execute before buys (two-pass ordering)."""
    trades, holdings, cash, _ = execute_portfolio_trades(
        target_weights={"B": 1.0},
        holdings={"A": 500},
        equity=100_000,
        cash=50_000,
        prices={"A": 100.0, "B": 100.0},
        raw_close_today={"A": 100.0, "B": 100.0},
        prev_raw_close={"A": 95.0, "B": 95.0},
        has_bar_today={"A", "B"},
        cost_model=_default_cost(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=False,
    )
    assert trades[0].side == "sell"  # sell A first
    assert trades[1].side == "buy"  # then buy B


def test_t_plus_1_blocks_rebuy():
    """T+1: sold symbol cannot be bought on the same day."""
    sold = set()
    trades, holdings, cash, _ = execute_portfolio_trades(
        target_weights={"X": 1.0},
        holdings={"X": 500},
        equity=100_000,
        cash=50_000,
        prices={"X": 100.0},
        raw_close_today={"X": 100.0},
        prev_raw_close={"X": 95.0},
        has_bar_today={"X"},
        cost_model=_default_cost(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        sold_today=sold,
    )
    # Weights want full allocation, but target shares (1000) > current (500),
    # so effectively this is a buy request. Since there's no sell first,
    # just a buy. Let's use a scenario where sell then buy would conflict.
    # Adjusted scenario: first sell X (target 0), then try to buy it back.
    sold2 = set()
    # Step 1: sell X (target_weights has no X -> target 0)
    trades2, holdings2, cash2, _ = execute_portfolio_trades(
        target_weights={},
        holdings={"X": 500},
        equity=100_000,
        cash=50_000,
        prices={"X": 100.0},
        raw_close_today={"X": 100.0},
        prev_raw_close={"X": 95.0},
        has_bar_today={"X"},
        cost_model=_default_cost(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        sold_today=sold2,
    )
    assert "X" in sold2  # X was sold
    # Step 2: try to buy X with sold_today already containing X
    trades3, holdings3, cash3, _ = execute_portfolio_trades(
        target_weights={"X": 1.0},
        holdings={},
        equity=cash2,
        cash=cash2,
        prices={"X": 100.0},
        raw_close_today={"X": 100.0},
        prev_raw_close={"X": 95.0},
        has_bar_today={"X"},
        cost_model=_default_cost(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=True,
        sold_today=sold2,
    )
    # T+1 blocks the buy
    assert len(trades3) == 0
    assert "X" not in holdings3


def test_limit_up_blocks_buy():
    """Limit-up (+10%) prevents buying."""
    trades, holdings, cash, _ = execute_portfolio_trades(
        target_weights={"Z": 1.0},
        holdings={},
        equity=100_000,
        cash=100_000,
        prices={"Z": 110.0},
        raw_close_today={"Z": 110.0},
        prev_raw_close={"Z": 100.0},
        has_bar_today={"Z"},
        cost_model=_default_cost(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=False,
    )
    assert len(trades) == 0
    assert cash == 100_000


def test_no_bar_skips_trade():
    """Symbol without today's bar is skipped."""
    trades, holdings, cash, _ = execute_portfolio_trades(
        target_weights={"Q": 1.0},
        holdings={},
        equity=100_000,
        cash=100_000,
        prices={"Q": 50.0},
        raw_close_today={"Q": 50.0},
        prev_raw_close={"Q": 48.0},
        has_bar_today=set(),  # Q not in has_bar_today
        cost_model=_default_cost(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=False,
    )
    assert len(trades) == 0


def test_lot_rounding():
    """Shares rounded down to lot_size boundary."""
    trades, holdings, cash, _ = execute_portfolio_trades(
        target_weights={"L": 1.0},
        holdings={},
        equity=100_000,
        cash=100_000,
        prices={"L": 33.33},  # 100000/33.33 = 2999.7 -> round to 2900
        raw_close_today={"L": 33.33},
        prev_raw_close={"L": 32.0},
        has_bar_today={"L"},
        cost_model=_default_cost(),
        lot_size=100,
        limit_pct=0.10,
        t_plus_1=False,
    )
    assert len(trades) == 1
    assert trades[0].shares % 100 == 0  # multiple of lot_size
