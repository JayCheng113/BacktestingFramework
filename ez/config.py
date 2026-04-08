"""Configuration loading from YAML + .env.

[CORE] — append-only. New config keys must have defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class DatabaseConfig(BaseModel):
    path: str = "data/ez_trading.db"


class DataSourceEntry(BaseModel):
    primary: str = "tencent"
    backup: list[str] = []


class DataSourcesConfig(BaseModel):
    cn_stock: DataSourceEntry = DataSourceEntry(primary="tushare", backup=["akshare", "tencent"])
    us_stock: DataSourceEntry = DataSourceEntry(primary="fmp", backup=["tencent"])
    hk_stock: DataSourceEntry = DataSourceEntry(primary="tencent", backup=[])
    timeout_seconds: int = 10
    max_retries: int = 2


class BacktestConfig(BaseModel):
    default_initial_capital: float = 100000.0
    default_commission_rate: float = 0.00008
    default_min_commission: float = 0.0
    risk_free_rate: float = 0.03


class StrategyConfig(BaseModel):
    scan_dirs: list[str] = ["ez/strategy/builtin", "strategies"]


class CorsConfig(BaseModel):
    origins: list[str] = ["http://localhost:3000"]


class LLMConfig(BaseModel):
    provider: str = "deepseek"  # deepseek | qwen | openai | local
    api_key: str = ""  # read from env: DEEPSEEK_API_KEY, QWEN_API_KEY, etc.
    model: str = ""  # empty = use provider default
    base_url: str = ""  # empty = use provider default
    timeout: float = 60.0
    max_tokens: int = 4096
    temperature: float = 0.3


class EzConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    data_sources: DataSourcesConfig = DataSourcesConfig()
    backtest: BacktestConfig = BacktestConfig()
    strategy: StrategyConfig = StrategyConfig()
    cors: CorsConfig = CorsConfig()
    llm: LLMConfig = LLMConfig()


_config: EzConfig | None = None


def _load_dotenv(env_path: str = ".env") -> None:
    """Load .env file into os.environ if it exists (no external dependency)."""
    path = Path(env_path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def get_project_root() -> Path:
    """Project root: respects EZ_ROOT env (frozen builds), else derives from file path."""
    import sys
    env_root = os.environ.get("EZ_ROOT")
    if env_root:
        return Path(env_root)
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent

_PROJECT_ROOT = get_project_root()


def load_config(config_path: str = "configs/default.yaml") -> EzConfig:
    """Load .env into environment, then YAML config with Pydantic defaults."""
    global _config
    if _config is not None:
        return _config

    _load_dotenv(str(_PROJECT_ROOT / ".env"))

    path = Path(config_path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        _config = EzConfig(**raw)
    else:
        _config = EzConfig()
    return _config


def reset_config() -> None:
    """Reset cached config (for testing)."""
    global _config
    _config = None
