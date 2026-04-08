"""V2.9+V2.16.2: Built-in portfolio strategies — strict 1:1 port from QMT scripts.

Four strategies:
1. EtfMacdRotation — QMT "ETF指标MACD周线收益率5分钟回测" calc_rotate_signal
2. EtfSectorSwitch — QMT "ETF加权行业宽基切换策略" calc_com_signal (full regression+MSE)
3. EtfRotateCombo — QMT "轮动加多组合回测V1.2" dual-schedule combination
4. EtfStockEnhance — QMT "ETF加权个股轮动增强涨跌停过滤" (simplified)
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


def _remove_outliers_and_refit(prices) -> tuple[float, float, np.ndarray, float]:
    """QMT remove_outliers_and_refit(): weighted linear regression with
    2-outlier removal. Returns (slope, intercept, outlier_indices, mse).

    Exact port of QMT code — line-by-line from 轮动加多组合回测V1.2.txt:509-555."""
    prices = np.asarray(prices, dtype=float)
    s = len(prices)
    if s < 4:
        return 0.0, 0.0, np.array([]), 1e-8

    # First weighted fit — QMT sets last 3 weights equal (emphasize recent bars)
    weights = np.linspace(1, s, s)
    if s >= 3:
        weights[-2] = weights[-1]
        weights[-3] = weights[-1]
    x = np.arange(s)
    A = np.vstack([x, np.ones(s)]).T
    W = np.diag(weights)
    try:
        slope, intercept = np.linalg.lstsq(W @ A, W @ prices, rcond=None)[0]
    except Exception:
        return 0.0, 0.0, np.array([]), 1e-8
    fitted = slope * x + intercept

    # Find 2 largest outliers
    residuals = prices - fitted
    outlier_indices = np.argsort(np.abs(residuals))[-2:]
    outlier_indices.sort()

    # Remove outliers and refit
    mask = np.ones(s, dtype=bool)
    mask[outlier_indices] = False
    x_clean = x[mask]
    prices_clean = prices[mask]
    weights_clean = weights[mask]

    A_clean = np.vstack([x_clean, np.ones(len(x_clean))]).T
    W_clean = np.diag(weights_clean)
    try:
        new_slope, new_intercept = np.linalg.lstsq(W_clean @ A_clean, W_clean @ prices_clean, rcond=None)[0]
    except Exception:
        return slope, intercept, outlier_indices, 1e-8
    new_fitted = new_slope * x + new_intercept

    # MSE with original weights
    new_residuals = prices - new_fitted
    new_mse = float(np.sum(weights * new_residuals**2) / np.sum(weights))

    return float(new_slope), float(new_intercept), outlier_indices, max(new_mse, 1e-12)


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


# ---------------------------------------------------------------------------
# Strategy 2: EtfSectorSwitch — QMT calc_com_signal (FULL regression+MSE)
# ---------------------------------------------------------------------------

class EtfSectorSwitch(PortfolioStrategy):
    """ETF 加权行业宽基切换 (严格移植 QMT calc_com_signal, 含回归拟合+MSE)。

    QMT 逻辑:
    1. 多窗口斜率 (5d/7d/21d) + remove_outliers_and_refit 回归拟合
    2. 动态 alpha: 0.15 → 2.5 → 3.5 → 6.5 → 1.5 → 25
    3. 累积投票 cW (最近2期加权)
    4. 惩罚机制 penaltyW [0.5, 1.0]
    5. W += (0.5 + penalty) * vote / mse  ← 除以 MSE
    6. W 归一化 (/ maxW)
    7. 周线 MACD 过滤
    8. 宽基/行业切换 (近8期收益差>2%)
    """

    DEFAULT_BROAD_ETFS = frozenset({
        "510300.SH", "510500.SH", "159915.SZ", "510880.SH", "515100.SH", "159531.SZ",
        "513100.SH", "513880.SH", "513260.SH", "513660.SH", "513600.SH",
        "518880.SH", "159985.SZ", "162411.SZ",
    })

    def __init__(self, top_n: int = 1, rank_period: int = 21, ma_period: int = 2,
                 broad_symbols: list[str] | None = None,
                 sector_symbols: list[str] | None = None, **params):
        super().__init__(**params)
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self.top_n = top_n
        self.rank_period = rank_period
        self.ma_period = ma_period
        self._broad = set(broad_symbols or [])
        self._sector = set(sector_symbols or [])

    @property
    def lookback_days(self) -> int:
        return 300

    @classmethod
    def get_description(cls) -> str:
        return "ETF 加权行业宽基切换 (含回归拟合+MSE)"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {"top_n": {"type": "int", "default": 1, "min": 1, "max": 10, "label": "持仓数"}}

    def generate_weights(self, universe_data, date, prev_weights, prev_returns):
        cW: dict[str, list[float]] = self.state.setdefault("cW", {})
        W: dict[str, float] = self.state.setdefault("W", {})
        penaltyW: dict[str, float] = self.state.setdefault("penaltyW", {})
        hReturns: dict[str, list[float]] = self.state.setdefault("hReturns", {})
        select_assets: list[str] = self.state.setdefault("select_assets", [])
        broad_ratios: list[float] = self.state.setdefault("broad_ratios", [])
        sector_ratios: list[float] = self.state.setdefault("sector_ratios", [])

        returns: dict[str, float] = {}
        macd_ok: dict[str, bool] = {}
        etf_ratio_top1: dict[str, float] = {}
        etf_ratio_top2: dict[str, float] = {}

        for sym, df in universe_data.items():
            if len(df) < self.rank_period + self.ma_period + 15:
                continue
            close = _get_raw_close(df)  # QMT uses raw close throughout
            raw_close = close  # same — QMT has no adj_close concept
            close_ma = close.rolling(self.ma_period).mean()
            valid = close_ma.dropna()
            if len(valid) < self.rank_period + 5:
                continue

            # Engine data = [date-lookback, date-1] (no today). valid = dropna(close_ma).
            # valid[-1] = QMT close_ma[-2] (yesterday)
            # valid[-k] = QMT close_ma[-(k+1)]
            # QMT: ratio = (close_ma[-2] - close_ma[-day-1]) / close_ma[-day-1]
            # Ours: ratio = (valid[-1] - valid[-day]) / valid[-day]
            day = self.rank_period
            if abs(-day) >= len(valid) or valid.iloc[-day] == 0:
                continue
            ratio = (valid.iloc[-1] - valid.iloc[-day]) / valid.iloc[-day]

            # QMT ratio5 = (close_ma[-2] - close_ma[-5]) / close_ma[-5]
            # Ours: (valid[-1] - valid[-4]) / valid[-4]
            ratio5 = (valid.iloc[-1] - valid.iloc[-4]) / valid.iloc[-4] if len(valid) > 4 and valid.iloc[-4] != 0 else 0
            if sym in self._broad:
                etf_ratio_top1[sym] = ratio5
            elif sym in self._sector:
                etf_ratio_top2[sym] = ratio5

            # QMT multi-window slopes (all using close_ma[-2] as "now")
            # ratio2 = (close_ma[-2]-close_ma[-7])/close_ma[-7] → (valid[-1]-valid[-6])/valid[-6]
            # ratio3 = (close_ma[-2]-close_ma[-4])/close_ma[-4] → (valid[-1]-valid[-3])/valid[-3]
            # slope1 = (close_ma[-2]-close_ma[-5])/close_ma[-5] → (valid[-1]-valid[-4])/valid[-4]
            # slope3 = (close_ma[-4]-close_ma[-7])/close_ma[-7] → (valid[-3]-valid[-6])/valid[-6]
            # slope2 = (close_ma[-2]-close_ma[-day+3])/close_ma[-day+3] → (valid[-1]-valid[-(day-4)])/valid[-(day-4)]
            ratio2 = (valid.iloc[-1] - valid.iloc[-6]) / valid.iloc[-6] if len(valid) > 6 and valid.iloc[-6] != 0 else 0
            ratio3 = (valid.iloc[-1] - valid.iloc[-3]) / valid.iloc[-3] if len(valid) > 3 and valid.iloc[-3] != 0 else 0
            slope1 = (valid.iloc[-1] - valid.iloc[-4]) / valid.iloc[-4] if len(valid) > 4 and valid.iloc[-4] != 0 else 0
            slope3 = (valid.iloc[-3] - valid.iloc[-6]) / valid.iloc[-6] if len(valid) > 6 and valid.iloc[-6] != 0 else 0
            slope2 = (valid.iloc[-1] - valid.iloc[-(day - 4)]) / valid.iloc[-(day - 4)] if len(valid) > (day - 4) and valid.iloc[-(day - 4)] != 0 else 0

            # QMT: remove_outliers_and_refit(closes['close'][-12:-2])
            # QMT [-12:-2] = 10 bars ending at yesterday. Our data has no today,
            # so [-11:-1] gives the same 10 bars.
            # QMT: mse = mse / closes['close'][-2]. Our: / raw_close.iloc[-1] (yesterday)
            raw_vals = raw_close.values
            if len(raw_vals) >= 11:
                slp, _incp, _outliers, mse = _remove_outliers_and_refit(raw_vals[-11:-1])
                mse = mse / raw_vals[-1] if raw_vals[-1] != 0 else 1e-8
            else:
                mse = 1e-8

            returns[sym] = 1.0

            # QMT: dynamic alpha (V1.2 values: 0.15→2.5→3.5→6.5→1.5→25)
            alpha = 0.15
            if slope1 > 0.001:
                alpha = 2.5
            if slope1 > 0.002 and slope2 > 0.001:
                alpha = 3.5
            if slope3 < 0.001 and slope2 > 0.0:
                alpha = 6.5
            if ratio > 0 and ratio3 < 0:
                alpha = 1.5  # 上涨趋势不明显
            if ratio2 < -0.05:
                alpha = 25   # 大跌后不买入

            ratio = ratio + ratio2 * alpha

            # QMT: cumulative voting
            if sym not in cW:
                cW[sym] = []
            cW[sym].append(ratio)
            if len(cW[sym]) > 20:
                cW[sym] = cW[sym][-20:]

            vote = 0.0
            N = 2
            total = 2
            n = 0
            for i in range(len(cW[sym]), 0, -1):
                vote += cW[sym][i - 1] * (N - n) / total
                n += 1
                if n >= N:
                    break

            # QMT: last_peri_ra = (closes['close'][-2] - closes['close'][-7]) / closes['close'][-7]
            # Our data: [-1]=QMT[-2]=yesterday, [-6]=QMT[-7]
            last_peri_ra = (raw_close.iloc[-1] - raw_close.iloc[-6]) / raw_close.iloc[-6] if len(raw_close) > 6 and raw_close.iloc[-6] != 0 else None
            # QMT: ContextInfo.pre_profit[etf] = last_peri_ra (for >6% check later)
            pre_profit = self.state.setdefault("pre_profit", {})
            if last_peri_ra is not None:
                pre_profit[sym] = last_peri_ra
            for prev_sym in select_assets:
                if last_peri_ra is not None and prev_sym == sym:
                    if sym not in hReturns:
                        hReturns[sym] = []
                    hReturns[sym].append(last_peri_ra)
                    if last_peri_ra < 0:
                        penaltyW[sym] = penaltyW.get(sym, 0.1) - 0.1
                    else:
                        penaltyW[sym] = penaltyW.get(sym, 0.1) + 0.1
                    penaltyW[sym] = float(np.clip(penaltyW.get(sym, 0.5), 0.5, 1.0))

            # QMT: W += (0.5 + penalty) * vote / mse  ← KEY: divided by MSE
            penalty = penaltyW.get(sym, 0.0)
            adjusted_mse = mse if mse != 0 else 1e-8
            increment = (0.5 + penalty) * vote / adjusted_mse
            W[sym] = W.get(sym, 0.0) + increment

            # Weekly MACD
            macd_ok[sym] = _weekly_macd_signal(close)

        if not returns:
            return {}

        # QMT: normalize W by max absolute value
        max_w = max((abs(v) for v in W.values()), default=1)
        if max_w > 0:
            for sym in W:
                W[sym] /= max_w

        # QMT: returns *= W
        for sym in returns:
            if sym in W:
                returns[sym] *= W[sym]

        # MACD filter + rank
        candidates = [(sym, score) for sym, score in
                      sorted(returns.items(), key=lambda x: x[1], reverse=True)
                      if macd_ok.get(sym, False)]

        if not candidates:
            return {}

        # QMT: broad/sector switching (V1.2 lines 476-498)
        # QMT iterates final_etf: first broad → append ratio to list_ratio1 (only first),
        # sector → append ratio to list_ratio2 (QMT bug: f2 never becomes 1, so ALL sector
        # entries are appended, not just the first. We replicate this exact behavior.)
        pre_profit: dict[str, float] = self.state.setdefault("pre_profit", {})
        if self._broad and self._sector and candidates:
            final_etf = [sym for sym, _ in candidates]
            f1_done = False
            for k in final_etf:
                if k in self._broad and k in etf_ratio_top1:
                    if not f1_done:
                        f1_done = True
                        if len(broad_ratios) > 10:
                            broad_ratios.pop(0)
                        broad_ratios.append(etf_ratio_top1[k])
                elif k in self._sector and k in etf_ratio_top2:
                    # QMT bug: f2 = 0 (never becomes 1), so all sector entries appended
                    if len(sector_ratios) > 10:
                        sector_ratios.pop(0)
                    sector_ratios.append(etf_ratio_top2[k])

            L1 = min(8, len(broad_ratios))
            L2 = min(8, len(sector_ratios))
            profit1 = sum(broad_ratios[-L1:]) if L1 > 0 else 0
            profit2 = sum(sector_ratios[-L2:]) if L2 > 0 else 0
            if profit1 - profit2 > 0.02:
                candidates = [(s, sc) for s, sc in candidates if s in self._broad]

        # QMT: select_assets = final_etf[:etf_pos]
        etf_pos = self.top_n
        final = [sym for sym, _ in candidates[:etf_pos]]
        self.state["select_assets"] = final

        # QMT V1.2 lines 503-506: if top-1 last period return > 6%, select one more
        # NOTE: QMT only updates win_etf_com (return value), NOT select_assets.
        # select_assets stays at original etf_pos for next call's penalty loop.
        if final and final[0] in pre_profit and pre_profit[final[0]] > 0.06:
            etf_pos += 1
            final = [sym for sym, _ in candidates[:etf_pos]]

        if not final:
            return {}
        w = 1.0 / len(final)
        return {sym: w for sym in final}


# ---------------------------------------------------------------------------
# Strategy 3: EtfRotateCombo — QMT "轮动加多组合回测V1.2" dual-schedule
# ---------------------------------------------------------------------------

class EtfRotateCombo(PortfolioStrategy):
    """轮动 + 加权切换组合策略 (严格复刻 QMT "轮动加多组合回测V1.2")。

    双日程调仓 — 需配合 freq="daily" 使用:
    - 周四 (rotate_weekday=3): 重算轮动仓位桶 (EtfMacdRotation, top 2, 30%)
    - 周五 (com_weekday=4): 重算加权仓位桶 (EtfSectorSwitch, top 1, 70%)
    - 其他交易日: 返回上次合并持仓 (不触发交易)
    - 两桶独立更新、合并输出, 同一标的可叠加权重

    QMT 特殊逻辑:
    - 510880.SH → 515100.SH 映射 (加权分支)
    - 不归一化: 30%+70%=100%, 一桶空则该部分为现金
    """

    # QMT V1.2 etf_list1 (宽基 broad)
    DEFAULT_BROAD_ETFS = frozenset({
        "510300.SH", "510500.SH", "159915.SZ", "510880.SH",
        "513100.SH", "513880.SH", "513260.SH", "513660.SH",
        "518880.SH", "159985.SZ",
    })

    # QMT V1.2 etf_list2 (行业 sector)
    DEFAULT_SECTOR_ETFS = frozenset({
        "512010.SH", "512690.SH", "515700.SH", "159852.SZ", "159813.SZ",
        "159851.SZ", "515220.SH", "159869.SZ", "515880.SH", "512660.SH", "512980.SH",
    })

    # QMT V1.2 etf_list_rotate
    DEFAULT_ROTATE_SYMBOLS = frozenset({
        "510500.SH", "159915.SZ", "159531.SZ", "515100.SH",
        "513100.SH", "513880.SH", "513260.SH", "513660.SH",
        "518880.SH", "159985.SZ",
    })

    _SYMBOL_MAP = {"510880.SH": "515100.SH"}

    # QMT etf_list = etf_list1 + etf_list2 + 159531.SZ = 22 symbols (the weighted scoring pool).
    # 159531.SZ is in etf_list (line 21) AND etf_list_rotate — it participates in BOTH branches.
    # Only 515100.SH is rotate-only (not in etf_list, enters weighted via 510880 mapping).
    DEFAULT_COM_SYMBOLS = DEFAULT_BROAD_ETFS | DEFAULT_SECTOR_ETFS | {"159531.SZ"}  # 10 + 11 + 1 = 22

    def __init__(self, rotate_rate: float = 0.3, rotate_top_n: int = 2,
                 com_top_n: int = 1, rotate_weekday: int = 3, com_weekday: int = 4,
                 rotate_symbols: list[str] | None = None,
                 broad_symbols: list[str] | None = None,
                 sector_symbols: list[str] | None = None, **params):
        super().__init__(**params)
        self.rotate_rate = max(0, min(1, rotate_rate))
        self._rotate_weekday = rotate_weekday
        self._com_weekday = com_weekday
        if rotate_weekday == com_weekday:
            raise ValueError(f"rotate_weekday ({rotate_weekday}) must differ from com_weekday ({com_weekday})")
        self._rotate_syms = set(rotate_symbols) if rotate_symbols else set(self.DEFAULT_ROTATE_SYMBOLS)
        _broad = broad_symbols if broad_symbols else list(self.DEFAULT_BROAD_ETFS)
        # 159531.SZ is in QMT etf_list but not in etf_list1 or etf_list2.
        # QMT's else branch (line 369-370) classifies it as sector (etf_ratio_top2).
        _sector = sector_symbols if sector_symbols else list(self.DEFAULT_SECTOR_ETFS | {"159531.SZ"})
        # QMT V1.2: ContextInfo.day=21 for BOTH rotate and com signals
        self._rotator = EtfMacdRotation(top_n=rotate_top_n, rank_period=21)
        self._switcher = EtfSectorSwitch(top_n=com_top_n, rank_period=21, broad_symbols=_broad, sector_symbols=_sector)

    @property
    def lookback_days(self) -> int:
        return 300

    @classmethod
    def get_description(cls) -> str:
        return "轮动(30%周四) + 加权切换(70%周五) 双日程组合, 需 freq=日度"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "rotate_rate": {"type": "float", "default": 0.3, "min": 0, "max": 1, "label": "轮动仓位占比"},
            "rotate_top_n": {"type": "int", "default": 2, "min": 1, "max": 10, "label": "轮动持仓数"},
            "com_top_n": {"type": "int", "default": 1, "min": 1, "max": 10, "label": "加权持仓数"},
            "rotate_weekday": {"type": "int", "default": 3, "min": 0, "max": 4, "label": "轮动调仓日 (0=周一..4=周五)"},
            "com_weekday": {"type": "int", "default": 4, "min": 0, "max": 4, "label": "加权调仓日 (0=周一..4=周五)"},
        }

    def generate_weights(self, universe_data, date, prev_weights, prev_returns):
        weekday = date.weekday() if hasattr(date, 'weekday') else date

        rotate_bucket: dict[str, float] = self.state.setdefault("rotate_bucket", {})
        switch_bucket: dict[str, float] = self.state.setdefault("switch_bucket", {})

        self._rotator.state = self.state.setdefault("_rotate_inner", {})
        self._switcher.state = self.state.setdefault("_switch_inner", {})

        is_rotate_day = (weekday == self._rotate_weekday)
        is_com_day = (weekday == self._com_weekday)

        if not is_rotate_day and not is_com_day:
            # Non-rebalance day: return None → engine skips this day entirely (no trade).
            # QMT: handlebar returns early on non-Thu/Fri — no trade at all.
            return None

        if is_rotate_day:
            rotate_data = {s: df for s, df in universe_data.items() if s in self._rotate_syms}
            raw = self._rotator.generate_weights(rotate_data, date, prev_weights, prev_returns) if rotate_data else {}
            rotate_bucket.clear()
            for sym, w in raw.items():
                rotate_bucket[sym] = w * self.rotate_rate

        if is_com_day:
            # QMT: Friday weighted scoring only uses etf_list (21 symbols).
            # 159531.SZ and 515100.SH are rotate-only, must not be candidates here.
            com_data = {s: df for s, df in universe_data.items() if s in self.DEFAULT_COM_SYMBOLS}
            raw = self._switcher.generate_weights(com_data, date, prev_weights, prev_returns)
            switch_bucket.clear()
            com_rate = 1.0 - self.rotate_rate
            for sym, w in raw.items():
                mapped = self._SYMBOL_MAP.get(sym, sym)
                switch_bucket[mapped] = switch_bucket.get(mapped, 0) + w * com_rate

        # Merge both buckets
        # NOTE: QMT line 238 uses total_value * 0.987 (1.3% cash reserve) at execution
        # layer, not at weight layer. Our engine applies weights to full equity, so the
        # effective difference is ~1.3% less invested capital — negligible for returns.
        combined = {}
        for sym, w in rotate_bucket.items():
            combined[sym] = combined.get(sym, 0) + w
        for sym, w in switch_bucket.items():
            combined[sym] = combined.get(sym, 0) + w

        # QMT line 240: if trade_holding_list == total_position: pass (no trade)
        # QMT updates trade_holding_list = total_position EVERY Thu/Fri (line 272),
        # whether or not a trade happened. So comparison is always vs the PREVIOUS
        # Thu/Fri's merged weights, not vs "last time weights changed".
        last_holding = self.state.get("_last_holding")
        self.state["_last_holding"] = dict(combined)  # always update (like QMT line 272)
        if last_holding is not None and combined == last_holding:
            return None  # same as previous Thu/Fri → no trade

        return combined if combined else {}


# ---------------------------------------------------------------------------
# Strategy 4: EtfStockEnhance (simplified — individual stock enhancement)
# ---------------------------------------------------------------------------

class EtfStockEnhance(PortfolioStrategy):
    """ETF 轮动 + 个股增强 (复合策略, 简化版)。

    逻辑 (移植自 QMT "ETF加权个股轮动增强涨跌停过滤"):
    1. 底层: EtfSectorSwitch 选出 ETF
    2. 如果标的池中有个股, 可将部分仓位分配给动量最强的个股
    3. stock_ratio 控制 ETF vs 个股的仓位比例
    """

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
        self._inner.state = self.state
        etf_weights = self._inner.generate_weights(universe_data, date, prev_weights, prev_returns)
        if not etf_weights or self.stock_ratio <= 0:
            return etf_weights

        etf_set = set(etf_weights.keys())
        stocks = {sym: df for sym, df in universe_data.items() if sym not in etf_set and len(df) > 20}
        if not stocks:
            return etf_weights

        stock_scores = {}
        for sym, df in stocks.items():
            close = _get_close(df)
            if len(close) < 20:
                continue
            ret = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] if close.iloc[-20] != 0 else 0
            stock_scores[sym] = ret

        if not stock_scores:
            return etf_weights

        ranked = sorted(stock_scores.items(), key=lambda x: x[1], reverse=True)[:2]
        result = {}
        etf_portion = 1.0 - self.stock_ratio
        for sym, w in etf_weights.items():
            result[sym] = w * etf_portion
        stock_w = self.stock_ratio / len(ranked) if ranked else 0
        for sym, _ in ranked:
            result[sym] = stock_w
        return result
