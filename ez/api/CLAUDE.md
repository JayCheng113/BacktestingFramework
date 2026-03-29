# ez/api — API Layer

## Responsibility
REST API exposing market data, backtesting, factor evaluation, experiments, code editor, and AI chat via FastAPI.

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
- `DELETE /api/experiments/{run_id}` — Delete experiment run (V2.5)
- `POST /api/experiments/cleanup` — Cleanup old experiment runs (V2.5)
- `POST /api/candidates/search` — Batch parameter search (V2.5)
- `POST /api/code/template` — Generate strategy/factor template (V2.7)
- `POST /api/code/validate` — Validate Python code syntax + security (V2.7)
- `POST /api/code/save` — Save code + run contract test (V2.7)
- `GET /api/code/files` — List user strategy files (V2.7)
- `GET /api/code/files/{filename}` — Read a strategy file (V2.7)
- `DELETE /api/code/files/{filename}` — Delete a strategy file (V2.7)
- `POST /api/chat/send` — SSE streaming chat with AI assistant (V2.7)
- `GET /api/chat/status` — Check LLM provider availability (V2.7)
- `GET /api/settings/llm` — Get LLM configuration status (V2.7)
- `POST /api/settings/llm` — Update LLM provider/key/model (V2.7)
- `GET /api/settings/tushare` — Get Tushare token status (V2.7)
- `POST /api/settings/tushare` — Update Tushare token (V2.7)

## Files
| File | Role |
|------|------|
| app.py | FastAPI app entry, CORS, lifespan, router registration |
| deps.py | Singleton DuckDBStore + DataProviderChain + cleanup lifecycle |
| routes/market_data.py | Market data endpoints |
| routes/backtest.py | Backtest + walk-forward endpoints |
| routes/factors.py | Factor listing + evaluation endpoints |
| routes/experiments.py | Experiment submit/list/get/delete/cleanup endpoints (V2.4+V2.5) |
| routes/candidates.py | Batch parameter search endpoint (V2.5) |
| routes/code.py | Code editor: template, validate, save, list, read, delete (V2.7) |
| routes/chat.py | AI chat SSE endpoint + status (V2.7) |
| routes/settings.py | LLM + Tushare config read/write (V2.7) |

## Dependencies
- Upstream: All ez modules (including ez/agent/ for experiments, ez/llm/ for chat)
- Downstream: web/ (frontend)

## Running
```bash
uvicorn ez.api.app:app --host 0.0.0.0 --port 8000
```

## Critical Notes
- SPA catch-all (`/{path:path}`) returns 404 JSON for `/api/*` paths, HTML for all others
- Settings endpoints write to `.env` (api keys) and `configs/default.yaml` (provider/model)
- Walk-Forward: `n_splits >= 2`, `0 < train_ratio < 1` enforced by Pydantic

## Status
- Implemented: All V1 endpoints + V2.2 trading costs + V2.4 experiments + V2.5 batch search + V2.7 code editor + AI chat + settings
- V2.7: Code editor API, Chat SSE, Settings API (LLM/Tushare read/write with .env injection guard)
- V2.7.1: Chat SSE fully async (achat_stream), ExperimentStore shared singleton, multi-column factor evaluation, provider cache invalidation on settings change
