"""Tests for JQDataProvider — code mapping, daily fetch, error handling.

All tests mock jqdatasdk so they run without the real package or credentials.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_jq():
    """Create a fake jqdatasdk module with controllable returns."""
    fake = MagicMock()
    fake.auth = MagicMock()
    fake.logout = MagicMock()
    fake.get_price = MagicMock(return_value=None)
    fake.get_valuation = MagicMock(return_value=None)
    fake.get_history_fundamentals = MagicMock(return_value=None)
    fake.get_index_stocks = MagicMock(return_value=None)
    return fake


def _build_raw_df(dates, closes, factors, opens=None, highs=None, lows=None,
                  volumes=None, moneys=None, pre_closes=None):
    """Build a DataFrame mimicking jqdatasdk.get_price(fq=None) output."""
    n = len(dates)
    idx = pd.DatetimeIndex(dates)
    df = pd.DataFrame({
        "open": opens or [10.0] * n,
        "close": closes,
        "high": highs or [11.0] * n,
        "low": lows or [9.0] * n,
        "volume": volumes or [100000] * n,
        "money": moneys or [1000000.0] * n,
        "factor": factors,
        "pre_close": pre_closes or [9.5] * n,
    }, index=idx)
    return df


# ---------------------------------------------------------------------------
# 1. Code mapping (>= 6 cases)
# ---------------------------------------------------------------------------

class TestCodeMapping:
    def test_sz_to_jq(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        assert JQDataProvider.to_jq_code("000001.SZ") == "000001.XSHE"

    def test_sh_to_jq(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        assert JQDataProvider.to_jq_code("600000.SH") == "600000.XSHG"

    def test_bj_to_jq(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        assert JQDataProvider.to_jq_code("830799.BJ") == "830799.XBJE"

    def test_jq_to_sz(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        assert JQDataProvider.from_jq_code("000001.XSHE") == "000001.SZ"

    def test_jq_to_sh(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        assert JQDataProvider.from_jq_code("600000.XSHG") == "600000.SH"

    def test_jq_to_bj(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        assert JQDataProvider.from_jq_code("830799.XBJE") == "830799.BJ"

    def test_unknown_suffix_passthrough(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        assert JQDataProvider.to_jq_code("AAPL") == "AAPL"
        assert JQDataProvider.from_jq_code("AAPL") == "AAPL"

    def test_roundtrip(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        for code in ["000001.SZ", "600000.SH", "830799.BJ"]:
            assert JQDataProvider.from_jq_code(JQDataProvider.to_jq_code(code)) == code


# ---------------------------------------------------------------------------
# 2. get_daily returns standard columns and types
# ---------------------------------------------------------------------------

class TestGetDaily:
    def test_returns_standard_columns(self):
        fake_jq = _make_fake_jq()
        df_raw = _build_raw_df(
            dates=["2025-06-02", "2025-06-03"],
            closes=[10.0, 10.5],
            factors=[1.0, 1.05],
        )
        fake_jq.get_price.return_value = df_raw

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            result = p.get_daily("000001.SZ", "2025-06-01", "2025-06-03")

        assert result is not None
        expected_cols = {"date", "open", "high", "low", "close", "raw_close",
                         "adj_close", "volume", "amount", "pre_close", "factor"}
        assert expected_cols.issubset(set(result.columns))
        assert len(result) == 2

    def test_adj_close_forward_adjustment(self):
        """adj_close = raw_close * factor / latest_factor."""
        fake_jq = _make_fake_jq()
        # Two days: factor grows from 1.0 to 2.0
        # raw_close: 10, 20
        # latest_factor = 2.0
        # adj_close day1 = 10 * 1.0 / 2.0 = 5.0
        # adj_close day2 = 20 * 2.0 / 2.0 = 20.0
        df_raw = _build_raw_df(
            dates=["2025-06-02", "2025-06-03"],
            closes=[10.0, 20.0],
            factors=[1.0, 2.0],
        )
        fake_jq.get_price.return_value = df_raw

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            result = p.get_daily("000001.SZ", "2025-06-01", "2025-06-03")

        assert result is not None
        assert abs(result.iloc[0]["adj_close"] - 5.0) < 1e-6
        assert abs(result.iloc[1]["adj_close"] - 20.0) < 1e-6
        assert abs(result.iloc[0]["raw_close"] - 10.0) < 1e-6
        assert abs(result.iloc[1]["raw_close"] - 20.0) < 1e-6

    def test_volume_is_int(self):
        fake_jq = _make_fake_jq()
        df_raw = _build_raw_df(
            dates=["2025-06-02"],
            closes=[10.0],
            factors=[1.0],
            volumes=[123456],
        )
        fake_jq.get_price.return_value = df_raw

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            result = p.get_daily("000001.SZ", "2025-06-01", "2025-06-03")

        assert result is not None
        assert result.iloc[0]["volume"] == 123456

    def test_empty_response_returns_none(self):
        fake_jq = _make_fake_jq()
        fake_jq.get_price.return_value = pd.DataFrame()

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            result = p.get_daily("000001.SZ", "2025-06-01", "2025-06-03")

        assert result is None


# ---------------------------------------------------------------------------
# 3. No credentials -> returns None, does not raise
# ---------------------------------------------------------------------------

class TestNoCredentials:
    def test_no_creds_returns_none(self):
        with patch.dict("os.environ", {}, clear=False):
            # Ensure env vars are absent
            env = {k: v for k, v in __import__("os").environ.items()
                   if k not in ("JQDATA_USERNAME", "JQDATA_PASSWORD")}
            with patch.dict("os.environ", env, clear=True):
                from ez.data.providers.jqdata_provider import JQDataProvider
                p = JQDataProvider()
                result = p.get_daily("000001.SZ", "2025-06-01", "2025-06-03")
                assert result is None

    def test_no_creds_get_kline_returns_empty(self):
        with patch.dict("os.environ", {}, clear=False):
            env = {k: v for k, v in __import__("os").environ.items()
                   if k not in ("JQDATA_USERNAME", "JQDATA_PASSWORD")}
            with patch.dict("os.environ", env, clear=True):
                from ez.data.providers.jqdata_provider import JQDataProvider
                p = JQDataProvider()
                bars = p.get_kline("000001.SZ", "cn_stock", "daily",
                                   date(2025, 6, 1), date(2025, 6, 30))
                assert bars == []


# ---------------------------------------------------------------------------
# 4. jqdatasdk not installed -> import error -> returns None
# ---------------------------------------------------------------------------

class TestJQDataNotInstalled:
    def test_import_error_returns_none(self):
        """When jqdatasdk is not importable, auth fails gracefully -> None."""
        from ez.data.providers.jqdata_provider import JQDataProvider
        p = JQDataProvider(username="test", password="test")
        p._authenticated = False
        p._auth_failed = False

        # Simulate jqdatasdk not installed by making import raise
        with patch.dict(sys.modules, {"jqdatasdk": None}):
            # Force re-auth attempt by resetting state
            p._auth_failed = False
            p._authenticated = False
            result = p.get_daily("000001.SZ", "2025-06-01", "2025-06-03")
            assert result is None

    def test_import_error_get_kline_returns_empty(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        p = JQDataProvider(username="test", password="test")

        with patch.dict(sys.modules, {"jqdatasdk": None}):
            p._auth_failed = False
            p._authenticated = False
            bars = p.get_kline("000001.SZ", "cn_stock", "daily",
                               date(2025, 6, 1), date(2025, 6, 30))
            assert bars == []


# ---------------------------------------------------------------------------
# 5. get_kline produces valid Bar objects
# ---------------------------------------------------------------------------

class TestGetKline:
    def test_produces_sorted_bars(self):
        fake_jq = _make_fake_jq()
        # Return out-of-order dates to verify sorting
        df_raw = _build_raw_df(
            dates=["2025-06-03", "2025-06-02"],
            closes=[10.5, 10.0],
            factors=[1.0, 1.0],
            opens=[10.2, 9.8],
            highs=[10.8, 10.3],
            lows=[9.9, 9.5],
        )
        fake_jq.get_price.return_value = df_raw

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            bars = p.get_kline("000001.SZ", "cn_stock", "daily",
                               date(2025, 6, 1), date(2025, 6, 3))

        assert len(bars) == 2
        assert bars[0].time < bars[1].time
        assert bars[0].symbol == "000001.SZ"
        assert bars[0].market == "cn_stock"

    def test_unsupported_market_returns_empty(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        p = JQDataProvider(username="test", password="test")
        p._authenticated = True
        bars = p.get_kline("AAPL", "us_stock", "daily",
                           date(2025, 6, 1), date(2025, 6, 30))
        assert bars == []

    def test_unsupported_period_returns_empty(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        p = JQDataProvider(username="test", password="test")
        p._authenticated = True
        bars = p.get_kline("000001.SZ", "cn_stock", "weekly",
                           date(2025, 6, 1), date(2025, 6, 30))
        assert bars == []

    def test_bar_close_is_raw_close(self):
        """Bar.close should be raw_close, Bar.adj_close should be forward-adjusted."""
        fake_jq = _make_fake_jq()
        df_raw = _build_raw_df(
            dates=["2025-06-02"],
            closes=[10.0],
            factors=[0.5],
        )
        fake_jq.get_price.return_value = df_raw

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            bars = p.get_kline("000001.SZ", "cn_stock", "daily",
                               date(2025, 6, 1), date(2025, 6, 3))

        assert len(bars) == 1
        # close = raw_close = 10.0
        assert bars[0].close == 10.0
        # adj_close = 10.0 * 0.5 / 0.5 = 10.0 (single day, latest factor = own factor)
        assert abs(bars[0].adj_close - 10.0) < 1e-6


# ---------------------------------------------------------------------------
# 6. get_valuation
# ---------------------------------------------------------------------------

class TestGetValuation:
    def test_returns_dataframe(self):
        fake_jq = _make_fake_jq()
        val_df = pd.DataFrame({
            "day": ["2025-06-02"],
            "code": ["000001.XSHE"],
            "pe_ratio": [8.5],
            "pb_ratio": [1.2],
            "market_cap": [1500.0],
            "circulating_market_cap": [1200.0],
            "turnover_ratio": [0.03],
        })
        fake_jq.get_valuation.return_value = val_df

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            result = p.get_valuation("000001.SZ", "2025-06-01", "2025-06-03")

        assert result is not None
        assert "date" in result.columns  # renamed from 'day'
        assert abs(result.iloc[0]["pe_ratio"] - 8.5) < 1e-6


# ---------------------------------------------------------------------------
# 7. get_index_constituents
# ---------------------------------------------------------------------------

class TestGetIndexConstituents:
    def test_returns_tushare_codes(self):
        fake_jq = _make_fake_jq()
        fake_jq.get_index_stocks.return_value = [
            "000001.XSHE", "600000.XSHG", "000002.XSHE",
        ]

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            result = p.get_index_constituents("000300.SH", "2025-06-02")

        assert result is not None
        assert "000001.SZ" in result
        assert "600000.SH" in result
        assert "000002.SZ" in result


# ---------------------------------------------------------------------------
# 8. API exception -> None, not raise
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_get_price_exception_returns_none(self):
        fake_jq = _make_fake_jq()
        fake_jq.get_price.side_effect = RuntimeError("network timeout")

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            result = p.get_daily("000001.SZ", "2025-06-01", "2025-06-03")

        assert result is None

    def test_get_valuation_exception_returns_none(self):
        fake_jq = _make_fake_jq()
        fake_jq.get_valuation.side_effect = Exception("quota exceeded")

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="test", password="test")
            p._authenticated = True
            p._last_call_time = 0
            result = p.get_valuation("000001.SZ", "2025-06-01", "2025-06-03")

        assert result is None

    def test_auth_exception_marks_failed(self):
        fake_jq = _make_fake_jq()
        fake_jq.auth.side_effect = Exception("invalid credentials")

        with patch.dict(sys.modules, {"jqdatasdk": fake_jq}):
            from ez.data.providers.jqdata_provider import JQDataProvider
            p = JQDataProvider(username="bad", password="bad")
            result = p.get_daily("000001.SZ", "2025-06-01", "2025-06-03")

        assert result is None
        assert p._auth_failed is True

    def test_search_symbols_always_empty(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        p = JQDataProvider()
        assert p.search_symbols("test") == []
        assert p.search_symbols("000001", "cn_stock") == []


# ---------------------------------------------------------------------------
# 9. Provider name
# ---------------------------------------------------------------------------

class TestProviderName:
    def test_name_is_jqdata(self):
        from ez.data.providers.jqdata_provider import JQDataProvider
        p = JQDataProvider()
        assert p.name == "jqdata"
