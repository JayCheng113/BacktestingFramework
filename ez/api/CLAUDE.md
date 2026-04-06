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
- `GET /api/code/registry` — List all registered strategies/factors (builtin + user, 4 categories) (V2.11.1)
- `DELETE /api/code/cleanup-research-strategies` — Delete all research_* strategy files (V2.11.1)
- `POST /api/code/refresh` — Re-scan user directories and reload registries (V2.11.1)
- `POST /api/chat/send` — SSE streaming chat with AI assistant (V2.7)
- `GET /api/chat/status` — Check LLM provider availability (V2.7)
- `GET /api/settings/llm` — Get LLM configuration status (V2.7)
- `POST /api/settings/llm` — Update LLM provider/key/model (V2.7)
- `GET /api/settings/tushare` — Get Tushare token status (V2.7)
- `POST /api/settings/tushare` — Update Tushare token (V2.7)
- `POST /api/research/start` — Start autonomous research task (V2.8)
- `GET /api/research/tasks` — List research tasks (V2.8)
- `GET /api/research/tasks/{task_id}` — Get research task detail + iterations (V2.8)
- `POST /api/research/tasks/{task_id}/cancel` — Cancel running research task (V2.8)
- `GET /api/research/tasks/{task_id}/stream` — SSE progress stream (V2.8)
- `POST /api/code/promote` — Promote research strategy to user strategy (V2.8)
- `GET /api/portfolio/strategies` — List portfolio strategies + schemas (V2.9)
- `POST /api/portfolio/run` — Run portfolio backtest (V2.9)
- `GET /api/portfolio/runs` — List portfolio backtest runs (V2.9)
- `GET /api/portfolio/runs/{run_id}` — Get portfolio run detail (V2.9)
- `DELETE /api/portfolio/runs/{run_id}` — Delete portfolio run (V2.9)
- `POST /api/portfolio/evaluate-factors` — Cross-sectional factor evaluation (IC/RankIC/ICIR/decay/quintile) (V2.10)
- `POST /api/portfolio/factor-correlation` — Factor pairwise Spearman correlation matrix (V2.10)
- `POST /api/portfolio/walk-forward` — Portfolio walk-forward validation + significance (V2.10)
- `POST /api/portfolio/search` — Batch parameter search for portfolio strategies (V2.11.1)
- `POST /api/fundamental/fetch` — Fetch and cache fundamental data for symbols (V2.11)
- `POST /api/fundamental/quality` — Data quality report for symbols (V2.11)
- `GET /api/fundamental/factors` — List fundamental factors with categories (V2.11)
- `POST /api/portfolio/ml-alpha/diagnostics` — Run MLDiagnostics on a user MLAlpha (overfitting assessment: feature importance CV, IS/OOS IC, turnover, verdict) (V2.13.1)

## Files
| File | Role |
|------|------|
| app.py | FastAPI app entry, CORS, lifespan, router registration |
| deps.py | Singleton DuckDBStore + DataProviderChain + fetch_kline_df shared helper + cleanup lifecycle |
| routes/market_data.py | Market data endpoints |
| routes/backtest.py | Backtest + walk-forward endpoints |
| routes/factors.py | Factor listing + evaluation endpoints |
| routes/experiments.py | Experiment submit/list/get/delete/cleanup endpoints (V2.4+V2.5) |
| routes/candidates.py | Batch parameter search endpoint (V2.5) |
| routes/code.py | Code editor: template, validate, save, list, read, delete, promote (V2.7+V2.8) |
| routes/chat.py | AI chat SSE endpoint + status (V2.7) |
| routes/settings.py | LLM + Tushare config read/write (V2.7) |
| routes/portfolio.py | Portfolio: strategies/run/runs/detail/delete + factor evaluation/correlation + walk-forward + fundamental factor injection (V2.9+V2.10+V2.11) |
| routes/fundamental.py | Fundamental: fetch/quality/factors endpoints (V2.11) |
| routes/research.py | Autonomous research: start/list/detail/cancel/stream + serialization guard (V2.8) |

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
- Implemented: All V1 endpoints + V2.2 trading costs + V2.4 experiments + V2.5 batch search + V2.7 code editor + AI chat + settings + V2.8 research
- V2.7: Code editor API, Chat SSE, Settings API (LLM/Tushare read/write with .env injection guard)
- V2.7.1: Chat SSE fully async (achat_stream), ExperimentStore shared singleton, multi-column factor evaluation, provider cache invalidation on settings change
- V2.8: Research API (start/list/detail/cancel/stream), promote endpoint, asyncio.Lock serialization guard, register_task pre-registration for SSE, experiment list_runs加start_date/end_date
- V2.8.1: SSE heartbeat (15s keepalive), get_start_lock() public accessor, promote regex precision (Research+uppercase only)
- V2.9: Portfolio API (strategies/run/runs/detail/delete), buy/sell commission split, limit_pct, benchmark, cost validation ge=0, skipped symbols reporting, code save kind param (portfolio_strategy/cross_factor)
- V2.9.1: Single-stock backtest MarketRules integration (stamp_tax_rate+lot_size+limit_pct → MarketRulesMatcher wrapper)
- V2.10: Portfolio factor research API (evaluate-factors, factor-correlation, walk-forward) — cross-sectional IC/ICIR/decay/quintile, Spearman correlation matrix, walk-forward + Bootstrap/Monte Carlo significance
- V2.10 post-release: fetch_kline_df shared helper in deps.py
- V2.11: Fundamental data API (fetch/quality/factors), fundamental factor injection in portfolio routes, FundamentalStore singleton in deps.py, factor categories in strategies endpoint
- V2.11.1: evaluate-factors +neutralize param, NeutralizedWrapper (warnings accumulate+dedup), AlphaCombiner integration (_create_alpha_combiner + _compute_alpha_weights, IC weight sign-preserving, alpha_method validation), portfolio /search endpoint (batch parameter search, MultiFactorRotation preload, skip_ensure), /fundamental/fetch+quality symbols min_length=1 validation
- V2.12.1 post-release (codex 6 轮 + Claude reviewer 8 轮):
  - **PortfolioCommonConfig mixin** (ez/api/routes/portfolio.py): 3 个 request model (PortfolioRunRequest/PortfolioWFRequest/PortfolioSearchRequest) 共享 20+ 字段 (cost/optimizer/risk/index) 的单一来源, 防止默认值和 Field constraints drift
  - **_build_optimizer_risk_factories() 共享 helper**: /run, /walk-forward, /search 统一构造 optimizer_factory + risk_manager_factory + index_weights + helper_warnings, 3 endpoint 参数和警告完全一致
  - **strategy key 撞名**: _get_strategy() 三阶段解析 (exact key → unique name → 409 ambiguous), 前端 option value 改 s.key
  - **walk-forward 配置完整继承**: PortfolioWFRequest 继承 mixin → /walk-forward 同样支持 optimizer/risk_control/index_benchmark, 同时 aggregate optimizer_fallback_events + risk_events 到 response
  - **search 同样聚合**: /search 每 combo 捕获 combo_opt + combo_result, try/finally 聚合 fallback_events + risk_events (partial 事件保护)
  - **latest_weights 返回最后非空**: next((w for w in reversed(history) if w), {}) 绕过清仓后 append({}) 的空 entry
  - **evaluate-factors / factor-correlation lookback propagation**: _max_factor_warmup(factors) 不止传给 fetch, 还传给 evaluate_cross_sectional_factor() 和 compute_factor_correlation() 的 lookback_days 参数
  - **PortfolioSearchRequest 加 optimizer/risk 字段**: 之前搜索完全忽略 optimizer 配置
- V2.12.2 post-release:
  - **`_compute_alpha_weights` lookback 补齐**: `dynamic_lb` 之前只传 `_fetch_data()`, 没传 `evaluate_cross_sectional_factor()`, 长 warmup 因子的训练窗被默认 252 截断. V2.12.1 round 5 同类修复遗漏的最后一个 sibling.
  - **/run 持久化完整上下文**: PortfolioStore 新增 `config` / `warnings` / `dates` 三列 (ALTER 迁移), `/run` endpoint 打包 market/optimizer/risk/index/cost 到 `run_config` dict, 历史对比图表可按真实交易日 time axis 对齐 (替代原 index-based 硬拼).
  - **routes/code.py dual-dict cleanup**: 新增 `_get_all_registries_for_kind()` 返回 dict list, delete 路由 + `/refresh` endpoint 统一清理名字键 + 全键两个 dict, 避免 Factor/CrossSectionalFactor/PortfolioStrategy 的 `_registry_by_key` 在删除/刷新时泄漏 zombie 类.
- V2.14: `_create_strategy` StrategyEnsemble 分支 (列表格式 sub_strategies, 递归子策略实例化), `/strategies` 追加 Ensemble 元信息 (is_ensemble: true), `ParamRangeRequest.values` 放宽为 `list[int|float|str|bool]`, candidates.py 搜索支持 bool/str 参数
