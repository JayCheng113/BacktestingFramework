"""RSI Extreme Threshold Strategy — uses extreme RSI levels (25/75) for entry and 50 midline for exit.

Strategy logic:
1. Buy when RSI(14) falls below 25 (oversold), sell when RSI rises back to 50
2. Sell when RSI(14) rises above 75 (overbought), buy when RSI falls back to 50
This approach filters false signals and improves risk-reward ratio.
"""
from __future__ import annotations

import pandas as pd

from ez.factor.base import Factor
from ez.factor.builtin.technical import RSI
from ez.strategy.base import Strategy


class RSIExtremeThreshold(Strategy):
    """RSI extreme threshold strategy with 50 midline exit."""

    def __init__(self, rsi_period: int = 14, oversold: float = 25.0, overbought: float = 75.0):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    @classmethod
    def get_description(cls) -> str:
        return "RSI极端阈值策略: RSI低于25时买入，回升至50时卖出；RSI高于75时卖出，回落至50时买入。使用极端阈值和50中线过滤假信号。"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {
            "rsi_period": {"type": "int", "default": 14, "min": 5, "max": 30, "label": "RSI周期"},
            "oversold": {"type": "float", "default": 25.0, "min": 10.0, "max": 40.0, "label": "超卖阈值"},
            "overbought": {"type": "float", "default": 75.0, "min": 60.0, "max": 90.0, "label": "超买阈值"},
        }

    def required_factors(self) -> list[Factor]:
        return [RSI(period=self.rsi_period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        rsi_col = f"rsi_{self.rsi_period}"
        rsi = data[rsi_col]
        
        # Initialize signal series with NaN
        signals = pd.Series(0.0, index=data.index)
        
        # Track position state: 1 = long, 0 = neutral, -1 = short
        position = 0
        
        for i in range(1, len(data)):
            current_rsi = rsi.iloc[i]
            prev_rsi = rsi.iloc[i-1]
            
            if position == 0:  # No position
                # Buy signal: RSI crosses below oversold threshold
                if prev_rsi >= self.oversold and current_rsi < self.oversold:
                    position = 1
                    signals.iloc[i] = 1.0
                # Sell signal: RSI crosses above overbought threshold  
                elif prev_rsi <= self.overbought and current_rsi > self.overbought:
                    position = -1
                    signals.iloc[i] = 0.0
                else:
                    signals.iloc[i] = 0.0
                    
            elif position == 1:  # Long position
                # Exit long: RSI rises back to 50
                if current_rsi >= 50.0:
                    position = 0
                    signals.iloc[i] = 0.0
                else:
                    signals.iloc[i] = 1.0
                    
            elif position == -1:  # Short position (sell position)
                # Exit short: RSI falls back to 50
                if current_rsi <= 50.0:
                    position = 0
                    signals.iloc[i] = 1.0  # Return to neutral means buy back
                else:
                    signals.iloc[i] = 0.0
        
        return signals