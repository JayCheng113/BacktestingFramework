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

from ez.live.allocation import AllocationContext, RuntimeAllocatorConfig
from ez.live.broker import BrokerAdapter
from ez.live.oms import PaperOMS
from ez.live.paper_broker import PaperBroker
from ez.portfolio.optimizer import ledoit_wolf_shrinkage
from ez.portfolio.execution import CostModel

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

    def __init__(
        self,
        spec,
        strategy,
        data_chain,
        optimizer=None,
        risk_manager=None,
        deployment_id: str | None = None,
        broker: BrokerAdapter | None = None,
        shadow_broker: BrokerAdapter | None = None,
    ):
        self.spec = spec  # DeploymentSpec — Scheduler reads engine.spec.market
        self.strategy = strategy
        self.data_chain = data_chain
        self.optimizer = optimizer
        self.risk_manager = risk_manager
        self.deployment_id = deployment_id or spec.spec_id
        self.broker = broker or PaperBroker()
        self.shadow_broker = shadow_broker
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
        self._order_statuses: dict[str, str] = {}
        self._recovery_warnings: list[str] = []
        # V2.17 round 8: data sanity dedup — avoid spamming the same
        # warning every hour when a data anomaly persists for days.
        # Key: (symbol, check_kind) — dropped when the condition clears.
        self._sanity_warned: set[tuple[str, str]] = set()
        self._prev_day_prices: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Main entry point — called by Scheduler once per trading day
    # ------------------------------------------------------------------

    def execute_day(self, today: date) -> dict:
        """Execute one trading day.

        Returns a dict with keys: date, equity, cash, holdings, weights,
        prev_returns, trades, risk_events, rebalanced.
        Internal scheduler-only key: `_oms_events`.
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
                "_oms_events": [],
                "_market_snapshot": {
                    "prices": dict(self._last_prices),
                    "has_bar_symbols": [],
                    "source": "cached",
                },
                "_market_bars": [],
            }

        # 1. Pre-trade mark-to-market
        prices = self._get_latest_prices(universe_data)
        pre_equity = self._mark_to_market(prices)

        # 2. Risk check (every trading day, not just rebalance)
        day_risk_events: list[dict] = []
        dd_scale = 1.0
        rebalanced = self._is_rebalance_day(today)
        if self.risk_manager:
            dd_scale, dd_event = self.risk_manager.check_drawdown(pre_equity)
            if dd_event:
                day_risk_events.append({
                    "date": str(today),
                    "drawdown_scale": dd_scale,
                    "action": dd_event,
                })
            # I2: Emergency sell on non-rebalance days (parity with portfolio engine)
            if dd_scale < 1.0 and dd_event is not None and not rebalanced:
                raw_closes = self._get_raw_closes(universe_data)
                prev_raw = self._get_prev_raw_closes(universe_data)
                has_bar = self._get_has_bar_today(universe_data, today)
                from ez.portfolio.execution import _lot_round, _compute_commission, CostModel as _CM
                for sym in list(self.holdings.keys()):
                    if sym not in prices or sym not in has_bar:
                        continue
                    target = _lot_round(self.holdings[sym] * dd_scale, self.spec.lot_size)
                    delta = target - self.holdings[sym]
                    if delta >= 0:
                        continue
                    if self.spec.price_limit_pct > 0 and sym in raw_closes and sym in prev_raw:
                        prev_rc = prev_raw[sym]
                        chg = (raw_closes[sym] - prev_rc) / prev_rc if prev_rc > 0 else 0
                        if chg <= -self.spec.price_limit_pct + 1e-6:
                            continue
                    sell_price = prices[sym] * (1 - self.spec.slippage_rate)
                    sell_amount = abs(delta) * sell_price
                    comm = _compute_commission(sell_amount, self.spec.sell_commission_rate, self.spec.min_commission)
                    stamp = sell_amount * self.spec.stamp_tax_rate
                    self.cash += sell_amount - comm - stamp
                    if target == 0:
                        self.holdings.pop(sym, None)
                    else:
                        self.holdings[sym] = target
                    day_risk_events.append({
                        "date": str(today), "event": "emergency_sell",
                        "symbol": sym, "shares": abs(delta), "price": sell_price,
                    })
                pre_equity = self._mark_to_market(prices)

        # 3. Rebalance (trade execution)
        day_trades: list[dict] = []

        if rebalanced:
            # Slice data [lookback, today-1] for anti-lookahead
            sliced = self._slice_history(universe_data, today)

            # Call strategy — second arg must be datetime, not date
            dt = datetime.combine(today, datetime.min.time())
            target_weights = self.strategy.generate_weights(
                sliced, dt, self.prev_weights, self.prev_returns,
            )

            # V2.17 round 6: strategy can return None on non-rebalance days
            # to signal "skip rebalancing today, hold prior positions".
            # This mirrors `ez/portfolio/engine.py` V2.17 semantics so the
            # same strategy behaves identically in backtest and paper.
            # Without this guard, any strategy delegating to an inner
            # weekly-signaled one (e.g. strategies returning None) would
            # crash with 'NoneType has no attribute items'.
            if target_weights is None:
                rebalanced = False
                # Post-trade mark-to-market uses current prices (no trades)
                post_equity = self._mark_to_market(prices)
                self.equity_curve.append(post_equity)
                self.dates.append(today)
                self.risk_events.extend(day_risk_events)
                self.prev_weights = self._compute_weights(prices, post_equity)
                self.prev_returns = self._compute_returns()
                self._prev_day_prices = dict(prices)
                return {
                    "date": str(today), "equity": post_equity, "cash": self.cash,
                    "holdings": dict(self.holdings),
                    "weights": dict(self.prev_weights),
                    "prev_returns": dict(self.prev_returns),
                    "trades": [], "risk_events": day_risk_events,
                    "rebalanced": False, "_oms_events": [],
                    "_market_snapshot": {
                        "prices": dict(prices),
                        "has_bar_symbols": sorted(self._get_has_bar_today(universe_data, today)),
                        "source": "live",
                    },
                    "_market_bars": self._build_market_bar_payloads(universe_data, today),
                }

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

            # use_open_price parity with portfolio engine: compute adj_open
            # for trade execution while tracking equity at adj_close.
            use_open = getattr(self.spec, "use_open_price", False)
            if use_open:
                exec_prices = self._get_exec_open_prices(universe_data, prices, today)
            else:
                exec_prices = prices

            cost_model = CostModel(
                buy_commission_rate=self.spec.buy_commission_rate,
                sell_commission_rate=self.spec.sell_commission_rate,
                min_commission=self.spec.min_commission,
                stamp_tax_rate=self.spec.stamp_tax_rate,
                slippage_rate=self.spec.slippage_rate,
            )

            oms = PaperOMS(self.deployment_id, broker=self.broker)
            oms_result = oms.execute_rebalance(
                business_date=today,
                target_weights=target_weights,
                holdings=self.holdings,
                equity=pre_equity,
                cash=self.cash,
                prices=exec_prices,
                raw_close_today=raw_closes,
                prev_raw_close=prev_raw,
                has_bar_today=has_bar,
                cost_model=cost_model,
                lot_size=self.spec.lot_size,
                limit_pct=self.spec.price_limit_pct,
                t_plus_1=self.spec.t_plus_1,
                risk_params=self.spec.risk_params if self.spec.risk_control else None,
                allocator_context=self._build_allocator_context(
                    sliced,
                    self.spec.risk_params if self.spec.risk_control else None,
                    prices,
                    pre_equity,
                ),
                broker_type=getattr(self.spec, "broker_type", "paper") or "paper",
            )
            self.holdings = oms_result.holdings
            self.cash = oms_result.cash
            day_trades = oms_result.trades
            self.trades.extend(day_trades)
            day_risk_events.extend(oms_result.risk_events)
            self._order_statuses.update(
                {order.client_order_id: order.status.value for order in oms_result.orders}
            )

        # I3: Idempotency guard — prevent double-append if called twice for same date
        if self.dates and self.dates[-1] == today:
            logger.warning("execute_day called twice for %s — skipping duplicate", today)
            return {
                "date": str(today), "equity": self.equity_curve[-1], "cash": self.cash,
                "holdings": dict(self.holdings), "weights": dict(self.prev_weights),
                "prev_returns": dict(self.prev_returns), "trades": [], "risk_events": [],
                "rebalanced": False, "stale_prices": None, "_oms_events": [],
                "_market_snapshot": {"prices": dict(prices), "has_bar_symbols": [], "source": "cached"},
                "_market_bars": [],
            }

        # 4. Post-trade mark-to-market
        post_equity = self._mark_to_market(prices)

        # I1: Accounting invariant (parity with portfolio engine)
        EPS_FUND = 0.01
        if self.cash < -EPS_FUND:
            logger.error("Paper engine: negative cash on %s: cash=%.2f", today, self.cash)
        if post_equity <= 0:
            logger.error("Paper engine: non-positive equity on %s: equity=%.2f", today, post_equity)

        # 5. Record
        self.equity_curve.append(post_equity)
        self.dates.append(today)
        self.risk_events.extend(day_risk_events)

        # Compute current weights and portfolio return
        self.prev_weights = self._compute_weights(prices, post_equity)
        self.prev_returns = self._compute_returns()
        self._prev_day_prices = dict(prices)

        stale = getattr(self, "_stale_price_symbols", [])
        if stale:
            day_risk_events.append({
                "date": str(today),
                "event": "stale_price_warning",
                "message": f"{len(stale)}/{len(self.spec.symbols)} symbols 使用过期价格 (carry-forward)",
                "symbols": stale[:20],
            })

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
            "stale_prices": stale if stale else None,
            "_oms_events": oms_result.events if rebalanced else [],
            "_market_snapshot": {
                "prices": dict(prices),
                "has_bar_symbols": sorted(self._get_has_bar_today(universe_data, today)),
                "source": "live",
            },
            "_market_bars": self._build_market_bar_payloads(universe_data, today),
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

    # ------------------------------------------------------------------
    # V2.17 round 8: data sanity guard
    # ------------------------------------------------------------------

    # Thresholds for anomaly detection — tuned for A-share ETFs where
    # "normal" daily moves are < 10% (涨跌停) and ex-dividend days on
    # stock ETFs typically stay within 5% of adjusted.
    _RAW_SPIKE_PCT = 0.15        # raw close daily change > 15% = anomaly
    _ADJ_RAW_DIVERGENCE = 0.15   # adj spike without matching raw move
    _ADJ_RAW_TOLERANCE = 0.05    # raw move < 5% = "not a real price change"

    def _sanity_check_fresh_bars_detailed(self, symbol: str, df: "pd.DataFrame") -> list[tuple[str, str]]:
        """Runtime data quality check on the most recent 2 bars.

        Designed to catch V2.18.1-class silent data errors at tick time
        rather than at parquet rebuild time:

        1. Raw close daily change > 15%: likely suspension+resumption,
           cash dividend, data source error, or stock split with missing
           adj_factor. Any of these warrants operator attention.
        2. adj_close jumps but raw close doesn't: exact pattern of the
           V2.18.1 Tushare fund_adj anomalies (stored adj_factor spiked
           to 1.0 for a day, then back to correct value). If this still
           occurs in live data, we want to know immediately — it causes
           phantom equity moves.

        Returns list of human-readable warnings. Empty if data looks OK.
        Dedup is handled by the caller (`_sanity_warned` set).
        """
        import math
        if len(df) < 2:
            return []
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_raw = float(prev.get("close", float("nan")))
        curr_raw = float(curr.get("close", float("nan")))
        prev_adj = float(prev.get("adj_close", float("nan")))
        curr_adj = float(curr.get("adj_close", float("nan")))
        warnings: list[tuple[str, str]] = []

        # Check 1: raw close single-day spike > 15%
        if math.isfinite(prev_raw) and math.isfinite(curr_raw) and prev_raw > 0:
            raw_change = (curr_raw - prev_raw) / prev_raw
            if abs(raw_change) > self._RAW_SPIKE_PCT:
                warnings.append(
                    (
                        "raw_spike",
                        f"{symbol}: raw close 单日变动 {raw_change:+.1%} "
                        f"({prev_raw:.3f} → {curr_raw:.3f}). 可能原因: 除权分红、"
                        f"停牌复牌、数据源异常. 建议核对真实行情.",
                    )
                )

        # Check 2: adj spike without matching raw move (V2.18.1 pattern)
        if (
            math.isfinite(prev_adj) and math.isfinite(curr_adj)
            and math.isfinite(prev_raw) and math.isfinite(curr_raw)
            and prev_adj > 0 and prev_raw > 0
        ):
            adj_change = (curr_adj - prev_adj) / prev_adj
            raw_change = (curr_raw - prev_raw) / prev_raw
            if (
                abs(adj_change) > self._ADJ_RAW_DIVERGENCE
                and abs(raw_change) < self._ADJ_RAW_TOLERANCE
            ):
                warnings.append(
                    (
                        "adj_raw_divergence",
                        f"{symbol}: adj_close 跳变 {adj_change:+.1%} 但 raw "
                        f"close 仅变 {raw_change:+.1%} — V2.18.1 类型的 "
                        f"adj_factor 异常 pattern. 建议检查数据源 adj_factor.",
                    )
                )
        return warnings

    def _sanity_check_fresh_bars(self, symbol: str, df: "pd.DataFrame") -> list[str]:
        return [message for _, message in self._sanity_check_fresh_bars_detailed(symbol, df)]

    def _fetch_latest(self, today: date) -> dict[str, pd.DataFrame]:
        """Fetch lookback window of daily bars for each symbol in spec."""
        natural_days = int(self.strategy.lookback_days * 1.5) + 30
        start = today - timedelta(days=natural_days)
        data: dict[str, pd.DataFrame] = {}
        stale_symbols: list[str] = []
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
                last_bar_date = df.index[-1].date() if len(df) > 0 else None
                if last_bar_date is not None and (today - last_bar_date).days > 1:
                    stale_symbols.append(sym)
                data[sym] = df
            except Exception:
                logger.warning("Failed to fetch data for %s", sym, exc_info=True)
        if stale_symbols:
            logger.warning(
                "[数据滞后] %d/%d symbols 缺少 %s 的行情 (最新数据距今>1天): %s",
                len(stale_symbols), len(self.spec.symbols), today,
                stale_symbols[:10],
            )
            self._stale_price_symbols = stale_symbols
        else:
            self._stale_price_symbols = []
        # V2.17 round 8: runtime data sanity check on freshly fetched bars.
        # Catches V2.18.1-class silent anomalies (adj_factor spikes,
        # suspect daily moves) that would otherwise flow through to the
        # strategy and produce phantom signals.
        for sym, df in data.items():
            active_kinds: set[str] = set()
            for kind, msg in self._sanity_check_fresh_bars_detailed(sym, df):
                active_kinds.add(kind)
                key = (sym, kind)
                if key in self._sanity_warned:
                    continue
                self._sanity_warned.add(key)
                logger.warning("[数据异常] %s", msg)
            self._sanity_warned = {
                warned
                for warned in self._sanity_warned
                if warned[0] != sym or warned[1] in active_kinds
            }
        return data

    def _get_latest_prices(self, data: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Latest adj_close per symbol, falling back to raw close if adj is NaN.

        V2.16.2: parity with `ez/portfolio/engine.py` V2.18.1 fix. If a bar
        has adj_close=NaN (provider lag / fallback data source that skipped
        adj computation), previously paper_engine would insert NaN into
        `prices`, which later propagated into `execute_portfolio_trades`
        and crashed `_lot_round(NaN)` with ValueError. Fallback chain:
          1. adj_close if finite
          2. raw close if finite (symbol omitted from prices otherwise —
             execute_portfolio_trades's `sym not in prices` guard will
             skip the trade)
        `_mark_to_market` handles missing symbols via `_last_prices` cache.
        """
        import math
        prices: dict[str, float] = {}
        for sym, df in data.items():
            if len(df) == 0:
                continue
            adj = float(df["adj_close"].iloc[-1])
            if math.isfinite(adj):
                prices[sym] = adj
                continue
            raw = float(df["close"].iloc[-1])
            if math.isfinite(raw):
                prices[sym] = raw
            # else: drop — downstream code will skip this symbol
        return prices

    def _get_raw_closes(self, data: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Latest raw close for each symbol (for limit up/down check).

        NaN guarded: raw close used only for limit_pct compare; NaN would
        poison the % change math. Skip the symbol instead.
        """
        import math
        result: dict[str, float] = {}
        for sym, df in data.items():
            if len(df) == 0:
                continue
            v = float(df["close"].iloc[-1])
            if math.isfinite(v):
                result[sym] = v
        return result

    def _get_prev_raw_closes(self, data: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Previous day raw close for each symbol (for limit up/down check)."""
        import math
        result: dict[str, float] = {}
        for sym, df in data.items():
            if len(df) >= 2:
                v = float(df["close"].iloc[-2])
                if math.isfinite(v):
                    result[sym] = v
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

    def _build_market_bar_payloads(
        self,
        data: dict[str, pd.DataFrame],
        today: date,
    ) -> list[dict[str, float | str]]:
        payloads: list[dict[str, float | str]] = []
        has_bar_today = self._get_has_bar_today(data, today)
        for sym in sorted(has_bar_today):
            df = data.get(sym)
            if df is None or len(df) == 0:
                continue
            latest = df.iloc[-1]
            payloads.append(
                {
                    "symbol": sym,
                    "open": float(latest["open"]),
                    "high": float(latest["high"]),
                    "low": float(latest["low"]),
                    "close": float(latest["close"]),
                    "adj_close": float(latest["adj_close"]),
                    "volume": float(latest["volume"]),
                    "source": "live",
                }
            )
        return payloads

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
        """Per-symbol daily price returns (parity with portfolio engine).

        Prior version returned {"_portfolio": portfolio_return} which diverged
        from portfolio engine's per-symbol dict. Any strategy using prev_returns
        for per-symbol momentum/vol would silently get wrong data in paper mode.
        """
        prices = self._last_prices
        prev_prices = getattr(self, "_prev_day_prices", {})
        returns: dict[str, float] = {}
        for sym in self.holdings:
            if sym in prices and sym in prev_prices:
                old_p = prev_prices[sym]
                if old_p > 0:
                    returns[sym] = (prices[sym] - old_p) / old_p
        return returns

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

    def _get_exec_open_prices(
        self, data: dict[str, pd.DataFrame], adj_close_prices: dict[str, float], today: date,
    ) -> dict[str, float]:
        """Compute adj_open = open × (adj_close / raw_close) for trade execution.

        Parity with portfolio engine's use_open_price path (V2.18.1).
        """
        import math
        exec_prices: dict[str, float] = {}
        for sym, df in data.items():
            if df.empty:
                continue
            last = df.iloc[-1]
            last_date = df.index[-1].date() if hasattr(df.index[-1], "date") else None
            if last_date != today:
                continue
            raw_open = float(last.get("open", float("nan")))
            raw_close = float(last.get("close", float("nan")))
            adj_close = adj_close_prices.get(sym, float("nan"))
            if math.isfinite(raw_open) and math.isfinite(raw_close) and raw_close > 0 and math.isfinite(adj_close):
                exec_prices[sym] = raw_open * (adj_close / raw_close)
            elif math.isfinite(raw_open):
                exec_prices[sym] = raw_open
        return exec_prices

    def _build_allocator_context(
        self,
        sliced: dict[str, pd.DataFrame],
        risk_params: dict[str, Any] | None,
        prices: dict[str, float],
        equity: float,
    ) -> AllocationContext | None:
        config = RuntimeAllocatorConfig.from_params(risk_params)
        needs_context = (
            config.allocation_mode in {"risk_budget_cap", "constrained_opt"}
            or config.target_portfolio_vol is not None
        )
        if not needs_context:
            return None

        current_weights = self._compute_weights(prices, equity)
        vols: dict[str, float] = {}
        covariance_symbols: list[str] = []
        covariance_returns: list[list[float]] = []
        for symbol, df in sliced.items():
            if "adj_close" not in df.columns:
                continue
            returns = df["adj_close"].pct_change().dropna()
            if config.vol_lookback_days > 0:
                vol_returns = returns.iloc[-config.vol_lookback_days:]
            else:
                vol_returns = returns
            if len(vol_returns) >= 2:
                vol = float(vol_returns.std(ddof=1)) * (252 ** 0.5)
                if vol > 0:
                    vols[symbol] = vol

            if config.covariance_lookback_days > 0:
                cov_returns = returns.iloc[-config.covariance_lookback_days:]
            else:
                cov_returns = returns
            if len(cov_returns) >= 3:
                covariance_symbols.append(symbol)
                covariance_returns.append([float(x) for x in cov_returns.values])

        covariance_matrix = None
        if covariance_returns:
            min_len = min(len(ret) for ret in covariance_returns)
            if min_len >= 3:
                import numpy as np

                mat = np.column_stack([
                    ret[-min_len:] for ret in covariance_returns
                ])
                covariance_matrix = ledoit_wolf_shrinkage(mat)
        if config.allocation_mode == "constrained_opt":
            return AllocationContext(
                volatility_by_symbol=vols,
                current_weights=current_weights,
                covariance_symbols=tuple(covariance_symbols),
                covariance_matrix=covariance_matrix,
            )
        if not vols:
            return None
        return AllocationContext(
            volatility_by_symbol=vols,
            current_weights=current_weights,
            covariance_symbols=tuple(covariance_symbols),
            covariance_matrix=covariance_matrix,
        )
