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
