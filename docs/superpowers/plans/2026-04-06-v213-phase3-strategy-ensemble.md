# V2.13 Phase 3: StrategyEnsemble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `StrategyEnsemble(PortfolioStrategy)` — a composition-layer heuristic orchestrator that combines multiple portfolio strategies into a single weight vector. **NOT a statistical meta-optimizer** — V1 uses pure-alpha hypothetical returns as a proxy, explicitly documented as an approximation.

**Architecture:** StrategyEnsemble is a `PortfolioStrategy` subclass. The engine sees one strategy; internally the ensemble calls each sub-strategy's `generate_weights()`, maintains a per-sub-strategy hypothetical-return ledger in `self.state`, and combines outputs using one of 4 modes. No engine changes.

**Naming convention (codex requirement):** Mode names must be honest about what they actually compute:
- `"equal"` — arithmetic mean. Exact.
- `"manual"` — user-provided static weights. Exact.
- `"return_weighted"` — weight ∝ max(0, mean(recent hypothetical returns)). **NOT rank IC.** Proxy.
- `"inverse_vol"` — weight ∝ 1/std(recent hypothetical returns). **NOT full risk parity.** Proxy.

Plan originally called these `ic_weighted` and `risk_parity`. Renamed per codex: "don't package V1 as a true statistical optimizer".

**Sub-strategy ownership:** `StrategyEnsemble.__init__` takes a `list[PortfolioStrategy]` and `copy.deepcopy`s each one. This prevents external code from sharing instances across multiple ensembles and causing state pollution. If deepcopy fails (e.g., strategy holds DB connections), raise `TypeError` with guidance.

---

## Design Decisions (codex-confirmed)

1. **`return_weighted` not `ic_weighted`**: mean(hypothetical_returns), not spearmanr. Docstring says "proxy for IC". Fallback: all ≤ 0 → equal weight.
2. **Pop registry**: dual-dict pop like MLAlpha/AlphaCombiner. Cannot zero-arg instantiate.
3. **Task 3.5 correlation_warnings**: warn-only, based on return series (not weight vectors), threshold documented. No auto-drop.
4. **Per-sub exception logging**: keyed by sub-strategy index+name, one-shot per sub. Failed sub → `{}`, remaining subs' weights re-normalized.
5. **prev_weights/prev_returns transparent pass-through**: documented limitation. Sub-strategies see portfolio-level prev, not their own prev. Subs that need memory use `self.state`.
6. **Sub-strategy deepcopy**: constructor deepcopies to prevent external state sharing.
7. **lookback_buffer parametrized**: `lookback_buffer: int = 20`, not hardcoded.
8. **State = pure dict/list/float**: no pandas in `self.state`.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `ez/portfolio/ensemble.py` | `StrategyEnsemble(PortfolioStrategy)` + 4 modes + ledger + correlation_warnings (~350 lines) |
| `tests/test_portfolio/test_ensemble.py` | Unit + integration tests (~400 lines, ~25 tests) |

### Modified Files

| File | Changes |
|------|---------|
| `ez/portfolio/__init__.py` | Export `StrategyEnsemble` |
| `CLAUDE.md` | Test count + V2.13 Phase 3 entry |
| `ez/portfolio/CLAUDE.md` | Files table + Status |
| `docs/core-changes/v2.3-roadmap.md` | Check off D5 |

---

## Tasks

### Task 3.1: Skeleton + equal mode + manual mode + validation

**Files:** Create `ez/portfolio/ensemble.py` + `tests/test_portfolio/test_ensemble.py`

Core class with:
- `__init__`: validate strategies (non-empty), mode, ensemble_weights (length, non-negative, sum>0), warmup_rebalances. `copy.deepcopy` each sub-strategy.
- `EnsembleMode = Literal["equal", "manual", "return_weighted", "inverse_vol"]`
- `lookback_days`: `max(s.lookback_days for s in strategies) + lookback_buffer`
- `generate_weights`: equal mode (arithmetic mean) + manual mode (normalized ensemble_weights)
- Pop registry after class definition

Tests (~10):
- is_portfolio_strategy subclass
- equal_weight combines two static strategies correctly
- manual_weight with [3, 1] normalizes to [0.75, 0.25]
- manual requires ensemble_weights when mode="manual"
- ensemble_weights length mismatch → ValueError
- negative weights → ValueError
- empty strategies → ValueError
- deepcopy isolation: modifying original sub-strategy doesn't affect ensemble's copy
- pop registry: resolve_class("StrategyEnsemble") raises KeyError
- lookback_days = max of subs + buffer

### Task 3.2: Hypothetical-return ledger + `_update_hypothetical_returns`

Implement the ledger: at each rebalance, record sub-strategy target weights → on next rebalance, reconstruct per-sub return from close-to-close prices.

Tests (~4):
- ledger reconstruction matches expected close-to-close return
- zero-weight sub → 0.0 return
- missing symbol in universe_data → graceful skip
- ledger state is pure dict/list/float (no pandas)

### Task 3.3: `return_weighted` mode + `inverse_vol` mode

Implement `_return_weighted_or_fallback` + `_inverse_vol_or_fallback`. Both:
- Require `min_len >= warmup_rebalances` AND `min_days >= min_warmup_days` of ledger data
- Fall back to equal weight during warmup
- `return_weighted`: all ≤ 0 → equal fallback (not zero-weight)
- `inverse_vol`: use `std(ddof=1)`, eps=1e-6 floor

Tests (~4):
- return_weighted prefers sub with higher mean return after warmup
- inverse_vol assigns more weight to lower-vol sub after warmup
- both fall back to equal during warmup
- return_weighted all-negative fallback to equal

### Task 3.4: Per-sub exception handling + re-normalization

Sub-strategy `generate_weights` exceptions:
- One-shot warning per sub-strategy (keyed by index)
- Failed sub treated as `{}` (cash)
- Remaining subs' ensemble weights re-normalized to sum=1

Tests (~3):
- one sub throws → others still produce weights + warning logged
- all subs throw → returns `{}` + warning
- failure count tracked in state

### Task 3.5: Correlation warnings (warn-only)

After warmup, compute pairwise Pearson correlation between sub-strategies' hypothetical return series. If any pair > threshold (default 0.9), append warning to `self.state["correlation_warnings"]`.

Tests (~2):
- identical sub-strategies → correlation warning emitted
- uncorrelated subs → no warning

### Task 3.6: Nested ensembles + end-to-end integration

Tests (~3):
- `StrategyEnsemble([EnsembleA, EnsembleB, TopNRotation])` works recursively
- Inner ensemble's `self.state` ledger is isolated from outer
- End-to-end: StrategyEnsemble through `run_portfolio_backtest` produces valid result

### Task 3.7: Code review + CLAUDE.md update

- Full test suite
- Dispatch code-reviewer
- Fix Critical/Important
- Update CLAUDE.md + roadmap D5

---

## Execution Recommendation

7 tasks, ~25 tests. Tasks 3.1-3.3 sequential (ledger builds on skeleton). Tasks 3.4-3.5 can parallel after 3.3. Task 3.6 depends on all.
