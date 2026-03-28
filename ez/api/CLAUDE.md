# ez/api — API Layer

## Responsibility
REST API exposing market data, backtesting, and factor evaluation via FastAPI.

## Public Interfaces
- `GET /api/health` — Health check
- `GET /api/market-data/kline` — Fetch K-line data
- `GET /api/market-data/symbols` — Search symbols
- `GET /api/market-data/daily-basic` — Fetch daily basic indicators
- `GET /api/market-data/index-kline` — Fetch index K-line data
- `GET /api/market-data/trade-cal` — Fetch trade calendar
- `POST /api/backtest/run` — Run backtest
- `POST /api/backtest/walk-forward` — Walk-forward validation
- `GET /api/backtest/strategies` — List registered strategies
- `GET /api/factors` — List available factors
- `POST /api/factors/evaluate` — Evaluate factor IC

## Files
| File | Role |
|------|------|
| app.py | FastAPI app entry, CORS, lifespan, router registration |
| deps.py | Singleton DuckDBStore + DataProviderChain (shared across routes) |
| routes/market_data.py | Market data endpoints |
| routes/backtest.py | Backtest + walk-forward endpoints |
| routes/factors.py | Factor listing + evaluation endpoints |

## Dependencies
- Upstream: All ez modules
- Downstream: web/ (frontend)

## Running
```bash
uvicorn ez.api.app:app --host 0.0.0.0 --port 8000
```

## Status
- Implemented: All V1 endpoints
- V2.2: BacktestRequest accepts `commission_rate`, `min_commission`, `slippage_rate` (all >= 0, validated server-side). SlippageMatcher used when slippage_rate > 0.
