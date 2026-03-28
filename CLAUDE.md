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
ez/types.py -> ez/data/ -> ez/factor/ -> ez/strategy/ -> ez/backtest/ -> ez/api/ -> web/
```

## Core Files (DO NOT MODIFY without proposal in docs/core-changes/)
ez/types.py, ez/errors.py, ez/config.py, ez/data/provider.py, ez/data/validator.py,
ez/data/store.py, ez/factor/base.py, ez/factor/evaluator.py, ez/strategy/base.py,
ez/strategy/loader.py, ez/backtest/engine.py, ez/backtest/portfolio.py,
ez/backtest/metrics.py, ez/backtest/walk_forward.py, ez/backtest/significance.py

## Adding Extensions (no Core changes needed)
| Type | Directory | Base Class | Test |
|------|-----------|------------|------|
| Data source | ez/data/providers/ | DataProvider | pytest tests/test_data/test_provider_contract.py |
| Factor | ez/factor/builtin/ | Factor | pytest tests/test_factor/test_factor_contract.py |
| Strategy | strategies/ or ez/strategy/builtin/ | Strategy | pytest tests/test_strategy/ |

## Quick Commands
```bash
./scripts/start.sh          # Start backend (8000) + frontend (3000)
./scripts/stop.sh            # Stop all
pytest tests/test_smoke.py   # Smoke tests (after every change)
pytest tests/                # Full test suite
```

## Spec
docs/superpowers/specs/2026-03-27-ez-trading-design.md
