"""RSI反转策略 - 捕捉超卖反弹和超买回调。

策略逻辑：
1. RSI(14)低于30时买入（超卖区域，预期反弹）
2. RSI(14)高于70时卖出（超买区域，预期回调）
适用于震荡市中的反转交易。
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import RSI
from ez.strategy.base import Strategy


class RSIReversalStrategy(Strategy):
    """RSI反转策略：超卖买入，超买卖出。"""

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @classmethod
    def get_description(cls) -> str:
        return "RSI反转策略: RSI低于30时买入（超卖反弹），RSI高于70时卖出（超买回调）。适用于震荡市。"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "period": {"type": "int", "default": 14, "min": 5, "max": 30, "label": "RSI周期"},
            "oversold": {"type": "float", "default": 30.0, "min": 10.0, "max": 40.0, "label": "超卖阈值"},
            "overbought": {"type": "float", "default": 70.0, "min": 60.0, "max": 90.0, "label": "超买阈值"},
        }

    def required_factors(self) -> list[Factor]:
        return [RSI(period=self.period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        rsi_col = f"rsi_{self.period}"
        # RSI低于超卖阈值时买入（信号=1.0），RSI高于超买阈值时卖出（信号=0.0）
        # 其他情况保持原有仓位（使用前向填充）
        signals = pd.Series(0.0, index=data.index)
        
        # 生成买入信号
        buy_signals = data[rsi_col] < self.oversold
        # 生成卖出信号
        sell_signals = data[rsi_col] > self.overbought
        
        # 当出现买入信号时，信号设为1.0
        signals[buy_signals] = 1.0
        # 当出现卖出信号时，信号设为0.0
        signals[sell_signals] = 0.0
        
        # 对于没有信号的位置，使用前向填充保持仓位
        # 初始位置如果没有信号，保持空仓（0.0）
        signals = signals.replace(0.0, pd.NA).ffill().fillna(0.0)
        
        return signals