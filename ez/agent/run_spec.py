"""B1: RunSpec — standardized experiment input.

A RunSpec fully describes a reproducible experiment: which strategy,
which data, which parameters, which run modes. The spec_id is a content
hash so the same experiment input always maps to the same identifier
(idempotency key).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date


@dataclass
class RunSpec:
    """Immutable description of a single experiment run."""

    # Required
    strategy_name: str
    strategy_params: dict[str, int | float]
    symbol: str
    market: str
    start_date: date
    end_date: date

    # Defaults
    period: str = "daily"
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.00008
    min_commission: float = 0.0
    slippage_rate: float = 0.0

    # Market rules (V2.6)
    use_market_rules: bool = False
    t_plus_1: bool = True
    price_limit_pct: float = 0.1
    lot_size: int = 100

    # Run modes
    run_backtest: bool = True
    run_wfo: bool = True
    wfo_n_splits: int = 5
    wfo_train_ratio: float = 0.7

    # Metadata (not included in spec_id)
    tags: list[str] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.strategy_name:
            raise ValueError("strategy_name must not be empty")
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.commission_rate < 0 or self.min_commission < 0 or self.slippage_rate < 0:
            raise ValueError("cost parameters must be >= 0")
        if self.price_limit_pct < 0 or self.price_limit_pct > 1:
            raise ValueError("price_limit_pct must be in [0, 1]")
        if self.lot_size < 0:
            raise ValueError("lot_size must be >= 0")
        if not self.run_backtest and not self.run_wfo:
            raise ValueError("at least one of run_backtest/run_wfo must be True")

    @property
    def spec_id(self) -> str:
        """Content hash (16 hex chars) for idempotency.

        Same inputs always produce the same spec_id regardless of
        metadata (tags, description).
        """
        d = {
            "strategy_name": self.strategy_name,
            "strategy_params": {
                k: int(v) if isinstance(v, float) and v == int(v) else v
                for k, v in sorted(self.strategy_params.items())
            },
            "symbol": self.symbol,
            "market": self.market,
            "period": self.period,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "initial_capital": self.initial_capital,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "slippage_rate": self.slippage_rate,
            "run_backtest": self.run_backtest,
            "run_wfo": self.run_wfo,
            "wfo_n_splits": self.wfo_n_splits,
            "wfo_train_ratio": self.wfo_train_ratio,
            "use_market_rules": self.use_market_rules,
            "t_plus_1": self.t_plus_1,
            "price_limit_pct": self.price_limit_pct,
            "lot_size": self.lot_size,
        }
        raw = json.dumps(d, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Serialize to dict (for JSON/DuckDB storage)."""
        return {
            "spec_id": self.spec_id,
            "strategy_name": self.strategy_name,
            "strategy_params": self.strategy_params,
            "symbol": self.symbol,
            "market": self.market,
            "period": self.period,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "initial_capital": self.initial_capital,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "slippage_rate": self.slippage_rate,
            "run_backtest": self.run_backtest,
            "run_wfo": self.run_wfo,
            "wfo_n_splits": self.wfo_n_splits,
            "wfo_train_ratio": self.wfo_train_ratio,
            "use_market_rules": self.use_market_rules,
            "t_plus_1": self.t_plus_1,
            "price_limit_pct": self.price_limit_pct,
            "lot_size": self.lot_size,
            "tags": self.tags,
            "description": self.description,
        }
