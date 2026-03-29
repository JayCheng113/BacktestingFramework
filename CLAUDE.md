# ez-trading

Agent-Native quantitative trading platform. Human researchers and AI agents are both
first-class citizens — same pipeline, same gates, same audit trail.
Python 3.12+ / FastAPI / DuckDB / React 19 / ECharts / C++ (nanobind).
Version: 0.2.6 | Tests: 795 | C++ acceleration: up to 7.9x

## Architecture Docs (MUST READ before major changes)
- [System Architecture](docs/architecture/system-architecture.md) — 7-layer design, gates (Research/Deploy/Runtime + PreTradeRisk), dual state machine
- [Engineering Governance](docs/architecture/governance.md) — thin core, lifecycle labels, version discipline
- [V2.3+ Roadmap](docs/core-changes/v2.3-roadmap.md) — detailed per-version plan with exit gates

**Note**: ResearchGate implemented in V2.4 (ez/agent/gates.py). MarketRules in V2.6 (ez/core/market_rules.py). Deploy/Runtime gates planned for V2.7+.

## Module Map
- `ez/core/` — Computational primitives: matcher, ts_ops (C++ accelerated) [CLAUDE.md](ez/core/CLAUDE.md)
- `ez/data/` — Data ingestion, validation, caching [CLAUDE.md](ez/data/CLAUDE.md)
- `ez/factor/` — Factor computation + IC evaluation [CLAUDE.md](ez/factor/CLAUDE.md)
- `ez/strategy/` — Strategy framework, auto-registration [CLAUDE.md](ez/strategy/CLAUDE.md)
- `ez/backtest/` — Backtest engine, Walk-Forward, significance [CLAUDE.md](ez/backtest/CLAUDE.md)
- `ez/api/` — FastAPI REST endpoints [CLAUDE.md](ez/api/CLAUDE.md)
- `web/` — React frontend dashboard [CLAUDE.md](web/CLAUDE.md)
- `ez/agent/` — Agent loop: RunSpec, Runner, Gates, Report, ExperimentStore, CandidateSearch, BatchRunner, Prefilter [CLAUDE.md](ez/agent/CLAUDE.md)
- `ez/live/` — Deploy Gate, OMS, Broker (V2.6+, planned)
- `ez/ops/` — Scheduling, monitoring, audit (V3.2+, planned)

## Dependency Flow
```
ez/types.py -> ez/data/ -> ez/factor/ -> ez/strategy/ -> ez/backtest/ -> ez/api/ -> web/
                            ↑ ts_ops                      ↑ matcher
                            └────────── ez/core/ ─────────┘  (leaf, no deps)

ez/agent/ consumes backtest/core interfaces (V2.4). Future: ez/live/, ez/ops/ — never modify core.
```

## Core Files (DO NOT MODIFY without proposal in docs/core-changes/)
ez/types.py, ez/errors.py, ez/config.py, ez/core/matcher.py, ez/core/ts_ops.py,
ez/data/provider.py, ez/data/validator.py, ez/data/store.py, ez/factor/base.py,
ez/factor/evaluator.py, ez/strategy/base.py, ez/strategy/loader.py,
ez/backtest/engine.py, ez/backtest/portfolio.py, ez/backtest/metrics.py,
ez/backtest/walk_forward.py, ez/backtest/significance.py

## Adding Extensions (no Core changes needed)
| Type | Directory | Base Class | Test |
|------|-----------|------------|------|
| Data source | ez/data/providers/ | DataProvider | pytest tests/test_data/test_provider_contract.py |
| Factor | ez/factor/builtin/ | Factor | pytest tests/test_factor/test_factor_contract.py |
| Strategy | strategies/ or ez/strategy/builtin/ | Strategy | pytest tests/test_strategy/ |
| Matcher | ez/core/matcher.py | Matcher | pytest tests/test_core/test_matcher_contract.py |

## Quick Commands
```bash
./scripts/start.sh          # Start backend (8000) + frontend (3000)
./scripts/stop.sh            # Stop all
pytest tests/                # Full test suite (805 collected, 795 pass, 10 skip). 停掉后端再跑: ./scripts/stop.sh
python scripts/benchmark.py  # Performance baseline
pip install -e . --no-build-isolation  # Rebuild C++ extension
```

## Mandatory: Code Review After Every Version/Feature
Use `/superpowers:requesting-code-review` or send to Codex for review.
No version tag without review pass. No push without critical issues resolved.

## Current Version Progress
- V1.0-V1.1: Full Python pipeline (data → factor → strategy → backtest → API → web)
- V2.0: Matcher + ts_ops extraction into ez/core/
- V2.1: C++ nanobind acceleration (up to 7.9x)
- V2.2: SlippageMatcher + user-configurable trading costs
- **V2.3**: Correctness hardening — accounting invariants (173 tests), C++/Python dual-path parity (109 tests), rolling_std Welford O(n) (5.2x vs Python), architecture gate tests (47 tests), ewm_mean span=1 NaN fix
- **V2.4**: Agent Loop — RunSpec, Runner, ResearchGate (DD sign fix), Report, ExperimentStore, /experiments API, Experiments UI
- **V2.4.1**: Stability — PK-based idempotency (completed_specs), gate DD sign fix, NaN sanitization, JSON double-encoding fix, concurrent regression tests, 725 total tests
- **V2.5**: Scale — param grid/random search (144 specs in 4.7s), pre-filter engine, batch runner + ranking, new factors (VWAP/OBV/ATR), multi-period frontend, experiment delete/cleanup, DatePicker + Min/Max/Step UI, 779 total tests
- **V2.5 post-release fixes**: factor adj_close split-adjustment, delete FK consistency, int truncation rejection, timezone off-by-one, render perf (O(1) combos), Sortino formula, NaN price guard, pnl_pct cost_basis, significance constant signal, pct_change deprecation
- **V2.6**: MarketRules — T+1 (买入当天不可卖), 涨跌停 (10%/20%), 整手交易 (100股), MarketRulesMatcher 装饰器, engine on_bar 钩子, 前端开关, 795 total tests
- **Next: V2.6.1** — Stability
