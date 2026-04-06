"""V2.12 D4: Portfolio risk manager — drawdown circuit breaker + turnover limiter.

Responsibilities (no overlap with Optimizer):
  - Drawdown: daily check, state machine (NORMAL <-> BREACHED)
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
          NORMAL -> drawdown > threshold -> BREACHED (scale = reduce_ratio)
          BREACHED -> drawdown < recovery_ratio -> NORMAL (scale = 1.0)
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

    def replay_equity(self, equity_curve: list[float]) -> None:
        """Rebuild internal state from historical equity curve.
        Used by Scheduler crash recovery to restore drawdown state machine."""
        self._peak_equity = 0.0
        self._is_breached = False
        for eq in equity_curve:
            self.check_drawdown(eq)

    def check_turnover(self, new_weights: dict[str, float],
                       prev_weights: dict[str, float]
                       ) -> tuple[dict[str, float], str | None]:
        """Rebalance-day turnover check. Mixes new/old if over limit.

        Formula: w_final = alpha * w_new + (1-alpha) * w_old
        where alpha = min(1, max_turnover / actual_turnover)
        """
        all_syms = set(new_weights) | set(prev_weights)
        # Single-sided turnover: max(total buys, total sells) as fraction of portfolio
        buy_side = sum(max(0, new_weights.get(s, 0) - prev_weights.get(s, 0)) for s in all_syms)
        sell_side = sum(max(0, prev_weights.get(s, 0) - new_weights.get(s, 0)) for s in all_syms)
        actual_turnover = max(buy_side, sell_side)
        if actual_turnover <= self._config.max_turnover:
            return new_weights, None

        alpha = self._config.max_turnover / actual_turnover
        mixed: dict[str, float] = {}
        for s in all_syms:
            w = alpha * new_weights.get(s, 0) + (1 - alpha) * prev_weights.get(s, 0)
            if w > 1e-10:
                mixed[s] = w
        # Normalize: mixing can produce sum != 1 if new/old have different totals
        total = sum(mixed.values())
        if total > 1.0 + 1e-9:
            mixed = {k: v / total for k, v in mixed.items()}
        return mixed, f"换手率{actual_turnover:.1%}超限{self._config.max_turnover:.0%}→混合α={alpha:.2f}"
