"""F1: Parameter grid + random search — generates candidate RunSpecs.

Given a strategy, data range, and parameter search space, produce
a list of RunSpec instances covering all (grid) or sampled (random)
parameter combinations.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from datetime import date

from ez.agent.run_spec import RunSpec


@dataclass
class ParamRange:
    """Search space for a single parameter."""

    name: str
    values: list[int | float | str | bool]  # discrete choices for grid; sample pool for random


@dataclass
class SearchConfig:
    """Configuration for candidate generation.

    V2.12.1 post-review (codex): now includes A-share market rule fields
    (use_market_rules / t_plus_1 / price_limit_pct / lot_size) so candidate
    search produces RunSpecs whose execution environment matches the final
    experiment run. Prior version omitted these fields, causing the search
    results to rank strategies under a different execution environment than
    what the ExperimentPanel would actually use.
    """

    strategy_name: str
    param_ranges: list[ParamRange]
    symbol: str
    market: str = "cn_stock"
    period: str = "daily"
    start_date: date = field(default_factory=lambda: date(2020, 1, 1))
    end_date: date = field(default_factory=lambda: date(2024, 12, 31))
    run_wfo: bool = True
    wfo_n_splits: int = 3
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.00008
    min_commission: float = 0.0
    slippage_rate: float = 0.001
    # Market rules (V2.12.1 codex fix)
    use_market_rules: bool = False
    t_plus_1: bool = True
    price_limit_pct: float = 0.1
    lot_size: int = 100


def grid_search(config: SearchConfig) -> list[RunSpec]:
    """Generate RunSpecs for all parameter combinations (Cartesian product)."""
    if not config.param_ranges:
        return [_make_spec(config, {})]

    names = [pr.name for pr in config.param_ranges]
    value_lists = [pr.values for pr in config.param_ranges]

    specs = []
    for combo in itertools.product(*value_lists):
        params = dict(zip(names, combo))
        specs.append(_make_spec(config, params))
    return specs


def random_search(config: SearchConfig, n_samples: int, seed: int | None = None) -> list[RunSpec]:
    """Sample n_samples random parameter combinations (no duplicates if possible)."""
    if not config.param_ranges:
        return [_make_spec(config, {})]

    rng = random.Random(seed)
    names = [pr.name for pr in config.param_ranges]
    value_lists = [pr.values for pr in config.param_ranges]

    # Total possible combinations
    total = 1
    for vl in value_lists:
        total *= len(vl)

    if n_samples >= total:
        return grid_search(config)

    seen: set[tuple] = set()
    specs = []
    max_iters = n_samples * 100  # safety limit to prevent infinite loop
    iters = 0
    while len(specs) < n_samples and iters < max_iters:
        iters += 1
        combo = tuple(rng.choice(vl) for vl in value_lists)
        if combo in seen:
            continue
        seen.add(combo)
        params = dict(zip(names, combo))
        specs.append(_make_spec(config, params))
    return specs


def _make_spec(config: SearchConfig, params: dict[str, int | float]) -> RunSpec:
    return RunSpec(
        strategy_name=config.strategy_name,
        strategy_params=params,
        symbol=config.symbol,
        market=config.market,
        period=config.period,
        start_date=config.start_date,
        end_date=config.end_date,
        initial_capital=config.initial_capital,
        commission_rate=config.commission_rate,
        min_commission=config.min_commission,
        slippage_rate=config.slippage_rate,
        run_wfo=config.run_wfo,
        wfo_n_splits=config.wfo_n_splits,
        # Propagate market rule fields so search, prefilter, and full run
        # all use the same execution environment (codex fix).
        use_market_rules=config.use_market_rules,
        t_plus_1=config.t_plus_1,
        price_limit_pct=config.price_limit_pct,
        lot_size=config.lot_size,
    )
