"""Tests for AKShareDataProvider and Tushare ETF routing."""
from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

try:
    import akshare  # noqa: F401
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False


@pytest.mark.skipif(not HAS_AKSHARE, reason="akshare not installed")
class TestAKShareProvider:
    def test_etf_prefix_detection(self):
        from ez.data.providers.akshare_provider import _ETF_PREFIXES
        assert "510300".startswith(_ETF_PREFIXES)  # Shanghai ETF
        assert "159915".startswith(_ETF_PREFIXES)  # Shenzhen ETF
        assert "513100".startswith(_ETF_PREFIXES)  # Cross-border ETF
        assert not "000001".startswith(_ETF_PREFIXES)  # Stock
        assert not "600519".startswith(_ETF_PREFIXES)  # Stock

    def test_unsupported_market_returns_empty(self):
        from ez.data.providers.akshare_provider import AKShareDataProvider
        p = AKShareDataProvider()
        result = p.get_kline("AAPL", "us_stock", "daily", date(2024, 1, 1), date(2024, 1, 10))
        assert result == []

    def test_search_symbols_returns_empty(self):
        from ez.data.providers.akshare_provider import AKShareDataProvider
        p = AKShareDataProvider()
        assert p.search_symbols("test") == []

    def test_close_vs_adj_close_split(self):
        """Verify close comes from raw, adj_close from qfq."""
        from ez.data.providers.akshare_provider import AKShareDataProvider

        df_adj = pd.DataFrame({
            "日期": ["2024-01-02"], "开盘": [10.5], "收盘": [11.0],
            "最高": [11.2], "最低": [10.3], "成交量": [100000],
        })
        df_raw = pd.DataFrame({
            "日期": ["2024-01-02"], "开盘": [10.0], "收盘": [10.5],
            "最高": [10.7], "最低": [9.8], "成交量": [100000],
        })

        with patch("akshare.stock_zh_a_hist", side_effect=[df_adj, df_raw]):
            p = AKShareDataProvider()
            p._last_call_time = 0  # skip throttle
            bars = p.get_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 3))

        assert len(bars) == 1
        b = bars[0]
        assert b.close == 10.5      # from raw
        assert b.adj_close == 11.0   # from qfq
        assert b.open == 10.0        # from raw
        assert b.high == 10.7        # from raw

    def test_raw_fallback_to_adj(self):
        """When raw data is empty, fall back to adj values."""
        from ez.data.providers.akshare_provider import AKShareDataProvider

        df_adj = pd.DataFrame({
            "日期": ["2024-01-02"], "开盘": [10.5], "收盘": [11.0],
            "最高": [11.2], "最低": [10.3], "成交量": [100000],
        })

        with patch("akshare.stock_zh_a_hist", side_effect=[df_adj, pd.DataFrame()]):
            p = AKShareDataProvider()
            p._last_call_time = 0
            bars = p.get_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 3))

        assert len(bars) == 1
        assert bars[0].close == 11.0  # fallback to adj
        assert bars[0].adj_close == 11.0

    def test_period_passed_through(self):
        """Verify weekly/monthly period is passed, not hardcoded daily."""
        from ez.data.providers.akshare_provider import AKShareDataProvider

        with patch("akshare.stock_zh_a_hist", return_value=pd.DataFrame()) as mock:
            p = AKShareDataProvider()
            p._last_call_time = 0
            p.get_kline("000001.SZ", "cn_stock", "weekly", date(2024, 1, 1), date(2024, 3, 1))

        # Should have been called with period="weekly" (twice: qfq + raw)
        assert mock.call_count == 2
        assert mock.call_args_list[0][1]["period"] == "weekly"


class TestTushareETFRouting:
    def test_is_fund_code(self):
        from ez.data.providers.tushare_provider import TushareDataProvider
        assert TushareDataProvider._is_fund_code("510300.SH") is True
        assert TushareDataProvider._is_fund_code("159915.SZ") is True
        assert TushareDataProvider._is_fund_code("162411.SZ") is True
        assert TushareDataProvider._is_fund_code("000001.SZ") is False
        assert TushareDataProvider._is_fund_code("600519.SH") is False

    def test_date_conversion_error_handling(self):
        from ez.data.providers.tushare_provider import _tushare_to_date
        from ez.errors import ProviderError
        with pytest.raises(ProviderError):
            _tushare_to_date("invalid")
        with pytest.raises(ProviderError):
            _tushare_to_date("20241333")
