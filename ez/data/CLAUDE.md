# ez/data — Data Layer

## Responsibility
Fetch, validate, cache, and serve market data (K-line) from multiple sources with automatic failover.

## Public Interfaces
- `DataProvider(ABC)` — [CORE] base class for all data sources. Methods: `name`, `get_kline()`, `search_symbols()`
- `DataStore(ABC)` — [CORE] base class for storage. Methods: `query_kline()`, `save_kline()`, `has_data()`
- `DataProviderChain` — [CORE] failover chain: cache -> primary -> backup -> stale cache
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
| providers/tushare_provider.py | Tushare Pro API (A-share primary) | EXTENSION |

## Dependencies
- Upstream: `ez/types.py`, `ez/errors.py`
- Downstream: `ez/factor/`, `ez/backtest/`, `ez/api/`

## Adding a New Data Source
1. Create `ez/data/providers/your_provider.py`
2. Inherit from `DataProvider`, implement `name`, `get_kline()`, `search_symbols()`
3. Run `pytest tests/test_data/test_provider_contract.py` — auto-validates your provider
4. Register in `configs/default.yaml` under `data_sources`

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

## Status
- Implemented: DuckDBStore, TencentProvider, FMPProvider, TushareProvider, DataValidator, DataProviderChain
- Not implemented: AKShare
