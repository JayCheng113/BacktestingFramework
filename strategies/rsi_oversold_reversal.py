"""RSI超卖反转策略 - 在RSI超卖区域结合价格动能确认进行精准反转交易"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import RSI, MA
from ez.strategy.base import Strategy


class RsiOversoldReversal(Strategy):
    """RSI超卖反转策略: RSI低于30且价格出现看涨信号时买入，RSI高于70或跌破短期均线时卖出"""

    def __init__(self, rsi_period: int = 14, ma_period: int = 5, oversold_level: int = 30, overbought_level: int = 70):
        self.rsi_period = rsi_period
        self.ma_period = ma_period
        self.oversold_level = oversold_level
        self.overbought_level = overbought_level

    @classmethod
    def get_description(cls) -> str:
        return "RSI超卖反转策略: RSI低于30且当日收盘价高于前一日收盘价时买入，RSI高于70或价格跌破短期均线时卖出"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "rsi_period": {"type": "int", "default": 14, "min": 5, "max": 30, "label": "RSI周期"},
            "ma_period": {"type": "int", "default": 5, "min": 2, "max": 20, "label": "短期均线周期"},
            "oversold_level": {"type": "int", "default": 30, "min": 10, "max": 40, "label": "超卖阈值"},
            "overbought_level": {"type": "int", "default": 70, "min": 60, "max": 90, "label": "超买阈值"},
        }

    def required_factors(self) -> list[Factor]:
        return [
            RSI(period=self.rsi_period),
            MA(period=self.ma_period)
        ]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        # 获取因子列名
        rsi_col = f"rsi_{self.rsi_period}"
        ma_col = f"ma_{self.ma_period}"
        
        # 计算价格动量：当日收盘价高于前一日收盘价
        price_momentum = data["adj_close"] > data["adj_close"].shift(1)
        
        # 买入条件：RSI超卖且价格出现看涨信号
        buy_condition = (data[rsi_col] < self.oversold_level) & price_momentum
        
        # 卖出条件：RSI超买或价格跌破短期均线
        sell_condition = (data[rsi_col] > self.overbought_level) | (data["adj_close"] < data[ma_col])
        
        # 生成信号：买入时为1.0（满仓），卖出时为0.0（空仓）
        signals = pd.Series(0.0, index=data.index)
        signals[buy_condition] = 1.0
        signals[sell_condition] = 0.0
        
        return signals