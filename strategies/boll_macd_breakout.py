"""布林带突破结合MACD柱状线策略 - 当价格突破布林带上轨且MACD柱状线由负转正时买入，当价格跌破布林带中轨或MACD柱状线由正转负时卖出。"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import BOLL, MACD
from ez.strategy.base import Strategy


class BollMacdBreakout(Strategy):
    """布林带突破结合MACD柱状线策略"""

    def __init__(self, boll_period: int = 20, boll_std_dev: float = 2.0):
        self.boll_period = boll_period
        self.boll_std_dev = boll_std_dev

    @classmethod
    def get_description(cls) -> str:
        return "布林带突破结合MACD柱状线策略: 价格突破布林带上轨且MACD柱状线由负转正时买入，价格跌破布林带中轨或MACD柱状线由正转负时卖出。"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "boll_period": {"type": "int", "default": 20, "min": 10, "max": 60, "label": "布林带周期"},
            "boll_std_dev": {"type": "float", "default": 2.0, "min": 1.5, "max": 3.0, "step": 0.1, "label": "布林带标准差倍数"},
        }

    def required_factors(self) -> list[Factor]:
        return [BOLL(period=self.boll_period, std_dev=self.boll_std_dev), MACD()]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        # 获取布林带列名
        boll_upper_col = f"boll_upper_{self.boll_period}"
        boll_mid_col = f"boll_mid_{self.boll_period}"
        boll_lower_col = f"boll_lower_{self.boll_period}"
        
        # 获取MACD柱状线
        macd_hist_col = "macd_hist"
        
        # 计算MACD柱状线变化：当前值 > 0 且 前一日值 <= 0 表示由负转正
        macd_turn_positive = (data[macd_hist_col] > 0) & (data[macd_hist_col].shift(1) <= 0)
        
        # 计算MACD柱状线变化：当前值 < 0 且 前一日值 >= 0 表示由正转负
        macd_turn_negative = (data[macd_hist_col] < 0) & (data[macd_hist_col].shift(1) >= 0)
        
        # 买入条件：价格突破布林带上轨 且 MACD柱状线由负转正
        buy_condition = (data["adj_close"] > data[boll_upper_col]) & macd_turn_positive
        
        # 卖出条件：价格跌破布林带中轨 或 MACD柱状线由正转负
        sell_condition = (data["adj_close"] < data[boll_mid_col]) | macd_turn_negative
        
        # 初始化信号序列
        signals = pd.Series(0.0, index=data.index)
        
        # 状态跟踪：1表示持有，0表示空仓
        position = 0
        
        for i in range(len(data)):
            if position == 0 and buy_condition.iloc[i]:
                # 空仓状态下满足买入条件，买入
                signals.iloc[i] = 1.0
                position = 1
            elif position == 1 and sell_condition.iloc[i]:
                # 持仓状态下满足卖出条件，卖出
                signals.iloc[i] = 0.0
                position = 0
            else:
                # 保持当前状态
                signals.iloc[i] = float(position)
        
        return signals