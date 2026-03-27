import pandas as pd
import numpy as np
from ez.factor.builtin.technical import MA, EMA


def test_ma_warmup_period():
    assert MA(period=5).warmup_period == 5
    assert MA(period=20).warmup_period == 20


def test_ma_computation_known_values():
    data = pd.DataFrame({"adj_close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]})
    result = MA(period=5).compute(data)
    assert "ma_5" in result.columns
    assert pd.isna(result["ma_5"].iloc[3])
    assert result["ma_5"].iloc[4] == 3.0
    assert result["ma_5"].iloc[5] == 4.0


def test_ema_warmup_period():
    assert EMA(period=12).warmup_period == 12


def test_ema_computation():
    data = pd.DataFrame({"adj_close": list(range(1, 21))})
    result = EMA(period=10).compute(data)
    assert "ema_10" in result.columns
    assert pd.isna(result["ema_10"].iloc[8])
    assert not pd.isna(result["ema_10"].iloc[9])


def test_ma_preserves_original_columns():
    data = pd.DataFrame({"adj_close": [1, 2, 3, 4, 5], "volume": [100, 200, 300, 400, 500]})
    result = MA(period=3).compute(data)
    assert "adj_close" in result.columns
    assert "volume" in result.columns
    assert "ma_3" in result.columns
