from pathlib import Path
from ez.config import load_config, EzConfig


def test_load_default_config():
    config = load_config()
    assert config.server.port == 8000
    assert config.database.path == "data/ez_trading.db"
    assert config.backtest.default_initial_capital == 100000.0
    assert config.backtest.default_commission_rate == 0.0003


def test_config_data_sources():
    config = load_config()
    assert config.data_sources.cn_stock.primary == "tushare"
    assert "tencent" in config.data_sources.cn_stock.backup


def test_config_strategy_scan_dirs():
    config = load_config()
    assert len(config.strategy.scan_dirs) >= 2
