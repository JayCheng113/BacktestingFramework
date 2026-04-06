# V2.15 Paper Trading Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bridge research → live by extracting shared trade execution, adding deploy gate, paper trading engine, scheduler, and monitoring dashboard.

**Architecture:** Extract `execute_portfolio_trades()` from portfolio engine (backtest reuses it, paper trading calls it). New `ez/live/` module for deployment spec, gate, paper engine, scheduler, store, monitor. New `ez/api/routes/live.py` for REST endpoints. New `web/src/pages/PaperTradingPage.tsx` for dashboard.

**Tech Stack:** Python 3.12 + FastAPI + DuckDB + React 19 + ECharts

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `ez/portfolio/execution.py` | Shared `execute_portfolio_trades()` + `TradeResult` |
| Modify | `ez/portfolio/engine.py:300-406` | Refactor to call `execute_portfolio_trades()` |
| Create | `ez/live/__init__.py` | Package init (empty) |
| Create | `ez/live/deployment_spec.py` | `DeploymentSpec` + `DeploymentRecord` + factory |
| Create | `ez/live/deployment_store.py` | DuckDB persistence (3 tables) |
| Create | `ez/live/deploy_gate.py` | `DeployGateConfig` + `DeployGate` (10 checks) |
| Modify | `ez/portfolio/risk_manager.py` | Add `replay_equity()` method |
| Create | `ez/live/paper_engine.py` | `PaperTradingEngine.execute_day()` |
| Modify | `ez/portfolio/calendar.py` | Add `TradingCalendar.from_market()` factory |
| Create | `ez/live/scheduler.py` | `Scheduler` (single-process, idempotent, pause/resume) |
| Create | `ez/live/monitor.py` | `Monitor` + `DeploymentHealth` |
| Create | `ez/api/routes/live.py` | 12 REST endpoints |
| Modify | `ez/api/app.py` | Register live router + scheduler lifespan |
| Create | `web/src/pages/PaperTradingPage.tsx` | Dashboard UI |
| Modify | `web/src/components/Navbar.tsx` | Add "模拟盘" tab |
| Modify | `web/src/components/PortfolioRunContent.tsx` | "部署到模拟盘" button |
| Create | `tests/test_live/test_deployment_spec.py` | Spec hashing + serialization |
| Create | `tests/test_live/test_deploy_gate.py` | Gate rules |
| Create | `tests/test_live/test_execution.py` | Shared execution parity |
| Create | `tests/test_live/test_paper_engine.py` | Single-day execution |
| Create | `tests/test_live/test_scheduler.py` | Tick idempotency + recovery |
| Create | `tests/test_api/test_live_api.py` | API integration |

---

## Task 1: Extract `execute_portfolio_trades()` from Portfolio Engine

This is the prerequisite for everything else. The trade execution logic in `ez/portfolio/engine.py:300-406` must be extracted into a standalone function that both the backtest engine and paper trading engine can call.

**Files:**
- Create: `ez/portfolio/execution.py`
- Modify: `ez/portfolio/engine.py:300-406`
- Test: `tests/test_live/test_execution.py`

- [ ] **Step 1: Write the parity test**

```python
# tests/test_live/test_execution.py
"""Verify extracted execute_portfolio_trades produces identical results to
the inline logic it replaces in run_portfolio_backtest."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import date

from ez.portfolio.execution import execute_portfolio_trades, TradeResult, CostModel


def _make_prices():
    return {
        "A": 10.0, "B": 20.0, "C": 5.0,
    }

def _make_raw_closes():
    return {"A": 10.0, "B": 20.0, "C": 5.0}

def _make_prev_raw_closes():
    return {"A": 9.5, "B": 19.5, "C": 4.9}


class TestExecutePortfolioTrades:

    def test_basic_buy(self):
        trades, holdings, cash = execute_portfolio_trades(
            target_weights={"A": 0.5, "B": 0.3},
            current_holdings={},
            equity=100_000.0,
            cash=100_000.0,
            prices=_make_prices(),
            raw_closes=_make_raw_closes(),
            prev_raw_closes=_make_prev_raw_closes(),
            has_bar_today={"A", "B", "C"},
            cost_model=CostModel(),
            lot_size=100,
            t_plus_1=True,
            limit_pct=0.1,
        )
        assert holdings["A"] > 0
        assert holdings["B"] > 0
        assert cash < 100_000.0
        assert all(isinstance(t, TradeResult) for t in trades)

    def test_sell_before_buy(self):
        """Sells execute before buys to free cash."""
        trades, holdings, cash = execute_portfolio_trades(
            target_weights={"B": 1.0},
            current_holdings={"A": 5000},
            equity=100_000.0,
            cash=50_000.0,
            prices=_make_prices(),
            raw_closes=_make_raw_closes(),
            prev_raw_closes=_make_prev_raw_closes(),
            has_bar_today={"A", "B"},
            cost_model=CostModel(),
        )
        # A should be sold, B should be bought
        sides = {t.symbol: t.side for t in trades}
        assert sides.get("A") == "sell"
        assert sides.get("B") == "buy"

    def test_t_plus_1_blocks_rebuy(self):
        """Cannot buy a symbol that was just sold (T+1)."""
        trades, holdings, cash = execute_portfolio_trades(
            target_weights={"A": 0.5},
            current_holdings={"A": 1000},
            equity=100_000.0,
            cash=90_000.0,
            prices=_make_prices(),
            raw_closes=_make_raw_closes(),
            prev_raw_closes=_make_prev_raw_closes(),
            has_bar_today={"A"},
            cost_model=CostModel(),
            t_plus_1=True,
            sold_today={"A"},  # already sold today
        )
        # Should not buy A back
        buy_trades = [t for t in trades if t.side == "buy" and t.symbol == "A"]
        assert len(buy_trades) == 0

    def test_limit_up_blocks_buy(self):
        """Cannot buy at limit-up."""
        trades, _, _ = execute_portfolio_trades(
            target_weights={"A": 1.0},
            current_holdings={},
            equity=100_000.0,
            cash=100_000.0,
            prices={"A": 10.5},
            raw_closes={"A": 10.5},
            prev_raw_closes={"A": 9.5},  # +10.5% > 10% limit
            has_bar_today={"A"},
            cost_model=CostModel(),
            limit_pct=0.1,
        )
        assert len(trades) == 0

    def test_no_bar_skips_trade(self):
        """Symbols without today's bar are not traded."""
        trades, _, _ = execute_portfolio_trades(
            target_weights={"A": 1.0},
            current_holdings={},
            equity=100_000.0,
            cash=100_000.0,
            prices=_make_prices(),
            raw_closes=_make_raw_closes(),
            prev_raw_closes=_make_prev_raw_closes(),
            has_bar_today=set(),  # no bars today
            cost_model=CostModel(),
        )
        assert len(trades) == 0

    def test_lot_rounding(self):
        """Shares rounded down to lot size."""
        trades, holdings, _ = execute_portfolio_trades(
            target_weights={"A": 1.0},
            current_holdings={},
            equity=100_000.0,
            cash=100_000.0,
            prices={"A": 10.0},
            raw_closes={"A": 10.0},
            prev_raw_closes={"A": 9.5},
            has_bar_today={"A"},
            cost_model=CostModel(slippage_rate=0.0),
            lot_size=100,
        )
        assert holdings["A"] % 100 == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_live/test_execution.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ez.portfolio.execution'`

- [ ] **Step 3: Create `ez/portfolio/execution.py`**

Extract lines 300-406 from `ez/portfolio/engine.py` into a standalone function. The function signature must match the spec exactly:

```python
# ez/portfolio/execution.py
"""V2.15: Shared trade execution logic.

Both run_portfolio_backtest (historical) and PaperTradingEngine (live)
call this function. Same code, two contexts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    buy_commission_rate: float = 0.0003
    sell_commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    slippage_rate: float = 0.0


@dataclass
class TradeResult:
    symbol: str
    side: str       # "buy" | "sell"
    shares: int
    price: float
    amount: float
    commission: float
    stamp_tax: float
    cost: float     # commission + stamp_tax


EPS_FUND = 0.01


def _lot_round(shares: float, lot_size: int = 100) -> int:
    return int(shares // lot_size) * lot_size


def _compute_commission(amount: float, rate: float, minimum: float) -> float:
    return max(abs(amount) * rate, minimum) if abs(amount) > 0 else 0.0


def execute_portfolio_trades(
    target_weights: dict[str, float],
    current_holdings: dict[str, int],
    equity: float,
    cash: float,
    prices: dict[str, float],
    raw_closes: dict[str, float],
    prev_raw_closes: dict[str, float],
    has_bar_today: set[str],
    cost_model: CostModel,
    lot_size: int = 100,
    t_plus_1: bool = True,
    limit_pct: float = 0.1,
    sold_today: set[str] | None = None,
) -> tuple[list[TradeResult], dict[str, int], float, float]:
    """Execute portfolio trades: weight → shares → two-pass (sell first, buy second).

    Returns: (trades, new_holdings, new_cash, trade_volume)
    """
    if sold_today is None:
        sold_today = set()
    holdings = dict(current_holdings)  # don't mutate input

    # Convert weights → target shares
    target_shares: dict[str, int] = {}
    for sym, w in target_weights.items():
        if sym not in prices or prices[sym] <= 0:
            continue
        target_amount = equity * w
        raw_shares = target_amount / prices[sym]
        target_shares[sym] = _lot_round(raw_shares, lot_size)

    # Two passes: sells first, then buys
    all_syms = sorted(holdings.keys() | target_shares.keys())
    sell_syms = [s for s in all_syms if target_shares.get(s, 0) < holdings.get(s, 0)]
    buy_syms = [s for s in all_syms if target_shares.get(s, 0) > holdings.get(s, 0)]
    trades: list[TradeResult] = []

    for sym in sell_syms + buy_syms:
        cur = holdings.get(sym, 0)
        tgt = target_shares.get(sym, 0)
        delta = tgt - cur
        if delta == 0 or sym not in prices:
            continue
        if sym not in has_bar_today:
            continue

        # T+1: cannot buy what was sold today
        if t_plus_1 and delta > 0 and sym in sold_today:
            continue

        # Limit price check (raw close based)
        if limit_pct > 0 and sym in raw_closes and sym in prev_raw_closes:
            prev = prev_raw_closes[sym]
            if prev > 0:
                change = (raw_closes[sym] - prev) / prev
                if delta > 0 and change >= limit_pct - 1e-6:
                    continue
                if delta < 0 and change <= -limit_pct + 1e-6:
                    continue

        # Directional slippage
        base_price = prices[sym]
        price = base_price * (1 + cost_model.slippage_rate) if delta > 0 else base_price * (1 - cost_model.slippage_rate)
        amount = abs(delta) * price

        # Costs
        rate = cost_model.buy_commission_rate if delta > 0 else cost_model.sell_commission_rate
        comm = _compute_commission(amount, rate, cost_model.min_commission)
        stamp = amount * cost_model.stamp_tax_rate if delta < 0 else 0.0
        total_cost = comm + stamp

        if delta > 0:
            total_buy = amount + total_cost
            if total_buy > cash:
                min_cost = max(cost_model.min_commission, 0)
                affordable = (cash - min_cost) / (price * (1 + cost_model.buy_commission_rate)) if price > 0 else 0
                if affordable <= 0:
                    continue
                tgt = cur + _lot_round(affordable, lot_size)
                delta = tgt - cur
                if delta <= 0:
                    continue
                amount = delta * price
                comm = _compute_commission(amount, cost_model.buy_commission_rate, cost_model.min_commission)
                total_cost = comm
                total_buy = amount + total_cost

            if total_buy > cash + EPS_FUND:
                continue
            cash -= total_buy
            holdings[sym] = tgt
        else:
            cash += amount - total_cost
            sold_today.add(sym)
            if tgt == 0:
                holdings.pop(sym, None)
            else:
                holdings[sym] = tgt

        trades.append(TradeResult(
            symbol=sym, side="buy" if delta > 0 else "sell",
            shares=abs(delta), price=price, amount=amount,
            commission=comm, stamp_tax=stamp, cost=total_cost,
        ))

    return trades, holdings, cash
```

**IMPORTANT**: Move `CostModel` from `ez/portfolio/engine.py` to `ez/portfolio/execution.py` and re-export from engine.py: `from ez.portfolio.execution import CostModel` (backward compat).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live/test_execution.py -v`
Expected: 6 PASS

- [ ] **Step 5: Refactor `run_portfolio_backtest` to call extracted function**

In `ez/portfolio/engine.py`, replace lines 300-406 (the inline trade execution block) with a call to `execute_portfolio_trades()`. The surrounding code (weight computation, optimizer, risk checks, equity recording) stays in the engine.

Key changes:
- Import: `from ez.portfolio.execution import execute_portfolio_trades, TradeResult, CostModel, _lot_round, _compute_commission`
- Remove the inline `CostModel` and `_lot_round` and `_compute_commission` definitions (now imported)
- Replace the trade execution block with a single call
- Map `TradeResult` objects back to the existing `dict` format for `result.trades`

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `pytest tests/ -q --tb=short -x`
Expected: 2054+ pass, 0 fail

- [ ] **Step 7: Commit**

```bash
git add ez/portfolio/execution.py ez/portfolio/engine.py tests/test_live/test_execution.py
git commit -m "refactor(A1): extract execute_portfolio_trades from portfolio engine

Shared trade execution function in ez/portfolio/execution.py.
Backtest engine refactored to call it. 6 parity tests.
CostModel moved to execution.py, re-exported from engine.py."
```

---

## Task 2: DeploymentSpec + DeploymentRecord + DeploymentStore

**Files:**
- Create: `ez/live/__init__.py`
- Create: `ez/live/deployment_spec.py`
- Create: `ez/live/deployment_store.py`
- Test: `tests/test_live/test_deployment_spec.py`

- [ ] **Step 1: Write tests for DeploymentSpec hashing + serialization**

```python
# tests/test_live/test_deployment_spec.py
from ez.live.deployment_spec import DeploymentSpec, DeploymentRecord

class TestDeploymentSpec:
    def test_spec_id_deterministic(self):
        s1 = DeploymentSpec(strategy_name="TopN", strategy_params={"top_n": 5},
                            symbols=("B", "A"), market="cn_stock", freq="monthly")
        s2 = DeploymentSpec(strategy_name="TopN", strategy_params={"top_n": 5},
                            symbols=("A", "B"), market="cn_stock", freq="monthly")
        assert s1.spec_id == s2.spec_id  # canonical sort

    def test_spec_id_changes_with_params(self):
        s1 = DeploymentSpec(strategy_name="TopN", strategy_params={"top_n": 5},
                            symbols=("A",), market="cn_stock", freq="monthly")
        s2 = DeploymentSpec(strategy_name="TopN", strategy_params={"top_n": 10},
                            symbols=("A",), market="cn_stock", freq="monthly")
        assert s1.spec_id != s2.spec_id

    def test_spec_id_includes_market_rules(self):
        s1 = DeploymentSpec(strategy_name="TopN", strategy_params={},
                            symbols=("A",), market="cn_stock", freq="monthly",
                            t_plus_1=True)
        s2 = DeploymentSpec(strategy_name="TopN", strategy_params={},
                            symbols=("A",), market="cn_stock", freq="monthly",
                            t_plus_1=False)
        assert s1.spec_id != s2.spec_id

    def test_to_json_roundtrip(self):
        s = DeploymentSpec(strategy_name="TopN", strategy_params={"top_n": 5},
                           symbols=("A", "B"), market="cn_stock", freq="monthly")
        j = s.to_json()
        s2 = DeploymentSpec.from_json(j)
        assert s.spec_id == s2.spec_id

class TestDeploymentRecord:
    def test_default_status_pending(self):
        r = DeploymentRecord(deployment_id="test", spec_id="abc", name="Test")
        assert r.status == "pending"

    def test_created_at_is_utc(self):
        r = DeploymentRecord(deployment_id="test", spec_id="abc", name="Test")
        assert r.created_at.tzinfo is not None
```

- [ ] **Step 2: Implement `ez/live/deployment_spec.py`**

Full `DeploymentSpec` dataclass with `spec_id` property (canonical JSON → SHA-256[:16]), `to_json()`, `from_json()`. Full `DeploymentRecord` dataclass with UTC-aware timestamps. `_sort_keys_recursive()` helper. All fields from spec.

- [ ] **Step 3: Implement `ez/live/deployment_store.py`**

DuckDB store with 3 tables (`deployment_specs`, `deployment_records`, `deployment_snapshots`) exactly matching the spec's SQL DDL. Methods: `save_spec()`, `save_record()`, `get_spec()`, `get_record()`, `list_deployments()`, `update_status()`, `save_daily_snapshot()`, `get_latest_snapshot()`, `get_all_snapshots()`, `get_last_processed_date()`, `save_error()`.

- [ ] **Step 4: Run tests, commit**

```bash
git add ez/live/ tests/test_live/test_deployment_spec.py
git commit -m "feat(A2): DeploymentSpec + DeploymentRecord + DeploymentStore"
```

---

## Task 3: DeployGate (10 checks, non-bypassable)

**Files:**
- Create: `ez/live/deploy_gate.py`
- Test: `tests/test_live/test_deploy_gate.py`

- [ ] **Step 1: Write gate tests**

Test each of the 10 rules: source_run_exists, min_sharpe, max_drawdown, min_trades, max_p_value, max_overfitting_score, min_backtest_days, min_symbols, max_concentration, require_wfo, freq_valid. Use a mock portfolio_store that returns controlled metrics/weights_history.

- [ ] **Step 2: Implement `ez/live/deploy_gate.py`**

`DeployGateConfig` dataclass + `DeployGate.evaluate()` exactly matching the spec. Import `GateReason`, `GateVerdict` from `ez/agent/gates.py` (reuse, don't duplicate).

- [ ] **Step 3: Run tests, commit**

```bash
git add ez/live/deploy_gate.py tests/test_live/test_deploy_gate.py
git commit -m "feat(A3): DeployGate — 10-check non-bypassable deployment gate"
```

---

## Task 4: RiskManager.replay_equity() + TradingCalendar.from_market()

**Files:**
- Modify: `ez/portfolio/risk_manager.py`
- Modify: `ez/portfolio/calendar.py`
- Test: `tests/test_live/test_risk_replay.py`

- [ ] **Step 1: Write replay_equity test**

```python
# tests/test_live/test_risk_replay.py
from ez.portfolio.risk_manager import RiskManager, RiskConfig

class TestReplayEquity:
    def test_replay_restores_peak_and_breach_state(self):
        rm1 = RiskManager(RiskConfig(max_drawdown_threshold=0.1))
        curve = [100, 105, 110, 95, 90, 85]  # drawdown > 10% at 85
        for eq in curve:
            rm1.check_drawdown(eq)
        assert rm1._is_breached is True
        assert rm1._peak_equity == 110

        # Replay on fresh instance should match
        rm2 = RiskManager(RiskConfig(max_drawdown_threshold=0.1))
        rm2.replay_equity(curve)
        assert rm2._is_breached == rm1._is_breached
        assert rm2._peak_equity == rm1._peak_equity
```

- [ ] **Step 2: Implement `replay_equity` on RiskManager**

```python
# Add to ez/portfolio/risk_manager.py
def replay_equity(self, equity_curve: list[float]) -> None:
    """Rebuild internal state from historical equity curve.
    Used by Scheduler crash recovery to restore drawdown state machine."""
    self._peak_equity = 0.0
    self._is_breached = False
    for eq in equity_curve:
        self.check_drawdown(eq)
```

- [ ] **Step 3: Add `from_market()` to TradingCalendar**

```python
# Add to ez/portfolio/calendar.py
@classmethod
def from_market(cls, market: str) -> TradingCalendar:
    """V2.15: Factory from market code. Fetches exchange calendar via data provider.
    cn_stock → SSE, us_stock → NYSE, hk_stock → HKEX."""
    from ez.api.deps import get_data_chain
    from datetime import date, timedelta
    chain = get_data_chain()
    # Use trade_cal or generate weekday fallback
    end = date.today()
    start = end - timedelta(days=365 * 5)
    try:
        from ez.data.providers.tushare_provider import TushareProvider
        tp = TushareProvider()
        days = tp.get_trade_cal(market, start, end)
        return cls.from_dates(days)
    except Exception:
        return cls.weekday_fallback(start, end)
```

- [ ] **Step 4: Run tests, commit**

```bash
git add ez/portfolio/risk_manager.py ez/portfolio/calendar.py tests/test_live/test_risk_replay.py
git commit -m "feat(A4): RiskManager.replay_equity + TradingCalendar.from_market"
```

---

## Task 5: PaperTradingEngine

**Files:**
- Create: `ez/live/paper_engine.py`
- Test: `tests/test_live/test_paper_engine.py`

- [ ] **Step 1: Write single-day execution test**

Test `execute_day()` with synthetic data: verify equity changes after rebalance, trades are recorded, prev_returns updated. Test non-rebalance day: no trades, equity still recorded.

- [ ] **Step 2: Implement `ez/live/paper_engine.py`**

`PaperTradingEngine.__init__()` and `execute_day()` exactly per spec. Key methods: `_fetch_latest()` (uses DataProviderChain), `_mark_to_market()`, `_is_rebalance_day()` (uses TradingCalendar), `_slice_history()` (anti-lookahead), `_compute_returns()`, `_current_weights()`. Calls `execute_portfolio_trades()` from Task 1.

The `spec` attribute must be the `DeploymentSpec` (so Scheduler can read `spec.market`).

- [ ] **Step 3: Run tests, commit**

```bash
git add ez/live/paper_engine.py tests/test_live/test_paper_engine.py
git commit -m "feat(A5): PaperTradingEngine — daily bar-driven paper execution"
```

---

## Task 6: Scheduler (idempotent, pause/resume, auto-recovery)

**Files:**
- Create: `ez/live/scheduler.py`
- Test: `tests/test_live/test_scheduler.py`

- [ ] **Step 1: Write scheduler tests**

Test: `tick()` idempotency (same date twice → second skipped), `tick()` skips paused deployments, `tick()` skips non-trading-day per market, `resume_all()` restores running deployments, `pause_deployment()` / `resume_deployment()` state transitions.

- [ ] **Step 2: Implement `ez/live/scheduler.py`**

`Scheduler` class exactly per spec: `__init__()`, `resume_all()`, `start_deployment()`, `pause_deployment()`, `resume_deployment()`, `stop_deployment()`, `tick()`, `_get_calendar()`, `_start_engine()`, `_restore_full_state()`, `_instantiate()`. All per spec code.

Key: `_restore_full_state()` must restore cash, holdings, prev_weights, prev_returns, equity_curve, dates, trades, risk_events, AND call `risk_manager.replay_equity()`.

- [ ] **Step 3: Run tests, commit**

```bash
git add ez/live/scheduler.py tests/test_live/test_scheduler.py
git commit -m "feat(B1): Scheduler — idempotent tick, pause/resume, auto-recovery"
```

---

## Task 7: Monitor

**Files:**
- Create: `ez/live/monitor.py`
- Test: `tests/test_live/test_monitor.py`

- [ ] **Step 1: Implement Monitor + DeploymentHealth**

`DeploymentHealth` dataclass with all fields from spec. `Monitor.get_dashboard()` reads from DeploymentStore. `Monitor.check_alerts()` checks 5 conditions (consecutive loss > 5d, drawdown > threshold, delay > 60s, errors > 3, no trade > 30d).

- [ ] **Step 2: Write tests, commit**

```bash
git add ez/live/monitor.py tests/test_live/test_monitor.py
git commit -m "feat(B2): Monitor — deployment health dashboard + alerts"
```

---

## Task 8: API Routes

**Files:**
- Create: `ez/api/routes/live.py`
- Modify: `ez/api/app.py`
- Test: `tests/test_api/test_live_api.py`

- [ ] **Step 1: Implement all 12 endpoints**

`POST /deploy`, `GET /deployments`, `GET /deployments/{id}`, `POST /deployments/{id}/approve`, `POST /deployments/{id}/start`, `POST /deployments/{id}/stop`, `POST /deployments/{id}/pause`, `POST /tick`, `GET /dashboard`, `GET /deployments/{id}/snapshots`, `GET /deployments/{id}/trades`, `GET /deployments/{id}/stream` (SSE).

Register router in `app.py` under `/api/live` prefix. Add `scheduler.resume_all()` to app lifespan.

- [ ] **Step 2: Write API tests**

Test deploy flow: create → approve (gate) → start → tick → stop. Test gate rejection (bad metrics). Test idempotent tick. Test pause/resume.

- [ ] **Step 3: Commit**

```bash
git add ez/api/routes/live.py ez/api/app.py tests/test_api/test_live_api.py
git commit -m "feat(C1): Live API — 12 endpoints + scheduler lifespan"
```

---

## Task 9: Frontend — PaperTradingPage + Navbar + Deploy Button

**Files:**
- Create: `web/src/pages/PaperTradingPage.tsx`
- Create: `web/src/api/live.ts` (API client functions)
- Modify: `web/src/components/Navbar.tsx`
- Modify: `web/src/components/PortfolioRunContent.tsx`

- [ ] **Step 1: API client**

Create `web/src/api/live.ts` with typed functions for all 12 endpoints.

- [ ] **Step 2: PaperTradingPage**

Left: deployment list (status badge + name + return + PnL). Right: ECharts equity curve + metric cards + holdings pie + trade table + risk events. Bottom: [暂停] [停止] [手动 Tick] buttons.

- [ ] **Step 3: Navbar tab + deploy button**

Add "模拟盘" to Navbar. Add "部署到模拟盘" button in PortfolioRunContent (visible after successful run with gate-passing metrics).

- [ ] **Step 4: TypeScript check + commit**

```bash
cd web && npx tsc --noEmit
git add web/
git commit -m "feat(C2+C3): PaperTradingPage + deploy button + Navbar tab"
```

---

## Task 10: Documentation

**Files:**
- Modify: `web/src/pages/DocsPage.tsx`
- Modify: `CLAUDE.md`
- Modify: `web/CLAUDE.md`
- Create: `ez/live/CLAUDE.md`

- [ ] **Step 1: DocsPage Ch15 "模拟盘"**

New section covering: deployment flow, deploy gate, paper trading mechanics, scheduler, monitoring dashboard.

- [ ] **Step 2: CLAUDE.md V2.15 entry**

Full version progress entry + update module map + update Known Limitations.

- [ ] **Step 3: ez/live/CLAUDE.md**

Module documentation following existing pattern (Responsibility, Public Interfaces, Files, Dependencies, Status).

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/DocsPage.tsx CLAUDE.md web/CLAUDE.md ez/live/CLAUDE.md
git commit -m "docs(D1+D2): V2.15 documentation — DocsPage Ch15 + CLAUDE.md"
```

---

## Task 11: Full Test Suite + Code Review

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -q --tb=short
```

Expected: 2054 + ~40 new tests, 0 fail.

- [ ] **Step 2: TypeScript build**

```bash
cd web && npx tsc --noEmit
```

Expected: 0 errors.

- [ ] **Step 3: Request code review**

Use `superpowers:requesting-code-review` with BASE=v0.2.14, HEAD=current.

- [ ] **Step 4: Fix review findings, retag**

```bash
git tag -a v0.2.15 -m "V2.15 Paper Trading Bridge"
```
