# ez/api — API Layer

## Responsibility
REST API exposing market data, backtesting, factor evaluation, and experiments via FastAPI.

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
- `POST /api/experiments` — Submit and run experiment (V2.4)
- `GET /api/experiments` — List recent experiments (V2.4)
- `GET /api/experiments/{run_id}` — Get experiment detail (V2.4)

## Files
| File | Role |
|------|------|
| app.py | FastAPI app entry, CORS, lifespan, router registration |
| deps.py | Singleton DuckDBStore + DataProviderChain + cleanup lifecycle |
| routes/market_data.py | Market data endpoints |
| routes/backtest.py | Backtest + walk-forward endpoints |
| routes/factors.py | Factor listing + evaluation endpoints |
| routes/experiments.py | Experiment submit/list/get endpoints (V2.4) |

## Dependencies
- Upstream: All ez modules (including ez/agent/ for experiments)
- Downstream: web/ (frontend)

## Running
```bash
uvicorn ez.api.app:app --host 0.0.0.0 --port 8000
```

## Status
- Implemented: All V1 endpoints + V2.2 trading costs + V2.4 experiments
- V2.2: BacktestRequest accepts `commission_rate`, `min_commission`, `slippage_rate`
- V2.4: ExperimentRequest with RunSpec validation (422 on invalid), idempotency (duplicate detection), query parameter bounds
