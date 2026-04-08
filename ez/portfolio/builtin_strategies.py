"""V2.9: Built-in portfolio strategies — ported from QMT production strategies.

Three strategies:
1. EtfMacdRotation — ETF momentum rotation with weekly MACD filter
2. EtfSectorSwitch — Multi-signal weighted rotation with sector/broad switching
3. EtfStockEnhance — Composite: ETF rotation base + individual stock enhancement
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from ez.portfolio.portfolio_strategy import PortfolioStrategy


def _weekly_macd_signal(close: pd.Series) -> bool:
    """Weekly MACD filter: True = bullish (MACD bar increasing)."""
    weekly = close.resample("W").last().dropna()
    if len(weekly) < 15:  # relaxed: 15 weeks minimum (was 27), shorter history still useful
        return False
    ema12 = weekly.ewm(span=12, adjust=False).mean()
    ema26 = weekly.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    return bool(macd_bar.iloc[-1] > macd_bar.iloc[-2])


def _get_close(df: pd.DataFrame) -> pd.Series:
    return df["adj_close"] if "adj_close" in df.columns else df["close"]


class EtfMacdRotation(PortfolioStrategy):
    """ETF 动量轮动 + 周线 MACD 过滤。

    逻辑 (移植自 QMT "ETF指标MACD周线收益率5分钟回测"):
    1. 每只 ETF 计算 N 日均线收益率
    2. 指数加权历史收益调整排名
    3. 周线 MACD 过滤: 柱递增 = 可买, 否则跳过
    4. 市场恐慌保护: >75% 标的收益为负时空仓
    5. 选 Top N 等权持有
    """

    def __init__(self, top_n: int = 2, rank_period: int = 20, ma_period: int = 2, **params):
        super().__init__(**params)
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self.top_n = top_n
        self.rank_period = rank_period
        self.ma_period = ma_period

    @property
    def lookback_days(self) -> int:
        return 300  # ~27 weeks MACD + 20 day rank + buffer

    @classmethod
    def get_description(cls) -> str:
        return "ETF 动量轮动 + 周线 MACD 过滤"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "top_n": {"type": "int", "default": 2, "min": 1, "max": 20, "label": "持仓数"},
            "rank_period": {"type": "int", "default": 20, "min": 5, "max": 60, "label": "排名周期"},
        }

    def generate_weights(self, universe_data, date, prev_weights, prev_returns):
        returns = {}
        macd_ok = {}
        history_returns = {}
        cur_returns = {}

        for sym, df in universe_data.items():
            if len(df) < self.rank_period + self.ma_period + 10:
                continue
            close = _get_close(df)
            close_ma = close.rolling(self.ma_period).mean()
            if len(close_ma.dropna()) < self.rank_period + 2:
                continue

            # MA-smoothed momentum (standard percent return)
            start_val = close_ma.iloc[-self.rank_period - 1]
            end_val = close_ma.iloc[-2]
            ratio = (end_val - start_val) / start_val if start_val != 0 else 0
            returns[sym] = ratio

            # Historical returns for exponential weighting (adaptive: use available data)
            hr = []
            max_periods = min(20, (len(close_ma) - 2) // (self.rank_period + 1))
            for i in range(max_periods):
                idx_end = -2 - i
                idx_start = -2 - i - self.rank_period
                if abs(idx_start) > len(close_ma):
                    break
                v = close_ma.iloc[idx_start]
                if v != 0:
                    hr.append((close_ma.iloc[idx_end] - v) / v)
            history_returns[sym] = hr
            cur_returns[sym] = float(close_ma.iloc[-2] - close_ma.iloc[-3]) if len(close_ma) > 3 else 0

            # Weekly MACD filter
            macd_ok[sym] = _weekly_macd_signal(close)

        if not returns:
            return {}

        # Exponential weighting
        # H5 fix: use pure return-space (no mixing with absolute price)
        exp_rets = {}
        total_exp = 0
        lessthan_zero = sum(1 for v in cur_returns.values() if v < 0)
        for sym, hr in history_returns.items():
            if not hr:
                continue
            # max historical return as confidence signal (dimensionless)
            _EXP_SCALE = 10  # tuning: higher = more aggressive differentiation between assets
            exp_rets[sym] = math.exp(min(max(hr) * _EXP_SCALE, 500))
            total_exp += exp_rets[sym]

        # Market panic check: >75% negative → stay cash
        if lessthan_zero > len(returns) * 3 / 4:
            return {}

        # Adjust returns by exponential weight
        if total_exp > 0:
            for sym in returns:
                if sym in exp_rets:
                    returns[sym] *= exp_rets[sym] / total_exp

        # Filter by MACD + rank
        candidates = [(sym, score) for sym, score in sorted(returns.items(), key=lambda x: x[1], reverse=True)
                      if macd_ok.get(sym, False)]
        if not candidates:
            return {}

        top = candidates[:self.top_n]
        w = 1.0 / len(top)
        return {sym: w for sym, _ in top}


class EtfSectorSwitch(PortfolioStrategy):
    """ETF 加权行业宽基切换策略。

    逻辑 (移植自 QMT "ETF加权行业宽基切换策略5分钟回测V1.0"):
    1. 多时间窗口斜率 + 回归拟合 + 动态 alpha 加权
    2. 累积投票 (self.state) + 惩罚机制 (亏损降权)
    3. 周线 MACD 过滤
    4. 宽基 vs 行业近期收益对比: 宽基显著占优时只从宽基选
    5. 选 Top N 持有
    """

    # Default broad ETFs for auto-classification when caller doesn't specify.
    # Strategy owns this list (not the route layer) so it stays in sync with QMT config.
    DEFAULT_BROAD_ETFS = frozenset({
        "510300.SH", "510500.SH", "159915.SZ", "510880.SH", "515100.SH", "159531.SZ",
        "513100.SH", "513880.SH", "513260.SH", "513660.SH", "513600.SH",
        "518880.SH", "159985.SZ", "162411.SZ",
    })

    def __init__(self, top_n: int = 1, broad_symbols: list[str] | None = None,
                 sector_symbols: list[str] | None = None, **params):
        super().__init__(**params)
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self.top_n = top_n
        self._broad = set(broad_symbols or [])
        self._sector = set(sector_symbols or [])

    @property
    def lookback_days(self) -> int:
        return 300

    @classmethod
    def get_description(cls) -> str:
        return "ETF 加权行业宽基切换"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {"top_n": {"type": "int", "default": 1, "min": 1, "max": 10, "label": "持仓数"}}

    def generate_weights(self, universe_data, date, prev_weights, prev_returns):
        returns = {}
        macd_ok = {}
        cW = self.state.setdefault("cW", {})
        W = self.state.setdefault("W", {})
        penaltyW = self.state.setdefault("penaltyW", {})
        broad_ratios = self.state.setdefault("broad_ratios", [])
        sector_ratios = self.state.setdefault("sector_ratios", [])

        for sym, df in universe_data.items():
            if len(df) < 30:
                continue
            close = _get_close(df)
            close_ma = close.rolling(2).mean()
            if len(close_ma.dropna()) < 22:
                continue

            # Multi-window slopes
            ratio21 = (close_ma.iloc[-2] - close_ma.iloc[-22]) / close_ma.iloc[-22] if close_ma.iloc[-22] != 0 else 0
            ratio5 = (close_ma.iloc[-2] - close_ma.iloc[-6]) / close_ma.iloc[-6] if close_ma.iloc[-6] != 0 else 0
            ratio7 = (close_ma.iloc[-2] - close_ma.iloc[-8]) / close_ma.iloc[-8] if close_ma.iloc[-8] != 0 else 0
            slope1 = ratio5
            slope3 = (close_ma.iloc[-5] - close_ma.iloc[-8]) / close_ma.iloc[-8] if close_ma.iloc[-8] != 0 else 0

            # Dynamic alpha weighting
            alpha = 0.15
            if slope1 > 0.001:
                alpha = 2.5
            if slope1 > 0.002 and ratio21 > 0.001:
                alpha = 3.5
            if slope3 < 0.001 and ratio21 > 0:
                alpha = 8.5
            if ratio21 > 0 and ratio7 < -0.05:
                alpha = 25  # big drop → penalize

            combined = ratio21 + ratio7 * alpha

            # Cumulative voting
            if sym not in cW:
                cW[sym] = []
            cW[sym].append(combined)
            if len(cW[sym]) > 20:
                cW[sym] = cW[sym][-20:]

            vote = 0
            for j, v in enumerate(reversed(cW[sym][-2:])):
                vote += v * (2 - j) / 2

            # Penalty from prev_returns
            if sym in prev_weights and prev_weights[sym] > 0:
                pr = prev_returns.get(sym, 0)
                p = penaltyW.get(sym, 0.5)
                penaltyW[sym] = float(np.clip(p + (0.1 if pr >= 0 else -0.1), 0.5, 1.0))

            penalty = penaltyW.get(sym, 0.5)
            W[sym] = W.get(sym, 0) + (0.5 + penalty) * vote

            returns[sym] = 1.0
            macd_ok[sym] = _weekly_macd_signal(close)

        if not returns:
            return {}

        # Normalize W
        max_w = max(abs(v) for v in W.values()) if W else 1
        if max_w > 0:
            for sym in W:
                W[sym] /= max_w

        # Apply W to returns
        for sym in returns:
            if sym in W:
                returns[sym] *= W[sym]

        # MACD filter + rank
        candidates = [(sym, score) for sym, score in sorted(returns.items(), key=lambda x: x[1], reverse=True)
                      if macd_ok.get(sym, False)]

        if not candidates:
            return {}

        # Broad vs sector switching
        if self._broad and self._sector:
            top_sym = candidates[0][0]
            r5 = 0
            if top_sym in universe_data and len(universe_data[top_sym]) > 6:
                c = _get_close(universe_data[top_sym])
                r5 = (c.iloc[-2] - c.iloc[-6]) / c.iloc[-6] if c.iloc[-6] != 0 else 0

            if top_sym in self._broad:
                broad_ratios.append(r5)
            elif top_sym in self._sector:
                sector_ratios.append(r5)

            if len(broad_ratios) > 20:
                broad_ratios[:] = broad_ratios[-20:]
            if len(sector_ratios) > 20:
                sector_ratios[:] = sector_ratios[-20:]

            b_sum = sum(broad_ratios[-8:]) if broad_ratios else 0
            s_sum = sum(sector_ratios[-8:]) if sector_ratios else 0
            if b_sum - s_sum > 0.02:
                candidates = [(s, sc) for s, sc in candidates if s in self._broad]

        top = candidates[:self.top_n]
        if not top:
            return {}
        w = 1.0 / len(top)
        return {sym: w for sym, _ in top}


class EtfStockEnhance(PortfolioStrategy):
    """ETF 轮动 + 个股增强 (复合策略)。

    逻辑 (移植自 QMT "ETF加权个股轮动增强涨跌停过滤"):
    1. 底层: EtfSectorSwitch 选出 ETF
    2. 如果 ETF 对应有行业个股, 可将部分仓位分配给行业内强势个股
    3. stock_ratio 控制 ETF vs 个股的仓位比例
    注: 简化版 — 个股选择使用动量排名
    """

    # Inherit broad ETF classification from EtfSectorSwitch (reviewer I1 fix)
    DEFAULT_BROAD_ETFS = EtfSectorSwitch.DEFAULT_BROAD_ETFS

    def __init__(self, top_n: int = 1, stock_ratio: float = 0.0,
                 broad_symbols: list[str] | None = None,
                 sector_symbols: list[str] | None = None, **params):
        super().__init__(**params)
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self.top_n = top_n
        self.stock_ratio = max(0, min(1, stock_ratio))
        self._inner = EtfSectorSwitch(top_n=top_n, broad_symbols=broad_symbols, sector_symbols=sector_symbols)

    @property
    def lookback_days(self) -> int:
        return 300

    @classmethod
    def get_description(cls) -> str:
        return "ETF 轮动 + 个股增强"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "top_n": {"type": "int", "default": 1, "min": 1, "max": 10, "label": "ETF 持仓数"},
            "stock_ratio": {"type": "float", "default": 0.0, "min": 0, "max": 1, "label": "个股仓位比例"},
        }

    def generate_weights(self, universe_data, date, prev_weights, prev_returns):
        # Share state with inner strategy
        self._inner.state = self.state

        # Get ETF weights from inner strategy
        etf_weights = self._inner.generate_weights(universe_data, date, prev_weights, prev_returns)
        if not etf_weights or self.stock_ratio <= 0:
            return etf_weights

        # Find individual stocks (non-ETF symbols in universe)
        etf_set = set(etf_weights.keys())
        stocks = {sym: df for sym, df in universe_data.items() if sym not in etf_set and len(df) > 20}

        if not stocks:
            return etf_weights

        # Rank stocks by 20-day momentum
        stock_scores = {}
        for sym, df in stocks.items():
            close = _get_close(df)
            if len(close) < 20:
                continue
            ret = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] if close.iloc[-20] != 0 else 0
            stock_scores[sym] = ret

        if not stock_scores:
            return etf_weights

        # Top 2 stocks
        ranked = sorted(stock_scores.items(), key=lambda x: x[1], reverse=True)[:2]

        # Split: ETF gets (1 - stock_ratio), stocks get stock_ratio
        result = {}
        etf_portion = 1.0 - self.stock_ratio
        for sym, w in etf_weights.items():
            result[sym] = w * etf_portion

        stock_w = self.stock_ratio / len(ranked) if ranked else 0
        for sym, _ in ranked:
            result[sym] = stock_w

        return result
