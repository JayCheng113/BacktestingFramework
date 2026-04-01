# ez/data — Data Layer

## Responsibility
Fetch, validate, cache, and serve market data (K-line) from multiple sources with automatic failover.

## Public Interfaces
- `DataProvider(ABC)` — [CORE] base class for all data sources. Methods: `name`, `get_kline()`, `search_symbols()`
- `DataStore(ABC)` — [CORE] base class for storage. Methods: `query_kline()`, `save_kline()`, `has_data()`
- `DataProviderChain` — [CORE] failover chain: cache -> primary -> backup -> stale cache
- `DataValidator` — [CORE] validates bars before storage (OHLC consistency, volume)
- `DuckDBStore` — [CORE impl] DuckDB implementation of DataStore. Also exposes `save_symbols()`, `query_symbols()`, `symbols_count()`

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| provider.py | DataProvider ABC, DataStore ABC, DataProviderChain | CORE |
| validator.py | DataValidator | CORE |
| store.py | DuckDBStore | CORE |
| providers/tencent_provider.py | Tencent Finance API | EXTENSION |
| providers/fmp_provider.py | FMP API | EXTENSION |
| providers/tushare_provider.py | Tushare Pro API (A-share primary) | EXTENSION |

## Dependencies
- Upstream: `ez/types.py`, `ez/errors.py`
- Downstream: `ez/factor/`, `ez/backtest/`, `ez/api/`

## Adding a New Data Source
1. Create `ez/data/providers/your_provider.py`
2. Inherit from `DataProvider`, implement `name`, `get_kline()`, `search_symbols()`
3. Run `pytest tests/test_data/test_provider_contract.py` — auto-validates your provider
4. Register in `configs/default.yaml` under `data_sources`
5. Edit `ez/api/deps.py` to wire the provider into `get_chain()`

## Tushare Provider Notes
- **Auth:** Requires `TUSHARE_TOKEN` env var (or constructor param). Get token at tushare.pro.
- **Market:** `cn_stock` only (Chinese A-shares). Raises ProviderError for other markets.
- **Periods:** `daily`, `weekly`, `monthly`. Daily includes forward-adjusted close via `adj_factor` API.
- **Adj close formula:** `adj_close = close * adj_factor_today / adj_factor_latest` (forward adjustment).
- **Volume:** Tushare returns vol in 手 (lots of 100 shares); provider converts to individual shares.
- **Rate limit:** Built-in 0.3s throttle between API calls.
- **Date format:** Tushare uses `YYYYMMDD` strings; helper functions handle conversion.
- **Error handling:** Checks response `code` field; code 2002 = auth error, other non-zero = API error.
- **No SDK:** Uses direct HTTP via httpx (no tushare pip package needed).
- **ETF 支持 (V2.11.1):** 自动检测 ETF 代码 (51/15/16 开头) 使用 `fund_daily` API 而非 `daily`。

## Files (V2.11 additions)
| File | Role | Core/Extension |
|------|------|---------------|
| fundamental.py | FundamentalStore: DuckDB tables (fundamental_daily, fina_indicator), PIT query, preload cache, data quality report | EXTENSION |

## Files (V2.11.1 additions)
| File | Role | Core/Extension |
|------|------|---------------|
| providers/akshare_provider.py | AKShare: 免费A股+ETF数据, 无需注册, 全历史 (V2.11.1) | EXTENSION |

## Status
- Implemented: DuckDBStore, TencentProvider, FMPProvider, TushareProvider, AKShareProvider, DataValidator, DataProviderChain
- V2.11: FundamentalStore (DuckDB fundamental_daily + fina_indicator tables, PIT ann_date alignment, in-memory preload cache, data quality report), TushareProvider extended (get_fina_indicator, dv_ratio in daily_basic)
- V2.11.1: PIT query改用max(end_date)不依赖排序假设, fina DO UPDATE不覆盖ann_date(保留首次公告日, 重报正确), ann_date INDEX加速, threading.Lock保护preload, Tushare日期转换try/except容错, /fundamental/fetch+quality symbols min_length校验
- V2.11.1 post-release: LRU缓存淘汰(symbol粒度, 统一cache units=daily+fina+industry, protect防自淘汰, 75%hysteresis, ghost清理, 读路径best-effort时间戳更新)
- V2.11.1: AKShareProvider (免费fallback: 股票+ETF全历史, 双fetch qfq+raw, 0.6s节流)
- V2.12.1: DuckDBStore.query_kline_batch(单SQL批量查询), DataProviderChain.get_kline_batch(热缓存批量+冷缺失逐个fetch)
