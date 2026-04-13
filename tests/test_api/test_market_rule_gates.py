"""V2.16.2 round 3: cross-API market rule gating.

Ensures trading-rule defaults (stamp_tax / lot_size / limit_pct) align
with `market` when the caller doesn't explicitly set them. Frontend
sends correct per-market defaults via BacktestSettings.tsx, but:
- Backend-direct scripts
- AI agent tools building Pydantic models
- Automated test fixtures

... all rely on Pydantic's default-fill, which was market-blind.

Two complementary traps:
- Portfolio API defaults (lot_size=100, limit_pct=0.10) silently
  applied A-share rules to non-CN markets — US backtest rounds to
  100-share lots and rejects normal >10% moves.
- Single-stock API defaults (stamp_tax=0, lot_size=0, limit_pct=0)
  silently SKIPPED A-share rules on CN markets — CN backtest gets
  zero stamp tax + no lot rounding + no 涨跌停 checks, inflating P&L.

This test pins both gates. model_fields_set correctly distinguishes
explicit 0/1 override from default-fill.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Single-stock backtest API: CN defaults activated when market=cn_stock
# ---------------------------------------------------------------------------

def test_single_stock_cn_auto_applies_stamp_lot_limit() -> None:
    from ez.api.routes.backtest import BacktestRequest
    req = BacktestRequest(
        symbol="600519.SH",
        market="cn_stock",
        strategy_name="T",
        start_date="2024-01-01",
        end_date="2024-06-30",
        # no stamp_tax_rate / lot_size / limit_pct provided
    )
    assert req.stamp_tax_rate == 0.0005, (
        "CN single-stock must apply 0.05% stamp tax when not overridden"
    )
    assert req.lot_size == 100, "CN single-stock must round to 100-share lots"
    assert req.limit_pct == 0.10, "CN single-stock must enforce 10% daily limit"


def test_single_stock_us_leaves_defaults_off() -> None:
    from ez.api.routes.backtest import BacktestRequest
    req = BacktestRequest(
        symbol="AAPL",
        market="us_stock",
        strategy_name="T",
        start_date="2024-01-01",
        end_date="2024-06-30",
    )
    # US defaults: no A-share rules auto-applied
    assert req.stamp_tax_rate == 0.0
    assert req.lot_size == 0
    assert req.limit_pct == 0.0


def test_single_stock_cn_explicit_zero_honored() -> None:
    """If user explicitly sets stamp_tax_rate=0 for a CN backtest
    (maybe for reproducing pre-2008 data when stamp tax was different),
    the gate must not override the explicit value."""
    from ez.api.routes.backtest import BacktestRequest
    req = BacktestRequest(
        symbol="600519.SH",
        market="cn_stock",
        strategy_name="T",
        start_date="2024-01-01",
        end_date="2024-06-30",
        stamp_tax_rate=0.0,   # explicit — must NOT be overridden to 0.0005
        lot_size=1,           # explicit disable
        limit_pct=0.0,        # explicit disable
    )
    assert req.stamp_tax_rate == 0.0
    assert req.lot_size == 1
    assert req.limit_pct == 0.0


# ---------------------------------------------------------------------------
# Portfolio API: non-CN markets must NOT inherit A-share defaults
# ---------------------------------------------------------------------------

def test_portfolio_us_zeroes_lot_and_limit_by_default() -> None:
    from ez.api.routes.portfolio import PortfolioRunRequest
    # Minimal required fields for validation; fills defaults for the rest
    req = PortfolioRunRequest(
        strategy_name="TopNRotation",
        symbols=["AAPL", "MSFT"],
        start_date="2024-01-01",
        end_date="2024-06-30",
        market="us_stock",
    )
    assert req.stamp_tax_rate == 0.0, "V2.13.2 gate: US -> no stamp tax"
    assert req.lot_size == 1, "V2.16.2 round 3: US -> 1-share lots"
    assert req.limit_pct == 0.0, "V2.16.2 round 3: US -> no daily limit"


def test_portfolio_cn_keeps_a_share_defaults() -> None:
    from ez.api.routes.portfolio import PortfolioRunRequest
    req = PortfolioRunRequest(
        strategy_name="TopNRotation",
        symbols=["600519.SH", "000001.SZ"],
        start_date="2024-01-01",
        end_date="2024-06-30",
        market="cn_stock",
    )
    assert req.stamp_tax_rate == 0.0005
    assert req.lot_size == 100
    assert req.limit_pct == 0.10


def test_portfolio_us_explicit_lot_override_wins() -> None:
    from ez.api.routes.portfolio import PortfolioRunRequest
    req = PortfolioRunRequest(
        strategy_name="TopNRotation",
        symbols=["AAPL"],
        start_date="2024-01-01",
        end_date="2024-06-30",
        market="us_stock",
        lot_size=10,   # odd-lot market? explicit value must survive the gate
    )
    assert req.lot_size == 10


def test_portfolio_hk_follows_non_cn_defaults() -> None:
    from ez.api.routes.portfolio import PortfolioRunRequest
    req = PortfolioRunRequest(
        strategy_name="TopNRotation",
        symbols=["0700.HK"],
        start_date="2024-01-01",
        end_date="2024-06-30",
        market="hk_stock",
    )
    # HK has its own lot size rules (per-security) and stamp duty, but
    # the platform doesn't currently model those. Defaults to no-rules
    # rather than A-share rules to avoid silent wrong execution.
    assert req.lot_size == 1
    assert req.limit_pct == 0.0
    assert req.stamp_tax_rate == 0.0
