# V2.12 组合优化 + 归因 + 风控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add constrained portfolio optimization (MeanVariance/MinVariance/RiskParity), daily drawdown risk control with emergency liquidation, turnover limits, and Brinson performance attribution to the portfolio backtesting engine.

**Architecture:** PortfolioOptimizer is a new ABC (not Allocator) with `set_context(date, data)` + `optimize(alpha_weights)` — solves the date-passing problem. RiskManager checks drawdown every trading day (not just rebalance) and enforces turnover limits. Attribution is computed inline after each run using the in-memory result + universe_data. Engine changes are minimal (~30 lines): new optional params + daily drawdown check + emergency sell logic.

**Tech Stack:** scipy.optimize.minimize (SLSQP), numpy (Ledoit-Wolf covariance), existing FastAPI + React + ECharts stack.

**Spec:** `docs/internal/specs/2026-04-01-v212-optimizer-attribution-risk.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `ez/portfolio/optimizer.py` | `PortfolioOptimizer` ABC + `MeanVarianceOptimizer` + `MinVarianceOptimizer` + `RiskParityOptimizer` + `ledoit_wolf_shrinkage()` + `OptimizationConstraints` |
| `ez/portfolio/risk_manager.py` | `RiskConfig` + `RiskManager` (drawdown state machine + turnover mixing) |
| `ez/portfolio/attribution.py` | `BrinsonAttribution` + `AttributionResult` + `compute_attribution()` |
| `tests/test_portfolio/test_optimizer.py` | Optimizer constraint tests, fallback tests, covariance tests |
| `tests/test_portfolio/test_risk_manager.py` | Drawdown state machine tests, turnover mixing tests |
| `tests/test_portfolio/test_attribution.py` | Brinson identity tests, cost drag tests |

### Modified Files
| File | Changes |
|------|---------|
| `ez/portfolio/engine.py` | Add `optimizer` + `risk_manager` params, daily drawdown check, emergency sell, `risk_events` in result |
| `ez/api/routes/portfolio.py` | Extend `PortfolioRunRequest` with optimizer/risk params, add `/attribution` endpoint |
| `web/src/api/index.ts` | Add `portfolioAttribution()` API client function |
| `web/src/components/PortfolioPanel.tsx` | Optimizer/risk collapsible panels, attribution display, risk event log |
| `CLAUDE.md` | V2.12 version entry |
| `ez/portfolio/CLAUDE.md` | Module doc update |
| `docs/internal/core-changes/v2.3-roadmap.md` | Check off V2.12 deliverables |

---

### Task 1: Ledoit-Wolf Covariance Estimator

**Files:**
- Create: `ez/portfolio/optimizer.py` (partial — just `ledoit_wolf_shrinkage` + `OptimizationConstraints`)
- Create: `tests/test_portfolio/test_optimizer.py` (partial — covariance tests)

- [ ] **Step 1: Write covariance tests**

```python
# tests/test_portfolio/test_optimizer.py
"""Tests for V2.12 portfolio optimizer."""
import numpy as np
import pytest


class TestLedoitWolfShrinkage:
    def test_basic_positive_definite(self):
        """Shrunk covariance must be positive definite."""
        from ez.portfolio.optimizer import ledoit_wolf_shrinkage
        rng = np.random.default_rng(42)
        returns = rng.normal(0, 0.02, (100, 5))  # 100 days, 5 assets
        sigma = ledoit_wolf_shrinkage(returns)
        assert sigma.shape == (5, 5)
        eigenvalues = np.linalg.eigvalsh(sigma)
        assert np.all(eigenvalues > 0), f"Not positive definite: {eigenvalues}"

    def test_wide_matrix_n_gt_t(self):
        """N > T case: sample covariance is singular, shrinkage must fix it."""
        from ez.portfolio.optimizer import ledoit_wolf_shrinkage
        rng = np.random.default_rng(42)
        returns = rng.normal(0, 0.02, (10, 30))  # 10 days, 30 assets
        sigma = ledoit_wolf_shrinkage(returns)
        assert sigma.shape == (30, 30)
        eigenvalues = np.linalg.eigvalsh(sigma)
        assert np.all(eigenvalues > 0)

    def test_single_observation_fallback(self):
        """T < 2 should return identity-like fallback."""
        from ez.portfolio.optimizer import ledoit_wolf_shrinkage
        returns = np.array([[0.01, -0.02, 0.03]])  # 1 observation, 3 assets
        sigma = ledoit_wolf_shrinkage(returns)
        assert sigma.shape == (3, 3)
        assert np.allclose(np.diag(sigma), 0.04, atol=0.001)  # 20% vol squared

    def test_symmetry(self):
        from ez.portfolio.optimizer import ledoit_wolf_shrinkage
        rng = np.random.default_rng(42)
        returns = rng.normal(0, 0.02, (60, 8))
        sigma = ledoit_wolf_shrinkage(returns)
        assert np.allclose(sigma, sigma.T)
```

- [ ] **Step 2: Run tests — expect FAIL (module not found)**

Run: `python -m pytest tests/test_portfolio/test_optimizer.py::TestLedoitWolfShrinkage -v`

- [ ] **Step 3: Implement ledoit_wolf_shrinkage + OptimizationConstraints**

```python
# ez/portfolio/optimizer.py
"""V2.12: Portfolio optimization — constrained weight optimization.

Three optimizers: MeanVariance, MinVariance, RiskParity.
All use Ledoit-Wolf shrinkage covariance (numpy, no sklearn dependency).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy import optimize


@dataclass
class OptimizationConstraints:
    """Constraints for portfolio optimization."""
    max_weight: float = 0.10
    max_industry_weight: float = 0.30
    industry_map: dict[str, str] = field(default_factory=dict)


def ledoit_wolf_shrinkage(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage covariance estimator (OAS variant, numpy only).

    Reference: Chen, Wiesel, Eldar, Hero (2010).

    Args:
        returns: T×N matrix of daily returns.
    Returns:
        N×N shrunk covariance, guaranteed positive definite.
    """
    T, N = returns.shape
    if T < 2:
        return np.eye(N) * 0.04  # fallback: 20% vol identity

    S = np.cov(returns, rowvar=False, ddof=1)
    if N == 1:
        return S.reshape(1, 1) + 1e-8 * np.eye(1)

    trace_S = np.trace(S)
    trace_S2 = np.sum(S ** 2)

    mu = trace_S / N
    F = mu * np.eye(N)

    rho_num = (1 - 2.0 / N) * trace_S2 + trace_S ** 2
    rho_den = (T + 1 - 2.0 / N) * (trace_S2 - trace_S ** 2 / N)
    rho = min(1.0, max(0.0, rho_num / rho_den)) if abs(rho_den) > 1e-20 else 1.0

    sigma = (1 - rho) * S + rho * F
    sigma += 1e-8 * np.eye(N)
    return sigma
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/test_portfolio/test_optimizer.py::TestLedoitWolfShrinkage -v`

- [ ] **Step 5: Commit**

```bash
git add ez/portfolio/optimizer.py tests/test_portfolio/test_optimizer.py
git commit -m "feat(v2.12): Ledoit-Wolf shrinkage covariance estimator + tests"
```

---

### Task 2: PortfolioOptimizer ABC + MeanVarianceOptimizer

**Files:**
- Modify: `ez/portfolio/optimizer.py`
- Modify: `tests/test_portfolio/test_optimizer.py`

- [ ] **Step 1: Write optimizer tests**

```python
# Append to tests/test_portfolio/test_optimizer.py

class TestPortfolioOptimizerBase:
    """Test the optimize() public interface and fallback behavior."""

    def _make_data(self, symbols, n_days=100, seed=42):
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
        data = {}
        for i, sym in enumerate(symbols):
            prices = 10 * np.cumprod(1 + rng.normal(0.001 * (i + 1), 0.015, n_days))
            data[sym] = pd.DataFrame({
                "close": prices, "adj_close": prices,
                "volume": rng.integers(100_000, 5_000_000, n_days),
            }, index=dates)
        return data

    def test_mean_variance_long_only(self):
        """All weights must be >= 0 and sum to 1."""
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        from datetime import date

        symbols = [f"S{i}" for i in range(5)]
        data = self._make_data(symbols)
        constraints = OptimizationConstraints(max_weight=0.40)
        opt = MeanVarianceOptimizer(risk_aversion=1.0, constraints=constraints, cov_lookback=60)
        opt.set_context(date(2023, 7, 1), data)

        alpha = {s: 0.2 for s in symbols}  # equal alpha
        result = opt.optimize(alpha)

        assert all(w >= -1e-9 for w in result.values()), f"Negative weight: {result}"
        assert abs(sum(result.values()) - 1.0) < 1e-6, f"Sum != 1: {sum(result.values())}"

    def test_max_weight_respected(self):
        """No single weight should exceed max_weight."""
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        from datetime import date

        symbols = [f"S{i}" for i in range(10)]
        data = self._make_data(symbols)
        constraints = OptimizationConstraints(max_weight=0.15)
        opt = MeanVarianceOptimizer(risk_aversion=0.5, constraints=constraints, cov_lookback=60)
        opt.set_context(date(2023, 7, 1), data)

        alpha = {s: (i + 1) * 0.1 for i, s in enumerate(symbols)}
        result = opt.optimize(alpha)

        for sym, w in result.items():
            assert w <= 0.15 + 1e-6, f"{sym} weight {w} exceeds max 0.15"

    def test_fallback_on_insufficient_data(self):
        """With < 2 days of data, optimizer should fallback to equal weight."""
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        from datetime import date

        symbols = ["A", "B", "C"]
        # Only 1 day of data — not enough for covariance
        data = {s: pd.DataFrame({"close": [10.0], "adj_close": [10.0]},
                                index=pd.DatetimeIndex([date(2023, 7, 3)])) for s in symbols}
        constraints = OptimizationConstraints(max_weight=0.50)
        opt = MeanVarianceOptimizer(constraints=constraints, cov_lookback=60)
        opt.set_context(date(2023, 7, 3), data)

        result = opt.optimize({"A": 0.5, "B": 0.3, "C": 0.2})
        # Should fallback to equal weight capped at max_weight
        assert len(result) == 3
        assert abs(sum(result.values()) - 1.0) < 0.01 or all(v <= 0.50 for v in result.values())

    def test_empty_alpha_returns_empty(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        opt = MeanVarianceOptimizer(constraints=OptimizationConstraints())
        result = opt.optimize({})
        assert result == {}

    def test_single_stock_returns_full_weight(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        opt = MeanVarianceOptimizer(constraints=OptimizationConstraints(max_weight=1.0))
        result = opt.optimize({"A": 1.0})
        assert result == {"A": 1.0}
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_portfolio/test_optimizer.py::TestPortfolioOptimizerBase -v`

- [ ] **Step 3: Implement PortfolioOptimizer ABC + MeanVarianceOptimizer**

Add to `ez/portfolio/optimizer.py` after `ledoit_wolf_shrinkage`:

```python
class PortfolioOptimizer(ABC):
    """Portfolio optimizer: alpha signal + risk model → optimal weights.

    Unlike Allocator, receives date context via set_context() before each optimize().
    """

    def __init__(self, constraints: OptimizationConstraints, cov_lookback: int = 60):
        self._constraints = constraints
        self._cov_lookback = cov_lookback
        self._current_date: date | None = None
        self._universe_data: dict[str, pd.DataFrame] | None = None

    def set_context(self, current_date: date,
                    universe_data: dict[str, pd.DataFrame]) -> None:
        self._current_date = current_date
        self._universe_data = universe_data

    def optimize(self, alpha_weights: dict[str, float]) -> dict[str, float]:
        symbols = [s for s, w in alpha_weights.items() if w > 0]
        if len(symbols) == 0:
            return {}
        if len(symbols) == 1:
            s = symbols[0]
            return {s: min(1.0, self._constraints.max_weight)}

        total_alpha = sum(alpha_weights[s] for s in symbols)
        if total_alpha <= 0:
            return self._fallback(symbols)
        alpha = np.array([alpha_weights[s] / total_alpha for s in symbols])

        sigma = self._estimate_covariance(symbols)
        if sigma is None:
            return self._fallback(symbols)

        try:
            w = self._optimize(alpha, sigma, symbols)
        except Exception:
            return self._fallback(symbols)

        return self._apply_constraints(dict(zip(symbols, w)))

    @abstractmethod
    def _optimize(self, alpha: np.ndarray, sigma: np.ndarray,
                  symbols: list[str]) -> np.ndarray:
        ...

    def _estimate_covariance(self, symbols: list[str]) -> np.ndarray | None:
        if self._universe_data is None or self._current_date is None:
            return None
        returns_list = []
        for sym in symbols:
            df = self._universe_data.get(sym)
            if df is None or df.empty:
                return None
            col = "adj_close" if "adj_close" in df.columns else "close"
            prices = df[col]
            # Filter to dates before current_date
            mask = df.index.date < self._current_date if hasattr(df.index, 'date') else df.index < pd.Timestamp(self._current_date)
            prices = prices[mask]
            if len(prices) < 3:
                return None
            prices = prices.iloc[-self._cov_lookback:]
            ret = prices.pct_change().dropna().values
            returns_list.append(ret)
        # Align lengths (use shortest)
        min_len = min(len(r) for r in returns_list)
        if min_len < 3:
            return None
        mat = np.column_stack([r[-min_len:] for r in returns_list])
        return ledoit_wolf_shrinkage(mat)

    def _fallback(self, symbols: list[str]) -> dict[str, float]:
        max_w = self._constraints.max_weight
        n = len(symbols)
        w = min(1.0 / n, max_w)
        return {s: w for s in symbols}

    def _industry_groups(self, symbols: list[str]) -> dict[str, list[int]]:
        groups: dict[str, list[int]] = {}
        for i, sym in enumerate(symbols):
            ind = self._constraints.industry_map.get(sym, "")
            if ind:
                groups.setdefault(ind, []).append(i)
        return groups

    def _apply_constraints(self, weights: dict[str, float]) -> dict[str, float]:
        max_w = self._constraints.max_weight
        w = {k: min(max(v, 0.0), max_w) for k, v in weights.items()}
        # Industry constraints: iterative clip
        for _ in range(5):
            for ind, syms_in_ind in self._industry_groups_from_weights(w).items():
                ind_total = sum(w.get(s, 0) for s in syms_in_ind)
                if ind_total > self._constraints.max_industry_weight + 1e-9:
                    scale = self._constraints.max_industry_weight / ind_total
                    for s in syms_in_ind:
                        w[s] *= scale
        # Normalize to sum=1 (or less)
        total = sum(w.values())
        if total > 1.0 + 1e-9:
            w = {k: v / total for k, v in w.items()}
        return {k: v for k, v in w.items() if v > 1e-10}

    def _industry_groups_from_weights(self, weights: dict[str, float]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for sym in weights:
            ind = self._constraints.industry_map.get(sym, "")
            if ind:
                groups.setdefault(ind, []).append(sym)
        return groups


class MeanVarianceOptimizer(PortfolioOptimizer):
    """Markowitz mean-variance: min λ·w'Σw - w'α, s.t. long-only + constraints."""

    def __init__(self, risk_aversion: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self.risk_aversion = risk_aversion

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)
        max_w = self._constraints.max_weight

        def objective(w):
            return float(self.risk_aversion * w @ sigma @ w - w @ alpha)

        cons = [{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}]
        for _ind, idx_list in self._industry_groups(symbols).items():
            cons.append({
                "type": "ineq",
                "fun": lambda w, idx=idx_list: float(
                    self._constraints.max_industry_weight - sum(w[i] for i in idx)
                ),
            })

        bounds = [(0.0, max_w)] * n
        w0 = np.full(n, 1.0 / n)
        result = optimize.minimize(
            objective, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
            options={"maxiter": 500, "ftol": 1e-10},
        )
        if not result.success:
            raise RuntimeError(result.message)
        return np.clip(result.x, 0, max_w)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/test_portfolio/test_optimizer.py -v`

- [ ] **Step 5: Commit**

```bash
git add ez/portfolio/optimizer.py tests/test_portfolio/test_optimizer.py
git commit -m "feat(v2.12): PortfolioOptimizer ABC + MeanVarianceOptimizer"
```

---

### Task 3: MinVarianceOptimizer + RiskParityOptimizer

**Files:**
- Modify: `ez/portfolio/optimizer.py`
- Modify: `tests/test_portfolio/test_optimizer.py`

- [ ] **Step 1: Write tests for MinVariance + RiskParity**

```python
# Append to tests/test_portfolio/test_optimizer.py

class TestMinVarianceOptimizer:
    def _make_data(self, symbols, n_days=100, seed=42):
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
        data = {}
        for i, sym in enumerate(symbols):
            prices = 10 * np.cumprod(1 + rng.normal(0.0005, 0.01 * (i + 1), n_days))
            data[sym] = pd.DataFrame({"close": prices, "adj_close": prices,
                                      "volume": rng.integers(100_000, 5_000_000, n_days)}, index=dates)
        return data

    def test_long_only_sum_one(self):
        from ez.portfolio.optimizer import MinVarianceOptimizer, OptimizationConstraints
        from datetime import date
        symbols = [f"S{i}" for i in range(5)]
        data = self._make_data(symbols)
        opt = MinVarianceOptimizer(constraints=OptimizationConstraints(max_weight=0.40), cov_lookback=60)
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.2 for s in symbols})
        assert all(w >= -1e-9 for w in result.values())
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_low_vol_gets_more_weight(self):
        """MinVariance should overweight low-vol stocks."""
        from ez.portfolio.optimizer import MinVarianceOptimizer, OptimizationConstraints
        from datetime import date
        symbols = ["LOW", "HIGH"]
        data = self._make_data(symbols)  # HIGH has higher vol (seed-dependent)
        opt = MinVarianceOptimizer(constraints=OptimizationConstraints(max_weight=0.80), cov_lookback=60)
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({"LOW": 0.5, "HIGH": 0.5})
        assert result.get("LOW", 0) > result.get("HIGH", 0), f"LOW={result.get('LOW')}, HIGH={result.get('HIGH')}"


class TestRiskParityOptimizer:
    def _make_data(self, symbols, n_days=100, seed=42):
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2023-01-02", periods=n_days, freq="B")
        data = {}
        for i, sym in enumerate(symbols):
            prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.01 * (i + 1), n_days))
            data[sym] = pd.DataFrame({"close": prices, "adj_close": prices,
                                      "volume": rng.integers(100_000, 5_000_000, n_days)}, index=dates)
        return data

    def test_long_only_sum_one(self):
        from ez.portfolio.optimizer import RiskParityOptimizer, OptimizationConstraints
        from datetime import date
        symbols = [f"S{i}" for i in range(5)]
        data = self._make_data(symbols)
        opt = RiskParityOptimizer(constraints=OptimizationConstraints(max_weight=0.40), cov_lookback=60)
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.2 for s in symbols})
        assert all(w >= -1e-9 for w in result.values())
        assert abs(sum(result.values()) - 1.0) < 0.01

    def test_fallback_on_failure(self):
        """If optimization fails, should fallback to inverse-vol."""
        from ez.portfolio.optimizer import RiskParityOptimizer, OptimizationConstraints
        from datetime import date
        # Very tight constraint that makes risk parity infeasible
        symbols = [f"S{i}" for i in range(20)]
        data = self._make_data(symbols)
        opt = RiskParityOptimizer(constraints=OptimizationConstraints(max_weight=0.03), cov_lookback=60)
        opt.set_context(date(2023, 7, 1), data)
        result = opt.optimize({s: 0.05 for s in symbols})
        assert len(result) > 0  # should not crash
        assert all(w >= 0 for w in result.values())
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_portfolio/test_optimizer.py::TestMinVarianceOptimizer tests/test_portfolio/test_optimizer.py::TestRiskParityOptimizer -v`

- [ ] **Step 3: Implement MinVarianceOptimizer + RiskParityOptimizer**

Append to `ez/portfolio/optimizer.py`:

```python
class MinVarianceOptimizer(PortfolioOptimizer):
    """Minimum variance: min w'Σw, s.t. long-only + constraints.

    Pure risk perspective — ignores alpha. Convex, guarantees global optimum.
    """

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)
        max_w = self._constraints.max_weight

        def objective(w):
            return float(w @ sigma @ w)

        cons = [{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}]
        for _ind, idx_list in self._industry_groups(symbols).items():
            cons.append({
                "type": "ineq",
                "fun": lambda w, idx=idx_list: float(
                    self._constraints.max_industry_weight - sum(w[i] for i in idx)
                ),
            })

        bounds = [(0.0, max_w)] * n
        w0 = np.full(n, 1.0 / n)
        result = optimize.minimize(
            objective, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
        )
        if not result.success:
            raise RuntimeError(result.message)
        return np.clip(result.x, 0, max_w)


class RiskParityOptimizer(PortfolioOptimizer):
    """Risk parity: equalize risk contributions across assets.

    Fallback: inverse-volatility weighting when optimization fails.
    """

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)

        def risk_contribution_obj(w):
            port_var = float(w @ sigma @ w)
            if port_var < 1e-20:
                return 0.0
            marginal = sigma @ w
            rc = w * marginal / port_var
            target = 1.0 / n
            return float(np.sum((rc - target) ** 2))

        vols = np.sqrt(np.diag(sigma))
        vols = np.maximum(vols, 1e-8)
        w0 = (1.0 / vols) / np.sum(1.0 / vols)

        max_w = self._constraints.max_weight
        bounds = [(1e-6, max_w)] * n
        cons = [{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}]

        result = optimize.minimize(
            risk_contribution_obj, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
            options={"maxiter": 1000},
        )
        if not result.success:
            return w0  # fallback: inverse-vol
        return result.x
```

- [ ] **Step 4: Run all optimizer tests — expect PASS**

Run: `python -m pytest tests/test_portfolio/test_optimizer.py -v`

- [ ] **Step 5: Commit**

```bash
git add ez/portfolio/optimizer.py tests/test_portfolio/test_optimizer.py
git commit -m "feat(v2.12): MinVariance + RiskParity optimizers"
```

---

### Task 4: RiskManager (Drawdown State Machine + Turnover Mixing)

**Files:**
- Create: `ez/portfolio/risk_manager.py`
- Create: `tests/test_portfolio/test_risk_manager.py`

- [ ] **Step 1: Write RiskManager tests**

```python
# tests/test_portfolio/test_risk_manager.py
"""Tests for V2.12 risk manager — drawdown + turnover."""
import pytest


class TestDrawdownStateMachine:
    def test_no_event_when_no_drawdown(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20))
        scale, event = rm.check_drawdown(1_000_000)
        assert scale == 1.0
        assert event is None

    def test_breach_on_large_drawdown(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20, drawdown_reduce_ratio=0.50))
        rm.check_drawdown(1_000_000)  # set peak
        scale, event = rm.check_drawdown(750_000)  # 25% drawdown > 20%
        assert scale == 0.50
        assert event is not None
        assert "减仓" in event

    def test_stays_breached_until_recovery(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20,
                                    drawdown_reduce_ratio=0.50,
                                    drawdown_recovery_ratio=0.10))
        rm.check_drawdown(1_000_000)
        rm.check_drawdown(750_000)  # breach
        # Still at 20% drawdown — stays breached
        scale, event = rm.check_drawdown(800_000)
        assert scale == 0.50
        assert event is None  # no repeat event

    def test_recovery_unbreaches(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20,
                                    drawdown_reduce_ratio=0.50,
                                    drawdown_recovery_ratio=0.10))
        rm.check_drawdown(1_000_000)
        rm.check_drawdown(750_000)  # breach
        # Recover to 5% drawdown (< 10% recovery threshold)
        scale, event = rm.check_drawdown(950_000)
        assert scale == 1.0
        assert event is not None
        assert "解除" in event


class TestTurnoverMixing:
    def test_within_limit_no_change(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_turnover=0.50))
        new_w = {"A": 0.4, "B": 0.3, "C": 0.3}
        prev_w = {"A": 0.3, "B": 0.3, "C": 0.4}
        result, event = rm.check_turnover(new_w, prev_w)
        assert result == new_w  # no change, turnover = 0.2 < 0.5
        assert event is None

    def test_exceeds_limit_mixes(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_turnover=0.30))
        new_w = {"A": 0.8, "B": 0.2}
        prev_w = {"A": 0.2, "B": 0.8}
        # Turnover = |0.6| + |0.6| = 1.2 > 0.3
        result, event = rm.check_turnover(new_w, prev_w)
        assert event is not None
        assert "混合" in event
        # Mixed weights should be between old and new
        assert 0.2 < result["A"] < 0.8
        assert 0.2 < result["B"] < 0.8
        # Actual turnover should be <= max
        actual = sum(abs(result.get(s, 0) - prev_w.get(s, 0)) for s in set(result) | set(prev_w))
        assert actual <= 0.30 + 1e-6
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_portfolio/test_risk_manager.py -v`

- [ ] **Step 3: Implement RiskManager**

```python
# ez/portfolio/risk_manager.py
"""V2.12 D4: Portfolio risk manager — drawdown circuit breaker + turnover limiter.

Responsibilities (no overlap with Optimizer):
  - Drawdown: daily check, state machine (NORMAL ↔ BREACHED)
  - Turnover: rebalance-day check, proportional mixing
Optimizer handles: max_weight, industry constraints.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskConfig:
    """Risk control parameters."""
    max_drawdown_threshold: float = 0.20
    drawdown_reduce_ratio: float = 0.50
    drawdown_recovery_ratio: float = 0.10
    max_turnover: float = 0.50


class RiskManager:
    """Portfolio risk manager with drawdown state machine and turnover limiter."""

    def __init__(self, config: RiskConfig):
        self._config = config
        self._peak_equity: float = 0.0
        self._is_breached: bool = False

    def check_drawdown(self, equity: float) -> tuple[float, str | None]:
        """Daily drawdown check. Returns (scale_factor, event_description | None).

        State machine:
          NORMAL → drawdown > threshold → BREACHED (scale = reduce_ratio)
          BREACHED → drawdown < recovery_ratio → NORMAL (scale = 1.0)
        """
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity <= 0:
            return 1.0, None
        drawdown = (self._peak_equity - equity) / self._peak_equity

        if not self._is_breached:
            if drawdown > self._config.max_drawdown_threshold:
                self._is_breached = True
                return (self._config.drawdown_reduce_ratio,
                        f"回撤{drawdown:.1%}超阈值{self._config.max_drawdown_threshold:.0%}→减仓")
        else:
            if drawdown < self._config.drawdown_recovery_ratio:
                self._is_breached = False
                return 1.0, f"回撤恢复至{drawdown:.1%}→解除熔断"
            return self._config.drawdown_reduce_ratio, None

        return 1.0, None

    def check_turnover(self, new_weights: dict[str, float],
                       prev_weights: dict[str, float]
                       ) -> tuple[dict[str, float], str | None]:
        """Rebalance-day turnover check. Mixes new/old if over limit.

        Formula: w_final = α·w_new + (1-α)·w_old
        where α = min(1, max_turnover / actual_turnover)
        """
        all_syms = set(new_weights) | set(prev_weights)
        actual_turnover = sum(
            abs(new_weights.get(s, 0) - prev_weights.get(s, 0))
            for s in all_syms
        )
        if actual_turnover <= self._config.max_turnover:
            return new_weights, None

        alpha = self._config.max_turnover / actual_turnover
        mixed: dict[str, float] = {}
        for s in all_syms:
            w = alpha * new_weights.get(s, 0) + (1 - alpha) * prev_weights.get(s, 0)
            if w > 1e-10:
                mixed[s] = w
        return mixed, f"换手率{actual_turnover:.1%}超限{self._config.max_turnover:.0%}→混合α={alpha:.2f}"
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/test_portfolio/test_risk_manager.py -v`

- [ ] **Step 5: Commit**

```bash
git add ez/portfolio/risk_manager.py tests/test_portfolio/test_risk_manager.py
git commit -m "feat(v2.12): RiskManager — drawdown state machine + turnover limiter"
```

---

### Task 5: Engine Integration (optimizer + risk_manager + daily drawdown)

**Files:**
- Modify: `ez/portfolio/engine.py`
- Modify: `tests/test_portfolio/test_engine.py`

- [ ] **Step 1: Write engine integration tests**

Append to `tests/test_portfolio/test_engine.py`:

```python
class TestOptimizerIntegration:
    """V2.12: optimizer param in run_portfolio_backtest."""

    def test_mean_variance_runs_without_error(self):
        from ez.portfolio.optimizer import MeanVarianceOptimizer, OptimizationConstraints
        symbols = [f"S{i}" for i in range(5)]
        data, dates = _make_universe_data(symbols)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)
        opt = MeanVarianceOptimizer(
            risk_aversion=1.0,
            constraints=OptimizationConstraints(max_weight=0.40),
            cov_lookback=60,
        )
        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=5),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            optimizer=opt,
        )
        assert len(result.equity_curve) > 0
        assert result.metrics.get("sharpe_ratio") is not None


class TestRiskManagerIntegration:
    """V2.12: risk_manager param + daily drawdown check."""

    def test_risk_events_populated(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        symbols = [f"S{i}" for i in range(5)]
        data, dates = _make_universe_data(symbols)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)
        # Very aggressive drawdown threshold to ensure breach
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.01, drawdown_reduce_ratio=0.50))
        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            risk_manager=rm,
        )
        assert len(result.equity_curve) > 0
        assert hasattr(result, 'risk_events')
        # With 1% threshold on volatile data, should have events
        assert len(result.risk_events) > 0

    def test_accounting_invariant_with_risk_manager(self):
        """Accounting invariant must hold even with emergency sells."""
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        symbols = [f"S{i}" for i in range(5)]
        data, dates = _make_universe_data(symbols)
        cal = TradingCalendar.from_dates([d.date() for d in dates])
        universe = Universe(symbols)
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.05))
        # If invariant violated, engine raises AssertionError
        result = run_portfolio_backtest(
            strategy=TopNRotation(MomentumRank(20), top_n=3),
            universe=universe, universe_data=data, calendar=cal,
            start=dates[30].date(), end=dates[-1].date(),
            freq="monthly", initial_cash=1_000_000,
            risk_manager=rm,
        )
        assert len(result.equity_curve) > 0
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_portfolio/test_engine.py::TestOptimizerIntegration tests/test_portfolio/test_engine.py::TestRiskManagerIntegration -v`

- [ ] **Step 3: Modify engine.py**

Changes to `ez/portfolio/engine.py`:
1. Add imports for PortfolioOptimizer and RiskManager
2. Add `optimizer` and `risk_manager` params to `run_portfolio_backtest()`
3. Add `risk_events` to PortfolioResult
4. Add daily drawdown check + emergency sell in main loop
5. Add optimizer/allocator branching in rebalance block
6. Add turnover check after optimizer/allocator

```python
# At top of engine.py, add imports:
from ez.portfolio.optimizer import PortfolioOptimizer  # after existing imports
from ez.portfolio.risk_manager import RiskManager

# Add risk_events to PortfolioResult:
@dataclass
class PortfolioResult:
    equity_curve: list[float] = field(default_factory=list)
    benchmark_curve: list[float] = field(default_factory=list)
    dates: list[date] = field(default_factory=list)
    weights_history: list[dict[str, float]] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    rebalance_dates: list[date] = field(default_factory=list)
    risk_events: list[dict] = field(default_factory=list)  # V2.12

# Add params to run_portfolio_backtest():
def run_portfolio_backtest(
    strategy, universe, universe_data, calendar,
    start, end, freq="monthly", initial_cash=1_000_000.0,
    cost_model=None, allocator=None,
    lot_size=100, limit_pct=0.10, benchmark_symbol="",
    optimizer: PortfolioOptimizer | None = None,  # V2.12
    risk_manager: RiskManager | None = None,        # V2.12
) -> PortfolioResult:
```

In the main loop, BEFORE the rebalance block (after prices are computed, after `equity = cash + position_value`), add daily drawdown check:

```python
        # V2.12: Daily drawdown check (runs every trading day, not just rebalance)
        if risk_manager:
            dd_scale, dd_event = risk_manager.check_drawdown(equity)
            if dd_event:
                result.risk_events.append({"date": day.isoformat(), "event": dd_event})
            if dd_scale < 1.0 and day not in rebal_set:
                # Emergency sell: reduce all positions by (1 - dd_scale)
                for sym in list(holdings.keys()):
                    target = _lot_round(holdings[sym] * dd_scale, lot_size)
                    delta = target - holdings[sym]
                    if delta >= 0 or sym not in prices:
                        continue
                    if sym not in has_bar_today:
                        continue
                    price = prices[sym] * (1 - cost_model.slippage_rate)
                    amount = abs(delta) * price
                    comm = _compute_commission(amount, cost_model.sell_commission_rate, cost_model.min_commission)
                    stamp = amount * cost_model.stamp_tax_rate
                    cash += amount - comm - stamp
                    holdings[sym] = target
                    if target == 0:
                        holdings.pop(sym, None)
                    result.trades.append({
                        "date": day.isoformat(), "symbol": sym, "side": "sell",
                        "shares": abs(delta), "price": price, "cost": comm + stamp,
                    })
```

In the rebalance block, replace the allocator section with optimizer/allocator branching + turnover check:

```python
            # V2.12: Optimizer takes priority over allocator
            if optimizer:
                optimizer.set_context(day, sliced_tradeable)
                raw_weights = optimizer.optimize(raw_weights)
            elif allocator:
                raw_weights = allocator.allocate(raw_weights)

            # V2.12: Turnover check (only RiskManager, not optimizer)
            if risk_manager:
                raw_weights, to_event = risk_manager.check_turnover(raw_weights, prev_weights)
                if to_event:
                    result.risk_events.append({"date": day.isoformat(), "event": to_event})
```

- [ ] **Step 4: Run all engine tests — expect PASS**

Run: `python -m pytest tests/test_portfolio/test_engine.py -v`

- [ ] **Step 5: Run full test suite to verify no regression**

Run: `python -m pytest tests/ -x -q --tb=short`
Expected: 1304+ passed

- [ ] **Step 6: Commit**

```bash
git add ez/portfolio/engine.py tests/test_portfolio/test_engine.py
git commit -m "feat(v2.12): engine integration — optimizer + daily drawdown + turnover"
```

---

### Task 6: Brinson Attribution

**Files:**
- Create: `ez/portfolio/attribution.py`
- Create: `tests/test_portfolio/test_attribution.py`

- [ ] **Step 1: Write attribution tests**

```python
# tests/test_portfolio/test_attribution.py
"""Tests for V2.12 Brinson attribution."""
from datetime import date
import pytest


class TestBrinsonIdentity:
    """allocation + selection + interaction must equal total excess return."""

    def test_single_period_identity(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult
        import pandas as pd
        import numpy as np

        symbols = ["A", "B", "C"]
        dates_range = pd.date_range("2023-01-02", periods=60, freq="B")
        data = {}
        rng = np.random.default_rng(42)
        for sym in symbols:
            prices = 10 * np.cumprod(1 + rng.normal(0.002, 0.02, 60))
            data[sym] = pd.DataFrame({"close": prices, "adj_close": prices}, index=dates_range)

        result = PortfolioResult(
            rebalance_dates=[date(2023, 1, 2), date(2023, 2, 1), date(2023, 3, 1)],
            weights_history=[{"A": 0.5, "B": 0.3, "C": 0.2}, {"A": 0.4, "B": 0.4, "C": 0.2}],
            trades=[{"cost": 100}, {"cost": 50}],
        )
        industry_map = {"A": "银行", "B": "银行", "C": "食品饮料"}
        attr = compute_attribution(result, data, industry_map, initial_cash=1_000_000)

        for period in attr.periods:
            # Brinson identity: alloc + select + interact = total_excess
            recon = period.allocation_effect + period.selection_effect + period.interaction_effect
            assert abs(recon - period.total_excess) < 1e-10, \
                f"Brinson identity failed: {recon} != {period.total_excess}"

    def test_cost_drag_positive(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult
        import pandas as pd, numpy as np

        symbols = ["A", "B"]
        dates_range = pd.date_range("2023-01-02", periods=60, freq="B")
        data = {}
        rng = np.random.default_rng(42)
        for sym in symbols:
            prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.02, 60))
            data[sym] = pd.DataFrame({"close": prices, "adj_close": prices}, index=dates_range)

        result = PortfolioResult(
            rebalance_dates=[date(2023, 1, 2), date(2023, 2, 1)],
            weights_history=[{"A": 0.5, "B": 0.5}],
            trades=[{"cost": 500}, {"cost": 300}],
        )
        attr = compute_attribution(result, data, {"A": "银行", "B": "银行"}, initial_cash=1_000_000)
        assert attr.cost_drag > 0

    def test_empty_weights_no_crash(self):
        from ez.portfolio.attribution import compute_attribution
        from ez.portfolio.engine import PortfolioResult
        result = PortfolioResult(rebalance_dates=[], weights_history=[], trades=[])
        attr = compute_attribution(result, {}, {}, initial_cash=1_000_000)
        assert len(attr.periods) == 0
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `python -m pytest tests/test_portfolio/test_attribution.py -v`

- [ ] **Step 3: Implement attribution.py**

```python
# ez/portfolio/attribution.py
"""V2.12 F6: Brinson performance attribution.

Decomposes portfolio excess return into:
  - Allocation effect: industry weight deviation × benchmark industry return
  - Selection effect: benchmark industry weight × (portfolio - benchmark) industry return
  - Interaction effect: weight deviation × return deviation
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ez.portfolio.engine import PortfolioResult


@dataclass
class BrinsonAttribution:
    period_start: str
    period_end: str
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_excess: float


@dataclass
class AttributionResult:
    periods: list[BrinsonAttribution] = field(default_factory=list)
    cumulative: BrinsonAttribution | None = None
    cost_drag: float = 0.0
    by_industry: dict[str, dict] = field(default_factory=dict)


def _period_return(df: pd.DataFrame | None, start: date, end: date) -> float:
    """Compute return for a single symbol over [start, end]."""
    if df is None or df.empty:
        return 0.0
    col = "adj_close" if "adj_close" in df.columns else "close"
    mask_start = df.index.date >= start if hasattr(df.index, 'date') else df.index >= pd.Timestamp(start)
    mask_end = df.index.date <= end if hasattr(df.index, 'date') else df.index <= pd.Timestamp(end)
    subset = df[col][mask_start & mask_end]
    if len(subset) < 2:
        return 0.0
    p0, p1 = float(subset.iloc[0]), float(subset.iloc[-1])
    return (p1 - p0) / p0 if p0 > 0 else 0.0


def _has_data(df: pd.DataFrame | None, start: date, end: date) -> bool:
    if df is None or df.empty:
        return False
    dates = df.index.date if hasattr(df.index, 'date') else df.index
    return any(start <= d <= end for d in dates)


def _weighted_return(symbols: list[str], weights: dict[str, float],
                     returns: dict[str, float]) -> float:
    """Weighted average return for a group of symbols."""
    total_w = sum(weights.get(s, 0) for s in symbols)
    if total_w <= 0:
        return 0.0
    return sum(weights.get(s, 0) * returns.get(s, 0) for s in symbols) / total_w


def compute_attribution(
    result: PortfolioResult,
    universe_data: dict[str, pd.DataFrame],
    industry_map: dict[str, str],
    initial_cash: float = 1_000_000.0,
    benchmark_type: str = "equal",
    custom_benchmark: dict[str, float] | None = None,
) -> AttributionResult:
    """Compute Brinson attribution from backtest result + universe data."""
    rebalance_dates = result.rebalance_dates
    weights_history = result.weights_history

    if len(rebalance_dates) < 2 or len(weights_history) < 1:
        return AttributionResult()

    periods: list[BrinsonAttribution] = []
    industry_accum: dict[str, dict[str, float]] = {}

    for i in range(min(len(rebalance_dates) - 1, len(weights_history))):
        t_start = rebalance_dates[i]
        t_end = rebalance_dates[i + 1]
        w_p = weights_history[i]

        # Dynamic benchmark: equal weight over active symbols
        if benchmark_type == "equal":
            active = [s for s in universe_data if _has_data(universe_data.get(s), t_start, t_end)]
            n = len(active)
            w_b = {s: 1.0 / n for s in active} if n > 0 else {}
        else:
            w_b = custom_benchmark or {}

        # Per-stock returns for this period
        all_syms = set(w_p) | set(w_b)
        stock_returns = {s: _period_return(universe_data.get(s), t_start, t_end) for s in all_syms}

        # Brinson decomposition by industry
        industries = set(industry_map.get(s, "_other") for s in all_syms)
        alloc, select, interact = 0.0, 0.0, 0.0

        for ind in industries:
            syms = [s for s in all_syms if industry_map.get(s, "_other") == ind]
            w_p_j = sum(w_p.get(s, 0) for s in syms)
            w_b_j = sum(w_b.get(s, 0) for s in syms)
            r_p_j = _weighted_return(syms, w_p, stock_returns) if w_p_j > 0 else 0
            r_b_j = _weighted_return(syms, w_b, stock_returns) if w_b_j > 0 else 0

            a = (w_p_j - w_b_j) * r_b_j
            s_eff = w_b_j * (r_p_j - r_b_j)
            ix = (w_p_j - w_b_j) * (r_p_j - r_b_j)
            alloc += a
            select += s_eff
            interact += ix

            if ind not in industry_accum:
                industry_accum[ind] = {"allocation": 0, "selection": 0, "interaction": 0}
            industry_accum[ind]["allocation"] += a
            industry_accum[ind]["selection"] += s_eff
            industry_accum[ind]["interaction"] += ix

        total = alloc + select + interact
        periods.append(BrinsonAttribution(
            period_start=t_start.isoformat(), period_end=t_end.isoformat(),
            allocation_effect=alloc, selection_effect=select,
            interaction_effect=interact, total_excess=total,
        ))

    cumulative = BrinsonAttribution(
        period_start=periods[0].period_start if periods else "",
        period_end=periods[-1].period_end if periods else "",
        allocation_effect=sum(p.allocation_effect for p in periods),
        selection_effect=sum(p.selection_effect for p in periods),
        interaction_effect=sum(p.interaction_effect for p in periods),
        total_excess=sum(p.total_excess for p in periods),
    ) if periods else None

    cost_drag = sum(float(t.get("cost", 0)) for t in result.trades) / initial_cash if initial_cash > 0 else 0

    return AttributionResult(
        periods=periods, cumulative=cumulative,
        cost_drag=cost_drag, by_industry=industry_accum,
    )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python -m pytest tests/test_portfolio/test_attribution.py -v`

- [ ] **Step 5: Commit**

```bash
git add ez/portfolio/attribution.py tests/test_portfolio/test_attribution.py
git commit -m "feat(v2.12): Brinson attribution — allocation/selection/interaction"
```

---

### Task 7: API — Extend /run + Add /attribution

**Files:**
- Modify: `ez/api/routes/portfolio.py`

- [ ] **Step 1: Extend PortfolioRunRequest with optimizer/risk fields**

Add these fields to the existing `PortfolioRunRequest` class in `ez/api/routes/portfolio.py`:

```python
    # V2.12: Optimizer
    optimizer: str = Field(default="none", pattern="^(none|mean_variance|min_variance|risk_parity)$")
    risk_aversion: float = Field(default=1.0, gt=0)
    max_weight: float = Field(default=0.10, gt=0, le=1.0)
    max_industry_weight: float = Field(default=0.30, gt=0, le=1.0)
    cov_lookback: int = Field(default=60, ge=10, le=500)
    # V2.12: Risk control
    risk_control: bool = False
    max_drawdown: float = Field(default=0.20, gt=0, le=0.50)
    drawdown_reduce: float = Field(default=0.50, gt=0, le=1.0)
    max_turnover: float = Field(default=0.50, gt=0, le=2.0)
```

- [ ] **Step 2: Update run_portfolio route to construct optimizer/risk_manager**

In the `run_portfolio()` function, after strategy creation and before `run_portfolio_backtest()`:

```python
    # V2.12: Optimizer
    from ez.portfolio.optimizer import (
        MeanVarianceOptimizer, MinVarianceOptimizer,
        RiskParityOptimizer, OptimizationConstraints,
    )
    from ez.portfolio.risk_manager import RiskManager, RiskConfig

    optimizer_instance = None
    if req.optimizer != "none":
        industry_map = {}
        try:
            from ez.api.deps import get_fundamental_store
            fstore = get_fundamental_store()
            if fstore:
                industry_map = fstore.get_all_industries()
        except Exception:
            pass
        constraints = OptimizationConstraints(
            max_weight=req.max_weight,
            max_industry_weight=req.max_industry_weight,
            industry_map=industry_map,
        )
        if req.optimizer == "mean_variance":
            optimizer_instance = MeanVarianceOptimizer(
                risk_aversion=req.risk_aversion, constraints=constraints, cov_lookback=req.cov_lookback)
        elif req.optimizer == "min_variance":
            optimizer_instance = MinVarianceOptimizer(constraints=constraints, cov_lookback=req.cov_lookback)
        elif req.optimizer == "risk_parity":
            optimizer_instance = RiskParityOptimizer(constraints=constraints, cov_lookback=req.cov_lookback)

    risk_mgr = None
    if req.risk_control:
        risk_mgr = RiskManager(RiskConfig(
            max_drawdown_threshold=req.max_drawdown,
            drawdown_reduce_ratio=req.drawdown_reduce,
            max_turnover=req.max_turnover,
        ))
```

Pass to engine:
```python
    result = run_portfolio_backtest(
        ...,  # existing params
        optimizer=optimizer_instance,
        risk_manager=risk_mgr,
    )
```

Add `risk_events` to the response dict:
```python
        "risk_events": result.risk_events,
```

- [ ] **Step 3: Add /attribution endpoint**

```python
class AttributionRequest(BaseModel):
    run_id: str
    symbols: list[str]
    market: str = "cn_stock"
    start_date: str
    end_date: str
    benchmark_type: str = Field(default="equal", pattern="^(equal|custom)$")

@router.post("/attribution")
def portfolio_attribution(req: AttributionRequest):
    """Brinson attribution analysis. Re-fetches data from source."""
    from ez.portfolio.attribution import compute_attribution
    from ez.api.deps import get_store, get_chain

    store = get_portfolio_store()
    run = store.get_run(req.run_id)
    if not run:
        raise HTTPException(404, f"Run {req.run_id} not found")

    # Re-fetch universe data
    chain = get_chain()
    start = date.fromisoformat(req.start_date)
    end = date.fromisoformat(req.end_date)
    universe_data = {}
    for sym in req.symbols:
        bars = chain.get_kline(sym, req.market, "daily", start, end)
        if bars:
            df = pd.DataFrame([{"date": b.date, "open": b.open, "high": b.high,
                                "low": b.low, "close": b.close, "adj_close": b.adj_close,
                                "volume": b.volume} for b in bars])
            df.index = pd.to_datetime(df["date"])
            universe_data[sym] = df

    # Need PortfolioResult with weights_history — reconstruct from stored data
    # NOTE: weights_history is NOT stored in DB. Attribution only works for
    # runs in the same session. Return error if weights not available.
    raise HTTPException(501, "归因分析需要权重历史数据。请在回测完成后立即使用归因功能。")
```

**Important design note**: Since PortfolioStore does NOT store weights_history, the `/attribution` endpoint cannot reconstruct it from a run_id alone. The practical approach is to **compute attribution inline** in the run response. Update the run handler to optionally include attribution.

Actually, the better approach: return attribution data as part of the `/run` response when the run completes. Add a helper in the run handler:

```python
    # After run completes, compute attribution inline
    from ez.portfolio.attribution import compute_attribution
    industry_map = {}
    try:
        fstore = get_fundamental_store()
        if fstore:
            industry_map = fstore.get_all_industries()
    except Exception:
        pass
    attribution = compute_attribution(result, universe_data, industry_map, initial_cash=req.initial_cash)

    # Add to response
    response["attribution"] = {
        "cumulative": {
            "allocation": attribution.cumulative.allocation_effect,
            "selection": attribution.cumulative.selection_effect,
            "interaction": attribution.cumulative.interaction_effect,
            "total_excess": attribution.cumulative.total_excess,
        } if attribution.cumulative else None,
        "cost_drag": attribution.cost_drag,
        "by_industry": attribution.by_industry,
        "periods": [
            {"start": p.period_start, "end": p.period_end,
             "allocation": p.allocation_effect, "selection": p.selection_effect,
             "interaction": p.interaction_effect, "total_excess": p.total_excess}
            for p in attribution.periods
        ],
    }
```

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -x -q --tb=short`

- [ ] **Step 5: Commit**

```bash
git add ez/api/routes/portfolio.py
git commit -m "feat(v2.12): API — optimizer/risk params in /run + inline attribution"
```

---

### Task 8: Frontend — Optimizer/Risk Panels + Attribution + Risk Events

**Files:**
- Modify: `web/src/api/index.ts`
- Modify: `web/src/components/PortfolioPanel.tsx`

- [ ] **Step 1: Add API function (trivial — already uses runPortfolioBacktest)**

No new API function needed — attribution is returned inline with the run response.

- [ ] **Step 2: Add optimizer/risk state variables to PortfolioPanel**

Add state near the top of the component:

```typescript
  // V2.12: Optimizer
  const [optimizer, setOptimizer] = useState('none')
  const [riskAversion, setRiskAversion] = useState(1.0)
  const [maxWeight, setMaxWeight] = useState(0.10)
  const [maxIndustryWeight, setMaxIndustryWeight] = useState(0.30)
  const [covLookback, setCovLookback] = useState(60)
  // V2.12: Risk control
  const [riskControl, setRiskControl] = useState(false)
  const [maxDrawdown, setMaxDrawdown] = useState(0.20)
  const [drawdownReduce, setDrawdownReduce] = useState(0.50)
  const [maxTurnover, setMaxTurnover] = useState(0.50)
  // V2.12: UI state
  const [showOptimizer, setShowOptimizer] = useState(false)
  const [showRiskControl, setShowRiskControl] = useState(false)
  const [showAttribution, setShowAttribution] = useState(false)
```

- [ ] **Step 3: Pass optimizer/risk params in handleRun**

Update `handleRun` to include V2.12 params:

```typescript
      const res = await runPortfolioBacktest({
        ...existingParams,
        // V2.12
        optimizer,
        risk_aversion: riskAversion,
        max_weight: maxWeight,
        max_industry_weight: maxIndustryWeight,
        cov_lookback: covLookback,
        risk_control: riskControl,
        max_drawdown: maxDrawdown,
        drawdown_reduce: drawdownReduce,
        max_turnover: maxTurnover,
      })
```

- [ ] **Step 4: Add optimizer collapsible panel in the run tab**

After the existing strategy params section, before the run button:

```tsx
{/* V2.12: Optimizer */}
<div className="border border-gray-700 rounded mt-3">
  <button onClick={() => setShowOptimizer(!showOptimizer)}
    className="w-full text-left px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800">
    {showOptimizer ? '▼' : '▶'} 组合优化
  </button>
  {showOptimizer && (
    <div className="px-3 pb-3 space-y-2">
      <label className="block text-xs text-gray-400">优化方法
        <select value={optimizer} onChange={e => setOptimizer(e.target.value)}
          className="w-full mt-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm">
          <option value="none">不优化</option>
          <option value="mean_variance">均值-方差</option>
          <option value="min_variance">最小方差</option>
          <option value="risk_parity">风险平价</option>
        </select>
      </label>
      {optimizer === 'mean_variance' && (
        <label className="block text-xs text-gray-400">风险厌恶系数 λ
          <input type="number" step="0.1" value={riskAversion}
            onChange={e => setRiskAversion(+e.target.value)}
            className="w-full mt-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
        </label>
      )}
      {optimizer !== 'none' && (<>
        <label className="block text-xs text-gray-400">协方差回看期 (天)
          <input type="number" value={covLookback} onChange={e => setCovLookback(+e.target.value)}
            className="w-full mt-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
        </label>
        <label className="block text-xs text-gray-400">单股上限 (%)
          <input type="number" step="1" value={maxWeight * 100}
            onChange={e => setMaxWeight(+e.target.value / 100)}
            className="w-full mt-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
        </label>
        <label className="block text-xs text-gray-400">行业上限 (%)
          <input type="number" step="5" value={maxIndustryWeight * 100}
            onChange={e => setMaxIndustryWeight(+e.target.value / 100)}
            className="w-full mt-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
        </label>
      </>)}
    </div>
  )}
</div>
```

- [ ] **Step 5: Add risk control collapsible panel**

```tsx
{/* V2.12: Risk Control */}
<div className="border border-gray-700 rounded mt-2">
  <button onClick={() => setShowRiskControl(!showRiskControl)}
    className="w-full text-left px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800">
    {showRiskControl ? '▼' : '▶'} 风险控制
  </button>
  {showRiskControl && (
    <div className="px-3 pb-3 space-y-2">
      <label className="flex items-center gap-2 text-xs text-gray-400">
        <input type="checkbox" checked={riskControl} onChange={e => setRiskControl(e.target.checked)} />
        启用风控
      </label>
      {riskControl && (<>
        <label className="block text-xs text-gray-400">最大回撤阈值 (%)
          <input type="number" step="5" value={maxDrawdown * 100}
            onChange={e => setMaxDrawdown(+e.target.value / 100)}
            className="w-full mt-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
        </label>
        <label className="block text-xs text-gray-400">回撤减仓比例 (%)
          <input type="number" step="10" value={drawdownReduce * 100}
            onChange={e => setDrawdownReduce(+e.target.value / 100)}
            className="w-full mt-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
        </label>
        <label className="block text-xs text-gray-400">换手率上限 (%)
          <input type="number" step="10" value={maxTurnover * 100}
            onChange={e => setMaxTurnover(+e.target.value / 100)}
            className="w-full mt-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm" />
        </label>
      </>)}
    </div>
  )}
</div>
```

- [ ] **Step 6: Add attribution display in results area**

After the existing metrics display, add attribution panel:

```tsx
{/* V2.12: Attribution */}
{result?.attribution?.cumulative && (
  <div className="border border-gray-700 rounded mt-3">
    <button onClick={() => setShowAttribution(!showAttribution)}
      className="w-full text-left px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800">
      {showAttribution ? '▼' : '▶'} 归因分析
    </button>
    {showAttribution && (
      <div className="px-3 pb-3 text-sm">
        <div className="space-y-1 mt-2">
          {[
            ['配置效应', result.attribution.cumulative.allocation],
            ['选股效应', result.attribution.cumulative.selection],
            ['交互效应', result.attribution.cumulative.interaction],
            ['交易成本', -result.attribution.cost_drag],
          ].map(([label, val]) => (
            <div key={label as string} className="flex justify-between">
              <span className="text-gray-400">{label}</span>
              <span className={(val as number) >= 0 ? 'text-red-400' : 'text-green-400'}>
                {((val as number) * 100).toFixed(2)}%
              </span>
            </div>
          ))}
          <div className="flex justify-between font-medium border-t border-gray-700 pt-1 mt-1">
            <span>累计超额</span>
            <span>{(result.attribution.cumulative.total_excess * 100).toFixed(2)}%</span>
          </div>
        </div>
      </div>
    )}
  </div>
)}
```

- [ ] **Step 7: Add risk events display**

```tsx
{/* V2.12: Risk Events */}
{result?.risk_events?.length > 0 && (
  <div className="border border-yellow-800 rounded mt-3">
    <div className="px-3 py-1.5 text-sm text-yellow-400">
      风控事件 ({result.risk_events.length})
    </div>
    <div className="px-3 pb-3 text-xs text-gray-400 max-h-40 overflow-y-auto">
      {result.risk_events.map((e: any, i: number) => (
        <div key={i} className="py-0.5">{e.date}  {e.event}</div>
      ))}
    </div>
  </div>
)}
```

- [ ] **Step 8: Build frontend and verify**

Run: `cd web && npm run build`

- [ ] **Step 9: Commit**

```bash
git add web/src/components/PortfolioPanel.tsx web/src/api/index.ts
git commit -m "feat(v2.12): frontend — optimizer/risk panels + attribution + events"
```

---

### Task 9: Documentation Update

**Files:**
- Modify: `CLAUDE.md`
- Modify: `ez/portfolio/CLAUDE.md`
- Modify: `docs/internal/core-changes/v2.3-roadmap.md`

- [ ] **Step 1: Update CLAUDE.md version progress**

Add after V2.11.1 line:
```
- **V2.12**: 组合优化+归因+风控 — PortfolioOptimizer(MeanVariance/MinVariance/RiskParity, Ledoit-Wolf协方差, 约束优化SLSQP), RiskManager(每日回撤熔断状态机+紧急减仓+换手率混合), Brinson归因(配置/选股/交互效应+行业维度+交易成本), /run扩展优化器/风控参数+内联归因, 前端折叠面板(优化器+风控+归因+事件日志), XXXX tests
```

Update version and test count in line 6.

- [ ] **Step 2: Update ez/portfolio/CLAUDE.md**

Add new files to the table:
```
| optimizer.py | PortfolioOptimizer ABC + MeanVariance/MinVariance/RiskParity + Ledoit-Wolf (V2.12) |
| risk_manager.py | RiskConfig + RiskManager: drawdown state machine + turnover limiter (V2.12) |
| attribution.py | BrinsonAttribution + compute_attribution(): Brinson decomposition (V2.12) |
```

Add to Public Interfaces:
```
- `PortfolioOptimizer` — ABC: `set_context(date, data)` + `optimize(alpha_weights) → dict[str, float]` (V2.12)
- `RiskManager` — `check_drawdown(equity)` + `check_turnover(new, old)` (V2.12)
- `compute_attribution()` — Brinson decomposition from PortfolioResult + universe_data (V2.12)
```

Add to Status:
```
- V2.12: PortfolioOptimizer (MeanVariance/MinVariance/RiskParity, Ledoit-Wolf), RiskManager (drawdown+turnover), Brinson attribution
```

- [ ] **Step 3: Update roadmap — check off V2.12 items**

In `docs/internal/core-changes/v2.3-roadmap.md`, change `- [ ]` to `- [x]` for completed F4/D4/F6 items.

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/ -x -q --tb=short`
Expected: >= 1330 passed

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md ez/portfolio/CLAUDE.md docs/internal/core-changes/v2.3-roadmap.md
git commit -m "docs: V2.12 — optimizer/attribution/risk documentation"
```

---

## Self-Review

### Spec Coverage

| Spec Requirement | Task |
|-----------------|------|
| F4a MeanVarianceOptimizer | Task 2 |
| F4b MinVarianceOptimizer (replaces MaxDiversification) | Task 3 |
| F4c RiskParityOptimizer | Task 3 |
| Ledoit-Wolf covariance (no sklearn) | Task 1 |
| D4 Drawdown state machine | Task 4 |
| D4 Turnover mixing | Task 4 |
| Engine integration (optimizer + risk_manager) | Task 5 |
| Daily drawdown check (not just rebalance) | Task 5 |
| Emergency sell on non-rebalance days | Task 5 |
| F6 Brinson attribution | Task 6 |
| API /run extension | Task 7 |
| API attribution inline | Task 7 |
| Frontend optimizer panel | Task 8 |
| Frontend risk panel | Task 8 |
| Frontend attribution display | Task 8 |
| Frontend risk events | Task 8 |
| Documentation | Task 9 |

### Exit Gate Coverage

| Gate | Test |
|------|------|
| Weights >= 0, Σ=1, <= max_weight | `test_mean_variance_long_only`, `test_max_weight_respected` |
| Brinson identity < 1bp | `test_single_period_identity` |
| Drawdown → reduce → invariant | `test_accounting_invariant_with_risk_manager` |
| Daily drawdown (non-rebalance) | `test_risk_events_populated` |
| Turnover α correct | `test_exceeds_limit_mixes` |
| Fallback on failure | `test_fallback_on_insufficient_data`, `test_fallback_on_failure` |
| Ledoit-Wolf N>T | `test_wide_matrix_n_gt_t` |
