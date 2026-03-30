"""RSI超卖反转策略 — 在RSI超卖时买入，超买时卖出。"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import RSI
from ez.strategy.base import Strategy


class RSIReversalStrategy(Strategy):
    """RSI超卖反转策略: 当RSI低于超卖阈值时买入，高于超买阈值时卖出。"""

    def __init__(self, rsi_period: int = 14, oversold: float = 30.0, overbought: float = 70.0,
                 confirm_bars: int = 2, neutral_zone: float = 50.0):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.confirm_bars = confirm_bars  # 确认K线数
        self.neutral_zone = neutral_zone  # 中性区域，用于止损

    @classmethod
    def get_description(cls) -> str:
        return "RSI超卖反转策略: RSI低于超卖阈值时买入，高于超买阈值时卖出。优化版包含确认机制和止损。"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "rsi_period": {"type": "int", "default": 14, "min": 5, "max": 30, "label": "RSI周期"},
            "oversold": {"type": "float", "default": 30.0, "min": 10.0, "max": 40.0, "label": "超卖阈值"},
            "overbought": {"type": "float", "default": 70.0, "min": 60.0, "max": 90.0, "label": "超买阈值"},
            "confirm_bars": {"type": "int", "default": 2, "min": 1, "max": 5, "label": "确认K线数"},
            "neutral_zone": {"type": "float", "default": 50.0, "min": 40.0, "max": 60.0, "label": "中性区域"},
        }

    def required_factors(self) -> list[Factor]:
        return [RSI(period=self.rsi_period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        rsi_col = f"rsi_{self.rsi_period}"
        
        # 初始化信号序列为0（空仓）
        signals = pd.Series(0.0, index=data.index)
        
        # 计算RSI的滚动窗口状态
        rsi = data[rsi_col]
        
        # 创建超卖和超买条件
        oversold_condition = rsi < self.oversold
        overbought_condition = rsi > self.overbought
        neutral_condition = (rsi >= self.neutral_zone - 5) & (rsi <= self.neutral_zone + 5)
        
        # 计算连续超卖/超买的天数
        oversold_streak = oversold_condition.rolling(window=self.confirm_bars).sum()
        overbought_streak = overbought_condition.rolling(window=self.confirm_bars).sum()
        
        # 买入条件：连续超卖确认
        buy_condition = oversold_streak >= self.confirm_bars
        
        # 卖出条件：连续超买确认或进入中性区域（止损）
        sell_condition = (overbought_streak >= self.confirm_bars) | neutral_condition
        
        # 生成信号
        # 当买入条件满足时，信号为1（满仓）
        signals[buy_condition] = 1.0
        
        # 当卖出条件满足时，信号为0（空仓）
        signals[sell_condition] = 0.0
        
        # 使用前向填充来保持持仓状态，避免频繁交易
        signals = signals.ffill().fillna(0.0)
        
        return signals