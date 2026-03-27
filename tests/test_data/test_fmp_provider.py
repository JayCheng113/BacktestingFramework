"""FMP data provider unit tests with mocked HTTP."""
from datetime import date, datetime
from unittest.mock import MagicMock, patch
import httpx
import pytest
from ez.data.providers.fmp_provider import FMPDataProvider
from ez.errors import ProviderError


def _make_fmp_response(items=None):
    if items is None:
        items = [
            {"date": "2024-01-03", "open": 150.0, "high": 152.0, "low": 149.0,
             "close": 151.0, "adjClose": 150.5, "volume": 5000000},
            {"date": "2024-01-02", "open": 148.0, "high": 150.0, "low": 147.0,
             "close": 149.5, "adjClose": 149.0, "volume": 4000000},
        ]
    return {"historical": items}


class TestFMPInit:
    def test_name(self):
        assert FMPDataProvider(api_key="test").name == "fmp"

    def test_api_key_from_param(self):
        p = FMPDataProvider(api_key="my_key")
        assert p._api_key == "my_key"

    def test_api_key_from_env(self):
        with patch.dict("os.environ", {"FMP_API_KEY": "env_key"}):
            p = FMPDataProvider()
            assert p._api_key == "env_key"


class TestFMPGetKline:
    def test_raises_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            p = FMPDataProvider(api_key="")
            with pytest.raises(ProviderError, match="FMP_API_KEY not set"):
                p.get_kline("AAPL", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))

    def test_successful_kline(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_fmp_response()
        mock_resp.raise_for_status = MagicMock()

        p = FMPDataProvider(api_key="test")
        p._client = MagicMock()
        p._client.get.return_value = mock_resp

        bars = p.get_kline("AAPL", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        assert len(bars) == 2
        assert bars[0].time < bars[1].time  # sorted ascending
        assert bars[0].symbol == "AAPL"
        assert bars[0].market == "us_stock"

    def test_adj_close_from_response(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_fmp_response()
        mock_resp.raise_for_status = MagicMock()

        p = FMPDataProvider(api_key="test")
        p._client = MagicMock()
        p._client.get.return_value = mock_resp

        bars = p.get_kline("AAPL", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        assert bars[0].adj_close == 149.0  # first bar (sorted) has adjClose 149.0

    def test_missing_adj_close_uses_close(self):
        items = [{"date": "2024-01-02", "open": 10.0, "high": 11.0, "low": 9.0,
                  "close": 10.5, "volume": 1000}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"historical": items}
        mock_resp.raise_for_status = MagicMock()

        p = FMPDataProvider(api_key="test")
        p._client = MagicMock()
        p._client.get.return_value = mock_resp

        bars = p.get_kline("AAPL", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        assert bars[0].adj_close == 10.5  # fallback to close

    def test_empty_historical(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"historical": []}
        mock_resp.raise_for_status = MagicMock()

        p = FMPDataProvider(api_key="test")
        p._client = MagicMock()
        p._client.get.return_value = mock_resp

        bars = p.get_kline("AAPL", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        assert bars == []

    def test_http_error_raises(self):
        p = FMPDataProvider(api_key="test")
        p._client = MagicMock()
        p._client.get.side_effect = httpx.ConnectError("timeout")

        with pytest.raises(ProviderError, match="FMP API error"):
            p.get_kline("AAPL", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))

    def test_ticker_extracted_from_symbol(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_fmp_response([])
        mock_resp.raise_for_status = MagicMock()

        p = FMPDataProvider(api_key="test")
        p._client = MagicMock()
        p._client.get.return_value = mock_resp

        p.get_kline("AAPL.US", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
        call_url = p._client.get.call_args[0][0]
        assert "AAPL" in call_url
        assert ".US" not in call_url


class TestFMPSearchSymbols:
    def test_no_api_key_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            p = FMPDataProvider(api_key="")
            assert p.search_symbols("AAPL") == []

    def test_search_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"symbol": "AAPL", "name": "Apple Inc"},
            {"symbol": "AAPD", "name": "Direxion Daily AAPL Bear"},
        ]

        p = FMPDataProvider(api_key="test")
        p._client = MagicMock()
        p._client.get.return_value = mock_resp

        results = p.search_symbols("AAPL")
        assert len(results) == 2
        assert results[0]["symbol"] == "AAPL"

    def test_search_error_returns_empty(self):
        p = FMPDataProvider(api_key="test")
        p._client = MagicMock()
        p._client.get.side_effect = Exception("network error")
        assert p.search_symbols("AAPL") == []
