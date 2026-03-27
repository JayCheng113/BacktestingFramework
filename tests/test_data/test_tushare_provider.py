"""Unit tests for TushareDataProvider with mocked HTTP responses."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ez.data.providers.tushare_provider import (
    TushareDataProvider,
    _date_to_tushare,
    _tushare_to_datetime,
)
from ez.errors import ProviderError
from ez.types import Bar


# ── Date conversion tests ─────────────────────────────────────────


class TestDateConversion:
    def test_date_to_tushare_basic(self):
        assert _date_to_tushare(date(2024, 1, 2)) == "20240102"

    def test_date_to_tushare_padded(self):
        assert _date_to_tushare(date(2023, 3, 5)) == "20230305"

    def test_date_to_tushare_end_of_year(self):
        assert _date_to_tushare(date(2023, 12, 31)) == "20231231"

    def test_tushare_to_datetime(self):
        result = _tushare_to_datetime("20240102")
        assert result == datetime(2024, 1, 2)

    def test_tushare_to_datetime_roundtrip(self):
        d = date(2023, 7, 15)
        ts_str = _date_to_tushare(d)
        dt = _tushare_to_datetime(ts_str)
        assert dt.date() == d


# ── Helper to build mock Tushare responses ─────────────────────────


def _make_tushare_response(
    fields: list[str], items: list[list], code: int = 0, msg: str = "success"
) -> dict:
    return {
        "code": code,
        "msg": msg,
        "data": {"fields": fields, "items": items},
    }


def _make_kline_response(rows: list[list] | None = None) -> dict:
    """Build a daily kline response. Default: 3 rows of sample data."""
    fields = ["ts_code", "trade_date", "open", "high", "low", "close", "vol"]
    if rows is None:
        rows = [
            ["000001.SZ", "20231228", 10.50, 10.80, 10.40, 10.60, 12345.0],
            ["000001.SZ", "20231229", 10.60, 10.90, 10.55, 10.85, 15432.0],
            ["000001.SZ", "20240102", 10.90, 11.00, 10.70, 10.95, 20000.0],
        ]
    return _make_tushare_response(fields, rows)


def _make_adj_response(rows: list[list] | None = None) -> dict:
    """Build an adj_factor response matching the default kline dates."""
    fields = ["ts_code", "trade_date", "adj_factor"]
    if rows is None:
        rows = [
            ["000001.SZ", "20231228", 120.0],
            ["000001.SZ", "20231229", 120.5],
            ["000001.SZ", "20240102", 121.0],
        ]
    return _make_tushare_response(fields, rows)


def _make_empty_response() -> dict:
    return {"code": 0, "msg": "success", "data": {"fields": [], "items": []}}


def _make_error_response(code: int = -1, msg: str = "server error") -> dict:
    return {"code": code, "msg": msg, "data": None}


# ── Provider construction ──────────────────────────────────────────


class TestProviderInit:
    def test_name(self):
        p = TushareDataProvider(token="test_token")
        assert p.name == "tushare"

    def test_raises_without_token(self):
        with patch.dict("os.environ", {}, clear=True):
            p = TushareDataProvider(token="")
            with pytest.raises(ProviderError, match="TUSHARE_TOKEN not set"):
                p.get_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))

    def test_token_from_env(self):
        with patch.dict("os.environ", {"TUSHARE_TOKEN": "env_token"}):
            p = TushareDataProvider()
            assert p._token == "env_token"


# ── get_kline tests ────────────────────────────────────────────────


class TestGetKline:
    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_daily_with_adj_factor(self, mock_call):
        """Daily data should fetch both kline and adj_factor, computing adj_close."""
        kline_data = _make_kline_response()["data"]
        adj_data = _make_adj_response()["data"]
        mock_call.side_effect = [kline_data, adj_data]

        p = TushareDataProvider(token="test")
        bars = p.get_kline("000001.SZ", "cn_stock", "daily", date(2023, 12, 28), date(2024, 1, 2))

        assert len(bars) == 3
        assert mock_call.call_count == 2

        # Verify forward-adjusted close: adj_close = close * factor / latest_factor
        # Latest factor = 121.0 (2024-01-02)
        # First bar: close=10.60, factor=120.0 => adj_close = 10.60 * 120.0 / 121.0
        expected_adj = round(10.60 * 120.0 / 121.0, 4)
        assert bars[0].adj_close == expected_adj

        # Last bar: factor equals latest => adj_close = close
        expected_adj_last = round(10.95 * 121.0 / 121.0, 4)
        assert bars[2].adj_close == expected_adj_last

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_weekly_no_adj_factor(self, mock_call):
        """Weekly data should NOT fetch adj_factor; adj_close = close."""
        kline_data = _make_kline_response()["data"]
        mock_call.return_value = kline_data

        p = TushareDataProvider(token="test")
        bars = p.get_kline("000001.SZ", "cn_stock", "weekly", date(2023, 12, 28), date(2024, 1, 2))

        assert len(bars) == 3
        assert mock_call.call_count == 1  # only kline call, no adj_factor
        # adj_close should equal close when no adj_map
        for bar in bars:
            assert bar.adj_close == bar.close

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_monthly_no_adj_factor(self, mock_call):
        """Monthly behaves same as weekly — no adj_factor fetch."""
        kline_data = _make_kline_response()["data"]
        mock_call.return_value = kline_data

        p = TushareDataProvider(token="test")
        bars = p.get_kline("000001.SZ", "cn_stock", "monthly", date(2023, 12, 28), date(2024, 1, 2))

        assert mock_call.call_count == 1

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_empty_kline_response(self, mock_call):
        """Empty kline response should return empty list without calling adj_factor."""
        mock_call.return_value = None

        p = TushareDataProvider(token="test")
        bars = p.get_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))

        assert bars == []
        assert mock_call.call_count == 1  # only kline, skipped adj

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_daily_missing_adj_factor(self, mock_call):
        """Daily data with missing adj_factor should fall back to close = adj_close."""
        kline_data = _make_kline_response()["data"]
        mock_call.side_effect = [kline_data, None]  # kline OK, adj_factor empty

        p = TushareDataProvider(token="test")
        bars = p.get_kline("000001.SZ", "cn_stock", "daily", date(2023, 12, 28), date(2024, 1, 2))

        assert len(bars) == 3
        # No adj data => adj_close should equal close
        for bar in bars:
            assert bar.adj_close == bar.close

    def test_unsupported_market(self):
        p = TushareDataProvider(token="test")
        with pytest.raises(ProviderError, match="only supports markets"):
            p.get_kline("AAPL", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))

    def test_unsupported_period(self):
        p = TushareDataProvider(token="test")
        with pytest.raises(ProviderError, match="Unsupported period"):
            p.get_kline("000001.SZ", "cn_stock", "1min", date(2024, 1, 1), date(2024, 1, 31))

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_bars_sorted_by_time(self, mock_call):
        """Bars should come out sorted ascending by time regardless of API order."""
        # Return rows in reverse chronological order
        rows = [
            ["000001.SZ", "20240102", 10.9, 11.0, 10.7, 10.95, 20000.0],
            ["000001.SZ", "20231228", 10.5, 10.8, 10.4, 10.60, 12345.0],
        ]
        kline_data = _make_kline_response(rows)["data"]
        mock_call.side_effect = [kline_data, None]

        p = TushareDataProvider(token="test")
        bars = p.get_kline("000001.SZ", "cn_stock", "daily", date(2023, 12, 28), date(2024, 1, 2))

        times = [b.time for b in bars]
        assert times == sorted(times)

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_volume_conversion(self, mock_call):
        """Tushare vol is in 手 (100 shares); we convert to shares."""
        rows = [["000001.SZ", "20240102", 10.0, 11.0, 9.0, 10.5, 500.0]]
        kline_data = _make_kline_response(rows)["data"]
        mock_call.side_effect = [kline_data, None]

        p = TushareDataProvider(token="test")
        bars = p.get_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 2), date(2024, 1, 2))

        assert bars[0].volume == 50000  # 500 * 100

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_bar_fields_correct(self, mock_call):
        """Each bar should have correct symbol, market, and OHLC values."""
        rows = [["600000.SH", "20240102", 8.50, 8.80, 8.40, 8.70, 10000.0]]
        kline_data = _make_kline_response(rows)["data"]
        adj_rows = [["600000.SH", "20240102", 100.0]]
        adj_data = _make_adj_response(adj_rows)["data"]
        mock_call.side_effect = [kline_data, adj_data]

        p = TushareDataProvider(token="test")
        bars = p.get_kline("600000.SH", "cn_stock", "daily", date(2024, 1, 2), date(2024, 1, 2))

        assert len(bars) == 1
        b = bars[0]
        assert b.symbol == "600000.SH"
        assert b.market == "cn_stock"
        assert b.open == 8.50
        assert b.high == 8.80
        assert b.low == 8.40
        assert b.close == 8.70
        assert b.time == datetime(2024, 1, 2)


# ── _call_api tests ────────────────────────────────────────────────


class TestCallApi:
    @patch("ez.data.providers.tushare_provider.TushareDataProvider._throttle")
    def test_successful_call(self, mock_throttle):
        """Successful API call returns data dict."""
        response_body = _make_kline_response()
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_body
        mock_resp.raise_for_status = MagicMock()

        p = TushareDataProvider(token="test")
        p._client = MagicMock()
        p._client.post.return_value = mock_resp

        result = p._call_api("daily", {"ts_code": "000001.SZ"}, "ts_code,trade_date")

        assert result is not None
        assert "fields" in result
        assert "items" in result

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._throttle")
    def test_error_code_raises(self, mock_throttle):
        """Non-zero error code should raise ProviderError."""
        response_body = _make_error_response(code=-1, msg="system error")
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_body
        mock_resp.raise_for_status = MagicMock()

        p = TushareDataProvider(token="test")
        p._client = MagicMock()
        p._client.post.return_value = mock_resp

        with pytest.raises(ProviderError, match="Tushare API error"):
            p._call_api("daily", {}, "")

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._throttle")
    def test_auth_error_raises(self, mock_throttle):
        """Auth error (code=2002) should raise ProviderError with auth message."""
        response_body = _make_error_response(code=2002, msg="Invalid token")
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_body
        mock_resp.raise_for_status = MagicMock()

        p = TushareDataProvider(token="bad_token")
        p._client = MagicMock()
        p._client.post.return_value = mock_resp

        with pytest.raises(ProviderError, match="auth error"):
            p._call_api("daily", {}, "")

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._throttle")
    def test_empty_items_returns_none(self, mock_throttle):
        """Empty items list should return None."""
        response_body = _make_empty_response()
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_body
        mock_resp.raise_for_status = MagicMock()

        p = TushareDataProvider(token="test")
        p._client = MagicMock()
        p._client.post.return_value = mock_resp

        result = p._call_api("daily", {}, "")
        assert result is None

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._throttle")
    def test_http_error_raises(self, mock_throttle):
        """HTTP transport errors should raise ProviderError."""
        p = TushareDataProvider(token="test")
        p._client = MagicMock()
        p._client.post.side_effect = httpx.ConnectError("connection refused")

        with pytest.raises(ProviderError, match="Tushare HTTP error"):
            p._call_api("daily", {}, "")


# ── search_symbols tests ──────────────────────────────────────────


class TestSearchSymbols:
    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_search_by_code(self, mock_call):
        mock_call.return_value = {
            "fields": ["ts_code", "symbol", "name", "area", "industry", "market", "list_date"],
            "items": [
                ["000001.SZ", "000001", "Ping An Bank", "Shenzhen", "Banking", "Main", "19910403"],
                ["000002.SZ", "000002", "Vanke A", "Shenzhen", "Real Estate", "Main", "19910129"],
            ],
        }

        p = TushareDataProvider(token="test")
        results = p.search_symbols("000001")

        assert len(results) == 1
        assert results[0]["symbol"] == "000001.SZ"
        assert results[0]["name"] == "Ping An Bank"

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_search_by_name(self, mock_call):
        mock_call.return_value = {
            "fields": ["ts_code", "symbol", "name", "area", "industry", "market", "list_date"],
            "items": [
                ["000001.SZ", "000001", "Ping An Bank", "Shenzhen", "Banking", "Main", "19910403"],
                ["601318.SH", "601318", "Ping An Insurance", "Shenzhen", "Insurance", "Main", "20070301"],
            ],
        }

        p = TushareDataProvider(token="test")
        results = p.search_symbols("Ping An")

        assert len(results) == 2

    def test_search_no_token(self):
        p = TushareDataProvider(token="")
        results = p.search_symbols("test")
        assert results == []

    def test_search_unsupported_market(self):
        p = TushareDataProvider(token="test")
        results = p.search_symbols("AAPL", market="us_stock")
        assert results == []

    @patch("ez.data.providers.tushare_provider.TushareDataProvider._call_api")
    def test_search_empty_response(self, mock_call):
        mock_call.return_value = None

        p = TushareDataProvider(token="test")
        results = p.search_symbols("nonexistent")

        assert results == []


# ── adj_map tests ──────────────────────────────────────────────────


class TestBuildAdjMap:
    def test_build_adj_map_normal(self):
        data = {
            "fields": ["ts_code", "trade_date", "adj_factor"],
            "items": [
                ["000001.SZ", "20231228", 120.0],
                ["000001.SZ", "20231229", 120.5],
            ],
        }
        result = TushareDataProvider._build_adj_map(data)
        assert result == {"20231228": 120.0, "20231229": 120.5}

    def test_build_adj_map_none(self):
        result = TushareDataProvider._build_adj_map(None)
        assert result == {}
