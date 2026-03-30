"""RSI与布林带反转策略 - 结合RSI超卖/超买和布林带支撑/阻力进行多重确认的反转交易。
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import RSI, BOLL
from ez.strategy.base import Strategy


class RSIBollReversal(Strategy):
    """RSI与布林带反转策略：当RSI超卖且价格触及布林带下轨时买入，当RSI超买或价格触及布林带上轨时卖出。"""

    def __init__(self, rsi_period: int = 14, boll_period: int = 20, rsi_oversold: float = 30.0, rsi_overbought: float = 70.0):
        self.rsi_period = rsi_period
        self.boll_period = boll_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    @classmethod
    def get_description(cls) -> str:
        return "RSI与布林带反转策略：结合RSI超卖/超买和布林带支撑/阻力进行多重确认的反转交易。"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "rsi_period": {"type": "int", "default": 14, "min": 5, "max": 30, "label": "RSI周期"},
            "boll_period": {"type": "int", "default": 20, "min": 10, "max": 50, "label": "布林带周期"},
            "rsi_oversold": {"type": "float", "default": 30.0, "min": 10.0, "max": 40.0, "label": "RSI超卖阈值"},
            "rsi_overbought": {"type": "float", "default": 70.0, "min": 60.0, "max": 90.0, "label": "RSI超买阈值"},
        }

    def required_factors(self) -> list[Factor]:
        return [
            RSI(period=self.rsi_period),
            BOLL(period=self.boll_period)
        ]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        # 获取RSI列名
        rsi_col = f"rsi_{self.rsi_period}"
        
        # 获取布林带列名
        boll_lower_col = f"boll_lower_{self.boll_period}"
        boll_upper_col = f"boll_upper_{self.boll_period}"
        
        # 买入条件：RSI低于超卖阈值且价格触及布林带下轨（收盘价 <= 下轨）
        buy_condition = (
            (data[rsi_col] < self.rsi_oversold) & 
            (data["adj_close"] <= data[boll_lower_col])
        )
        
        # 卖出条件：RSI高于超买阈值或价格触及布林带上轨（收盘价 >= 上轨）
        sell_condition = (
            (data[rsi_col] > self.rsi_overbought) | 
            (data["adj_close"] >= data[boll_upper_col])
        )
        
        # 生成信号：买入时为1.0（满仓），卖出时为0.0（空仓）
        signals = pd.Series(0.0, index=data.index)
        signals[buy_condition] = 1.0
        signals[sell_condition] = 0.0
        
        return signals