"""V2.17 round 8: runtime data sanity guard in paper_engine.

V2.18.1 Tushare fund_adj anomaly affected 131 dates across 57 ETFs —
the error was only caught when building parquet cache with cross-source
validation. In a live deployment with stale cache or a different
provider, the bad data would flow silently into strategy signals.

These tests pin the runtime check:
1. Normal daily moves (< 15%) don't warn
2. Raw close jump > 15% warns
3. V2.18.1 pattern (adj spike without raw move) warns
4. Dedup: same anomaly same symbol only warned once per engine instance
5. Missing data / single-bar DataFrames don't crash
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from ez.live.deployment_spec import DeploymentSpec
from ez.live.paper_engine import PaperTradingEngine


class _FakeBar:
    def __init__(self, d: datetime, close: float, adj_close: float):
        self.time = d
        self.open = close
        self.high = close
        self.low = close
        self.close = close
        self.adj_close = adj_close
        self.volume = 1000


def _engine() -> PaperTradingEngine:
    spec = DeploymentSpec(
        strategy_name="T", strategy_params={},
        symbols=("X",), market="cn_stock", freq="daily",
        initial_cash=100000.0,
        t_plus_1=False, price_limit_pct=0.0, lot_size=1,
    )
    return PaperTradingEngine(spec=spec, strategy=object(), data_chain=MagicMock())


def _df(rows: list[dict]) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=dates)


def _bars(rows: list[dict]) -> list[_FakeBar]:
    dates = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    return [
        _FakeBar(ts.to_pydatetime(), float(row["close"]), float(row["adj_close"]))
        for ts, row in zip(dates, rows)
    ]


# ---------------------------------------------------------------------------
# Normal / safe cases
# ---------------------------------------------------------------------------

def test_normal_daily_move_no_warning() -> None:
    """±5% move is normal — no warning."""
    e = _engine()
    df = _df([
        {"close": 10.0, "adj_close": 10.0},
        {"close": 10.3, "adj_close": 10.3},  # +3%
    ])
    warnings = e._sanity_check_fresh_bars("AAA", df)
    assert warnings == []


def test_short_df_does_not_crash() -> None:
    """< 2 bars: can't compare, return empty."""
    e = _engine()
    df = _df([{"close": 10.0, "adj_close": 10.0}])
    assert e._sanity_check_fresh_bars("AAA", df) == []
    assert e._sanity_check_fresh_bars("AAA", _df([])) == []


def test_nan_bars_do_not_crash() -> None:
    """Missing / NaN values: skip the check, don't raise."""
    e = _engine()
    df = _df([
        {"close": float("nan"), "adj_close": float("nan")},
        {"close": 10.0, "adj_close": 10.0},
    ])
    # No crash, empty warnings
    warnings = e._sanity_check_fresh_bars("AAA", df)
    assert warnings == []


# ---------------------------------------------------------------------------
# Check 1: raw close single-day spike
# ---------------------------------------------------------------------------

def test_raw_close_spike_above_threshold_warns() -> None:
    """+20% raw move — likely ex-dividend or data issue."""
    e = _engine()
    df = _df([
        {"close": 10.0, "adj_close": 10.0},
        {"close": 12.5, "adj_close": 12.5},  # +25%
    ])
    warnings = e._sanity_check_fresh_bars("AAA", df)
    assert len(warnings) == 1
    assert "raw close 单日变动" in warnings[0]
    assert "+25" in warnings[0]


def test_raw_close_drop_above_threshold_warns() -> None:
    """-30% raw move — classic cash-dividend pattern for ETFs."""
    e = _engine()
    df = _df([
        {"close": 10.0, "adj_close": 10.0},
        {"close": 7.0, "adj_close": 10.0},  # raw -30%, adj flat (dividend day)
    ])
    warnings = e._sanity_check_fresh_bars("AAA", df)
    # Should catch on raw spike AND adj-raw divergence
    assert len(warnings) >= 1
    assert any("raw close 单日变动" in w for w in warnings)


def test_raw_at_threshold_boundary_does_not_warn() -> None:
    """Exactly 15% is the threshold — should be inclusive (no warn).
    14.9% below, 15.1% above."""
    e = _engine()
    df_just_under = _df([
        {"close": 10.0, "adj_close": 10.0},
        {"close": 11.49, "adj_close": 11.49},  # +14.9%
    ])
    # 14.9% < 15.0% threshold → no warn
    assert e._sanity_check_fresh_bars("AAA", df_just_under) == []


# ---------------------------------------------------------------------------
# Check 2: V2.18.1 pattern (adj spike without raw move)
# ---------------------------------------------------------------------------

def test_v2181_anomaly_pattern_warns() -> None:
    """adj_close jumps 100% but raw close barely moves — exact V2.18.1
    Tushare fund_adj anomaly pattern. Prior undetected cause of
    phantom equity moves."""
    e = _engine()
    df = _df([
        {"close": 10.0, "adj_close": 5.0},   # normal
        {"close": 10.1, "adj_close": 10.1},  # adj jumped to raw, raw +1%
    ])
    warnings = e._sanity_check_fresh_bars("AAA", df)
    assert len(warnings) >= 1
    assert any("V2.18.1" in w or "adj_factor" in w for w in warnings)


def test_adj_tracks_raw_smoothly_no_warn() -> None:
    """Normal pattern: adj and raw both move by similar amount. No warn."""
    e = _engine()
    df = _df([
        {"close": 10.0, "adj_close": 5.0},  # different levels (splits)
        {"close": 10.1, "adj_close": 5.05},  # both +1%
    ])
    warnings = e._sanity_check_fresh_bars("AAA", df)
    assert warnings == []


# ---------------------------------------------------------------------------
# Integration: dedup through _fetch_latest → _sanity_warned
# ---------------------------------------------------------------------------

def test_dedup_same_anomaly_warned_once(caplog) -> None:
    """If the same bad bar persists across multiple _fetch_latest calls
    (hourly auto-tick), we only warn once — not 24 times/day."""
    e = _engine()
    e.strategy = MagicMock(lookback_days=2)
    anomalous_bars = _bars([
        {"close": 10.0, "adj_close": 10.0},
        {"close": 13.0, "adj_close": 13.0},  # +30%
    ])
    e.data_chain.get_kline.return_value = anomalous_bars

    with caplog.at_level("WARNING"):
        e._sanity_warned.clear()
        e._fetch_latest(date(2024, 1, 2))
        first_text = caplog.text
        assert "[数据异常]" in first_text
        assert ("X", "raw_spike") in e._sanity_warned

        caplog.clear()
        e._fetch_latest(date(2024, 1, 2))
        assert caplog.text == ""
        assert ("X", "raw_spike") in e._sanity_warned


def test_fetch_latest_clears_warning_key_after_recovery(caplog) -> None:
    e = _engine()
    e.strategy = MagicMock(lookback_days=2)
    anomalous_bars = _bars([
        {"close": 10.0, "adj_close": 10.0},
        {"close": 13.0, "adj_close": 13.0},
    ])
    normal_bars = _bars([
        {"close": 10.0, "adj_close": 10.0},
        {"close": 10.2, "adj_close": 10.2},
    ])
    e.data_chain.get_kline.side_effect = [anomalous_bars, normal_bars]

    with caplog.at_level("WARNING"):
        e._fetch_latest(date(2024, 1, 2))
        assert ("X", "raw_spike") in e._sanity_warned

        caplog.clear()
        e._fetch_latest(date(2024, 1, 2))
        assert caplog.text == ""
        assert ("X", "raw_spike") not in e._sanity_warned


def test_fetch_latest_warns_again_for_new_anomaly_kind_after_recovery(caplog) -> None:
    e = _engine()
    e.strategy = MagicMock(lookback_days=2)
    normal_bars = _bars([
        {"close": 10.0, "adj_close": 10.0},
        {"close": 10.1, "adj_close": 10.1},
    ])
    divergence_bars = _bars([
        {"close": 10.0, "adj_close": 5.0},
        {"close": 10.1, "adj_close": 10.1},
    ])
    e.data_chain.get_kline.side_effect = [normal_bars, divergence_bars]

    with caplog.at_level("WARNING"):
        e._fetch_latest(date(2024, 1, 2))
        assert e._sanity_warned == set()

        e._fetch_latest(date(2024, 1, 2))
        assert "[数据异常]" in caplog.text
        assert ("X", "adj_raw_divergence") in e._sanity_warned
