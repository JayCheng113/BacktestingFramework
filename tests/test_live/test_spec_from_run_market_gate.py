"""V2.16.2 regression: `_build_spec_from_run` must read cost/market fields
from nested `config._cost` bucket (where `/run` actually saves them) and
respect market when falling back.

Prior bug: all cost/limit fields read top-level `config.get(...)`, which
silently missed because `/run` persists them under `config._cost`. Every
deployment therefore used hardcoded CN defaults (T+1 True, stamp tax
0.05%, lot 100, limit 10%). US / HK deployments silently enforced CN
market rules at paper-trading execution time.

This test pins the contract:
  - CN run -> spec has CN rules (T+1, stamp tax, 10% limit, 100 lot)
  - US run (same config shape) -> spec has US rules (no T+1, no stamp
    tax, no limit, 1-share lot)
  - Explicit values in _cost override market defaults
  - Legacy top-level fields still honored (backward compat for any
    old test harness passing them)

If the read-path regresses to top-level-only, the US case fails
because t_plus_1 comes back True and stamp_tax non-zero.
"""
from __future__ import annotations

from ez.api.routes.live import _build_spec_from_run


def _base_run(market: str, cost_overrides: dict | None = None) -> dict:
    """Shape matches what `routes/portfolio.py` saves via `store.save_run`."""
    cost = {
        "buy_commission_rate": 0.00008,
        "sell_commission_rate": 0.00008,
        "min_commission": 0.0,
        "stamp_tax_rate": 0.0005 if market == "cn_stock" else 0.0,
        "slippage_rate": 0.001,
        "lot_size": 100 if market == "cn_stock" else 1,
        "limit_pct": 0.10 if market == "cn_stock" else 0.0,
        "benchmark": "",
    }
    if cost_overrides:
        cost.update(cost_overrides)
    return {
        "strategy_name": "TopNRotation",
        "strategy_params": {"top_n": 5, "_cost": cost},
        "symbols": ["SYM1", "SYM2"],
        "initial_cash": 1_000_000.0,
        "config": {
            "market": market,
            "freq": "daily",
            "rebal_weekday": None,
            "_cost": cost,
            "_optimizer": {"kind": "none"},
            "_risk": {"enabled": False},
            "_index": {},
        },
    }


def test_cn_stock_run_builds_cn_market_rules() -> None:
    """A-share portfolio run -> spec has T+1, stamp tax, 10% limit, 100 lot."""
    run = _base_run("cn_stock")
    spec = _build_spec_from_run(run)
    assert spec.market == "cn_stock"
    assert spec.t_plus_1 is True
    assert spec.stamp_tax_rate == 0.0005
    assert spec.price_limit_pct == 0.10
    assert spec.lot_size == 100


def test_us_stock_run_builds_us_market_rules() -> None:
    """V2.16.2 CRITICAL: US run -> NO T+1, NO stamp tax, NO limit, 1-share lot.

    Regression: if this test fails, paper trading silently applies
    A-share rules to US deployments (T+1 block, 0.05% stamp tax, 10%
    limit rejects normal moves, 100-share lot wastes cash).
    """
    run = _base_run("us_stock")
    spec = _build_spec_from_run(run)
    assert spec.market == "us_stock"
    assert spec.t_plus_1 is False, "US stocks settle T+2 but have no T+1 wash"
    assert spec.stamp_tax_rate == 0.0, "US has no stamp tax"
    assert spec.price_limit_pct == 0.0, "US has no daily price limit"
    assert spec.lot_size == 1, "US trades single shares"


def test_hk_stock_run_builds_non_cn_rules() -> None:
    """HK market: also not CN — no stamp tax/limit/T+1 pattern that
    matches A-shares. (HK has its own stamp duty but this platform
    doesn't currently model it.)"""
    run = _base_run("hk_stock")
    spec = _build_spec_from_run(run)
    assert spec.t_plus_1 is False
    assert spec.stamp_tax_rate == 0.0
    assert spec.price_limit_pct == 0.0


def test_explicit_cost_override_wins_over_market_default() -> None:
    """User explicitly set stamp_tax_rate=0.001 for some exotic market —
    that value must round-trip through the spec, not be overwritten by
    the market-gated default."""
    run = _base_run(
        "us_stock",
        cost_overrides={"stamp_tax_rate": 0.001, "lot_size": 10},
    )
    spec = _build_spec_from_run(run)
    assert spec.stamp_tax_rate == 0.001
    assert spec.lot_size == 10


def test_legacy_top_level_fields_still_read() -> None:
    """Back-compat: if config has a flat layout (old saves or external
    callers), top-level keys should still be picked up. The nested
    bucket takes precedence, but a flat config should not break."""
    run = {
        "strategy_name": "T",
        "strategy_params": {},
        "symbols": ["A"],
        "initial_cash": 100000.0,
        "config": {
            "market": "cn_stock",
            "freq": "daily",
            # Legacy: all at top-level, no _cost bucket
            "t_plus_1": True,
            "stamp_tax_rate": 0.0005,
            "lot_size": 100,
            "price_limit_pct": 0.10,
        },
    }
    spec = _build_spec_from_run(run)
    assert spec.t_plus_1 is True
    assert spec.stamp_tax_rate == 0.0005
    assert spec.lot_size == 100
    assert spec.price_limit_pct == 0.10


def test_missing_config_uses_market_gated_defaults() -> None:
    """If config is empty (minimal run dict, e.g. from a legacy path
    that didn't save anything), market determines the defaults — CN
    gets CN rules, else non-CN rules."""
    run_cn = {"strategy_name": "T", "symbols": ["A"], "initial_cash": 100000.0,
              "config": {"market": "cn_stock"}}
    spec_cn = _build_spec_from_run(run_cn)
    assert spec_cn.t_plus_1 is True
    assert spec_cn.stamp_tax_rate == 0.0005

    run_us = {"strategy_name": "T", "symbols": ["A"], "initial_cash": 100000.0,
              "config": {"market": "us_stock"}}
    spec_us = _build_spec_from_run(run_us)
    assert spec_us.t_plus_1 is False
    assert spec_us.stamp_tax_rate == 0.0


def test_string_config_json_is_parsed() -> None:
    """config may be stored as a JSON string in DuckDB; the parser
    must still extract nested _cost bucket."""
    import json
    run = {
        "strategy_name": "T",
        "symbols": '["A"]',
        "initial_cash": 100000.0,
        "config": json.dumps({
            "market": "us_stock",
            "_cost": {"stamp_tax_rate": 0.0, "lot_size": 1},
        }),
    }
    spec = _build_spec_from_run(run)
    assert spec.market == "us_stock"
    assert spec.stamp_tax_rate == 0.0
    assert spec.lot_size == 1
