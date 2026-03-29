"""
RSI超卖反转策略
当RSI低于30时买入（超卖），高于70时卖出（超买）
"""

import pandas as pd
from typing import Dict, Any
from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import RSI


class RSIOversoldReversalStrategy(Strategy):
    """RSI超卖反转策略"""
    
    def __init__(self, rsi_period: int = 14, oversold_threshold: float = 30.0, overbought_threshold: float = 70.0):
        """初始化策略"""
        self.rsi_period = rsi_period
        self.oversold_threshold = oversold_threshold
        self.overbought_threshold = overbought_threshold
    
    @classmethod
    def get_description(cls) -> str:
        return "RSI超卖反转策略: RSI低于阈值买入(超卖)，高于阈值卖出(超买)。均值回归型策略。"
    
    @classmethod
    def get_parameters_schema(cls) -> Dict[str, Dict[str, Any]]:
        """返回策略参数配置"""
        return {
            "rsi_period": {
                "type": "int",
                "default": 14,
                "min": 5,
                "max": 30,
                "label": "RSI周期",
                "description": "RSI计算周期"
            },
            "oversold_threshold": {
                "type": "float",
                "default": 30.0,
                "min": 10.0,
                "max": 40.0,
                "label": "超卖阈值",
                "description": "RSI低于此值视为超卖，触发买入信号"
            },
            "overbought_threshold": {
                "type": "float",
                "default": 70.0,
                "min": 60.0,
                "max": 90.0,
                "label": "超买阈值",
                "description": "RSI高于此值视为超买，触发卖出信号"
            }
        }
    
    def required_factors(self) -> list[Factor]:
        """返回需要的因子列表"""
        # 只需要RSI因子
        return [RSI(period=self.rsi_period)]
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """
        生成交易信号
        返回: pd.Series, 值在0.0到1.0之间
        """
        # 获取RSI值
        rsi_column = f"RSI_{self.rsi_period}"
        
        # 如果数据中没有RSI列，返回全0信号
        if rsi_column not in data.columns:
            return pd.Series(0.0, index=data.index)
        
        rsi = data[rsi_column]
        
        # 构建信号序列
        signals = pd.Series(index=data.index, dtype=float)
        position = 0.0
        
        for i in range(len(data)):
            if pd.notna(rsi.iloc[i]):
                if rsi.iloc[i] < self.oversold_threshold:
                    position = 1.0  # 买入：超卖
                elif rsi.iloc[i] > self.overbought_threshold:
                    position = 0.0  # 卖出：超买
                # 其他情况：保持当前仓位
            signals.iloc[i] = position
        
        return signals