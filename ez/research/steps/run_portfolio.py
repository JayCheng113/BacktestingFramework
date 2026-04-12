"""RunPortfolioStep: run a single portfolio strategy, produce daily returns.

Reads:
  - artifacts['universe_data']: dict[symbol → DataFrame] (from DataLoadStep)

Writes (merge into existing if present):
  - artifacts['returns']: pd.DataFrame indexed by date, adds one column = label
  - artifacts['metrics']: dict[label → dict[metric_name → value]]
  - artifacts['equity_curves']: dict[label → pd.Series]
  - artifacts['portfolio_results']: dict[label → PortfolioResult]

V2.20.2: single portfolio strategy per step instance.  Multiple portfolio
strategies → multiple RunPortfolioStep instances in the pipeline.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd

from ..pipeline import ResearchStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


# Market-specific defaults, mirroring ez/api/routes/portfolio.py logic.
_MARKET_DEFAULTS: dict[str, dict[str, Any]] = {
    "cn_stock": {
        "t_plus_1": True,
        "lot_size": 100,
        "limit_pct": 0.10,
        "stamp_tax_rate": 0.0005,
    },
}
_NON_CN_DEFAULTS: dict[str, Any] = {
    "t_plus_1": False,
    "lot_size": 1,
    "limit_pct": 0.0,
    "stamp_tax_rate": 0.0,
}


class RunPortfolioStep(ResearchStep):
    """Run a portfolio strategy via ``run_portfolio_backtest`` and emit daily returns.

    Unlike ``RunStrategiesStep`` (single-stock engine), this step uses the
    full portfolio engine with discrete-share accounting, lot-size rounding,
    TradingCalendar, Universe, and CostModel.

    The output daily returns Series is merged into ``artifacts['returns']``
    so that downstream steps (e.g. ``NestedOOSStep``) see a unified
    DataFrame with columns from both single-stock and portfolio runs.
    """

    name = "run_portfolio"
    writes = ("returns", "metrics", "equity_curves", "portfolio_results")

    def __init__(
        self,
        strategy: Any,
        label: str,
        symbols: list[str],
        *,
        freq: str = "weekly",
        initial_cash: float = 1_000_000.0,
        market: str = "cn_stock",
        cost_model_kwargs: dict[str, float] | None = None,
        benchmark_symbol: str = "",
        rebal_weekday: int | None = None,
        skip_terminal_liquidation: bool = False,
        use_open_price: bool = False,
        lookback_days: int | None = None,
    ):
        """
        Parameters
        ----------
        strategy : PortfolioStrategy
            An already-instantiated portfolio strategy (e.g. ``EtfRotateCombo()``).
        label : str
            Column name for the output returns (e.g. ``"A"``).
        symbols : list[str]
            Universe symbols the strategy trades on.  Must be a subset of
            ``artifacts['universe_data']`` keys.
        freq : str
            Rebalance frequency: ``"daily"`` / ``"weekly"`` / ``"monthly"`` /
            ``"quarterly"`` / ``"annually"``.
        initial_cash : float
            Starting capital.
        market : str
            Market code for deriving T+1, lot size, stamp tax, limit pct.
        cost_model_kwargs : dict, optional
            Override ``CostModel`` fields (e.g. ``{"slippage_rate": 0.002}``).
        benchmark_symbol : str
            Benchmark symbol for alpha/beta computation.
        rebal_weekday : int | None
            For weekly freq: 0=Mon .. 4=Fri.
        skip_terminal_liquidation : bool
            QMT compat: skip forced liquidation at end of backtest.
        use_open_price : bool
            QMT compat: trade at open instead of close.
        lookback_days : int | None
            Override the lookback window **for date inference only**.
            When ``start_date`` / ``end_date`` are not in pipeline config,
            this value determines the buffer added to the data start date
            to allow strategy warm-up.  It is NOT passed to the engine
            (which derives lookback from ``strategy.lookback_days``).
            If None, falls back to ``strategy.lookback_days``.
        """
        if not label or not isinstance(label, str):
            raise ValueError("RunPortfolioStep requires a non-empty string label")
        if not symbols:
            raise ValueError("RunPortfolioStep requires at least one symbol")
        if isinstance(symbols, (str, bytes, bytearray)):
            raise TypeError(
                f"symbols must be a list of strings, got {type(symbols).__name__}"
            )

        self.strategy = strategy
        self.label = label
        self.symbols = list(symbols)
        self.freq = freq
        self.initial_cash = float(initial_cash)
        self.market = market
        self.cost_model_kwargs = dict(cost_model_kwargs) if cost_model_kwargs else {}
        self.benchmark_symbol = benchmark_symbol
        self.rebal_weekday = rebal_weekday
        self.skip_terminal_liquidation = skip_terminal_liquidation
        self.use_open_price = use_open_price
        self.lookback_days = lookback_days

    def _derive_market_params(self) -> dict[str, Any]:
        """Derive t_plus_1, lot_size, limit_pct, stamp_tax_rate from market."""
        defaults = _MARKET_DEFAULTS.get(self.market, _NON_CN_DEFAULTS)
        return dict(defaults)

    def _build_cost_model(self, market_params: dict[str, Any]):
        """Construct CostModel with market defaults + user overrides."""
        from ez.portfolio.execution import CostModel

        kwargs: dict[str, Any] = {}
        # Apply stamp_tax from market defaults
        if "stamp_tax_rate" in market_params:
            kwargs["stamp_tax_rate"] = market_params["stamp_tax_rate"]
        # User overrides take precedence
        kwargs.update(self.cost_model_kwargs)
        return CostModel(**kwargs)

    def _build_calendar(self, universe_data: dict[str, pd.DataFrame]) -> Any:
        """Build TradingCalendar from the union of all trading dates."""
        from ez.portfolio.calendar import TradingCalendar

        all_dates: set[date] = set()
        for df in universe_data.values():
            for ts in df.index:
                if hasattr(ts, "date"):
                    all_dates.add(ts.date())
                else:
                    all_dates.add(ts)
        if not all_dates:
            raise RuntimeError("RunPortfolioStep: no trading dates in universe_data")
        return TradingCalendar.from_dates(sorted(all_dates))

    def _resolve_dates(self, context: PipelineContext) -> tuple[date, date]:
        """Resolve start/end dates from config or data."""
        cfg = context.config
        start = cfg.get("start_date")
        end = cfg.get("end_date")
        if start is not None:
            if isinstance(start, str):
                start = date.fromisoformat(start)
            elif hasattr(start, "date"):
                start = start.date()
        if end is not None:
            if isinstance(end, str):
                end = date.fromisoformat(end)
            elif hasattr(end, "date"):
                end = end.date()
        return start, end

    def _infer_dates_from_data(
        self, universe_data: dict[str, pd.DataFrame]
    ) -> tuple[date, date]:
        """Infer start/end from the intersection of all symbols' date ranges."""
        all_min, all_max = [], []
        for df in universe_data.values():
            if len(df) > 0:
                idx = df.index
                mn = idx.min()
                mx = idx.max()
                all_min.append(mn.date() if hasattr(mn, "date") else mn)
                all_max.append(mx.date() if hasattr(mx, "date") else mx)
        if not all_min:
            raise RuntimeError("RunPortfolioStep: universe_data is empty")
        start, end = max(all_min), min(all_max)
        if start > end:
            raise RuntimeError(
                f"RunPortfolioStep: symbols have non-overlapping date ranges. "
                f"Intersection is empty (latest start={start}, earliest end={end})."
            )
        return start, end

    def run(self, context: PipelineContext) -> PipelineContext:
        from ez.portfolio.engine import run_portfolio_backtest
        from ez.portfolio.universe import Universe

        ud = context.require("universe_data")

        # Filter universe_data to only the symbols this step needs
        missing = [s for s in self.symbols if s not in ud]
        if missing:
            raise ValueError(
                f"RunPortfolioStep: symbols not found in universe_data: {missing}. "
                f"Available: {sorted(ud.keys())}"
            )
        sub_ud = {s: ud[s] for s in self.symbols}

        # Build infrastructure
        market_params = self._derive_market_params()
        cost_model = self._build_cost_model(market_params)
        calendar = self._build_calendar(sub_ud)
        universe = Universe(self.symbols)

        # Resolve dates
        start, end = self._resolve_dates(context)
        if start is None or end is None:
            inferred_start, inferred_end = self._infer_dates_from_data(sub_ud)
            # Add lookback buffer to start for strategy warm-up.
            # NOTE: lookback_days here is ONLY for date inference — it is
            # NOT passed to run_portfolio_backtest (which derives lookback
            # from strategy.lookback_days internally).
            lb = self.lookback_days or int(getattr(self.strategy, "lookback_days", 252))
            buffer_days = int(lb * 1.6)
            if start is None:
                start = inferred_start + timedelta(days=buffer_days)
            if end is None:
                end = inferred_end

        # C1 guard: lookback buffer may push start past end for short data
        if start >= end:
            raise RuntimeError(
                f"RunPortfolioStep: computed start={start} >= end={end} "
                f"(data too short for lookback buffer). "
                f"Provide explicit start_date/end_date in pipeline config."
            )

        # Run portfolio backtest
        result = run_portfolio_backtest(
            strategy=self.strategy,
            universe=universe,
            universe_data=sub_ud,
            calendar=calendar,
            start=start,
            end=end,
            freq=self.freq,
            initial_cash=self.initial_cash,
            cost_model=cost_model,
            lot_size=market_params.get("lot_size", 100),
            limit_pct=market_params.get("limit_pct", 0.10),
            benchmark_symbol=self.benchmark_symbol,
            t_plus_1=market_params.get("t_plus_1", True),
            rebal_weekday=self.rebal_weekday,
            skip_terminal_liquidation=self.skip_terminal_liquidation,
            use_open_price=self.use_open_price,
        )

        # Convert equity curve to daily returns
        if len(result.equity_curve) < 2:
            raise RuntimeError(
                f"RunPortfolioStep: backtest for '{self.label}' produced "
                f"fewer than 2 equity points — cannot compute returns"
            )
        equity_series = pd.Series(
            result.equity_curve,
            index=pd.DatetimeIndex(result.dates),
        )
        daily_returns = equity_series.pct_change().iloc[1:]
        daily_returns.name = self.label
        # V2.23.2 Important 6: normalize tz to prevent join crash when
        # other steps contribute tz-aware indexes.
        from .._metrics import normalize_returns_index, normalize_returns_frame
        daily_returns = normalize_returns_index(daily_returns)

        # Merge into existing artifacts (outer join if returns already exists)
        existing_returns = context.artifacts.get("returns")
        if existing_returns is not None and isinstance(existing_returns, pd.DataFrame):
            existing_returns = normalize_returns_frame(existing_returns)
            if self.label in existing_returns.columns:
                logger.warning(
                    "RunPortfolioStep: label '%s' already exists in returns — "
                    "overwriting. Use distinct labels to avoid data loss.",
                    self.label,
                )
                existing_returns = existing_returns.drop(columns=[self.label])
            new_col = daily_returns.to_frame(self.label)
            context.artifacts["returns"] = existing_returns.join(new_col, how="outer")
        else:
            context.artifacts["returns"] = daily_returns.to_frame(self.label)

        # Merge metrics
        existing_metrics = context.artifacts.get("metrics")
        if not isinstance(existing_metrics, dict):
            existing_metrics = {}
        existing_metrics[self.label] = dict(result.metrics)
        context.artifacts["metrics"] = existing_metrics

        # Merge equity curves
        existing_eq = context.artifacts.get("equity_curves")
        if not isinstance(existing_eq, dict):
            existing_eq = {}
        existing_eq[self.label] = equity_series
        context.artifacts["equity_curves"] = existing_eq

        # Store full PortfolioResult for downstream use
        existing_pr = context.artifacts.get("portfolio_results")
        if not isinstance(existing_pr, dict):
            existing_pr = {}
        existing_pr[self.label] = result
        context.artifacts["portfolio_results"] = existing_pr

        return context
