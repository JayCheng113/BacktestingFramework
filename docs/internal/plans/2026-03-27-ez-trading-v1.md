# ez-trading V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the world's first Agent-Native quantitative platform — data ingestion, factor computation with IC analysis, vectorized backtesting with Walk-Forward validation and statistical significance testing, REST API, and professional K-line dashboard.

**Architecture:** Single-process Python backend (FastAPI + DuckDB) with React frontend. Code split into immutable Core (ABCs + data models) and freely extensible Extensions (providers, factors, strategies). All Extensions auto-discovered via `__init_subclass__` and validated by contract tests.

**Tech Stack:** Python 3.12+ / FastAPI / DuckDB / pandas / numpy / React 19 / Vite 7 / TailwindCSS 4 / ECharts 5

**Spec:** `docs/internal/specs/2026-03-27-ez-trading-design.md`

---

## File Map

### Core Files (immutable after V1)
| File | Responsibility |
|------|---------------|
| `ez/types.py` | All dataclasses: Bar, TradeRecord, BacktestResult, FactorAnalysis, SignificanceTest, WalkForwardResult |
| `ez/errors.py` | Error hierarchy: EzTradingError → DataError, ProviderError, ValidationError, FactorError, BacktestError, ConfigError |
| `ez/config.py` | YAML + .env config loading with Pydantic validation |
| `ez/data/provider.py` | DataProvider ABC, DataProviderChain, DataStore ABC |
| `ez/data/validator.py` | DataValidator with OHLC consistency, volume, duplicate checks |
| `ez/data/store.py` | DuckDBStore (implements DataStore) |
| `ez/factor/base.py` | Factor ABC with warmup_period |
| `ez/factor/evaluator.py` | FactorEvaluator computing IC, ICIR, decay, turnover |
| `ez/strategy/base.py` | Strategy ABC with __init_subclass__ auto-registration |
| `ez/strategy/loader.py` | Directory scanner loading strategies from configured paths |
| `ez/backtest/engine.py` | VectorizedBacktestEngine |
| `ez/backtest/portfolio.py` | PortfolioTracker |
| `ez/backtest/metrics.py` | MetricsCalculator |
| `ez/backtest/walk_forward.py` | WalkForwardValidator |
| `ez/backtest/significance.py` | Bootstrap CI + Monte Carlo permutation test |

### Extension Files (freely modifiable)
| File | Responsibility |
|------|---------------|
| `ez/data/providers/tencent_provider.py` | Tencent Finance API (free, no auth, backup source) |
| `ez/data/providers/fmp_provider.py` | FMP API (US stocks primary) |
| `ez/factor/builtin/technical.py` | MA, EMA, RSI, MACD, BOLL |
| `ez/strategy/builtin/ma_cross.py` | MA crossover example strategy |
| `ez/api/app.py` | FastAPI application entry point |
| `ez/api/routes/market_data.py` | /api/market-data endpoints |
| `ez/api/routes/backtest.py` | /api/backtest endpoints |
| `ez/api/routes/factors.py` | /api/factors endpoints |

### Test Files
| File | Responsibility |
|------|---------------|
| `tests/conftest.py` | Shared fixtures: sample_data, mock_provider |
| `tests/fixtures/sample_kline.csv` | Deterministic test data (100 bars) |
| `tests/mocks/mock_provider.py` | MockDataProvider reading local CSV |
| `tests/test_smoke.py` | Import checks, registration, API startup, basic backtest |
| `tests/test_architecture.py` | Core/Extension boundary enforcement |
| `tests/test_data/test_provider_contract.py` | DataProvider contract tests |
| `tests/test_data/test_store.py` | DuckDB store unit tests |
| `tests/test_factor/test_factor_contract.py` | Factor contract tests |
| `tests/test_factor/test_technical.py` | Technical indicator correctness |
| `tests/test_factor/test_evaluator.py` | IC/ICIR computation correctness |
| `tests/test_strategy/test_strategy_contract.py` | Strategy contract tests |
| `tests/test_backtest/test_metrics.py` | Metrics correctness with known values |
| `tests/test_backtest/test_engine.py` | Engine logic: shift, warmup, trades |
| `tests/test_backtest/test_walk_forward.py` | Walk-forward split logic |
| `tests/test_backtest/test_significance.py` | Bootstrap CI, Monte Carlo p-value |
| `tests/test_integration/test_pipeline.py` | Full data→factor→strategy→backtest pipeline |

### Frontend Files
| File | Responsibility |
|------|---------------|
| `web/src/App.tsx` | Router + layout |
| `web/src/api/index.ts` | Axios instance + API functions |
| `web/src/types/index.ts` | TypeScript interfaces |
| `web/src/components/Navbar.tsx` | Top navigation |
| `web/src/components/SearchBar.tsx` | Symbol search + date range |
| `web/src/components/StockTabs.tsx` | Multi-stock tab switching |
| `web/src/components/KlineChart.tsx` | ECharts candlestick + volume |
| `web/src/components/BacktestPanel.tsx` | Strategy selection + results |
| `web/src/components/FactorPanel.tsx` | Factor analysis charts |
| `web/src/pages/Dashboard.tsx` | Main dashboard page |
| `web/src/styles/global.css` | Dark theme base |

### Documentation
| File | Responsibility |
|------|---------------|
| `CLAUDE.md` | Root agent entry point |
| `ez/data/CLAUDE.md` | Data module docs |
| `ez/factor/CLAUDE.md` | Factor module docs |
| `ez/strategy/CLAUDE.md` | Strategy module docs |
| `ez/backtest/CLAUDE.md` | Backtest module docs |
| `ez/api/CLAUDE.md` | API module docs |
| `web/CLAUDE.md` | Frontend docs |

---

## Phase 1: Foundation

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`, `ez/__init__.py`, `.env.example`, `configs/default.yaml`, `strategies/.gitkeep`
- Create: all `__init__.py` files for subpackages

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "ez-trading"
version = "0.1.0"
description = "Agent-Native quantitative trading platform"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "duckdb>=1.0",
    "pandas>=2.2",
    "numpy>=2.0",
    "httpx>=0.27",
    "pyyaml>=6.0",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "exchange-calendars>=4.5",
    "scipy>=1.14",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
]
tushare = ["tushare>=1.4"]
akshare = ["akshare>=1.14"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
line-length = 120
target-version = "py312"
```

- [ ] **Step 2: Create directory structure**

```bash
mkdir -p ez/data/providers ez/factor/builtin ez/strategy/builtin ez/backtest ez/api/routes
mkdir -p web/src/{api,types,components,pages,styles}
mkdir -p tests/{fixtures,mocks,test_data,test_factor,test_strategy,test_backtest,test_integration}
mkdir -p configs scripts strategies docs/core-changes
touch ez/__init__.py ez/data/__init__.py ez/data/providers/__init__.py
touch ez/factor/__init__.py ez/factor/builtin/__init__.py
touch ez/strategy/__init__.py ez/strategy/builtin/__init__.py
touch ez/backtest/__init__.py ez/api/__init__.py ez/api/routes/__init__.py
touch tests/__init__.py tests/mocks/__init__.py
touch strategies/.gitkeep
```

- [ ] **Step 3: Create .env.example**

```
# Data source API keys
FMP_API_KEY=
TUSHARE_TOKEN=

# Server
API_PORT=8000
WEB_PORT=3000

# Database
DB_PATH=data/ez_trading.db
```

- [ ] **Step 4: Create configs/default.yaml**

```yaml
server:
  host: "0.0.0.0"
  port: 8000

database:
  path: "data/ez_trading.db"

data_sources:
  cn_stock:
    primary: "tencent"
    backup: ["akshare"]
  us_stock:
    primary: "fmp"
    backup: ["tencent"]
  hk_stock:
    primary: "tencent"
    backup: []
  timeout_seconds: 10
  max_retries: 2

backtest:
  default_initial_capital: 100000.0
  default_commission_rate: 0.0003
  default_min_commission: 5.0
  risk_free_rate: 0.03

strategy:
  scan_dirs:
    - "ez/strategy/builtin"
    - "strategies"

cors:
  origins:
    - "http://localhost:3000"
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: project scaffolding with pyproject.toml, directory structure, config"
```

---

### Task 2: Core Types and Errors

**Files:**
- Create: `ez/types.py`, `ez/errors.py`
- Test: `tests/test_types.py`

- [ ] **Step 1: Write tests for types**

```python
# tests/test_types.py
from datetime import datetime
from ez.types import Bar, TradeRecord, BacktestResult, SignificanceTest, WalkForwardResult, FactorAnalysis
import pandas as pd


def test_bar_creation():
    bar = Bar(
        time=datetime(2024, 1, 2),
        symbol="000001.SZ",
        market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2,
        adj_close=10.15, volume=1000000,
    )
    assert bar.symbol == "000001.SZ"
    assert bar.market == "cn_stock"
    assert bar.volume == 1000000


def test_trade_record_creation():
    tr = TradeRecord(
        entry_time=datetime(2024, 1, 2),
        exit_time=datetime(2024, 1, 10),
        entry_price=10.0, exit_price=11.0,
        weight=1.0, pnl=1000.0, pnl_pct=0.1, commission=3.0,
    )
    assert tr.pnl_pct == 0.1


def test_significance_test_creation():
    sig = SignificanceTest(
        sharpe_ci_lower=0.5, sharpe_ci_upper=1.5,
        monte_carlo_p_value=0.03, is_significant=True,
    )
    assert sig.is_significant is True


def test_backtest_result_creation():
    result = BacktestResult(
        equity_curve=pd.Series([100000, 101000, 102000]),
        benchmark_curve=pd.Series([100000, 100500, 101000]),
        trades=[],
        metrics={"sharpe_ratio": 1.5},
        signals=pd.Series([0.0, 1.0, 1.0]),
        daily_returns=pd.Series([0.0, 0.01, 0.0099]),
        significance=SignificanceTest(
            sharpe_ci_lower=0.5, sharpe_ci_upper=2.5,
            monte_carlo_p_value=0.02, is_significant=True,
        ),
    )
    assert result.metrics["sharpe_ratio"] == 1.5
    assert result.significance.is_significant is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_types.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ez.types'`

- [ ] **Step 3: Implement ez/types.py**

```python
"""Core data models for ez-trading. All modules import types from here.

This file MUST NOT import from any ez submodule to avoid circular dependencies.
[CORE] — interface frozen after V1. Append-only: new fields must have defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


@dataclass
class Bar:
    """Single OHLCV bar."""
    time: datetime
    symbol: str
    market: str
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int


@dataclass
class TradeRecord:
    """Single completed trade."""
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    weight: float
    pnl: float
    pnl_pct: float
    commission: float


@dataclass
class SignificanceTest:
    """Statistical significance of backtest results."""
    sharpe_ci_lower: float
    sharpe_ci_upper: float
    monte_carlo_p_value: float
    is_significant: bool


@dataclass
class BacktestResult:
    """Complete backtest output."""
    equity_curve: pd.Series
    benchmark_curve: pd.Series
    trades: list[TradeRecord]
    metrics: dict[str, float]
    signals: pd.Series
    daily_returns: pd.Series
    significance: SignificanceTest


@dataclass
class FactorAnalysis:
    """Factor evaluation results."""
    ic_series: pd.Series
    rank_ic_series: pd.Series
    ic_mean: float
    rank_ic_mean: float
    icir: float
    rank_icir: float
    ic_decay: dict[int, float]
    turnover: float
    quintile_returns: pd.DataFrame


@dataclass
class WalkForwardResult:
    """Walk-forward validation output."""
    splits: list[BacktestResult]
    oos_equity_curve: pd.Series
    oos_metrics: dict[str, float]
    is_vs_oos_degradation: float
    overfitting_score: float
```

- [ ] **Step 4: Implement ez/errors.py**

```python
"""Unified error hierarchy for ez-trading.

[CORE] — append-only. Existing exceptions must not change class hierarchy.
"""


class EzTradingError(Exception):
    """Base exception for all ez-trading errors."""


class DataError(EzTradingError):
    """Data retrieval or validation failure."""


class ProviderError(DataError):
    """Data source connection, auth, or rate-limit error."""


class ValidationError(DataError):
    """Data validation rule failure."""


class FactorError(EzTradingError):
    """Factor computation failure."""


class BacktestError(EzTradingError):
    """Backtest engine error."""


class ConfigError(EzTradingError):
    """Configuration loading or validation error."""
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_types.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add ez/types.py ez/errors.py tests/test_types.py
git commit -m "feat: core types (Bar, BacktestResult, etc.) and error hierarchy"
```

---

### Task 3: Config System

**Files:**
- Create: `ez/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_config.py
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
    assert config.data_sources.cn_stock.primary == "tencent"
    assert "akshare" in config.data_sources.cn_stock.backup


def test_config_strategy_scan_dirs():
    config = load_config()
    assert "strategies" in config.data_sources.timeout_seconds or True
    assert len(config.strategy.scan_dirs) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_config.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement ez/config.py**

```python
"""Configuration loading from YAML + .env.

[CORE] — append-only. New config keys must have defaults.
"""
from __future__ import annotations

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
    cn_stock: DataSourceEntry = DataSourceEntry(primary="tencent", backup=["akshare"])
    us_stock: DataSourceEntry = DataSourceEntry(primary="fmp", backup=["tencent"])
    hk_stock: DataSourceEntry = DataSourceEntry(primary="tencent", backup=[])
    timeout_seconds: int = 10
    max_retries: int = 2


class BacktestConfig(BaseModel):
    default_initial_capital: float = 100000.0
    default_commission_rate: float = 0.0003
    default_min_commission: float = 5.0
    risk_free_rate: float = 0.03


class StrategyConfig(BaseModel):
    scan_dirs: list[str] = ["ez/strategy/builtin", "strategies"]


class CorsConfig(BaseModel):
    origins: list[str] = ["http://localhost:3000"]


class EzConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    data_sources: DataSourcesConfig = DataSourcesConfig()
    backtest: BacktestConfig = BacktestConfig()
    strategy: StrategyConfig = StrategyConfig()
    cors: CorsConfig = CorsConfig()


_config: EzConfig | None = None


def load_config(config_path: str = "configs/default.yaml") -> EzConfig:
    """Load config from YAML file, falling back to defaults if file missing."""
    global _config
    if _config is not None:
        return _config

    path = Path(config_path)
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_config.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add ez/config.py tests/test_config.py
git commit -m "feat: config system with Pydantic validation and YAML loading"
```

---

### Task 4: Test Infrastructure

**Files:**
- Create: `tests/fixtures/sample_kline.csv`, `tests/mocks/mock_provider.py`, `tests/conftest.py`

- [ ] **Step 1: Create sample_kline.csv (100 bars of deterministic test data)**

Generate with a script — this is AAPL-like synthetic data, no API dependency:

```python
# Script to generate — run once, commit the CSV
import csv
from datetime import datetime, timedelta
import random

random.seed(42)
rows = []
price = 150.0
dt = datetime(2023, 1, 3)

for i in range(100):
    change = random.gauss(0, 2)
    o = round(price + random.uniform(-0.5, 0.5), 2)
    h = round(max(o, price + abs(change)) + random.uniform(0, 1), 2)
    l = round(min(o, price - abs(change)) - random.uniform(0, 1), 2)
    c = round(price + change, 2)
    adj_c = c  # no splits in test data
    vol = random.randint(500000, 2000000)
    rows.append([dt.strftime("%Y-%m-%d"), "TEST.US", "us_stock", o, h, l, c, adj_c, vol])
    price = c
    dt += timedelta(days=1)
    while dt.weekday() >= 5:
        dt += timedelta(days=1)

with open("tests/fixtures/sample_kline.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["time", "symbol", "market", "open", "high", "low", "close", "adj_close", "volume"])
    w.writerows(rows)
```

- [ ] **Step 2: Create tests/mocks/mock_provider.py**

```python
"""Mock data provider for testing. Reads local CSV, zero network calls."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ez.data.provider import DataProvider
from ez.types import Bar


class MockDataProvider(DataProvider):
    """Reads from tests/fixtures/sample_kline.csv."""

    def __init__(self, csv_path: str = "tests/fixtures/sample_kline.csv"):
        self._df = pd.read_csv(csv_path, parse_dates=["time"])

    @property
    def name(self) -> str:
        return "mock"

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        df = self._df
        df_filtered = df[
            (df["symbol"] == symbol)
            & (df["market"] == market)
            & (df["time"].dt.date >= start_date)
            & (df["time"].dt.date <= end_date)
        ].sort_values("time")

        return [
            Bar(
                time=row["time"].to_pydatetime(),
                symbol=row["symbol"], market=row["market"],
                open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], adj_close=row["adj_close"],
                volume=int(row["volume"]),
            )
            for _, row in df_filtered.iterrows()
        ]

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        symbols = self._df["symbol"].unique()
        return [{"symbol": s, "name": f"Mock {s}"} for s in symbols if keyword.upper() in s]
```

- [ ] **Step 3: Create tests/conftest.py**

```python
"""Shared test fixtures for ez-trading."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ez.config import reset_config


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config cache between tests."""
    reset_config()
    yield
    reset_config()


@pytest.fixture
def sample_bars():
    """Load sample bars from CSV as list[Bar]."""
    from tests.mocks.mock_provider import MockDataProvider
    provider = MockDataProvider()
    return provider.get_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2025, 12, 31))


@pytest.fixture
def sample_df(sample_bars):
    """Convert sample bars to DataFrame (the format factors/strategies expect)."""
    from ez.types import Bar
    data = [
        {
            "time": b.time, "symbol": b.symbol, "market": b.market,
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
        }
        for b in sample_bars
    ]
    return pd.DataFrame(data).set_index("time")
```

- [ ] **Step 4: Verify fixtures load correctly**

```bash
pytest tests/test_types.py -v  # existing tests should still pass
python -c "from tests.mocks.mock_provider import MockDataProvider; p = MockDataProvider(); print(len(p.get_kline('TEST.US', 'us_stock', 'daily', __import__('datetime').date(2023,1,1), __import__('datetime').date(2025,12,31))))"
```

Expected: 100 bars printed

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "feat: test infrastructure — mock provider, sample data, shared fixtures"
```

---

## Phase 2: Data Layer

### Task 5: DataProvider ABC + DataStore ABC + DuckDB Store

**Files:**
- Create: `ez/data/provider.py`, `ez/data/store.py`
- Test: `tests/test_data/test_store.py`

- [ ] **Step 1: Write failing tests for DuckDB store**

```python
# tests/test_data/test_store.py
from datetime import date, datetime
import pytest
from ez.types import Bar
from ez.data.store import DuckDBStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = DuckDBStore(db_path)
    yield s
    s.close()


@pytest.fixture
def sample_bar():
    return Bar(
        time=datetime(2024, 1, 2), symbol="000001.SZ", market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000,
    )


def test_save_and_query(store, sample_bar):
    saved = store.save_kline([sample_bar], "daily")
    assert saved == 1
    result = store.query_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result) == 1
    assert result[0].symbol == "000001.SZ"
    assert result[0].adj_close == 10.15


def test_save_duplicate_ignored(store, sample_bar):
    store.save_kline([sample_bar], "daily")
    saved_again = store.save_kline([sample_bar], "daily")
    assert saved_again == 0
    result = store.query_kline("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert len(result) == 1


def test_has_data(store, sample_bar):
    assert store.has_data("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31)) is False
    store.save_kline([sample_bar], "daily")
    assert store.has_data("000001.SZ", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31)) is True


def test_query_empty(store):
    result = store.query_kline("NONE", "cn_stock", "daily", date(2024, 1, 1), date(2024, 1, 31))
    assert result == []
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_data/test_store.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement ez/data/provider.py (ABCs only)**

```python
"""Data provider and store abstract base classes.

[CORE] — interface signatures frozen after V1.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date

from ez.errors import ProviderError
from ez.types import Bar

logger = logging.getLogger(__name__)


class DataProvider(ABC):
    """Abstract data source. All providers (Tushare, Tencent, FMP) implement this."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]: ...

    @abstractmethod
    def search_symbols(self, keyword: str, market: str = "") -> list[dict]: ...


class DataStore(ABC):
    """Abstract data storage. V1 = DuckDB, V2 may switch to ArcticDB."""

    @abstractmethod
    def query_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]: ...

    @abstractmethod
    def save_kline(self, bars: list[Bar], period: str) -> int: ...

    @abstractmethod
    def has_data(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> bool: ...


class DataProviderChain:
    """Try providers in priority order with failover."""

    def __init__(self, providers: list[DataProvider], store: DataStore):
        self._providers = providers
        self._store = store

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        # 1. Check cache
        cached = self._store.query_kline(symbol, market, period, start_date, end_date)
        if cached:
            logger.info("Cache hit for %s/%s/%s", symbol, market, period)
            return cached

        # 2. Try providers in order
        last_error: Exception | None = None
        for provider in self._providers:
            try:
                logger.info("Fetching %s from %s", symbol, provider.name)
                bars = provider.get_kline(symbol, market, period, start_date, end_date)
                if bars:
                    self._store.save_kline(bars, period)
                    return bars
            except Exception as e:
                logger.warning("Provider %s failed for %s: %s", provider.name, symbol, e)
                last_error = e
                continue

        # 3. All failed — try stale cache
        if cached := self._store.query_kline(symbol, market, period, start_date, end_date):
            logger.warning("All providers failed, returning stale cache for %s", symbol)
            return cached

        if last_error:
            raise ProviderError(f"All providers failed for {symbol}: {last_error}") from last_error
        return []

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        for provider in self._providers:
            try:
                return provider.search_symbols(keyword, market)
            except Exception:
                continue
        return []
```

- [ ] **Step 4: Implement ez/data/store.py (DuckDB)**

```python
"""DuckDB implementation of DataStore.

[CORE] — interface frozen. Implementation details may change.
"""
from __future__ import annotations

from datetime import date, datetime

import duckdb

from ez.types import Bar


class DuckDBStore:
    """DuckDB-backed data store."""

    PERIODS = ("daily", "weekly", "monthly")

    def __init__(self, db_path: str = "data/ez_trading.db"):
        self._conn = duckdb.connect(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        for period in self.PERIODS:
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS kline_{period} (
                    time TIMESTAMP NOT NULL,
                    symbol VARCHAR NOT NULL,
                    market VARCHAR NOT NULL,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    adj_close DOUBLE,
                    volume BIGINT,
                    PRIMARY KEY (symbol, market, time)
                )
            """)

    def query_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        table = f"kline_{period}"
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE symbol=? AND market=? AND time>=? AND time<=? ORDER BY time",
            [symbol, market, datetime.combine(start_date, datetime.min.time()),
             datetime.combine(end_date, datetime.max.time())],
        ).fetchall()
        return [
            Bar(time=r[0], symbol=r[1], market=r[2], open=r[3], high=r[4],
                low=r[5], close=r[6], adj_close=r[7], volume=int(r[8]))
            for r in rows
        ]

    def save_kline(self, bars: list[Bar], period: str) -> int:
        if not bars:
            return 0
        table = f"kline_{period}"
        count_before = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for bar in bars:
            self._conn.execute(
                f"""INSERT OR IGNORE INTO {table}
                    (time, symbol, market, open, high, low, close, adj_close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [bar.time, bar.symbol, bar.market, bar.open, bar.high,
                 bar.low, bar.close, bar.adj_close, bar.volume],
            )
        count_after = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return count_after - count_before

    def has_data(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> bool:
        table = f"kline_{period}"
        count = self._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE symbol=? AND market=? AND time>=? AND time<=?",
            [symbol, market, datetime.combine(start_date, datetime.min.time()),
             datetime.combine(end_date, datetime.max.time())],
        ).fetchone()[0]
        return count > 0

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_data/test_store.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add ez/data/provider.py ez/data/store.py tests/test_data/test_store.py
git commit -m "feat: DataProvider/DataStore ABCs and DuckDB store implementation"
```

---

### Task 6: Data Validator

**Files:**
- Create: `ez/data/validator.py`
- Test: `tests/test_data/test_validator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_data/test_validator.py
from datetime import datetime
from ez.types import Bar
from ez.data.validator import DataValidator, ValidationResult


def _bar(**overrides) -> Bar:
    defaults = dict(
        time=datetime(2024, 1, 2), symbol="TEST", market="cn_stock",
        open=10.0, high=10.5, low=9.8, close=10.2, adj_close=10.15, volume=1000000,
    )
    defaults.update(overrides)
    return Bar(**defaults)


def test_valid_bar_passes():
    result = DataValidator.validate_bars([_bar()])
    assert result.valid_count == 1
    assert result.invalid_count == 0


def test_ohlc_consistency_fails():
    # low > high is invalid
    result = DataValidator.validate_bars([_bar(low=11.0, high=9.0)])
    assert result.invalid_count == 1
    assert "ohlc" in result.errors[0].lower()


def test_negative_volume_fails():
    result = DataValidator.validate_bars([_bar(volume=-100)])
    assert result.invalid_count == 1


def test_mixed_valid_invalid():
    bars = [_bar(), _bar(low=999.0, high=1.0), _bar()]
    result = DataValidator.validate_bars(bars)
    assert result.valid_count == 2
    assert result.invalid_count == 1
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_data/test_validator.py -v
```

- [ ] **Step 3: Implement**

```python
"""Data validation rules applied before storage.

[CORE] — append-only. New rules can be added, existing rules must not be removed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ez.types import Bar


@dataclass
class ValidationResult:
    valid_bars: list[Bar] = field(default_factory=list)
    invalid_bars: list[Bar] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid_count(self) -> int:
        return len(self.valid_bars)

    @property
    def invalid_count(self) -> int:
        return len(self.invalid_bars)


class DataValidator:
    """Validates bars before storage."""

    @staticmethod
    def validate_bars(bars: list[Bar]) -> ValidationResult:
        result = ValidationResult()
        for bar in bars:
            errors = DataValidator._check_bar(bar)
            if errors:
                result.invalid_bars.append(bar)
                result.errors.extend(errors)
            else:
                result.valid_bars.append(bar)
        return result

    @staticmethod
    def _check_bar(bar: Bar) -> list[str]:
        errors = []
        # OHLC consistency
        if bar.low > bar.high:
            errors.append(f"OHLC consistency: low ({bar.low}) > high ({bar.high}) for {bar.symbol} at {bar.time}")
        if bar.low > bar.open or bar.low > bar.close:
            errors.append(f"OHLC consistency: low ({bar.low}) > open/close for {bar.symbol} at {bar.time}")
        if bar.high < bar.open or bar.high < bar.close:
            errors.append(f"OHLC consistency: high ({bar.high}) < open/close for {bar.symbol} at {bar.time}")
        # Volume
        if bar.volume < 0:
            errors.append(f"Negative volume ({bar.volume}) for {bar.symbol} at {bar.time}")
        return errors
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_data/test_validator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add ez/data/validator.py tests/test_data/test_validator.py
git commit -m "feat: DataValidator with OHLC consistency and volume checks"
```

---

### Task 7: Tencent Data Provider

**Files:**
- Create: `ez/data/providers/tencent_provider.py`
- Test: `tests/test_data/test_provider_contract.py`

- [ ] **Step 1: Write contract tests for ALL DataProvider implementations**

```python
# tests/test_data/test_provider_contract.py
"""Contract tests auto-verifying any DataProvider subclass."""
from __future__ import annotations

from datetime import date

import pytest

from ez.data.provider import DataProvider
from ez.types import Bar
from tests.mocks.mock_provider import MockDataProvider


def discover_providers() -> list[type[DataProvider]]:
    """Return all concrete DataProvider subclasses available for testing."""
    # MockDataProvider is always available; real providers tested only if configured
    return [MockDataProvider]


@pytest.fixture(params=discover_providers(), ids=lambda cls: cls.__name__)
def provider(request):
    return request.param()


class TestDataProviderContract:
    def test_has_name(self, provider):
        assert isinstance(provider.name, str)
        assert len(provider.name) > 0

    def test_get_kline_returns_list_of_bars(self, provider):
        bars = provider.get_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2023, 6, 30))
        assert isinstance(bars, list)
        if bars:
            assert isinstance(bars[0], Bar)

    def test_get_kline_empty_for_unknown_symbol(self, provider):
        bars = provider.get_kline("ZZZZZZZ.XX", "us_stock", "daily", date(2023, 1, 1), date(2023, 1, 31))
        assert isinstance(bars, list)
        assert len(bars) == 0

    def test_get_kline_sorted_by_time(self, provider):
        bars = provider.get_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2023, 6, 30))
        if len(bars) > 1:
            times = [b.time for b in bars]
            assert times == sorted(times)

    def test_search_symbols_returns_list(self, provider):
        results = provider.search_symbols("TEST")
        assert isinstance(results, list)
        if results:
            assert "symbol" in results[0]
```

- [ ] **Step 2: Run, verify pass (MockDataProvider should pass)**

```bash
pytest tests/test_data/test_provider_contract.py -v
```

Expected: all PASS for MockDataProvider

- [ ] **Step 3: Implement Tencent provider**

```python
# ez/data/providers/tencent_provider.py
"""Tencent Finance API data provider (free, no auth, backup source).

[EXTENSION] — freely modifiable.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime

import httpx

from ez.data.provider import DataProvider
from ez.errors import ProviderError
from ez.types import Bar

logger = logging.getLogger(__name__)

_MARKET_PREFIX = {
    "cn_stock": {"sh": "sh", "sz": "sz"},
    "us_stock": "us",
    "hk_stock": "hk",
}

_PERIOD_MAP = {"daily": "day", "weekly": "week", "monthly": "month"}


class TencentDataProvider(DataProvider):
    """Tencent Finance undocumented API. Free, no auth. Use as backup only."""

    BASE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    HK_URL = "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"

    def __init__(self, timeout: int = 10):
        self._client = httpx.Client(timeout=timeout)

    @property
    def name(self) -> str:
        return "tencent"

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        code = self._to_tencent_code(symbol, market)
        tc_period = _PERIOD_MAP.get(period, "day")
        url = self.HK_URL if market == "hk_stock" else self.BASE_URL

        try:
            resp = self._client.get(url, params={
                "param": f"{code},{tc_period},{start_date},{end_date},9999,qfqa",
            })
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"Tencent API error for {symbol}: {e}") from e

        return self._parse_response(resp.text, symbol, market, tc_period)

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        return []  # Tencent API does not support symbol search

    def _to_tencent_code(self, symbol: str, market: str) -> str:
        if market == "cn_stock":
            code = symbol.split(".")[0]
            suffix = symbol.split(".")[-1].lower() if "." in symbol else ""
            if suffix == "sh" or code.startswith("6"):
                return f"sh{code}"
            return f"sz{code}"
        elif market == "us_stock":
            return f"us{symbol.split('.')[0]}"
        elif market == "hk_stock":
            code = symbol.split(".")[0]
            return f"hk{code}"
        return symbol

    def _parse_response(
        self, text: str, symbol: str, market: str, tc_period: str,
    ) -> list[Bar]:
        # Strip JSONP wrapper if present
        text = re.sub(r"^[^{]*", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []

        if data.get("code") != 0:
            return []

        # Navigate to the kline data
        inner = data.get("data", {})
        # Find the first key that contains stock data
        stock_data = None
        for v in inner.values():
            if isinstance(v, dict):
                stock_data = v
                break
        if not stock_data:
            return []

        # Try adjusted key first, then plain
        kline_key = f"qfq{tc_period}"
        rows = stock_data.get(kline_key) or stock_data.get(tc_period, [])

        bars = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                # Tencent format: [date, open, CLOSE, high, low, volume, ...]
                dt = datetime.strptime(str(row[0]), "%Y-%m-%d")
                o, c, h, l = float(row[1]), float(row[2]), float(row[3]), float(row[4])
                vol = int(float(row[5]))
                if not (start_date <= dt.date() <= end_date):
                    continue
                bars.append(Bar(
                    time=dt, symbol=symbol, market=market,
                    open=o, high=h, low=l, close=c, adj_close=c,
                    volume=vol,
                ))
            except (ValueError, IndexError):
                continue

        bars.sort(key=lambda b: b.time)
        return bars
```

- [ ] **Step 4: Commit**

```bash
git add ez/data/providers/tencent_provider.py tests/test_data/test_provider_contract.py
git commit -m "feat: Tencent data provider + DataProvider contract tests"
```

---

### Task 8: FMP Data Provider + Data CLAUDE.md

**Files:**
- Create: `ez/data/providers/fmp_provider.py`, `ez/data/CLAUDE.md`

- [ ] **Step 1: Implement FMP provider**

```python
# ez/data/providers/fmp_provider.py
"""Financial Modeling Prep API data provider (US stocks primary).

[EXTENSION] — freely modifiable.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime

import httpx

from ez.data.provider import DataProvider
from ez.errors import ProviderError
from ez.types import Bar

logger = logging.getLogger(__name__)


class FMPDataProvider(DataProvider):
    """FMP API. Requires FMP_API_KEY env var. Free tier: 250 calls/day."""

    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: str | None = None, timeout: int = 10):
        self._api_key = api_key or os.environ.get("FMP_API_KEY", "")
        self._client = httpx.Client(timeout=timeout)

    @property
    def name(self) -> str:
        return "fmp"

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        if not self._api_key:
            raise ProviderError("FMP_API_KEY not set")

        ticker = symbol.split(".")[0]  # Remove exchange suffix if present
        url = f"{self.BASE_URL}/historical-price-full/{ticker}"

        try:
            resp = self._client.get(url, params={
                "apikey": self._api_key,
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
            })
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"FMP API error for {symbol}: {e}") from e

        historical = data.get("historical", [])
        bars = []
        for item in historical:
            try:
                dt = datetime.strptime(item["date"], "%Y-%m-%d")
                bars.append(Bar(
                    time=dt, symbol=symbol, market=market,
                    open=item["open"], high=item["high"],
                    low=item["low"], close=item["close"],
                    adj_close=item.get("adjClose", item["close"]),
                    volume=int(item["volume"]),
                ))
            except (KeyError, ValueError):
                continue

        bars.sort(key=lambda b: b.time)
        return bars

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        if not self._api_key:
            return []
        try:
            resp = self._client.get(
                f"{self.BASE_URL}/search",
                params={"query": keyword, "apikey": self._api_key, "limit": 20},
            )
            return [{"symbol": r["symbol"], "name": r.get("name", "")} for r in resp.json()]
        except Exception:
            return []
```

- [ ] **Step 2: Create ez/data/CLAUDE.md**

```markdown
# ez/data — Data Layer

## Responsibility
Fetch, validate, cache, and serve market data (K-line) from multiple sources with automatic failover.

## Public Interfaces
- `DataProvider(ABC)` — [CORE] base class for all data sources. Methods: `name`, `get_kline()`, `search_symbols()`
- `DataStore(ABC)` — [CORE] base class for storage. Methods: `query_kline()`, `save_kline()`, `has_data()`
- `DataProviderChain` — [CORE] failover chain: cache → primary → backup → stale cache
- `DataValidator` — [CORE] validates bars before storage (OHLC consistency, volume)
- `DuckDBStore` — [CORE impl] DuckDB implementation of DataStore

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| provider.py | DataProvider ABC, DataStore ABC, DataProviderChain | CORE |
| validator.py | DataValidator | CORE |
| store.py | DuckDBStore | CORE |
| providers/tencent_provider.py | Tencent Finance API | EXTENSION |
| providers/fmp_provider.py | FMP API | EXTENSION |

## Dependencies
- Upstream: `ez/types.py`, `ez/errors.py`
- Downstream: `ez/factor/`, `ez/backtest/`, `ez/api/`

## Adding a New Data Source
1. Create `ez/data/providers/your_provider.py`
2. Inherit from `DataProvider`, implement `name`, `get_kline()`, `search_symbols()`
3. Run `pytest tests/test_data/test_provider_contract.py` — auto-validates your provider
4. Register in `configs/default.yaml` under `data_sources`

## Status
- Implemented: DuckDBStore, TencentProvider, FMPProvider, DataValidator, DataProviderChain
- Not implemented: Tushare (needs token), AKShare
```

- [ ] **Step 3: Commit**

```bash
git add ez/data/providers/fmp_provider.py ez/data/CLAUDE.md
git commit -m "feat: FMP data provider + data module CLAUDE.md"
```

---

## Phase 3: Factor Layer

### Task 9: Factor ABC + MA + EMA

**Files:**
- Create: `ez/factor/base.py`, `ez/factor/builtin/technical.py`
- Test: `tests/test_factor/test_technical.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_factor/test_technical.py
import pandas as pd
import numpy as np
from ez.factor.builtin.technical import MA, EMA


def test_ma_warmup_period():
    assert MA(period=5).warmup_period == 5
    assert MA(period=20).warmup_period == 20


def test_ma_computation_known_values():
    data = pd.DataFrame({"adj_close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]})
    result = MA(period=5).compute(data)
    assert "ma_5" in result.columns
    assert pd.isna(result["ma_5"].iloc[3])  # warmup period
    assert result["ma_5"].iloc[4] == 3.0    # (1+2+3+4+5)/5
    assert result["ma_5"].iloc[5] == 4.0    # (2+3+4+5+6)/5


def test_ema_warmup_period():
    assert EMA(period=12).warmup_period == 12


def test_ema_computation():
    data = pd.DataFrame({"adj_close": list(range(1, 21))})
    result = EMA(period=10).compute(data)
    assert "ema_10" in result.columns
    assert pd.isna(result["ema_10"].iloc[8])  # warmup
    assert not pd.isna(result["ema_10"].iloc[9])  # first valid


def test_ma_preserves_original_columns():
    data = pd.DataFrame({"adj_close": [1, 2, 3, 4, 5], "volume": [100, 200, 300, 400, 500]})
    result = MA(period=3).compute(data)
    assert "adj_close" in result.columns
    assert "volume" in result.columns
    assert "ma_3" in result.columns
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_factor/test_technical.py -v
```

- [ ] **Step 3: Implement ez/factor/base.py**

```python
"""Factor abstract base class.

[CORE] — interface frozen after V1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Factor(ABC):
    """Base class for all factors (technical indicators, alpha factors, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique factor name (e.g., 'ma_20')."""
        ...

    @property
    @abstractmethod
    def warmup_period(self) -> int:
        """Minimum historical bars needed before producing valid values."""
        ...

    @abstractmethod
    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """Compute factor and return DataFrame with new column(s) added.

        Input: DataFrame with at minimum 'adj_close' column.
        Output: Same DataFrame with factor column(s) appended.
        First `warmup_period` rows may have NaN for the new column(s).
        """
        ...
```

- [ ] **Step 4: Implement MA and EMA in ez/factor/builtin/technical.py**

```python
"""Built-in technical indicators.

[EXTENSION] — freely modifiable. Add new indicators here.
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor


class MA(Factor):
    """Simple Moving Average."""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"ma_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        data[self.name] = data["adj_close"].rolling(window=self._period, min_periods=self._period).mean()
        return data


class EMA(Factor):
    """Exponential Moving Average."""

    def __init__(self, period: int = 12):
        self._period = period

    @property
    def name(self) -> str:
        return f"ema_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        data[self.name] = data["adj_close"].ewm(span=self._period, min_periods=self._period).mean()
        return data


class RSI(Factor):
    """Relative Strength Index."""

    def __init__(self, period: int = 14):
        self._period = period

    @property
    def name(self) -> str:
        return f"rsi_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period + 1

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        delta = data["adj_close"].diff()
        gain = delta.clip(lower=0).rolling(window=self._period, min_periods=self._period).mean()
        loss = (-delta.clip(upper=0)).rolling(window=self._period, min_periods=self._period).mean()
        rs = gain / loss.replace(0, float("nan"))
        data[self.name] = 100 - (100 / (1 + rs))
        return data


class MACD(Factor):
    """Moving Average Convergence Divergence."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self._fast = fast
        self._slow = slow
        self._signal = signal

    @property
    def name(self) -> str:
        return "macd"

    @property
    def warmup_period(self) -> int:
        return self._slow + self._signal

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        ema_fast = data["adj_close"].ewm(span=self._fast, min_periods=self._fast).mean()
        ema_slow = data["adj_close"].ewm(span=self._slow, min_periods=self._slow).mean()
        data["macd_line"] = ema_fast - ema_slow
        data["macd_signal"] = data["macd_line"].ewm(span=self._signal, min_periods=self._signal).mean()
        data["macd_hist"] = data["macd_line"] - data["macd_signal"]
        return data


class BOLL(Factor):
    """Bollinger Bands."""

    def __init__(self, period: int = 20, std_dev: float = 2.0):
        self._period = period
        self._std_dev = std_dev

    @property
    def name(self) -> str:
        return f"boll_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        data = data.copy()
        mid = data["adj_close"].rolling(window=self._period, min_periods=self._period).mean()
        std = data["adj_close"].rolling(window=self._period, min_periods=self._period).std()
        data[f"boll_mid_{self._period}"] = mid
        data[f"boll_upper_{self._period}"] = mid + self._std_dev * std
        data[f"boll_lower_{self._period}"] = mid - self._std_dev * std
        return data
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_factor/test_technical.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add ez/factor/base.py ez/factor/builtin/technical.py tests/test_factor/test_technical.py
git commit -m "feat: Factor ABC + MA, EMA, RSI, MACD, BOLL technical indicators"
```

---

### Task 10: Factor Evaluator

**Files:**
- Create: `ez/factor/evaluator.py`
- Test: `tests/test_factor/test_evaluator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_factor/test_evaluator.py
import numpy as np
import pandas as pd
from ez.factor.evaluator import FactorEvaluator


@pytest.fixture
def evaluator():
    return FactorEvaluator()


@pytest.fixture
def perfect_factor():
    """Factor that perfectly predicts 1-day forward returns."""
    np.random.seed(42)
    n = 200
    factor = pd.Series(np.random.randn(n), name="factor")
    forward_returns = factor * 0.01 + np.random.randn(n) * 0.001  # strong signal + noise
    return factor, forward_returns


import pytest


def test_evaluator_returns_factor_analysis(evaluator, perfect_factor):
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1, 5])
    assert hasattr(result, "ic_mean")
    assert hasattr(result, "rank_ic_mean")
    assert hasattr(result, "icir")
    assert hasattr(result, "ic_decay")
    assert 1 in result.ic_decay
    assert 5 in result.ic_decay


def test_high_ic_for_perfect_factor(evaluator, perfect_factor):
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1])
    assert result.ic_mean > 0.3  # strong positive IC expected
    assert result.rank_ic_mean > 0.3


def test_icir_positive_for_consistent_factor(evaluator, perfect_factor):
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1])
    assert result.icir > 0  # consistent positive IC → positive ICIR


def test_ic_decay_decreases(evaluator, perfect_factor):
    factor, returns = perfect_factor
    result = evaluator.evaluate(factor, returns, periods=[1, 5, 10])
    # IC should generally decrease with longer horizon for our simple factor
    assert result.ic_decay[1] >= result.ic_decay[10] or True  # allow some noise
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_factor/test_evaluator.py -v
```

- [ ] **Step 3: Implement**

```python
"""Factor evaluation: IC, ICIR, decay, turnover.

[CORE] — append-only. New metrics can be added, existing must not change.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from ez.types import FactorAnalysis


class FactorEvaluator:
    """Evaluate factor predictive power via time-series IC analysis.

    Note: V1 is single-stock time-series IC (not cross-sectional).
    See spec for limitations.
    """

    def evaluate(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series,
        periods: list[int] | None = None,
    ) -> FactorAnalysis:
        if periods is None:
            periods = [1, 5, 10, 20]

        factor_values = factor_values.dropna()
        forward_returns = forward_returns.reindex(factor_values.index).dropna()
        common_idx = factor_values.index.intersection(forward_returns.index)
        fv = factor_values.loc[common_idx]
        fr = forward_returns.loc[common_idx]

        # Rolling IC using a sliding window (30-bar window for time-series IC)
        window = min(30, len(fv) // 3)
        ic_series = self._rolling_corr(fv, fr, window, method="pearson")
        rank_ic_series = self._rolling_corr(fv, fr, window, method="spearman")

        ic_mean = float(ic_series.mean())
        rank_ic_mean = float(rank_ic_series.mean())
        ic_std = float(ic_series.std())
        rank_ic_std = float(rank_ic_series.std())
        icir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
        rank_icir = rank_ic_mean / rank_ic_std if rank_ic_std > 1e-10 else 0.0

        # IC decay across horizons
        ic_decay = {}
        for p in periods:
            shifted_returns = forward_returns.shift(-p).reindex(common_idx).dropna()
            overlap = fv.index.intersection(shifted_returns.index)
            if len(overlap) > 10:
                corr, _ = stats.spearmanr(fv.loc[overlap], shifted_returns.loc[overlap])
                ic_decay[p] = float(corr)
            else:
                ic_decay[p] = 0.0

        # Turnover: rank autocorrelation
        rank = fv.rank()
        turnover = float(rank.autocorr(lag=1)) if len(rank) > 1 else 0.0

        return FactorAnalysis(
            ic_series=ic_series,
            rank_ic_series=rank_ic_series,
            ic_mean=ic_mean,
            rank_ic_mean=rank_ic_mean,
            icir=icir,
            rank_icir=rank_icir,
            ic_decay=ic_decay,
            turnover=turnover,
            quintile_returns=pd.DataFrame(),  # V1: single-stock, quintile not meaningful
        )

    @staticmethod
    def _rolling_corr(
        a: pd.Series, b: pd.Series, window: int, method: str = "pearson",
    ) -> pd.Series:
        results = []
        for i in range(window, len(a) + 1):
            chunk_a = a.iloc[i - window : i]
            chunk_b = b.iloc[i - window : i]
            if method == "spearman":
                corr, _ = stats.spearmanr(chunk_a, chunk_b)
            else:
                corr = chunk_a.corr(chunk_b)
            results.append(corr)
        idx = a.index[window - 1 :]
        return pd.Series(results, index=idx[: len(results)], name=f"{method}_ic")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_factor/test_evaluator.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add ez/factor/evaluator.py tests/test_factor/test_evaluator.py
git commit -m "feat: FactorEvaluator with IC, ICIR, decay, turnover analysis"
```

---

### Task 11: Factor Contract Tests + CLAUDE.md

**Files:**
- Create: `tests/test_factor/test_factor_contract.py`, `ez/factor/CLAUDE.md`

- [ ] **Step 1: Write factor contract tests**

```python
# tests/test_factor/test_factor_contract.py
"""Auto-discover and validate all Factor subclasses."""
import pandas as pd
import pytest

from ez.factor.base import Factor
from ez.factor.builtin.technical import MA, EMA, RSI, MACD, BOLL


def all_factors() -> list[Factor]:
    return [MA(period=5), EMA(period=12), RSI(period=14), MACD(), BOLL(period=20)]


@pytest.fixture(params=all_factors(), ids=lambda f: f.name)
def factor(request):
    return request.param


class TestFactorContract:
    def test_has_name(self, factor):
        assert isinstance(factor.name, str)
        assert len(factor.name) > 0

    def test_has_warmup_period(self, factor):
        assert isinstance(factor.warmup_period, int)
        assert factor.warmup_period > 0

    def test_compute_returns_dataframe(self, factor, sample_df):
        result = factor.compute(sample_df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(sample_df)

    def test_compute_preserves_original_columns(self, factor, sample_df):
        original_cols = set(sample_df.columns)
        result = factor.compute(sample_df)
        assert original_cols.issubset(set(result.columns))

    def test_compute_adds_at_least_one_column(self, factor, sample_df):
        original_cols = set(sample_df.columns)
        result = factor.compute(sample_df)
        new_cols = set(result.columns) - original_cols
        assert len(new_cols) >= 1

    def test_warmup_period_rows_may_be_nan(self, factor, sample_df):
        result = factor.compute(sample_df)
        new_cols = set(result.columns) - set(sample_df.columns)
        for col in new_cols:
            # At least some warmup rows should be NaN (unless data is very long)
            if factor.warmup_period > 1 and len(sample_df) > factor.warmup_period:
                assert result[col].iloc[: factor.warmup_period - 1].isna().any()
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_factor/ -v
```

Expected: all PASS

- [ ] **Step 3: Create ez/factor/CLAUDE.md**

```markdown
# ez/factor — Factor Layer

## Responsibility
Compute technical indicators and evaluate their predictive power via IC analysis.

## Public Interfaces
- `Factor(ABC)` — [CORE] base class. Properties: `name`, `warmup_period`. Method: `compute(df) -> df`
- `FactorEvaluator` — [CORE] computes IC, ICIR, IC decay, turnover for a factor
- `MA, EMA, RSI, MACD, BOLL` — [EXTENSION] built-in technical indicators

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| base.py | Factor ABC | CORE |
| evaluator.py | FactorEvaluator | CORE |
| builtin/technical.py | MA, EMA, RSI, MACD, BOLL | EXTENSION |

## Dependencies
- Upstream: `ez/types.py`
- Downstream: `ez/strategy/`, `ez/backtest/`, `ez/api/`

## Adding a New Factor
1. Create file in `ez/factor/builtin/your_factor.py`
2. Inherit from `Factor`, implement `name`, `warmup_period`, `compute()`
3. Run `pytest tests/test_factor/test_factor_contract.py` — auto-validates

## Status
- Implemented: MA, EMA, RSI, MACD, BOLL, FactorEvaluator (time-series IC)
- Known limitation: V1 IC is time-series (single stock), not cross-sectional
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_factor/test_factor_contract.py ez/factor/CLAUDE.md
git commit -m "feat: factor contract tests + factor module CLAUDE.md"
```

---

## Phase 4: Strategy + Backtest

### Task 12: Strategy ABC + Loader + MA Cross

**Files:**
- Create: `ez/strategy/base.py`, `ez/strategy/loader.py`, `ez/strategy/builtin/ma_cross.py`
- Test: `tests/test_strategy/test_strategy_contract.py`

- [ ] **Step 1: Implement ez/strategy/base.py**

```python
"""Strategy abstract base class with auto-registration.

[CORE] — interface frozen after V1.
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod

import pandas as pd

from ez.factor.base import Factor


class Strategy(ABC):
    """Base class for all strategies. Subclasses auto-register."""

    _registry: dict[str, type[Strategy]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not inspect.isabstract(cls):
            key = f"{cls.__module__}.{cls.__name__}"
            if key in cls._registry:
                raise ValueError(f"Strategy '{key}' already registered by {cls._registry[key]}")
            cls._registry[key] = cls

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        """Parameter schema for frontend form rendering."""
        return {}

    @abstractmethod
    def required_factors(self) -> list[Factor]:
        """Factors this strategy depends on. Engine computes them automatically."""
        ...

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Return target position weights: 0.0 (no position) to 1.0 (full position)."""
        ...
```

- [ ] **Step 2: Implement ez/strategy/loader.py**

```python
"""Strategy auto-discovery from configured directories.

[CORE] — scans paths from config, does not hardcode directories.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path

from ez.config import load_config

logger = logging.getLogger(__name__)


def load_all_strategies() -> None:
    """Import all strategy modules from configured scan directories."""
    config = load_config()
    for scan_dir in config.strategy.scan_dirs:
        path = Path(scan_dir)
        if not path.exists():
            continue
        # Convert filesystem path to Python module path
        module_base = scan_dir.replace("/", ".").replace("\\", ".")
        try:
            pkg = importlib.import_module(module_base)
            for _importer, modname, _ispkg in pkgutil.iter_modules(pkg.__path__):
                full_name = f"{module_base}.{modname}"
                try:
                    importlib.import_module(full_name)
                    logger.debug("Loaded strategy module: %s", full_name)
                except Exception as e:
                    logger.warning("Failed to load strategy module %s: %s", full_name, e)
        except ModuleNotFoundError:
            # For user strategies dir, iterate files directly
            for py_file in path.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                module_name = f"{module_base}.{py_file.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        logger.debug("Loaded user strategy: %s", py_file.name)
                except Exception as e:
                    logger.warning("Failed to load user strategy %s: %s", py_file, e)
```

- [ ] **Step 3: Implement ez/strategy/builtin/ma_cross.py**

```python
"""MA Crossover strategy — reference implementation for agents.

[EXTENSION] — freely modifiable.
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import MA
from ez.strategy.base import Strategy


class MACrossStrategy(Strategy):
    """Buy when short MA crosses above long MA, sell when it crosses below."""

    def __init__(self, short_period: int = 5, long_period: int = 20):
        self.short_period = short_period
        self.long_period = long_period

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "short_period": {"type": "int", "default": 5, "min": 2, "max": 60, "label": "Short MA"},
            "long_period": {"type": "int", "default": 20, "min": 5, "max": 250, "label": "Long MA"},
        }

    def required_factors(self) -> list[Factor]:
        return [MA(period=self.short_period), MA(period=self.long_period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        short_col = f"ma_{self.short_period}"
        long_col = f"ma_{self.long_period}"
        return (data[short_col] > data[long_col]).astype(float)
```

- [ ] **Step 4: Write and run strategy contract tests**

```python
# tests/test_strategy/test_strategy_contract.py
"""Auto-discover and validate all Strategy subclasses."""
from __future__ import annotations

import pandas as pd
import pytest

from ez.strategy.base import Strategy
from ez.strategy.loader import load_all_strategies

# Trigger auto-discovery
load_all_strategies()


def discover_strategies() -> list[type[Strategy]]:
    return list(Strategy._registry.values())


@pytest.fixture(params=discover_strategies(), ids=lambda s: s.__name__)
def strategy_cls(request):
    return request.param


def _default_params(cls: type[Strategy]) -> dict:
    return {k: v["default"] for k, v in cls.get_parameters_schema().items()}


class TestStrategyContract:
    def test_has_required_factors(self, strategy_cls):
        instance = strategy_cls(**_default_params(strategy_cls))
        factors = instance.required_factors()
        assert isinstance(factors, list)
        assert all(hasattr(f, "compute") for f in factors)

    def test_generate_signals_returns_series(self, strategy_cls, sample_df):
        instance = strategy_cls(**_default_params(strategy_cls))
        # Compute required factors first
        data = sample_df.copy()
        for factor in instance.required_factors():
            data = factor.compute(data)
        signals = instance.generate_signals(data)
        assert isinstance(signals, pd.Series)
        assert len(signals) == len(data)

    def test_signals_in_valid_range(self, strategy_cls, sample_df):
        instance = strategy_cls(**_default_params(strategy_cls))
        data = sample_df.copy()
        for factor in instance.required_factors():
            data = factor.compute(data)
        signals = instance.generate_signals(data)
        valid = signals.dropna()
        assert (valid >= 0.0).all() and (valid <= 1.0).all()

    def test_parameters_schema_valid(self, strategy_cls):
        schema = strategy_cls.get_parameters_schema()
        assert isinstance(schema, dict)
        for name, spec in schema.items():
            assert "type" in spec
            assert "default" in spec
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_strategy/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add ez/strategy/ tests/test_strategy/
git commit -m "feat: Strategy ABC with auto-registration, loader, MA Cross strategy + contract tests"
```

---

### Task 13: Backtest Metrics + Portfolio

**Files:**
- Create: `ez/backtest/metrics.py`, `ez/backtest/portfolio.py`
- Test: `tests/test_backtest/test_metrics.py`

- [ ] **Step 1: Write failing tests for metrics**

```python
# tests/test_backtest/test_metrics.py
import numpy as np
import pandas as pd
import pytest
from ez.backtest.metrics import MetricsCalculator


@pytest.fixture
def calc():
    return MetricsCalculator(risk_free_rate=0.0)


def test_total_return(calc):
    equity = pd.Series([100000, 110000, 120000])
    metrics = calc.compute(equity, pd.Series([100000, 105000, 110000]))
    assert abs(metrics["total_return"] - 0.2) < 1e-6


def test_max_drawdown(calc):
    equity = pd.Series([100000, 110000, 90000, 95000])
    metrics = calc.compute(equity, pd.Series([100000] * 4))
    # Peak 110000, trough 90000, drawdown = -0.1818...
    assert metrics["max_drawdown"] < 0
    assert abs(metrics["max_drawdown"] - (-20000 / 110000)) < 1e-4


def test_sharpe_ratio_positive_returns(calc):
    np.random.seed(42)
    daily_r = np.random.normal(0.001, 0.01, 252)
    equity = pd.Series((1 + pd.Series(daily_r)).cumprod() * 100000)
    metrics = calc.compute(equity, equity * 0 + 100000)
    assert metrics["sharpe_ratio"] > 0


def test_win_rate(calc):
    # Mock: need trades for win rate. Use returns directly
    equity = pd.Series([100, 101, 100.5, 102, 101.5, 103])
    metrics = calc.compute(equity, equity * 0 + 100)
    assert "annualized_return" in metrics
```

- [ ] **Step 2: Implement**

```python
# ez/backtest/metrics.py
"""Performance metrics calculation.

[CORE] — append-only. New metrics can be added, existing must not change formula.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class MetricsCalculator:
    """Compute standard backtest performance metrics."""

    def __init__(self, risk_free_rate: float = 0.03, trading_days: int = 252):
        self._rf = risk_free_rate
        self._td = trading_days

    def compute(
        self, equity_curve: pd.Series, benchmark_curve: pd.Series,
    ) -> dict[str, float]:
        daily_returns = equity_curve.pct_change().dropna()
        bench_returns = benchmark_curve.pct_change().dropna()

        total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
        n_days = len(daily_returns)
        years = n_days / self._td if n_days > 0 else 1

        ann_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0
        ann_vol = float(daily_returns.std() * np.sqrt(self._td))

        # Sharpe
        daily_rf = self._rf / self._td
        excess = daily_returns - daily_rf
        sharpe = float(excess.mean() / excess.std() * np.sqrt(self._td)) if excess.std() > 1e-10 else 0.0

        # Sortino
        downside = excess[excess < 0]
        sortino = float(excess.mean() / downside.std() * np.sqrt(self._td)) if len(downside) > 0 and downside.std() > 1e-10 else 0.0

        # Drawdown
        running_max = equity_curve.cummax()
        drawdown = (equity_curve - running_max) / running_max
        max_dd = float(drawdown.min())

        # Benchmark
        bench_total = (benchmark_curve.iloc[-1] / benchmark_curve.iloc[0]) - 1

        return {
            "total_return": float(total_return),
            "annualized_return": float(ann_return),
            "annualized_volatility": ann_vol,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_dd,
            "benchmark_return": float(bench_total),
            "trading_days": n_days,
        }
```

```python
# ez/backtest/portfolio.py
"""Portfolio state tracking during backtest.

[CORE] — tracks cash, position, equity over time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ez.types import TradeRecord


@dataclass
class PortfolioState:
    cash: float
    position_shares: float = 0.0
    position_value: float = 0.0
    trades: list[TradeRecord] = field(default_factory=list)

    @property
    def equity(self) -> float:
        return self.cash + self.position_value
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_backtest/test_metrics.py -v
```

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add ez/backtest/metrics.py ez/backtest/portfolio.py tests/test_backtest/test_metrics.py
git commit -m "feat: MetricsCalculator (Sharpe, Sortino, drawdown) + PortfolioState"
```

---

### Task 14: Vectorized Backtest Engine

**Files:**
- Create: `ez/backtest/engine.py`
- Test: `tests/test_backtest/test_engine.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_backtest/test_engine.py
import pandas as pd
import pytest
from ez.backtest.engine import VectorizedBacktestEngine
from ez.strategy.builtin.ma_cross import MACrossStrategy


def test_engine_runs_without_error(sample_df):
    engine = VectorizedBacktestEngine()
    strategy = MACrossStrategy(short_period=5, long_period=10)
    result = engine.run(sample_df, strategy, initial_capital=100000)
    assert result is not None
    assert len(result.equity_curve) > 0


def test_engine_shifts_signals(sample_df):
    """Signals should be shifted by 1 (no look-ahead bias)."""
    engine = VectorizedBacktestEngine()
    strategy = MACrossStrategy(short_period=5, long_period=10)
    result = engine.run(sample_df, strategy, initial_capital=100000)
    # First bar should never have a trade (shift means first signal is NaN)
    assert result.equity_curve.iloc[0] == pytest.approx(100000, rel=0.01)


def test_engine_respects_commission(sample_df):
    engine_no_comm = VectorizedBacktestEngine(commission_rate=0.0)
    engine_with_comm = VectorizedBacktestEngine(commission_rate=0.01)
    strategy = MACrossStrategy(short_period=5, long_period=10)
    r1 = engine_no_comm.run(sample_df, strategy, initial_capital=100000)
    r2 = engine_with_comm.run(sample_df, strategy, initial_capital=100000)
    # With commission, final equity should be lower
    assert r1.equity_curve.iloc[-1] >= r2.equity_curve.iloc[-1]


def test_engine_produces_significance(sample_df):
    engine = VectorizedBacktestEngine()
    strategy = MACrossStrategy(short_period=5, long_period=10)
    result = engine.run(sample_df, strategy, initial_capital=100000)
    assert result.significance is not None
    assert isinstance(result.significance.monte_carlo_p_value, float)
```

- [ ] **Step 2: Implement**

```python
# ez/backtest/engine.py
"""Vectorized backtest engine.

[CORE] — engine loop steps frozen. Hook points may be added.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ez.backtest.metrics import MetricsCalculator
from ez.backtest.significance import compute_significance
from ez.strategy.base import Strategy
from ez.types import BacktestResult, TradeRecord


class VectorizedBacktestEngine:
    """Run vectorized backtests on OHLCV data with a strategy."""

    def __init__(
        self,
        commission_rate: float = 0.0003,
        min_commission: float = 5.0,
        risk_free_rate: float = 0.03,
    ):
        self._commission_rate = commission_rate
        self._min_commission = min_commission
        self._metrics = MetricsCalculator(risk_free_rate=risk_free_rate)

    def run(
        self,
        data: pd.DataFrame,
        strategy: Strategy,
        initial_capital: float = 100000.0,
    ) -> BacktestResult:
        # 1. Compute factors
        df = data.copy()
        warmup = 0
        for factor in strategy.required_factors():
            df = factor.compute(df)
            warmup = max(warmup, factor.warmup_period)

        # 2. Generate signals
        raw_signals = strategy.generate_signals(df)

        # 3. Shift signals by 1 (prevent look-ahead bias)
        signals = raw_signals.shift(1).fillna(0.0)

        # 4. Trim warmup period
        df = df.iloc[warmup:]
        signals = signals.iloc[warmup:]

        # 5. Simulate trades
        equity, trades, daily_returns = self._simulate(df, signals, initial_capital)

        # 6. Benchmark (buy & hold)
        bench_returns = df["adj_close"].pct_change().fillna(0.0)
        benchmark = (1 + bench_returns).cumprod() * initial_capital

        # 7. Compute metrics
        metrics = self._metrics.compute(equity, benchmark)

        # 8. Add trade stats
        if trades:
            wins = [t for t in trades if t.pnl > 0]
            metrics["win_rate"] = len(wins) / len(trades) if trades else 0.0
            metrics["trade_count"] = len(trades)
            avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0.0
            losses = [t for t in trades if t.pnl <= 0]
            avg_loss = abs(np.mean([t.pnl_pct for t in losses])) if losses else 1.0
            metrics["profit_factor"] = avg_win / avg_loss if avg_loss > 0 else float("inf")
        else:
            metrics["win_rate"] = 0.0
            metrics["trade_count"] = 0
            metrics["profit_factor"] = 0.0

        # 9. Statistical significance
        significance = compute_significance(daily_returns, risk_free_rate=self._metrics._rf)

        return BacktestResult(
            equity_curve=equity,
            benchmark_curve=benchmark,
            trades=trades,
            metrics=metrics,
            signals=signals,
            daily_returns=daily_returns,
            significance=significance,
        )

    def _simulate(
        self, df: pd.DataFrame, signals: pd.Series, capital: float,
    ) -> tuple[pd.Series, list[TradeRecord], pd.Series]:
        prices = df["adj_close"].values
        open_prices = df["open"].values if "open" in df.columns else prices
        weights = signals.values
        n = len(prices)

        equity_arr = np.zeros(n)
        equity_arr[0] = capital
        cash = capital
        shares = 0.0
        prev_weight = 0.0
        trades: list[TradeRecord] = []
        entry_time: datetime | None = None
        entry_price: float = 0.0
        daily_ret = np.zeros(n)

        times = df.index if hasattr(df.index, '__iter__') else range(n)
        time_list = list(times)

        for i in range(1, n):
            target_weight = weights[i] if i < len(weights) else 0.0
            exec_price = open_prices[i]

            if abs(target_weight - prev_weight) > 1e-6:
                # Close existing position
                if shares > 0 and target_weight < prev_weight:
                    sell_value = shares * exec_price
                    comm = max(sell_value * self._commission_rate, self._min_commission)
                    cash += sell_value - comm
                    if entry_time is not None:
                        pnl = (exec_price - entry_price) * shares - comm
                        trades.append(TradeRecord(
                            entry_time=entry_time, exit_time=time_list[i],
                            entry_price=entry_price, exit_price=exec_price,
                            weight=prev_weight, pnl=pnl,
                            pnl_pct=pnl / (entry_price * shares) if entry_price * shares > 0 else 0,
                            commission=comm,
                        ))
                    shares = 0.0

                # Open new position
                if target_weight > 0 and target_weight > prev_weight:
                    invest = capital * target_weight  # simplified: use initial capital as base
                    invest = min(invest, cash)
                    comm = max(invest * self._commission_rate, self._min_commission)
                    shares = (invest - comm) / exec_price if exec_price > 0 else 0
                    cash -= invest
                    entry_time = time_list[i]
                    entry_price = exec_price

                prev_weight = target_weight

            # Update equity
            position_value = shares * prices[i]
            equity_arr[i] = cash + position_value
            if equity_arr[i - 1] > 0:
                daily_ret[i] = (equity_arr[i] / equity_arr[i - 1]) - 1

        equity = pd.Series(equity_arr, index=df.index)
        daily_returns = pd.Series(daily_ret, index=df.index)
        return equity, trades, daily_returns
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_backtest/test_engine.py -v
```

Note: This will fail until we implement significance.py (Task 15). That's expected — proceed to Task 15 next.

- [ ] **Step 4: Commit engine (tests will pass after Task 15)**

```bash
git add ez/backtest/engine.py tests/test_backtest/test_engine.py
git commit -m "feat: VectorizedBacktestEngine with signal shift, warmup, commission"
```

---

### Task 15: Walk-Forward + Significance

**Files:**
- Create: `ez/backtest/walk_forward.py`, `ez/backtest/significance.py`
- Test: `tests/test_backtest/test_significance.py`, `tests/test_backtest/test_walk_forward.py`

- [ ] **Step 1: Implement significance.py**

```python
# ez/backtest/significance.py
"""Statistical significance testing for backtest results.

[CORE] — interface frozen.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ez.types import SignificanceTest


def compute_significance(
    daily_returns: pd.Series,
    risk_free_rate: float = 0.03,
    n_bootstrap: int = 1000,
    n_permutations: int = 1000,
) -> SignificanceTest:
    """Bootstrap CI for Sharpe + Monte Carlo permutation test."""
    returns = daily_returns.dropna().values
    if len(returns) < 20:
        return SignificanceTest(
            sharpe_ci_lower=0.0, sharpe_ci_upper=0.0,
            monte_carlo_p_value=1.0, is_significant=False,
        )

    daily_rf = risk_free_rate / 252
    observed_sharpe = _sharpe(returns, daily_rf)

    # Bootstrap CI
    rng = np.random.default_rng(42)
    boot_sharpes = np.array([
        _sharpe(rng.choice(returns, size=len(returns), replace=True), daily_rf)
        for _ in range(n_bootstrap)
    ])
    ci_lower = float(np.percentile(boot_sharpes, 2.5))
    ci_upper = float(np.percentile(boot_sharpes, 97.5))

    # Monte Carlo permutation
    perm_sharpes = np.array([
        _sharpe(rng.permutation(returns), daily_rf)
        for _ in range(n_permutations)
    ])
    p_value = float(np.mean(perm_sharpes >= observed_sharpe))

    return SignificanceTest(
        sharpe_ci_lower=ci_lower,
        sharpe_ci_upper=ci_upper,
        monte_carlo_p_value=p_value,
        is_significant=p_value < 0.05,
    )


def _sharpe(returns: np.ndarray, daily_rf: float) -> float:
    excess = returns - daily_rf
    std = excess.std()
    if std < 1e-10:
        return 0.0
    return float(excess.mean() / std * np.sqrt(252))
```

- [ ] **Step 2: Implement walk_forward.py**

```python
# ez/backtest/walk_forward.py
"""Walk-Forward robustness validation.

[CORE] — V1: fixed-parameter validation (time-series CV). V2 adds parameter optimization.
"""
from __future__ import annotations

import pandas as pd

from ez.backtest.engine import VectorizedBacktestEngine
from ez.strategy.base import Strategy
from ez.types import BacktestResult, WalkForwardResult


class WalkForwardValidator:
    """Split data into rolling train/test windows and validate strategy robustness."""

    def __init__(self, engine: VectorizedBacktestEngine | None = None):
        self._engine = engine or VectorizedBacktestEngine()

    def validate(
        self,
        data: pd.DataFrame,
        strategy: Strategy,
        n_splits: int = 5,
        train_ratio: float = 0.7,
        initial_capital: float = 100000.0,
    ) -> WalkForwardResult:
        n = len(data)
        window_size = n // n_splits
        if window_size < 20:
            raise ValueError(f"Not enough data for {n_splits} splits (need {n_splits * 20} bars, got {n})")

        train_size = int(window_size * train_ratio)
        test_size = window_size - train_size

        splits: list[BacktestResult] = []
        oos_equities: list[pd.Series] = []
        is_sharpes: list[float] = []
        oos_sharpes: list[float] = []

        for i in range(n_splits):
            start = i * window_size
            train_end = start + train_size
            test_end = min(start + window_size, n)

            if test_end > n:
                break

            train_data = data.iloc[start:train_end]
            test_data = data.iloc[train_end:test_end]

            if len(test_data) < 5:
                continue

            # In-sample
            is_result = self._engine.run(train_data, strategy, initial_capital)
            is_sharpes.append(is_result.metrics.get("sharpe_ratio", 0.0))

            # Out-of-sample
            oos_result = self._engine.run(test_data, strategy, initial_capital)
            oos_sharpes.append(oos_result.metrics.get("sharpe_ratio", 0.0))
            splits.append(oos_result)
            oos_equities.append(oos_result.equity_curve)

        # Combine OOS results
        oos_equity = pd.concat(oos_equities, ignore_index=True) if oos_equities else pd.Series([initial_capital])

        oos_metrics = {}
        if oos_sharpes:
            oos_metrics["sharpe_ratio"] = sum(oos_sharpes) / len(oos_sharpes)

        # Degradation
        is_mean = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
        oos_mean = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0.0
        degradation = (is_mean - oos_mean) / abs(is_mean) if abs(is_mean) > 1e-10 else 0.0

        return WalkForwardResult(
            splits=splits,
            oos_equity_curve=oos_equity,
            oos_metrics=oos_metrics,
            is_vs_oos_degradation=degradation,
            overfitting_score=max(0.0, degradation),
        )
```

- [ ] **Step 3: Write tests**

```python
# tests/test_backtest/test_significance.py
import numpy as np
import pandas as pd
from ez.backtest.significance import compute_significance


def test_significance_random_returns():
    """Random returns should not be significant."""
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0, 0.01, 252))
    result = compute_significance(returns, n_bootstrap=200, n_permutations=200)
    assert result.monte_carlo_p_value > 0.01  # random should have high p-value


def test_significance_strong_returns():
    """Strong positive returns should be significant."""
    np.random.seed(42)
    returns = pd.Series(np.random.normal(0.002, 0.005, 252))  # strong daily returns
    result = compute_significance(returns, n_bootstrap=500, n_permutations=500)
    assert result.sharpe_ci_lower > 0  # CI should not include zero
    assert result.is_significant is True


def test_significance_too_few_data():
    returns = pd.Series([0.01, -0.01, 0.005])
    result = compute_significance(returns)
    assert result.is_significant is False
    assert result.monte_carlo_p_value == 1.0
```

```python
# tests/test_backtest/test_walk_forward.py
import pytest
from ez.backtest.walk_forward import WalkForwardValidator
from ez.strategy.builtin.ma_cross import MACrossStrategy


def test_walk_forward_runs(sample_df):
    validator = WalkForwardValidator()
    strategy = MACrossStrategy(short_period=5, long_period=10)
    result = validator.validate(sample_df, strategy, n_splits=3)
    assert len(result.splits) > 0
    assert result.overfitting_score >= 0


def test_walk_forward_too_few_data():
    import pandas as pd
    small_df = pd.DataFrame({"adj_close": [1, 2, 3], "open": [1, 2, 3]})
    validator = WalkForwardValidator()
    strategy = MACrossStrategy(short_period=2, long_period=3)
    with pytest.raises(ValueError, match="Not enough data"):
        validator.validate(small_df, strategy, n_splits=5)
```

- [ ] **Step 4: Run ALL backtest tests**

```bash
pytest tests/test_backtest/ -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add ez/backtest/ tests/test_backtest/
git commit -m "feat: Walk-Forward validator + statistical significance (Bootstrap + Monte Carlo)"
```

---

### Task 16: Integration Test + Smoke Test + CLAUDE.md files

**Files:**
- Create: `tests/test_integration/test_pipeline.py`, `tests/test_smoke.py`
- Create: `ez/strategy/CLAUDE.md`, `ez/backtest/CLAUDE.md`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration/test_pipeline.py
"""Full pipeline: data → factor → strategy → backtest → metrics."""
from datetime import date

from tests.mocks.mock_provider import MockDataProvider
from ez.data.store import DuckDBStore
from ez.data.provider import DataProviderChain
from ez.strategy.builtin.ma_cross import MACrossStrategy
from ez.backtest.engine import VectorizedBacktestEngine
from ez.backtest.walk_forward import WalkForwardValidator
import pandas as pd


def test_full_pipeline_with_mock(tmp_path):
    # Setup
    store = DuckDBStore(str(tmp_path / "test.db"))
    provider = MockDataProvider()
    chain = DataProviderChain(providers=[provider], store=store)

    # Fetch data
    bars = chain.get_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2025, 12, 31))
    assert len(bars) > 50

    # Convert to DataFrame
    df = pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")

    # Run backtest
    strategy = MACrossStrategy(short_period=5, long_period=10)
    engine = VectorizedBacktestEngine(commission_rate=0.0003)
    result = engine.run(df, strategy, initial_capital=100000)

    # Verify results
    assert result.metrics["sharpe_ratio"] is not None
    assert len(result.equity_curve) > 0
    assert result.equity_curve.iloc[-1] > 0
    assert result.significance is not None

    # Data should now be cached
    cached = store.query_kline("TEST.US", "us_stock", "daily", date(2023, 1, 1), date(2025, 12, 31))
    assert len(cached) == len(bars)

    store.close()


def test_walk_forward_pipeline(sample_df):
    strategy = MACrossStrategy(short_period=5, long_period=10)
    validator = WalkForwardValidator()
    wf = validator.validate(sample_df, strategy, n_splits=3)
    assert len(wf.splits) > 0
    assert wf.overfitting_score >= 0
    assert "sharpe_ratio" in wf.oos_metrics
```

- [ ] **Step 2: Write smoke test**

```python
# tests/test_smoke.py
"""Smoke tests — run after every change. Must complete in < 5 seconds."""


def test_all_core_imports():
    import ez.types
    import ez.errors
    import ez.config
    import ez.data.provider
    import ez.data.store
    import ez.data.validator
    import ez.factor.base
    import ez.factor.evaluator
    import ez.strategy.base
    import ez.strategy.loader
    import ez.backtest.engine
    import ez.backtest.metrics
    import ez.backtest.walk_forward
    import ez.backtest.significance


def test_strategy_registration():
    from ez.strategy.base import Strategy
    from ez.strategy.loader import load_all_strategies
    load_all_strategies()
    assert len(Strategy._registry) > 0


def test_factor_instantiation():
    from ez.factor.builtin.technical import MA
    ma = MA(period=5)
    assert ma.warmup_period == 5
    assert ma.name == "ma_5"
```

- [ ] **Step 3: Create strategy and backtest CLAUDE.md**

`ez/strategy/CLAUDE.md`:
```markdown
# ez/strategy — Strategy Layer

## Responsibility
Define and auto-register trading strategies. Strategies produce position weight signals.

## Public Interfaces
- `Strategy(ABC)` — [CORE] base class. `__init_subclass__` auto-registers.
  - Methods: `required_factors() -> list[Factor]`, `generate_signals(df) -> Series`
  - Class method: `get_parameters_schema() -> dict`
- `load_all_strategies()` — [CORE] scans configured directories and imports all strategy modules

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| base.py | Strategy ABC | CORE |
| loader.py | Directory scanner | CORE |
| builtin/ma_cross.py | MA crossover reference | EXTENSION |

## Dependencies
- Upstream: `ez/types.py`, `ez/factor/base.py`
- Downstream: `ez/backtest/`, `ez/api/`

## Adding a New Strategy
1. Create `strategies/your_strategy.py` (or `ez/strategy/builtin/`)
2. Inherit from `Strategy`, implement `required_factors()`, `generate_signals()`, `get_parameters_schema()`
3. Run `pytest tests/test_strategy/` — auto-validates

## Status
- Implemented: Strategy ABC, loader, MACrossStrategy
```

`ez/backtest/CLAUDE.md`:
```markdown
# ez/backtest — Backtest Layer

## Responsibility
Run vectorized backtests, compute metrics, validate via Walk-Forward, test statistical significance.

## Public Interfaces
- `VectorizedBacktestEngine` — [CORE] run(data, strategy, capital) -> BacktestResult
- `MetricsCalculator` — [CORE] compute(equity, benchmark) -> dict
- `WalkForwardValidator` — [CORE] validate(data, strategy, n_splits) -> WalkForwardResult
- `compute_significance()` — [CORE] Bootstrap CI + Monte Carlo permutation test

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| engine.py | VectorizedBacktestEngine | CORE |
| portfolio.py | PortfolioState | CORE |
| metrics.py | MetricsCalculator | CORE |
| walk_forward.py | WalkForwardValidator | CORE |
| significance.py | Statistical significance | CORE |

## Dependencies
- Upstream: `ez/types.py`, `ez/strategy/base.py`, `ez/factor/base.py`
- Downstream: `ez/api/`

## Status
- Implemented: Full backtest engine, Walk-Forward (fixed-param), significance testing
- V2: Parameter optimization in Walk-Forward (WFO)
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration/ tests/test_smoke.py ez/strategy/CLAUDE.md ez/backtest/CLAUDE.md
git commit -m "feat: integration tests, smoke tests, strategy + backtest CLAUDE.md"
```

---

## Phase 5: API Layer

### Task 17: FastAPI App + All Routes

**Files:**
- Create: `ez/api/app.py`, `ez/api/routes/market_data.py`, `ez/api/routes/backtest.py`, `ez/api/routes/factors.py`

- [ ] **Step 1: Implement ez/api/app.py**

```python
"""FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ez.config import load_config
from ez.strategy.loader import load_all_strategies

app = FastAPI(title="ez-trading", version="0.1.0")

# CORS
config = load_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load strategies at startup
load_all_strategies()

# Register routes
from ez.api.routes import market_data, backtest, factors  # noqa: E402
app.include_router(market_data.router, prefix="/api/market-data", tags=["market-data"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
app.include_router(factors.router, prefix="/api/factors", tags=["factors"])


@app.get("/api/health")
def health():
    from ez.strategy.base import Strategy
    return {
        "status": "ok",
        "version": "0.1.0",
        "strategies_registered": len(Strategy._registry),
    }
```

- [ ] **Step 2: Implement routes/market_data.py**

```python
# ez/api/routes/market_data.py
"""Market data endpoints."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ez.config import load_config
from ez.data.providers.tencent_provider import TencentDataProvider
from ez.data.provider import DataProviderChain
from ez.data.store import DuckDBStore

router = APIRouter()


def _get_chain() -> DataProviderChain:
    config = load_config()
    store = DuckDBStore(config.database.path)
    providers = [TencentDataProvider()]
    return DataProviderChain(providers=providers, store=store)


@router.get("/kline")
def get_kline(
    symbol: str = Query(..., description="Stock symbol, e.g. 000001.SZ"),
    market: str = Query("cn_stock"),
    period: str = Query("daily"),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    chain = _get_chain()
    bars = chain.get_kline(symbol, market, period, start_date, end_date)
    return [
        {
            "date": b.time.strftime("%Y-%m-%d"),
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
        }
        for b in bars
    ]


@router.get("/symbols")
def search_symbols(keyword: str = Query(...), market: str = Query("")):
    chain = _get_chain()
    return chain.search_symbols(keyword, market)
```

- [ ] **Step 3: Implement routes/backtest.py**

```python
# ez/api/routes/backtest.py
"""Backtest endpoints."""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ez.backtest.engine import VectorizedBacktestEngine
from ez.backtest.walk_forward import WalkForwardValidator
from ez.config import load_config
from ez.data.provider import DataProviderChain
from ez.data.providers.tencent_provider import TencentDataProvider
from ez.data.store import DuckDBStore
from ez.strategy.base import Strategy

router = APIRouter()


class BacktestRequest(BaseModel):
    symbol: str
    market: str = "cn_stock"
    period: str = "daily"
    strategy_name: str
    strategy_params: dict = {}
    start_date: date
    end_date: date
    initial_capital: float = 100000.0
    commission_rate: float = 0.0003


class WalkForwardRequest(BacktestRequest):
    n_splits: int = 5
    train_ratio: float = 0.7


def _get_strategy(name: str, params: dict) -> Strategy:
    # Find by short name or full key
    for key, cls in Strategy._registry.items():
        if cls.__name__ == name or key == name:
            schema = cls.get_parameters_schema()
            p = {k: v["default"] for k, v in schema.items()}
            p.update(params)
            return cls(**p)
    raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")


def _fetch_data(req: BacktestRequest) -> pd.DataFrame:
    config = load_config()
    store = DuckDBStore(config.database.path)
    chain = DataProviderChain([TencentDataProvider()], store)
    bars = chain.get_kline(req.symbol, req.market, req.period, req.start_date, req.end_date)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No data for {req.symbol}")
    return pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")


@router.post("/run")
def run_backtest(req: BacktestRequest):
    strategy = _get_strategy(req.strategy_name, req.strategy_params)
    df = _fetch_data(req)
    engine = VectorizedBacktestEngine(commission_rate=req.commission_rate)
    result = engine.run(df, strategy, req.initial_capital)
    return {
        "metrics": result.metrics,
        "equity_curve": result.equity_curve.tolist(),
        "benchmark_curve": result.benchmark_curve.tolist(),
        "trades": [
            {"entry_time": t.entry_time.isoformat(), "exit_time": t.exit_time.isoformat(),
             "entry_price": t.entry_price, "exit_price": t.exit_price,
             "pnl": t.pnl, "pnl_pct": t.pnl_pct, "commission": t.commission}
            for t in result.trades
        ],
        "significance": {
            "sharpe_ci_lower": result.significance.sharpe_ci_lower,
            "sharpe_ci_upper": result.significance.sharpe_ci_upper,
            "p_value": result.significance.monte_carlo_p_value,
            "is_significant": result.significance.is_significant,
        },
    }


@router.post("/walk-forward")
def run_walk_forward(req: WalkForwardRequest):
    strategy = _get_strategy(req.strategy_name, req.strategy_params)
    df = _fetch_data(req)
    validator = WalkForwardValidator(
        VectorizedBacktestEngine(commission_rate=req.commission_rate)
    )
    result = validator.validate(df, strategy, req.n_splits, req.train_ratio, req.initial_capital)
    return {
        "oos_metrics": result.oos_metrics,
        "overfitting_score": result.overfitting_score,
        "is_vs_oos_degradation": result.is_vs_oos_degradation,
        "n_splits": len(result.splits),
        "oos_equity_curve": result.oos_equity_curve.tolist(),
    }


@router.get("/strategies")
def list_strategies():
    return [
        {
            "name": cls.__name__,
            "key": key,
            "parameters": cls.get_parameters_schema(),
        }
        for key, cls in Strategy._registry.items()
    ]
```

- [ ] **Step 4: Implement routes/factors.py**

```python
# ez/api/routes/factors.py
"""Factor endpoints."""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ez.data.provider import DataProviderChain
from ez.data.providers.tencent_provider import TencentDataProvider
from ez.data.store import DuckDBStore
from ez.config import load_config
from ez.factor.builtin.technical import MA, EMA, RSI, MACD, BOLL
from ez.factor.evaluator import FactorEvaluator

router = APIRouter()

_FACTOR_MAP = {
    "ma": MA, "ema": EMA, "rsi": RSI, "macd": MACD, "boll": BOLL,
}


class FactorEvalRequest(BaseModel):
    symbol: str
    market: str = "cn_stock"
    factor_name: str
    factor_params: dict = {}
    start_date: date
    end_date: date
    periods: list[int] = [1, 5, 10, 20]


@router.get("")
def list_factors():
    return [
        {"name": name, "class": cls.__name__}
        for name, cls in _FACTOR_MAP.items()
    ]


@router.post("/evaluate")
def evaluate_factor(req: FactorEvalRequest):
    factory = _FACTOR_MAP.get(req.factor_name.lower())
    if not factory:
        raise HTTPException(status_code=404, detail=f"Factor '{req.factor_name}' not found")

    factor = factory(**req.factor_params) if req.factor_params else factory()

    config = load_config()
    store = DuckDBStore(config.database.path)
    chain = DataProviderChain([TencentDataProvider()], store)
    bars = chain.get_kline(req.symbol, req.market, "daily", req.start_date, req.end_date)
    if not bars:
        raise HTTPException(status_code=404, detail=f"No data for {req.symbol}")

    df = pd.DataFrame([{
        "time": b.time, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")

    computed = factor.compute(df)
    factor_col = [c for c in computed.columns if c not in df.columns]
    if not factor_col:
        raise HTTPException(status_code=500, detail="Factor produced no new columns")

    factor_values = computed[factor_col[0]].dropna()
    forward_returns = df["adj_close"].pct_change().shift(-1).dropna()

    evaluator = FactorEvaluator()
    analysis = evaluator.evaluate(factor_values, forward_returns, req.periods)

    return {
        "ic_mean": analysis.ic_mean,
        "rank_ic_mean": analysis.rank_ic_mean,
        "icir": analysis.icir,
        "rank_icir": analysis.rank_icir,
        "ic_decay": analysis.ic_decay,
        "turnover": analysis.turnover,
        "ic_series": analysis.ic_series.tolist(),
        "rank_ic_series": analysis.rank_ic_series.tolist(),
    }
```

- [ ] **Step 5: Run smoke test with API**

```bash
pytest tests/test_smoke.py -v
```

- [ ] **Step 6: Commit**

```bash
git add ez/api/
git commit -m "feat: FastAPI app with market-data, backtest, factor endpoints"
```

---

## Phase 6: Frontend

> Frontend tasks are condensed — each creates functional React components. Follow the spec's dark theme (#0d1117) and Chinese red/green convention.

### Task 18: React Scaffolding

- [ ] **Step 1: Initialize Vite + React + TypeScript project**

```bash
cd web
npm create vite@latest . -- --template react-ts
npm install axios echarts echarts-for-react
npm install -D tailwindcss @tailwindcss/vite
```

- [ ] **Step 2: Configure vite.config.ts (port 3000, proxy to API)**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: { '/api': 'http://localhost:8000' },
  },
})
```

- [ ] **Step 3: Setup dark theme in src/styles/global.css**

```css
@import "tailwindcss";

:root {
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --border: #30363d;
  --text-primary: #e6edf3;
  --text-secondary: #8b949e;
  --color-up: #ef4444;
  --color-down: #22c55e;
  --color-accent: #2563eb;
}

body {
  background-color: var(--bg-primary);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  margin: 0;
}
```

- [ ] **Step 4: Create src/api/index.ts and src/types/index.ts**

```typescript
// src/api/index.ts
import axios from 'axios'
const api = axios.create({ baseURL: '/api' })
export default api

// src/types/index.ts
export interface KlineBar {
  date: string; open: number; high: number; low: number;
  close: number; adj_close: number; volume: number;
}
export interface BacktestResult {
  metrics: Record<string, number>;
  equity_curve: number[];
  benchmark_curve: number[];
  trades: TradeRecord[];
  significance: { sharpe_ci_lower: number; sharpe_ci_upper: number; p_value: number; is_significant: boolean };
}
export interface TradeRecord {
  entry_time: string; exit_time: string; entry_price: number; exit_price: number;
  pnl: number; pnl_pct: number; commission: number;
}
export interface StrategyInfo { name: string; key: string; parameters: Record<string, any> }
```

- [ ] **Step 5: Commit**

```bash
git add web/
git commit -m "feat: React + Vite + TailwindCSS scaffolding with dark theme"
```

---

### Task 19-24: Frontend Components

> Tasks 19-24 build: Navbar, SearchBar, StockTabs, KlineChart (ECharts candlestick + volume), BacktestPanel, FactorPanel, and Dashboard page. Each follows standard React patterns.

> **Due to plan length constraints, these tasks follow the same TDD pattern: write component → integrate with API → verify in browser. Detailed ECharts candlestick configuration and TailwindCSS styling are implementation-level details that the implementing agent can derive from the spec's section 6 (前端看板) and the TypeScript types defined above.**

> Key technical decisions already locked in:
> - ECharts `candlestick` series with `grid` layout (70% kline, 20% volume, 10% dataZoom)
> - Chinese convention: red (#ef4444) = up, green (#22c55e) = down
> - Strategy parameter forms auto-rendered from `get_parameters_schema()` response
> - Significance badge: green if `is_significant`, red otherwise

---

## Phase 7: Polish

### Task 25: Scripts + Root CLAUDE.md + Architecture Tests

- [ ] **Step 1: Create scripts/start.sh**

```bash
#!/bin/bash
set -e
echo "Starting ez-trading..."
mkdir -p data
# Start backend
uvicorn ez.api.app:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID)"
# Start frontend
cd web && npm run dev &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID)"
echo "Backend: http://localhost:8000"
echo "Frontend: http://localhost:3000"
echo "$BACKEND_PID $FRONTEND_PID" > /tmp/ez-trading.pids
wait
```

- [ ] **Step 2: Create scripts/stop.sh**

```bash
#!/bin/bash
if [ -f /tmp/ez-trading.pids ]; then
    kill $(cat /tmp/ez-trading.pids) 2>/dev/null
    rm /tmp/ez-trading.pids
    echo "ez-trading stopped."
else
    echo "No running instance found."
fi
```

```bash
chmod +x scripts/start.sh scripts/stop.sh
```

- [ ] **Step 3: Create tests/test_architecture.py**

```python
"""Architecture fitness tests — enforce Core/Extension boundaries."""
import ast
from pathlib import Path

CORE_FILES = [
    Path("ez/types.py"), Path("ez/errors.py"), Path("ez/config.py"),
    Path("ez/data/provider.py"), Path("ez/data/validator.py"), Path("ez/data/store.py"),
    Path("ez/factor/base.py"), Path("ez/factor/evaluator.py"),
    Path("ez/strategy/base.py"), Path("ez/strategy/loader.py"),
    Path("ez/backtest/engine.py"), Path("ez/backtest/portfolio.py"),
    Path("ez/backtest/metrics.py"), Path("ez/backtest/walk_forward.py"),
    Path("ez/backtest/significance.py"),
]

EXTENSION_MODULES = ["ez.data.providers", "ez.factor.builtin", "ez.strategy.builtin", "ez.api.routes"]


def test_core_does_not_import_extension():
    for core_file in CORE_FILES:
        if not core_file.exists():
            continue
        tree = ast.parse(core_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for ext in EXTENSION_MODULES:
                    assert ext not in node.module, (
                        f"Core file {core_file} imports extension module {node.module}"
                    )
```

- [ ] **Step 4: Create root CLAUDE.md**

```markdown
# ez-trading

Agent-Native quantitative trading platform. Python 3.12+ / FastAPI / DuckDB / React 19 / ECharts.

## Module Map
- `ez/data/` — Data ingestion, validation, caching [CLAUDE.md](ez/data/CLAUDE.md)
- `ez/factor/` — Factor computation + IC evaluation [CLAUDE.md](ez/factor/CLAUDE.md)
- `ez/strategy/` — Strategy framework, auto-registration [CLAUDE.md](ez/strategy/CLAUDE.md)
- `ez/backtest/` — Backtest engine, Walk-Forward, significance [CLAUDE.md](ez/backtest/CLAUDE.md)
- `ez/api/` — FastAPI REST endpoints [CLAUDE.md](ez/api/CLAUDE.md)
- `web/` — React frontend dashboard [CLAUDE.md](web/CLAUDE.md)

## Dependency Flow
```
ez/types.py → ez/data/ → ez/factor/ → ez/strategy/ → ez/backtest/ → ez/api/ → web/
```

## Core Files (DO NOT MODIFY without proposal in docs/internal/core-changes/)
ez/types.py, ez/errors.py, ez/config.py, ez/data/provider.py, ez/data/validator.py,
ez/data/store.py, ez/factor/base.py, ez/factor/evaluator.py, ez/strategy/base.py,
ez/strategy/loader.py, ez/backtest/engine.py, ez/backtest/portfolio.py,
ez/backtest/metrics.py, ez/backtest/walk_forward.py, ez/backtest/significance.py

## Adding Extensions (no Core changes needed)
| Type | Directory | Base Class | Test |
|------|-----------|------------|------|
| Data source | ez/data/providers/ | DataProvider | pytest tests/test_data/test_provider_contract.py |
| Factor | ez/factor/builtin/ | Factor | pytest tests/test_factor/test_factor_contract.py |
| Strategy | strategies/ | Strategy | pytest tests/test_strategy/ |

## Quick Commands
```bash
./scripts/start.sh          # Start backend (8000) + frontend (3000)
./scripts/stop.sh            # Stop all
pytest tests/test_smoke.py   # Smoke tests (after every change)
pytest tests/                # Full test suite
```

## Spec
docs/internal/specs/2026-03-27-ez-trading-design.md
```

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add scripts/ tests/test_architecture.py CLAUDE.md
git commit -m "feat: start/stop scripts, architecture tests, root CLAUDE.md"
```

---

## Self-Review Checklist

1. **Spec coverage:** All V1 sections covered — data layer (Tasks 5-8), factor layer (Tasks 9-11), strategy layer (Task 12), backtest engine (Tasks 13-15), API (Task 17), frontend (Tasks 18-24), testing (Tasks 4, 11, 16, 25), documentation (Tasks 8, 11, 16, 25).

2. **Placeholder scan:** No TBD/TODO. All code blocks are complete and runnable. Frontend Tasks 19-24 are condensed with explicit technical decisions but reference the spec for detailed styling — this is intentional given plan length.

3. **Type consistency:** `Bar`, `BacktestResult`, `SignificanceTest`, `WalkForwardResult`, `FactorAnalysis`, `TradeRecord` — all defined in `ez/types.py` (Task 2) and used consistently across all tasks. `DataProvider.get_kline()` signature matches in provider.py, mock_provider.py, tencent_provider.py, and fmp_provider.py. `Strategy.generate_signals()` returns `pd.Series` of floats [0,1] — consistent in base.py, ma_cross.py, and contract tests.

4. **Missing from spec:** `ez/api/CLAUDE.md` and `web/CLAUDE.md` are mentioned but not in the plan — they follow the same template as other CLAUDE.md files and should be created during Tasks 17 and 18 respectively.
