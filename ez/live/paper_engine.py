"""V2.15 A5: Paper Trading Engine — daily bar-driven execution.

Called once per trading day by the Scheduler.  Reuses the *same* strategy
interface (``PortfolioStrategy.generate_weights``), cost model, market rules,
and trade execution logic (``execute_portfolio_trades``) as the backtest engine,
but fetches data from the live DataProviderChain instead of pre-loaded history.

Idempotency is the Scheduler's responsibility, NOT the engine's.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from ez.portfolio.execution import CostModel, execute_portfolio_trades

logger = logging.getLogger(__name__)


class PaperTradingEngine:
    """Forward execution engine that reuses backtest strategy/optimizer/risk/cost logic.

    Parameters
    ----------
    spec : DeploymentSpec
        Immutable deployment configuration (symbols, market, cost params, etc.).
    strategy : PortfolioStrategy
        Strategy instance — ``generate_weights()`` called on rebalance days.
    data_chain : DataProviderChain
        Live data source chain (cache -> provider failover).
    optimizer : PortfolioOptimizer | None
        Optional portfolio optimizer applied after strategy weights.
    risk_manager : RiskManager | None
        Optional daily drawdown / turnover risk manager.
    """

    def __init__(self, spec, strategy, data_chain, optimizer=None, risk_manager=None):
        self.spec = spec  # DeploymentSpec — Scheduler reads engine.spec.market
        self.strategy = strategy
        self.data_chain = data_chain
        self.optimizer = optimizer
        self.risk_manager = risk_manager
        self._calendar = None  # lazy-init on first _is_rebalance_day call

        # Running state -------------------------------------------------------
        self.cash: float = spec.initial_cash
        self.holdings: dict[str, int] = {}
        self.equity_curve: list[float] = []
        self.dates: list[date] = []
        self.trades: list[dict] = []
        self.prev_weights: dict[str, float] = {}
        self.prev_returns: dict[str, float] = {}
        self.risk_events: list[dict] = []
        self._last_prices: dict[str, float] = {}  # cache for mark-to-market on data gaps
        self._rebalance_dates_cache: set[date] | None = None

    # ------------------------------------------------------------------
    # Main entry point — called by Scheduler once per trading day
    # ------------------------------------------------------------------

    def execute_day(self, today: date) -> dict:
        """Execute one trading day.

        Returns a dict with keys: date, equity, cash, holdings, weights,
        prev_returns, trades, risk_events, rebalanced.
        """
        universe_data = self._fetch_latest(today)
        if not universe_data:
            # No data available — use last known prices for mark-to-market
            # (don't estimate holdings at 0, which would falsely crash equity)
            equity = self._mark_to_market({})  # uses _last_prices cache
            self.equity_curve.append(equity)
            self.dates.append(today)
            return {
                "date": str(today), "equity": equity, "cash": self.cash,
                "holdings": dict(self.holdings), "weights": self.prev_weights,
                "prev_returns": {}, "trades": [],
                "risk_events": [], "rebalanced": False,
            }

        # 1. Pre-trade mark-to-market
        prices = self._get_latest_prices(universe_data)
        pre_equity = self._mark_to_market(prices)

        # 2. Risk check (every trading day, not just rebalance)
        day_risk_events: list[dict] = []
        dd_scale = 1.0
        if self.risk_manager:
            dd_scale, dd_event = self.risk_manager.check_drawdown(pre_equity)
            if dd_event:
                day_risk_events.append({
                    "date": str(today),
                    "drawdown_scale": dd_scale,
                    "action": dd_event,
                })

        # 3. Rebalance (trade execution)
        day_trades: list[dict] = []
        rebalanced = self._is_rebalance_day(today)

        if rebalanced:
            # Slice data [lookback, today-1] for anti-lookahead
            sliced = self._slice_history(universe_data, today)

            # Call strategy — second arg must be datetime, not date
            dt = datetime.combine(today, datetime.min.time())
            target_weights = self.strategy.generate_weights(
                sliced, dt, self.prev_weights, self.prev_returns,
            )

            # Optional optimizer
            if self.optimizer:
                self.optimizer.set_context(today, sliced)
                target_weights = self.optimizer.optimize(target_weights)

            # Optional turnover check
            if self.risk_manager:
                target_weights, to_event = self.risk_manager.check_turnover(
                    target_weights, self.prev_weights,
                )
                if to_event:
                    day_risk_events.append({
                        "date": str(today), "event": to_event,
                    })

            # Apply drawdown scale on rebalance day
            if dd_scale < 1.0:
                target_weights = {
                    s: w * dd_scale for s, w in target_weights.items()
                }

            # Clip to long-only, normalize if sum > 1
            target_weights = {
                k: max(0.0, v) for k, v in target_weights.items() if v > 0
            }
            total_w = sum(target_weights.values())
            if total_w > 1.0:
                target_weights = {
                    k: v / total_w for k, v in target_weights.items()
                }

            # Build market context for execution
            raw_closes = self._get_raw_closes(universe_data)
            prev_raw = self._get_prev_raw_closes(universe_data)
            has_bar = self._get_has_bar_today(universe_data, today)

            cost_model = CostModel(
                buy_commission_rate=self.spec.buy_commission_rate,
                sell_commission_rate=self.spec.sell_commission_rate,
                min_commission=self.spec.min_commission,
                stamp_tax_rate=self.spec.stamp_tax_rate,
                slippage_rate=self.spec.slippage_rate,
            )

            exec_trades, self.holdings, self.cash, _ = execute_portfolio_trades(
                target_weights=target_weights,
                holdings=self.holdings,
                equity=pre_equity,
                cash=self.cash,
                prices=prices,
                raw_close_today=raw_closes,
                prev_raw_close=prev_raw,
                has_bar_today=has_bar,
                cost_model=cost_model,
                lot_size=self.spec.lot_size,
                limit_pct=self.spec.price_limit_pct,
                t_plus_1=self.spec.t_plus_1,
            )

            day_trades = [
                {
                    "symbol": t.symbol, "side": t.side,
                    "shares": t.shares, "price": t.price,
                    "cost": t.cost, "amount": t.amount,
                }
                for t in exec_trades
            ]
            self.trades.extend(day_trades)

        # 4. Post-trade mark-to-market
        post_equity = self._mark_to_market(prices)

        # 5. Record
        self.equity_curve.append(post_equity)
        self.dates.append(today)
        self.risk_events.extend(day_risk_events)

        # Compute current weights and portfolio return
        self.prev_weights = self._compute_weights(prices, post_equity)
        self.prev_returns = self._compute_returns()

        return {
            "date": str(today),
            "equity": post_equity,
            "cash": self.cash,
            "holdings": dict(self.holdings),
            "weights": dict(self.prev_weights),
            "prev_returns": dict(self.prev_returns),
            "trades": day_trades,
            "risk_events": day_risk_events,
            "rebalanced": rebalanced,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _mark_to_market(self, prices: dict[str, float]) -> float:
        """Cash + sum(holdings * price).

        For symbols with no current price (data gap / suspension), uses the
        last known price from _last_prices cache. Never estimates at 0 —
        that would falsely crash equity and trigger spurious risk events.
        """
        position_value = 0.0
        for s, shares in self.holdings.items():
            if shares == 0:
                continue
            p = prices.get(s)
            if p is not None and p > 0:
                self._last_prices[s] = p  # cache for future gaps
            else:
                p = self._last_prices.get(s, 0)  # use last known
            position_value += shares * p
        return self.cash + position_value

    def _fetch_latest(self, today: date) -> dict[str, pd.DataFrame]:
        """Fetch lookback window of daily bars for each symbol in spec."""
        natural_days = int(self.strategy.lookback_days * 1.5) + 30
        start = today - timedelta(days=natural_days)
        data: dict[str, pd.DataFrame] = {}
        for sym in self.spec.symbols:
            try:
                bars = self.data_chain.get_kline(
                    sym, self.spec.market, "daily", start, today,
                )
                if not bars:
                    continue
                rows = []
                for b in bars:
                    rows.append({
                        "date": b.time,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "adj_close": b.adj_close,
                        "volume": b.volume,
                    })
                df = pd.DataFrame(rows)
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                data[sym] = df
            except Exception:
                logger.warning("Failed to fetch data for %s", sym, exc_info=True)
        return data

    def _get_latest_prices(self, data: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Latest adj_close for each symbol."""
        prices: dict[str, float] = {}
        for sym, df in data.items():
            if len(df) > 0:
                prices[sym] = float(df["adj_close"].iloc[-1])
        return prices

    def _get_raw_closes(self, data: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Latest raw close for each symbol (for limit up/down check)."""
        result: dict[str, float] = {}
        for sym, df in data.items():
            if len(df) > 0:
                result[sym] = float(df["close"].iloc[-1])
        return result

    def _get_prev_raw_closes(self, data: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Previous day raw close for each symbol (for limit up/down check)."""
        result: dict[str, float] = {}
        for sym, df in data.items():
            if len(df) >= 2:
                result[sym] = float(df["close"].iloc[-2])
        return result

    def _get_has_bar_today(
        self, data: dict[str, pd.DataFrame], today: date,
    ) -> set[str]:
        """Symbols that have a bar on *today* (no stale-price trading)."""
        has: set[str] = set()
        for sym, df in data.items():
            if len(df) == 0:
                continue
            last_ts = df.index[-1]
            last_date = last_ts.date() if hasattr(last_ts, "date") else last_ts
            if last_date == today:
                has.add(sym)
        return has

    def _is_rebalance_day(self, today: date) -> bool:
        """Check if *today* is a rebalance day per TradingCalendar + spec.freq.
        Caches the rebalance date set after first computation."""
        if self._rebalance_dates_cache is None:
            if self._calendar is None:
                from ez.portfolio.calendar import TradingCalendar
                self._calendar = TradingCalendar.from_market(self.spec.market)
            reb_dates = self._calendar.rebalance_dates(
                self._calendar.start, self._calendar.end, self.spec.freq,
                rebal_weekday=getattr(self.spec, 'rebal_weekday', None),
            )
            self._rebalance_dates_cache = set(reb_dates)
        return today in self._rebalance_dates_cache

    def _slice_history(
        self, data: dict[str, pd.DataFrame], today: date,
    ) -> dict[str, pd.DataFrame]:
        """Anti-lookahead: slice each symbol's DataFrame to strictly before *today*."""
        cutoff = pd.Timestamp(today)
        return {sym: df[df.index < cutoff] for sym, df in data.items()}

    def _compute_returns(self) -> dict[str, float]:
        """Portfolio-level return from the last two equity points."""
        if len(self.equity_curve) < 2:
            return {}
        prev_eq = self.equity_curve[-2]
        curr_eq = self.equity_curve[-1]
        ret = (curr_eq - prev_eq) / prev_eq if prev_eq > 0 else 0.0
        return {"_portfolio": ret}

    def _compute_weights(
        self, prices: dict[str, float], equity: float,
    ) -> dict[str, float]:
        """Current weights = (shares * price) / equity for each held symbol."""
        if equity <= 0:
            return {}
        weights: dict[str, float] = {}
        for sym, shares in self.holdings.items():
            if sym in prices and prices[sym] > 0:
                weights[sym] = (shares * prices[sym]) / equity
        return weights
