"""Built-in portfolio strategy — strict 1:1 port from QMT script.

EtfMacdRotation — QMT "ETF指标MACD周线收益率5分钟回测" calc_rotate_signal
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from ez.portfolio.portfolio_strategy import PortfolioStrategy


# ---------------------------------------------------------------------------
# Shared helpers (matching QMT MyTT / utility functions)
# ---------------------------------------------------------------------------

def _weekly_macd_signal(close: pd.Series) -> bool:
    """Weekly MACD filter: True = bullish (MACD bar increasing).
    Matches QMT MACD_PLUS(): condition1 = (macd_bar > REF(macd_bar, 1)).
    QMT uses bar_week['close'].iloc[:-1] — excludes current (possibly incomplete) week."""
    weekly = close.resample("W").last().dropna()
    # QMT: close_data = bar_week['close'].iloc[:-1]
    # Exclude current/last week (may be incomplete on Thu/Fri)
    if len(weekly) > 1:
        weekly = weekly.iloc[:-1]
    if len(weekly) < 15:
        return False
    ema12 = weekly.ewm(span=12, adjust=False).mean()
    ema26 = weekly.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    return bool(macd_bar.iloc[-1] > macd_bar.iloc[-2])


def _get_close(df: pd.DataFrame) -> pd.Series:
    return df["adj_close"] if "adj_close" in df.columns else df["close"]


def _get_raw_close(df: pd.DataFrame) -> pd.Series:
    """QMT strategies use raw close (not adjusted). QMT: closes['close']."""
    return df["close"]


# ---------------------------------------------------------------------------
# Strategy 1: EtfMacdRotation — QMT calc_rotate_signal
# ---------------------------------------------------------------------------

class EtfMacdRotation(PortfolioStrategy):
    """ETF 动量轮动 + 周线 MACD 过滤 (严格移植 QMT calc_rotate_signal)。

    QMT 逻辑:
    1. close_ma = 2日均线
    2. ratio = (close_ma[-2] - close_ma[-21]) / close_ma[-21]
    3. ratio = ratio / close_ma[-2]  ← QMT 二次归一化
    4. exp加权: exp((max(hr) - c) * 1.02)
    5. >75% 标的当期负收益 → 空仓
    6. 周线 MACD 过滤, 选 top N 等权
    """

    def __init__(self, top_n: int = 2, rank_period: int = 20, ma_period: int = 2,
                 strict_weekday: int | None = None, **params):
        super().__init__(**params)
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self.top_n = top_n
        self.rank_period = rank_period
        self.ma_period = ma_period
        # QMT strict weekday: only trade if date.weekday() == strict_weekday.
        # Holiday on that weekday = no trade (QMT: if weekday != changeNum: return).
        # Use with freq=daily so engine calls every day, strategy skips non-target days.
        self._strict_weekday = strict_weekday

    @property
    def lookback_days(self) -> int:
        return 300

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
        # QMT strict weekday mode: only trade on exact weekday, no holiday fallback
        # QMT exception: date_index==0 fires exactly once (one-shot, unconditional)
        is_first_day = not self.state.get("_has_traded", False)
        if is_first_day:
            self.state["_has_traded"] = True  # one-shot: set immediately, not on trade success
        if self._strict_weekday is not None and not is_first_day:
            weekday = date.weekday() if hasattr(date, 'weekday') else date
            if weekday != self._strict_weekday:
                return None  # skip: QMT returns early if weekday != changeNum

        returns = {}
        macd_ok = {}
        history_returns: dict[str, list[float]] = {}
        cur_close_prices: dict[str, float] = {}
        cur_returns_map: dict[str, float] = {}

        for sym, df in universe_data.items():
            if len(df) < self.rank_period + self.ma_period + 10:
                continue
            close = _get_raw_close(df)  # QMT uses raw close, not adj_close
            close_ma = close.rolling(self.ma_period).mean()
            valid = close_ma.dropna()
            if len(valid) < self.rank_period + 2:
                continue

            # Engine data is [date-lookback, date-1] (anti-lookahead), so iloc[-1] = QMT's close_ma[-2].
            # QMT index_end=-2, index_start=-(day+1). In our indexing: [-1] = QMT[-2], [-2] = QMT[-3].
            # QMT: ratio = (close_ma[-2] - close_ma[-(day+1)]) / close_ma[-(day+1)]
            # QMT: ratio = ratio / close_ma[-2]
            end_val = valid.iloc[-1]       # = QMT close_ma[-2]
            start_val = valid.iloc[-(self.rank_period)]  # = QMT close_ma[-(day+1)]
            if start_val == 0 or end_val == 0:
                continue
            ratio = (end_val - start_val) / start_val
            ratio = ratio / end_val
            returns[sym] = ratio

            # QMT hr: close_ma[-2-i] to close_ma[-21-i], both using QMT indexing.
            # Our [-1] = QMT[-2], so: our[-1-i] = QMT[-2-i], our[-(rank+1)-i] = QMT[-(rank+1)-i-1]
            # Wait — QMT uses fixed -21 (hardcoded), not -(day+1). Let's match exactly:
            # QMT: r = (close_ma[-2-i] - close_ma[-21-i]) / close_ma[-21-i]
            # Our: r = (valid[-1-i] - valid[-20-i]) / valid[-20-i]
            hr: list[float] = []
            for i in range(20):
                idx_end = -1 - i       # = QMT close_ma[-2-i]
                idx_start = -20 - i    # = QMT close_ma[-21-i]
                if abs(idx_start) >= len(valid):
                    break
                v = valid.iloc[idx_start]
                if v != 0:
                    hr.append(float((valid.iloc[idx_end] - v) / v))
            history_returns[sym] = hr
            cur_close_prices[sym] = float(end_val)  # = QMT close_ma[-2]
            # QMT: cur_returns = close_ma[-2] - close_ma[-3]
            cur_returns_map[sym] = float(valid.iloc[-1] - valid.iloc[-2]) if len(valid) > 2 else 0

            macd_ok[sym] = _weekly_macd_signal(close)

        if not returns:
            return {}

        # QMT exp weighting: exp((max(hr) - c) * 1.02)
        exp_rets: dict[str, float] = {}
        total_exp = 0.0
        lessthan_zero_n = sum(1 for v in cur_returns_map.values() if v < 0)
        for sym, hr in history_returns.items():
            if not hr:
                continue
            c = cur_close_prices.get(sym, 1.0)
            exp_rets[sym] = math.exp(min(((max(hr) - c)) * 1.02, 500))
            total_exp += exp_rets[sym]

        # QMT: >75% negative → skip exp weighting (continue in per-symbol loop),
        # but still sort and rank by raw ratio. NOT return {} (that would be full cash).
        if lessthan_zero_n <= len(returns) * 3 / 4 and total_exp > 0:
            for sym in returns:
                if sym in exp_rets:
                    exp_rets[sym] /= total_exp
                    returns[sym] *= exp_rets[sym]

        # MACD filter + rank
        candidates = [(sym, score) for sym, score in
                      sorted(returns.items(), key=lambda x: x[1], reverse=True)
                      if macd_ok.get(sym, False)]
        if not candidates:
            return {}

        top = candidates[:self.top_n]
        # QMT line 187: total_value = get_total_value(...) * 0.987
        # Only 98.7% of equity is invested, 1.3% cash buffer
        _QMT_CASH_RESERVE = 0.987
        w = _QMT_CASH_RESERVE / len(top)
        result = {sym: w for sym, _ in top}

        # QMT V1.2 line 188: if trade_code_list == win_etf: pass (no trade)
        # QMT compares LISTS (ordered) — [A,B] != [B,A] even if same ETFs.
        # If ranking order changed, QMT rebalances (adjusts held positions to target).
        current_syms = [sym for sym, _ in top]
        last_syms = self.state.get("_last_syms")
        self.state["_last_syms"] = list(current_syms)
        if last_syms is not None and current_syms == last_syms:
            return None  # same selection AND same order → no trade

        return result
