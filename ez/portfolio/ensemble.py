"""V2.13 D5: StrategyEnsemble — composition-layer heuristic orchestrator.

Combines multiple ``PortfolioStrategy`` instances into a single weight
vector. **NOT a statistical meta-optimizer** — V1 uses pure-alpha
hypothetical returns as a proxy, documented as an approximation.

4 modes:
- ``"equal"`` — arithmetic mean of sub-strategy weight vectors. Exact.
- ``"manual"`` — user-provided static weights. Exact.
- ``"return_weighted"`` — weight ∝ max(0, mean(recent hypothetical
  returns)). **Proxy, NOT rank IC.** Docstring says so explicitly.
- ``"inverse_vol"`` — weight ∝ 1/std(recent hypothetical returns).
  **Proxy, NOT full risk parity** (no covariance matrix). Docstring
  says so explicitly.

Sub-strategy ownership: constructor ``copy.deepcopy``s each sub-strategy
to prevent external state sharing. If deepcopy fails, ``TypeError``
with guidance.

Consumption path: V1 is Python-only. StrategyEnsemble is NOT available
via dropdown/resolve (registry popped). Phase 6 UI needs a wizard.

Combination rules (see plan Combination Formula):
1. Sub outputs NOT individually normalized (cash intent preserved)
2. ``combined[s] = sum(ew[i] * sub_outputs[i].get(s, 0) for i)``
3. ``combined`` can sum < 1 (remainder = cash, intended)
4. Exception sub → warn + ``{}``, ew re-normalized across active subs
5. Legitimate ``{}`` return → no warn, sub "choosing cash this period"
"""
from __future__ import annotations

import copy
import logging
from datetime import date as _date, datetime
from typing import Literal

import numpy as np
import pandas as pd

from ez.portfolio.portfolio_strategy import PortfolioStrategy

_logger = logging.getLogger(__name__)

EnsembleMode = Literal["equal", "manual", "return_weighted", "inverse_vol"]


class StrategyEnsemble(PortfolioStrategy):
    """Combine multiple sub-strategies into a single weight vector.

    This is a **composition-layer heuristic orchestrator**, not a
    statistical meta-optimizer. ``return_weighted`` and ``inverse_vol``
    modes use hypothetical pure-alpha returns (ignoring costs, slippage,
    optimizer, risk-manager) as a proxy. Full realized-return ensemble
    is deferred to V2.13.1.

    Args:
        strategies: Sub-strategies to combine. Each is ``copy.deepcopy``'d
            at construction to prevent external state sharing.
        mode: Combination mode (see module docstring).
        ensemble_weights: Required for ``mode="manual"``. Non-negative,
            sum > 0. Auto-normalized to sum=1.
        warmup_rebalances: Minimum rebalance count before
            ``return_weighted`` / ``inverse_vol`` switch from equal
            fallback. Default 8 (~2 months weekly).
        correlation_threshold: Pairwise |corr| above this triggers a
            warning in ``self.state["correlation_warnings"]``.
    """

    def __init__(
        self,
        strategies: list[PortfolioStrategy],
        mode: EnsembleMode = "equal",
        ensemble_weights: list[float] | None = None,
        warmup_rebalances: int = 8,
        correlation_threshold: float = 0.9,
    ):
        super().__init__()
        if not strategies:
            raise ValueError("StrategyEnsemble requires at least one strategy")
        if mode not in ("equal", "manual", "return_weighted", "inverse_vol"):
            raise ValueError(f"Invalid mode: {mode}")
        if mode == "manual":
            if ensemble_weights is None:
                raise ValueError('mode="manual" requires ensemble_weights')
            if len(ensemble_weights) != len(strategies):
                raise ValueError(
                    f"ensemble_weights length {len(ensemble_weights)} != "
                    f"strategies length {len(strategies)}"
                )
            if any(w < 0 for w in ensemble_weights):
                raise ValueError("ensemble_weights must be non-negative")
            if sum(ensemble_weights) <= 0:
                raise ValueError("ensemble_weights must sum to > 0")
        if warmup_rebalances < 1:
            raise ValueError("warmup_rebalances must be >= 1")

        # Deepcopy each sub-strategy to prevent external state sharing.
        # If a sub holds non-picklable state (DB conn, file handle),
        # deepcopy will fail — that's intentional: such strategies
        # shouldn't be wrapped in an ensemble without a factory pattern.
        try:
            self._strategies = [copy.deepcopy(s) for s in strategies]
        except Exception as e:
            raise TypeError(
                f"copy.deepcopy failed on a sub-strategy: {e}. "
                f"StrategyEnsemble requires deepcopy-safe sub-strategies "
                f"(no DB connections, file handles, etc.)."
            ) from e

        self._mode = mode
        self._manual_weights = ensemble_weights
        self._warmup = warmup_rebalances
        self._corr_threshold = correlation_threshold
        self._min_warmup_days = warmup_rebalances * 7

        # Per-sub exception tracking (keyed by index)
        self._sub_exception_warned: dict[int, bool] = {}

        # State ledger (pure dict/list/float — no pandas)
        self.state.setdefault("sub_target_weights", [[] for _ in self._strategies])
        self.state.setdefault("sub_hypothetical_returns", [[] for _ in self._strategies])
        self.state.setdefault("last_rebalance_date", None)
        self.state.setdefault("first_rebalance_date", None)
        self.state.setdefault("correlation_warnings", [])
        self.state.setdefault("failure_counts", [0] * len(self._strategies))

    @property
    def lookback_days(self) -> int:
        """Max of sub-strategies' lookback. No buffer at ensemble level
        (buffer is leaf strategy's responsibility — "only leaf adds
        buffer" rule from Phase 1 round 6 codex review)."""
        if not self._strategies:
            return 252
        return max(s.lookback_days for s in self._strategies)

    def generate_weights(
        self,
        universe_data,
        date,
        prev_weights,
        prev_returns,
    ) -> dict[str, float]:
        current: _date = date.date() if hasattr(date, "date") else date

        # Step 1: call each sub-strategy. Distinguish exception from
        # legitimate {} (no-signal).
        sub_outputs: list[dict[str, float]] = []
        sub_active: list[bool] = []  # True = produced output (even if {})
        for i, s in enumerate(self._strategies):
            try:
                out = s.generate_weights(
                    universe_data, date, prev_weights, prev_returns,
                )
                sub_outputs.append(out if out is not None else {})
                sub_active.append(True)
            except Exception as e:
                if not self._sub_exception_warned.get(i, False):
                    _logger.warning(
                        "StrategyEnsemble: sub-strategy #%d (%s) raised "
                        "%s: %s. Treating as cash this period. "
                        "(one-shot warning per sub-strategy)",
                        i, type(s).__name__, type(e).__name__, e,
                    )
                    self._sub_exception_warned[i] = True
                sub_outputs.append({})
                sub_active.append(False)
                self.state["failure_counts"][i] += 1

        # Step 2: update hypothetical-return ledger (Task 3.2)
        self._update_hypothetical_returns(universe_data, current)

        # Step 3: compute ensemble weights
        ew = self._compute_ensemble_weights(sub_active)

        # Step 4: weighted combination (Combination Formula rules 1-3)
        combined: dict[str, float] = {}
        for i, (sub_out, w) in enumerate(zip(sub_outputs, ew)):
            for sym, weight in sub_out.items():
                combined[sym] = combined.get(sym, 0.0) + w * weight

        # Step 5: record this rebalance's sub-strategy target weights
        for i, out in enumerate(sub_outputs):
            self.state["sub_target_weights"][i].append({
                "date": current.isoformat(),
                "weights": dict(out),
            })
        if self.state["first_rebalance_date"] is None:
            self.state["first_rebalance_date"] = current.isoformat()
        self.state["last_rebalance_date"] = current.isoformat()

        # Step 6: correlation warnings (Task 3.4, after warmup)
        self._check_correlation_warnings()

        return combined

    def _compute_ensemble_weights(
        self, sub_active: list[bool],
    ) -> list[float]:
        """Compute per-sub weights based on mode. Re-normalize if some
        subs failed (exception, not no-signal)."""
        n = len(self._strategies)

        if self._mode == "manual":
            raw = list(self._manual_weights)  # type: ignore[arg-type]
        elif self._mode == "equal":
            raw = [1.0] * n
        elif self._mode == "return_weighted":
            raw = self._return_weighted_or_fallback()
        elif self._mode == "inverse_vol":
            raw = self._inverse_vol_or_fallback()
        else:
            raw = [1.0] * n

        # Re-normalize: zero out failed subs, redistribute among active
        for i in range(n):
            if not sub_active[i]:
                raw[i] = 0.0

        total = sum(raw)
        if total <= 0:
            # All subs failed or all weights zero → equal among active
            active_count = sum(1 for a in sub_active if a)
            if active_count == 0:
                return [0.0] * n
            return [1.0 / active_count if a else 0.0 for a in sub_active]
        return [w / total for w in raw]

    def _return_weighted_or_fallback(self) -> list[float]:
        """Weight ∝ max(0, mean(recent hypothetical returns)).

        **This is NOT rank IC.** It's a proxy that gives more weight to
        sub-strategies with higher recent pure-alpha returns. Falls back
        to equal weight during warmup or when all means are ≤ 0.
        """
        n = len(self._strategies)
        if not self._warmup_complete():
            return [1.0] * n

        raw = []
        for r_list in self.state["sub_hypothetical_returns"]:
            recent = r_list[-max(self._warmup, 12):]
            mean_r = float(np.mean(recent)) if recent else 0.0
            raw.append(max(0.0, mean_r))

        if sum(raw) <= 0:
            return [1.0] * n  # all non-positive → equal fallback
        return raw

    def _inverse_vol_or_fallback(self) -> list[float]:
        """Weight ∝ 1 / std(recent hypothetical returns).

        **This is NOT full risk parity** (no covariance matrix). It's
        inverse-volatility weighting, which is exact risk parity only
        when sub-strategies are uncorrelated. Falls back to equal weight
        during warmup.
        """
        n = len(self._strategies)
        if not self._warmup_complete():
            return [1.0] * n

        eps = 1e-6
        raw = []
        for r_list in self.state["sub_hypothetical_returns"]:
            recent = r_list[-max(self._warmup, 12):]
            sd = float(np.std(recent, ddof=1)) if len(recent) > 1 else 0.0
            raw.append(1.0 / max(sd, eps))
        return raw

    def _warmup_complete(self) -> bool:
        """Check both rebalance count AND elapsed days."""
        returns_lists = self.state["sub_hypothetical_returns"]
        if not returns_lists:
            return False
        min_len = min(len(r) for r in returns_lists)
        if min_len < self._warmup:
            return False
        # Check elapsed days
        first = self.state.get("first_rebalance_date")
        last = self.state.get("last_rebalance_date")
        if first is None or last is None:
            return False
        elapsed = (_date.fromisoformat(last) - _date.fromisoformat(first)).days
        return elapsed >= self._min_warmup_days

    def _update_hypothetical_returns(
        self,
        universe_data: dict[str, pd.DataFrame],
        current: _date,
    ) -> None:
        """Reconstruct per-sub hypothetical return since last rebalance.

        For each sub-strategy, compute the close-to-close return of its
        target weight vector between the previous rebalance date and the
        current date:

            ``r_i = sum(w_sym * (close[current-1] / close[prev] - 1))``

        Uses ``close`` prices (what the engine actually trades on).
        **Ignores** costs, slippage, optimizer override, risk-manager
        reduction, lot-size rounding. This is a pure-alpha approximation.

        Only runs if there's a previous rebalance recorded (first call
        has no prior weights to reconstruct returns from).
        """
        last_rebal_str = self.state.get("last_rebalance_date")
        if last_rebal_str is None:
            return  # first rebalance — nothing to reconstruct yet

        prev_date = _date.fromisoformat(last_rebal_str)
        if prev_date >= current:
            return  # same date or going backwards — skip

        for i, sub_records in enumerate(self.state["sub_target_weights"]):
            if not sub_records:
                # Sub has never produced weights — 0 return
                self.state["sub_hypothetical_returns"][i].append(0.0)
                continue

            prev_entry = sub_records[-1]
            prev_weights = prev_entry.get("weights", {})
            if not prev_weights:
                self.state["sub_hypothetical_returns"][i].append(0.0)
                continue

            period_return = 0.0
            for sym, w in prev_weights.items():
                if w <= 0 or sym not in universe_data:
                    continue
                df = universe_data[sym]
                if not isinstance(df.index, pd.DatetimeIndex):
                    continue

                # Close at previous rebalance date
                mask_prev = df.index.date <= prev_date
                if not mask_prev.any():
                    continue
                prev_close = float(df.loc[mask_prev, "close"].iloc[-1])
                if prev_close <= 0:
                    continue

                # Close at latest bar before current date (anti-lookahead:
                # < current, matching engine's slice_universe_data convention)
                mask_current = df.index.date < current
                if not mask_current.any():
                    continue
                current_close = float(df.loc[mask_current, "close"].iloc[-1])

                sym_ret = current_close / prev_close - 1.0
                period_return += w * sym_ret

            self.state["sub_hypothetical_returns"][i].append(period_return)

    def _check_correlation_warnings(self) -> None:
        """Pairwise correlation check on hypothetical return series.

        Placeholder for Task 3.4 — no-op for now.
        """
        # TODO: Task 3.4
        pass


# Prevent auto-registration: StrategyEnsemble cannot be zero-arg
# instantiated (requires `strategies` list). Mirrors MLAlpha and
# AlphaCombiner dual-dict pop pattern. V1 is Python-only direct use;
# Phase 6 UI needs a wizard/template path if it wants to offer ensemble.
PortfolioStrategy._registry.pop("StrategyEnsemble", None)
_se_key = f"{StrategyEnsemble.__module__}.StrategyEnsemble"
PortfolioStrategy._registry_by_key.pop(_se_key, None)
