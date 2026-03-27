"""Portfolio state tracking during backtest.

[CORE] — tracks cash, position, equity over time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ez.types import TradeRecord


@dataclass
class PortfolioState:
    cash: float
    position_shares: float = 0.0
    position_value: float = 0.0
    trades: list[TradeRecord] = field(default_factory=list)

    @property
    def equity(self) -> float:
        return self.cash + self.position_value
