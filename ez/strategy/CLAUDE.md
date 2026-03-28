# ez/strategy -- Strategy Layer

## Responsibility
Define and auto-register trading strategies. Strategies produce position weight signals.

## Public Interfaces
- `Strategy(ABC)` -- [CORE] base class. `__init_subclass__` auto-registers.
  - Methods: `required_factors() -> list[Factor]`, `generate_signals(df) -> Series`
  - Class method: `get_parameters_schema() -> dict`
- `load_all_strategies()` -- [CORE] scans configured directories and imports all strategy modules

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| base.py | Strategy ABC | CORE |
| loader.py | Directory scanner | CORE |
| builtin/ma_cross.py | MA crossover reference | EXTENSION |
| builtin/momentum.py | Momentum strategy | EXTENSION |
| builtin/boll_reversion.py | Bollinger band reversion strategy | EXTENSION |

## Conventions
- `get_description()` -- not enforced by ABC but used by the API to display strategy descriptions
- `strategies/` user dir has no `__init__.py` (standalone imports only)

## Adding a New Strategy
1. Create `strategies/your_strategy.py` (or `ez/strategy/builtin/`)
2. Inherit from `Strategy`, implement `required_factors()`, `generate_signals()`, `get_parameters_schema()`
3. Run `pytest tests/test_strategy/` -- auto-validates

## Status
- Implemented: Strategy ABC, loader, MACrossStrategy, MomentumStrategy, BollReversionStrategy
