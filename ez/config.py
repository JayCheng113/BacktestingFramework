"""Configuration loading from YAML + .env.

[CORE] — append-only. New config keys must have defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    host: str = Field(default="0.0.0.0", description="FastAPI 监听地址；生产环境通常绑定到 0.0.0.0")
    port: int = Field(default=8000, description="FastAPI 监听端口")


class DatabaseConfig(BaseModel):
    path: str = Field(default="data/ez_trading.db", description="DuckDB 数据库文件路径（相对于项目根目录）")


class DataSourceEntry(BaseModel):
    primary: str = Field(default="tencent", description="主数据源提供商名称")
    backup: list[str] = Field(default=[], description="备用数据源提供商列表，按优先级排序；空列表表示无备用")


class DataSourcesConfig(BaseModel):
    cn_stock: DataSourceEntry = Field(
        default=DataSourceEntry(primary="tushare", backup=["akshare", "tencent"]),
        description="A 股数据源配置；主源 tushare，备用 akshare / tencent",
    )
    us_stock: DataSourceEntry = Field(
        default=DataSourceEntry(primary="fmp", backup=["tencent"]),
        description="美股数据源配置",
    )
    hk_stock: DataSourceEntry = Field(
        default=DataSourceEntry(primary="tencent", backup=[]),
        description="港股数据源配置",
    )
    timeout_seconds: int = Field(default=10, description="单次数据源请求超时时间（秒）")
    max_retries: int = Field(default=2, description="数据源请求失败后的最大重试次数")


class BacktestConfig(BaseModel):
    default_initial_capital: float = Field(
        default=1000000.0,
        description="默认初始资金（人民币元）；回测未指定 initial_capital 时使用此值",
    )
    default_commission_rate: float = Field(
        default=0.00008,
        description="默认佣金费率（万 0.8，即 0.008%）；A 股卖出时额外收取印花税需在策略层单独建模",
    )
    default_min_commission: float = Field(
        default=0.0,
        description="单笔最低佣金（元）；0.0 表示不设最低，按费率计算",
    )
    risk_free_rate: float = Field(
        default=0.03,
        description="无风险利率（年化，小数形式）；用于 Sharpe、Sortino、Alpha 等指标的超额收益基准",
    )


class StrategyConfig(BaseModel):
    scan_dirs: list[str] = Field(
        default=["ez/strategy/builtin", "strategies"],
        description="策略自动扫描目录列表（相对于项目根目录）；新增自定义策略放到 strategies/ 即可被自动注册",
    )


class CorsConfig(BaseModel):
    origins: list[str] = Field(
        default=["http://localhost:3000"],
        description="允许跨域请求的前端域名列表；生产部署时替换为实际域名",
    )


class LLMConfig(BaseModel):
    provider: str = Field(
        default="deepseek",
        description="LLM 提供商标识；支持 deepseek | qwen | openai | local",
    )
    api_key: str = Field(
        default="",
        description="API 密钥；优先从环境变量读取（DEEPSEEK_API_KEY / QWEN_API_KEY / OPENAI_API_KEY）",
    )
    model: str = Field(
        default="",
        description="模型名称；留空则使用各提供商的默认模型",
    )
    base_url: str = Field(
        default="",
        description="API 基础 URL；留空则使用提供商官方地址（适用于代理或本地部署场景）",
    )
    timeout: float = Field(default=60.0, description="LLM 请求超时时间（秒）")
    max_tokens: int = Field(default=4096, description="单次 LLM 响应的最大 token 数")
    temperature: float = Field(
        default=0.3,
        description="采样温度（0.0–2.0）；较低值使输出更确定，适合代码生成；较高值增加创造性",
    )


class EzConfig(BaseModel):
    """顶层配置对象，由 load_config() 从 YAML 文件构建。"""
    server: ServerConfig = Field(default=ServerConfig(), description="FastAPI 服务器配置")
    database: DatabaseConfig = Field(default=DatabaseConfig(), description="DuckDB 数据库配置")
    data_sources: DataSourcesConfig = Field(default=DataSourcesConfig(), description="各市场数据源提供商配置")
    backtest: BacktestConfig = Field(default=BacktestConfig(), description="回测引擎默认参数")
    strategy: StrategyConfig = Field(default=StrategyConfig(), description="策略自动发现配置")
    cors: CorsConfig = Field(default=CorsConfig(), description="跨域资源共享配置")
    llm: LLMConfig = Field(default=LLMConfig(), description="LLM 提供商配置")


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
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        _config = EzConfig(**raw)
    else:
        _config = EzConfig()
    return _config


def reset_config() -> None:
    """Reset cached config (for testing)."""
    global _config
    _config = None
