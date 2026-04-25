# V2.13 Phase 3: StrategyEnsemble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `StrategyEnsemble(PortfolioStrategy)` — a composition-layer heuristic orchestrator that combines multiple portfolio strategies into a single weight vector. **NOT a statistical meta-optimizer** — V1 uses pure-alpha hypothetical returns as a proxy, explicitly documented as an approximation.

**Architecture:** StrategyEnsemble is a `PortfolioStrategy` subclass. The engine sees one strategy; internally the ensemble calls each sub-strategy's `generate_weights()`, maintains a per-sub-strategy hypothetical-return ledger in `self.state`, and combines outputs using one of 4 modes. No engine changes.

**Naming convention (codex requirement):** Mode names must be honest about what they actually compute:
- `"equal"` — arithmetic mean. Exact.
- `"manual"` — user-provided static weights. Exact.
- `"return_weighted"` — weight ∝ max(0, mean(recent hypothetical returns)). **NOT rank IC.** Proxy.
- `"inverse_vol"` — weight ∝ 1/std(recent hypothetical returns). **NOT full risk parity.** Proxy.

**Sub-strategy ownership:** `StrategyEnsemble.__init__` takes a `list[PortfolioStrategy]` and `copy.deepcopy`s each one. This prevents external code from sharing instances across multiple ensembles and causing state pollution. If deepcopy fails, raise `TypeError` with guidance.

**Consumption path:** V1 is **Python-only 直用型**. StrategyEnsemble is NOT available via dropdown/resolve. Phase 6 UI (if needed) should use a dedicated "wizard/template" path, not generic strategy registration.

---

## Design Decisions (codex rounds 1+2 confirmed)

1. **`return_weighted` not `ic_weighted`**: mean(hypothetical_returns), not spearmanr. Docstring says "proxy, not true IC". Fallback: all ≤ 0 → equal weight.
2. **`inverse_vol` not `risk_parity`**: 1/std, not covariance-based optimization. Docstring says "proxy, not true risk parity".
3. **Pop registry**: dual-dict pop like MLAlpha/AlphaCombiner. Cannot zero-arg instantiate. Python-only direct use.
4. **correlation_warnings** (not "dedup"): warn-only, based on return series (not weight vectors). Structured payload: `{"sub_i": int, "sub_j": int, "correlation": float, "n_samples": int}`. Min overlap = `max(warmup_rebalances, 8)` samples. No auto-drop.
5. **Per-sub exception logging**: keyed by sub-strategy index, one-shot per sub. **Distinct from "no signal"**: exception → warn + `{}` + re-normalize; legitimate `{}` return → no warn, treated as "all cash this period".
6. **prev_weights/prev_returns transparent pass-through**: documented. Sub-strategies see portfolio-level prev, not their own prev. Subs that need memory use their own `self.state`.
7. **Sub-strategy deepcopy**: constructor deepcopies to prevent external state sharing.
8. **State = pure dict/list/float**: no pandas in `self.state`.
9. **lookback_days = max(s.lookback_days for s in strategies)**: NO additional buffer at ensemble level. Buffer is the leaf strategy's responsibility (TopNRotation already has `+20` from Phase 1 round 5). Nested ensembles don't inflate: outer takes max of inner lookbacks, which already include their own buffers. This is the **"only leaf adds buffer"** rule.
10. **Warmup gate**: `warmup_rebalances: int = 8`. Internal `_min_warmup_days` computed as `warmup_rebalances * 7` (assumes ~weekly rebalance as floor). Not a user parameter. Both conditions must be met before `return_weighted` / `inverse_vol` switch from equal fallback.

---

## Combination Formula (executable rules)

```
Input: sub_outputs = [sub_1_weights, sub_2_weights, ...]  # each is dict[str, float]
       ew = [w_1, w_2, ...]  # ensemble weights (mode-dependent, sum=1)

Rules:
1. Sub-strategy outputs are NOT individually normalized. If sub_1
   returns {"A": 0.3, "B": 0.2} (sum=0.5, 50% cash), that cash
   intent is preserved in the combination.

2. Combined weights: for each symbol s:
       combined[s] = sum(ew[i] * sub_outputs[i].get(s, 0.0) for i in range(N))
   Missing symbol in a sub → treated as 0 weight (not as "symbol doesn't exist").

3. Final combined can sum < 1.0 (remainder = cash). This is intended:
   if all subs hold 50% cash, the ensemble also holds ~50% cash.

4. Exception sub vs no-signal sub:
   - sub.generate_weights() RAISES → warn, sub_outputs[i] = {}, ew re-normalized
     across remaining active subs. State tracks failure_count[i].
   - sub.generate_weights() returns {} normally → no warn, sub_outputs[i] = {},
     ew stays unchanged (sub is "choosing cash this period", not broken).

5. All subs return {} → combined = {} → engine holds 100% cash.
   This is NOT an error.
```

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `ez/portfolio/ensemble.py` | `StrategyEnsemble(PortfolioStrategy)` + 4 modes + ledger + correlation_warnings (~350 lines) |
| `tests/test_portfolio/test_ensemble.py` | Unit + integration tests (~700 lines, 38 tests) |

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
- `__init__`: validate strategies (non-empty), mode, ensemble_weights (for manual: length, non-negative, sum>0). `copy.deepcopy` each sub-strategy. warmup_rebalances >= 1.
- `EnsembleMode = Literal["equal", "manual", "return_weighted", "inverse_vol"]`
- `lookback_days`: `max(s.lookback_days for s in strategies)` (no buffer at ensemble level)
- `generate_weights`: equal mode (arithmetic mean) + manual mode (normalized ensemble_weights). Sub exceptions: per-index one-shot warn + `{}` + re-normalize.
- Pop registry after class definition (dual-dict)

Tests (~12):
- is_portfolio_strategy subclass
- equal_weight combines two static strategies: `{A:1, B:0}` + `{A:0, B:1}` → `{A:0.5, B:0.5}`
- equal_weight preserves cash: `{A:0.5}` + `{B:0.5}` → `{A:0.25, B:0.25}` (sum=0.5, 50% cash)
- manual_weight with [3, 1] normalizes to [0.75, 0.25]
- manual requires ensemble_weights when mode="manual" → ValueError
- ensemble_weights length mismatch → ValueError
- negative weights → ValueError
- empty strategies → ValueError
- deepcopy isolation: modifying original sub-strategy's state doesn't affect ensemble's copy
- pop registry: `PortfolioStrategy.resolve_class("StrategyEnsemble")` raises KeyError
- lookback_days = max of subs (no extra buffer)
- sub exception → warn + others still produce weights

### Task 3.2: Hypothetical-return ledger + `_update_hypothetical_returns`

Implement the ledger update logic inside `generate_weights`:
1. Before computing weights, call `_update_hypothetical_returns` to reconstruct each sub's return since last rebalance
2. Use `close` prices from universe_data: `close[current-1] / close[prev_rebalance] - 1` weighted by stored target weights
3. After computing and combining weights, record each sub's current target weights

Tests (~4):
- ledger reconstruction: two static subs, known close prices → exact hypothetical return values
- zero-weight sub → 0.0 return
- missing symbol in universe_data → graceful skip, partial return computed from available symbols
- ledger state is pure dict/list/float (json.dumps works)

### Task 3.3: `return_weighted` mode + `inverse_vol` mode

Implement `_return_weighted_or_fallback` + `_inverse_vol_or_fallback`:
- Warmup gate: `min_len >= warmup_rebalances` AND elapsed days since first ledger entry `>= warmup_rebalances * 7`
- Fall back to equal weight during warmup
- `return_weighted`: `weight_i = max(0, mean(recent_returns_i))`. All ≤ 0 → equal fallback.
- `inverse_vol`: `weight_i = 1 / max(std(recent_returns_i, ddof=1), 1e-6)`. Normalize to sum=1.
- Both use at most the last `max(warmup_rebalances, 12)` entries from the ledger.

Tests (~5):
- return_weighted prefers sub with higher mean return after warmup
- return_weighted all-negative → equal fallback
- inverse_vol assigns more weight to lower-vol sub
- both fall back to equal during warmup
- warmup gate checks both rebalance count AND elapsed days

### Task 3.4: Correlation warnings (warn-only)

After warmup, if `len(sub_hypothetical_returns[i]) >= min_overlap`:
- Compute pairwise Pearson correlation between all pairs of sub-strategies' return series
- If `|corr| > threshold` (default 0.9), append structured warning to `self.state["correlation_warnings"]`
- Warning payload: `{"sub_i": int, "sub_j": int, "correlation": float, "n_samples": int}`
- Min overlap = `max(warmup_rebalances, 8)`. Shorter → skip pair.

Tests (~3):
- identical static strategies → correlation ~1.0, warning emitted with correct payload structure
- uncorrelated strategies → no warning
- short series (below min_overlap) → no warning (avoid false positives)

### Task 3.5: Nested ensembles + end-to-end integration

Tests (~4):
- `StrategyEnsemble([EnsembleA, EnsembleB, TopNRotation])` works recursively
- Inner ensemble's `self.state` ledger is isolated from outer's `self.state` (no cross-pollution)
- Nested lookback: inner has lookback=300, outer lookback = max(300, other_sub_lookback) — no double-buffer
- End-to-end: StrategyEnsemble through `run_portfolio_backtest` with 2 TopNRotation subs produces valid equity curve

### Task 3.6: Code review + CLAUDE.md update

- Run full test suite
- Dispatch code-reviewer
- Fix Critical/Important
- Update CLAUDE.md + roadmap D5
- Commit + push

---

## Execution Recommendation

6 tasks, ~28 tests. Tasks 3.1-3.3 sequential (ledger builds on skeleton). Task 3.4 after 3.3 (needs ledger). Task 3.5 after 3.4 (needs all features). Use inline execution (tasks are tightly coupled, all modify `ensemble.py`).
