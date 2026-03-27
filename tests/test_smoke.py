"""Smoke tests -- run after every change."""


def test_all_core_imports():
    import ez.types
    import ez.errors
    import ez.config
    import ez.data.provider
    import ez.data.store
    import ez.data.validator
    import ez.factor.base
    import ez.factor.evaluator
    import ez.strategy.base
    import ez.strategy.loader
    import ez.backtest.engine
    import ez.backtest.metrics
    import ez.backtest.walk_forward
    import ez.backtest.significance


def test_strategy_registration():
    from ez.strategy.base import Strategy
    from ez.strategy.loader import load_all_strategies
    load_all_strategies()
    assert len(Strategy._registry) > 0


def test_factor_instantiation():
    from ez.factor.builtin.technical import MA
    ma = MA(period=5)
    assert ma.warmup_period == 5
    assert ma.name == "ma_5"
