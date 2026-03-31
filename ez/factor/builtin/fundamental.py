"""V2.11: Fundamental CrossSectionalFactors — 18 factors across 7 categories.

All factors extend CrossSectionalFactor via FundamentalCrossFactor base.
Data comes from FundamentalStore (daily_basic + fina_indicator).

Convention: all factors output percentile rank in [0, 1], higher = better.
Factors where raw value is "lower is better" (debt, size) are negated before ranking.

Categories:
  Value (4):     EP, BP, SP, DP            — source: daily_basic
  Quality (4):   ROE, ROA, GrossMargin, NetProfitMargin  — source: fina_indicator
  Growth (3):    RevenueGrowthYoY, ProfitGrowthYoY, ROEChange  — source: fina_indicator
  Size (2):      LnMarketCap, LnCircMV     — source: daily_basic (negated: small=high score)
  Liquidity (2): TurnoverRate, AmihudIlliquidity  — source: daily_basic
  Leverage (2):  DebtToAssets, CurrentRatio — source: fina_indicator
  Industry (1):  IndustryMomentum          — source: price data + industry classification
"""
from __future__ import annotations

import math
from abc import abstractmethod
from datetime import date, datetime

import pandas as pd

from ez.portfolio.cross_factor import CrossSectionalFactor

# Type alias — avoid importing at module level to prevent circular deps
FundamentalStore = None  # resolved lazily


def _get_store_type():
    global FundamentalStore
    if FundamentalStore is None:
        from ez.data.fundamental import FundamentalStore as _FS
        FundamentalStore = _FS
    return FundamentalStore


# ── Category labels for frontend grouping ─────────────────────────────
# NOTE: IndustryMomentum is appended at bottom of file after class definition
FACTOR_CATEGORIES: dict[str, list[str]] = {
    "value": ["EP", "BP", "SP", "DP"],
    "quality": ["ROE", "ROA", "GrossMargin", "NetProfitMargin"],
    "growth": ["RevenueGrowthYoY", "ProfitGrowthYoY", "ROEChange"],
    "size": ["LnMarketCap", "LnCircMV"],
    "liquidity": ["TurnoverRate", "AmihudIlliquidity"],
    "leverage": ["DebtToAssets", "CurrentRatio"],
}

CATEGORY_LABELS: dict[str, str] = {
    "value": "估值 (Value)",
    "quality": "质量 (Quality)",
    "growth": "成长 (Growth)",
    "size": "规模 (Size)",
    "liquidity": "流动性 (Liquidity)",
    "leverage": "杠杆 (Leverage)",
}

# Which factors need fina_indicator (paid tier)
NEEDS_FINA = {"ROE", "ROA", "GrossMargin", "NetProfitMargin",
              "RevenueGrowthYoY", "ProfitGrowthYoY", "ROEChange",
              "DebtToAssets", "CurrentRatio"}


# ── Base class ────────────────────────────────────────────────────────

class FundamentalCrossFactor(CrossSectionalFactor):
    """Base for fundamental factors. Queries FundamentalStore for data.

    Subclasses implement _raw_scores() returning {symbol: raw_value}.
    The base class handles ranking + direction normalization.
    """

    def __init__(self, store=None):
        self._store = store

    def set_store(self, store) -> None:
        """Inject store after construction (for lazy wiring in API)."""
        self._store = store

    @property
    def description(self) -> str:
        return ""

    @property
    def category(self) -> str:
        """Factor category key (value/quality/growth/size/liquidity/leverage)."""
        return ""

    @property
    def higher_is_better(self) -> bool:
        """If True, higher raw values get higher rank. Override for inverse factors."""
        return True

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Compute factor scores as percentile rank. Higher = better."""
        if self._store is None:
            return pd.Series(dtype=float)

        symbols = list(universe_data.keys())
        d = date.date() if hasattr(date, 'date') else date
        raw = self._raw_scores(symbols, d)

        if not raw:
            return pd.Series(dtype=float)

        s = pd.Series(raw)
        if not self.higher_is_better:
            s = -s  # negate so ranking gives high score to low raw values
        return s.rank(pct=True)

    @abstractmethod
    def _raw_scores(self, symbols: list[str], d: date) -> dict[str, float]:
        """Return {symbol: raw_value} for the given date. Subclass implements."""
        ...


# ── Value factors (daily_basic) ───────────────────────────────────────

class EP(FundamentalCrossFactor):
    """Earnings/Price = 1/PE_TTM. Higher = cheaper."""

    @property
    def name(self) -> str:
        return "ep"

    @property
    def description(self) -> str:
        return "盈利收益率 (1/PE_TTM)，越高越便宜"

    @property
    def category(self) -> str:
        return "value"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            data = self._store.get_daily_basic_at(sym, d)
            if data and data.get("pe_ttm") and data["pe_ttm"] != 0:
                scores[sym] = 1.0 / data["pe_ttm"]
        return scores


class BP(FundamentalCrossFactor):
    """Book/Price = 1/PB. Higher = cheaper."""

    @property
    def name(self) -> str:
        return "bp"

    @property
    def description(self) -> str:
        return "市净率倒数 (1/PB)，越高越便宜"

    @property
    def category(self) -> str:
        return "value"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            data = self._store.get_daily_basic_at(sym, d)
            if data and data.get("pb") and data["pb"] != 0:
                scores[sym] = 1.0 / data["pb"]
        return scores


class SP(FundamentalCrossFactor):
    """Sales/Price = 1/PS_TTM. Higher = cheaper."""

    @property
    def name(self) -> str:
        return "sp"

    @property
    def description(self) -> str:
        return "市销率倒数 (1/PS_TTM)，越高越便宜"

    @property
    def category(self) -> str:
        return "value"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            data = self._store.get_daily_basic_at(sym, d)
            if data and data.get("ps_ttm") and data["ps_ttm"] != 0:
                scores[sym] = 1.0 / data["ps_ttm"]
        return scores


class DP(FundamentalCrossFactor):
    """Dividend yield (dv_ratio). Higher = more income."""

    @property
    def name(self) -> str:
        return "dp"

    @property
    def description(self) -> str:
        return "股息率，越高越好"

    @property
    def category(self) -> str:
        return "value"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            data = self._store.get_daily_basic_at(sym, d)
            if data and data.get("dv_ratio") is not None:
                scores[sym] = data["dv_ratio"]
        return scores


# ── Quality factors (fina_indicator) ──────────────────────────────────

class ROE(FundamentalCrossFactor):
    """Return on Equity (weighted average). Higher = better profitability."""

    @property
    def name(self) -> str:
        return "roe"

    @property
    def description(self) -> str:
        return "加权平均净资产收益率，越高越好"

    @property
    def category(self) -> str:
        return "quality"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("roe_waa") is not None:
                scores[sym] = fina["roe_waa"]
            elif fina and fina.get("roe") is not None:
                scores[sym] = fina["roe"]
        return scores


class ROA(FundamentalCrossFactor):
    """Return on Assets. Higher = better asset efficiency."""

    @property
    def name(self) -> str:
        return "roa"

    @property
    def description(self) -> str:
        return "总资产收益率，越高越好"

    @property
    def category(self) -> str:
        return "quality"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("roa") is not None:
                scores[sym] = fina["roa"]
        return scores


class GrossMargin(FundamentalCrossFactor):
    """Gross profit margin. Higher = better pricing power."""

    @property
    def name(self) -> str:
        return "gross_margin"

    @property
    def description(self) -> str:
        return "毛利率，越高越好"

    @property
    def category(self) -> str:
        return "quality"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("grossprofit_margin") is not None:
                scores[sym] = fina["grossprofit_margin"]
        return scores


class NetProfitMargin(FundamentalCrossFactor):
    """Net profit margin. Higher = better overall profitability."""

    @property
    def name(self) -> str:
        return "net_profit_margin"

    @property
    def description(self) -> str:
        return "净利率，越高越好"

    @property
    def category(self) -> str:
        return "quality"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("netprofit_margin") is not None:
                scores[sym] = fina["netprofit_margin"]
        return scores


# ── Growth factors (fina_indicator YoY) ───────────────────────────────

class RevenueGrowthYoY(FundamentalCrossFactor):
    """Revenue year-over-year growth rate. Higher = faster growing."""

    @property
    def name(self) -> str:
        return "revenue_growth_yoy"

    @property
    def description(self) -> str:
        return "营收同比增速，越高越好"

    @property
    def category(self) -> str:
        return "growth"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("revenue_yoy") is not None:
                scores[sym] = fina["revenue_yoy"]
        return scores


class ProfitGrowthYoY(FundamentalCrossFactor):
    """Net profit year-over-year growth rate. Higher = faster growing."""

    @property
    def name(self) -> str:
        return "profit_growth_yoy"

    @property
    def description(self) -> str:
        return "净利润同比增速，越高越好"

    @property
    def category(self) -> str:
        return "growth"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("profit_yoy") is not None:
                scores[sym] = fina["profit_yoy"]
        return scores


class ROEChange(FundamentalCrossFactor):
    """ROE year-over-year change. Higher = improving profitability."""

    @property
    def name(self) -> str:
        return "roe_change"

    @property
    def description(self) -> str:
        return "ROE 同比变化，越高越好"

    @property
    def category(self) -> str:
        return "growth"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("roe_yoy") is not None:
                scores[sym] = fina["roe_yoy"]
        return scores


# ── Size factors (daily_basic, negated: small = high score) ───────────

class LnMarketCap(FundamentalCrossFactor):
    """Log total market cap. Lower = smaller = higher score (small-cap premium)."""

    @property
    def name(self) -> str:
        return "ln_market_cap"

    @property
    def description(self) -> str:
        return "总市值对数 (反转: 小盘得高分)"

    @property
    def category(self) -> str:
        return "size"

    @property
    def higher_is_better(self) -> bool:
        return False  # smaller market cap → higher score

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            data = self._store.get_daily_basic_at(sym, d)
            if data and data.get("total_mv") and data["total_mv"] > 0:
                scores[sym] = math.log(data["total_mv"])
        return scores


class LnCircMV(FundamentalCrossFactor):
    """Log circulating market cap. Lower = smaller = higher score."""

    @property
    def name(self) -> str:
        return "ln_circ_mv"

    @property
    def description(self) -> str:
        return "流通市值对数 (反转: 小盘得高分)"

    @property
    def category(self) -> str:
        return "size"

    @property
    def higher_is_better(self) -> bool:
        return False

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            data = self._store.get_daily_basic_at(sym, d)
            if data and data.get("circ_mv") and data["circ_mv"] > 0:
                scores[sym] = math.log(data["circ_mv"])
        return scores


# ── Liquidity factors (daily_basic) ───────────────────────────────────

class TurnoverRate(FundamentalCrossFactor):
    """Turnover rate. Higher = more liquid."""

    @property
    def name(self) -> str:
        return "turnover_rate"

    @property
    def description(self) -> str:
        return "换手率，越高流动性越好"

    @property
    def category(self) -> str:
        return "liquidity"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            data = self._store.get_daily_basic_at(sym, d)
            if data and data.get("turnover_rate") is not None:
                scores[sym] = data["turnover_rate"]
        return scores


class AmihudIlliquidity(FundamentalCrossFactor):
    """Amihud illiquidity: |return| / volume. Lower = more liquid = higher score."""

    @property
    def name(self) -> str:
        return "amihud_illiquidity"

    @property
    def description(self) -> str:
        return "Amihud 非流动性 (反转: 流动性好得高分)"

    @property
    def category(self) -> str:
        return "liquidity"

    @property
    def higher_is_better(self) -> bool:
        return False  # lower illiquidity = more liquid = better

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Uses price data from universe_data (not fundamental store)."""
        scores = {}
        d = date.date() if hasattr(date, 'date') else date
        lookback = 20
        for sym, df in universe_data.items():
            if len(df) < lookback + 1 or "adj_close" not in df.columns:
                continue
            ret = df["adj_close"].pct_change().iloc[-lookback:]
            vol = df["volume"].iloc[-lookback:]
            valid = (vol > 0) & ret.notna()
            if valid.sum() < lookback // 2:
                continue
            amihud = (ret[valid].abs() / vol[valid]).mean()
            if amihud > 0:
                scores[sym] = amihud
        if not scores:
            return pd.Series(dtype=float)
        s = pd.Series(scores)
        return (-s).rank(pct=True)  # negate: lower illiquidity = higher rank

    def _raw_scores(self, symbols, d):
        return {}  # not used — compute() overridden


# ── Leverage factors (fina_indicator) ─────────────────────────────────

class DebtToAssets(FundamentalCrossFactor):
    """Debt to assets ratio. Lower = less leveraged = higher score."""

    @property
    def name(self) -> str:
        return "debt_to_assets"

    @property
    def description(self) -> str:
        return "资产负债率 (反转: 低杠杆得高分)"

    @property
    def category(self) -> str:
        return "leverage"

    @property
    def higher_is_better(self) -> bool:
        return False  # lower debt = better

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("debt_to_assets") is not None:
                scores[sym] = fina["debt_to_assets"]
        return scores


class CurrentRatio(FundamentalCrossFactor):
    """Current ratio. Higher = better short-term solvency."""

    @property
    def name(self) -> str:
        return "current_ratio"

    @property
    def description(self) -> str:
        return "流动比率，越高短期偿债能力越好"

    @property
    def category(self) -> str:
        return "leverage"

    def _raw_scores(self, symbols, d):
        scores = {}
        for sym in symbols:
            fina = self._store.get_fina_pit(sym, d)
            if fina and fina.get("current_ratio") is not None:
                scores[sym] = fina["current_ratio"]
        return scores


# ── Industry factor (uses both price data and industry classification) ─

class IndustryMomentum(FundamentalCrossFactor):
    """Industry-average momentum. Stocks in strong industries score higher.

    Computes average 20-day return per industry, assigns industry score to each stock.
    """

    def __init__(self, store=None, period: int = 20):
        super().__init__(store)
        self._period = period

    @property
    def name(self) -> str:
        return "industry_momentum"

    @property
    def description(self) -> str:
        return "行业动量: 所属行业的平均涨幅"

    @property
    def category(self) -> str:
        return "industry"

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        if self._store is None:
            return pd.Series(dtype=float)

        # 1. Compute per-stock return
        stock_returns = {}
        for sym, df in universe_data.items():
            if len(df) < self._period or "adj_close" not in df.columns:
                continue
            close = df["adj_close"]
            ret = (close.iloc[-1] - close.iloc[-self._period]) / close.iloc[-self._period]
            stock_returns[sym] = ret

        if not stock_returns:
            return pd.Series(dtype=float)

        # 2. Group by industry, compute industry average return
        industry_returns: dict[str, list[float]] = {}
        sym_industry: dict[str, str] = {}
        for sym, ret in stock_returns.items():
            ind = self._store.get_industry(sym)
            if ind:
                industry_returns.setdefault(ind, []).append(ret)
                sym_industry[sym] = ind

        industry_avg = {ind: sum(rets) / len(rets) for ind, rets in industry_returns.items() if rets}

        # 3. Assign industry score to each stock
        scores = {}
        for sym in stock_returns:
            ind = sym_industry.get(sym)
            if ind and ind in industry_avg:
                scores[sym] = industry_avg[ind]

        if not scores:
            return pd.Series(dtype=float)
        return pd.Series(scores).rank(pct=True)

    def _raw_scores(self, symbols, d):
        return {}  # compute() overridden


# Update category registry
FACTOR_CATEGORIES["industry"] = ["IndustryMomentum"]
CATEGORY_LABELS["industry"] = "行业 (Industry)"
NEEDS_FINA.discard("IndustryMomentum")  # doesn't need fina data


# ── Registry helper ───────────────────────────────────────────────────

def get_fundamental_factors() -> dict[str, type]:
    """Return all concrete FundamentalCrossFactor subclasses, keyed by class name."""
    return {
        name: cls for name, cls in CrossSectionalFactor.get_registry().items()
        if isinstance(cls, type) and issubclass(cls, FundamentalCrossFactor)
        and not getattr(cls, '__abstractmethods__', None)
    }
