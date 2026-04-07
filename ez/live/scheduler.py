"""V2.15 B1: Scheduler — single-process, idempotent, with pause/resume and auto-recovery.

Single-process scheduler that drives PaperTradingEngine instances daily.
Process restart -> resume_all() from DB. No multi-worker support.

Key invariants:
- tick() is serial (asyncio.Lock covers entire method)
- Idempotent: per-deployment last_processed_date check skips duplicates
- Error escalation: 3 consecutive errors -> status "error" + engine removed
- Success resets error count to 0
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime

from ez.live.deployment_spec import DeploymentSpec
from ez.live.deployment_store import DeploymentStore
from ez.live.paper_engine import PaperTradingEngine
from ez.portfolio.calendar import TradingCalendar

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 3


class Scheduler:
    """Single-process scheduler. No multi-worker support.
    Process restart -> resume_all() from DB."""

    def __init__(self, store: DeploymentStore, data_chain):
        self.store = store
        self.data_chain = data_chain
        self._engines: dict[str, PaperTradingEngine] = {}
        self._paused: set[str] = set()  # in-memory paused marker
        self._lock = asyncio.Lock()
        self._calendars: dict[str, TradingCalendar] = {}

    # ------------------------------------------------------------------
    # Lifecycle — start / pause / resume / stop
    # ------------------------------------------------------------------

    async def resume_all(self) -> int:
        """Startup: restore all status='running' deployments from DB.
        On engine init failure, rolls back status to 'error' (not phantom running).
        Returns the number of engines successfully restored.
        Locked with same _lock as tick() to prevent concurrent mutation."""
        async with self._lock:
            records = self.store.list_deployments(status="running")
            restored = 0
            for record in records:
                dep_id = record.deployment_id
                try:
                    await self._start_engine(dep_id)
                    restored += 1
                    logger.info("Restored deployment %s", dep_id)
                except Exception:
                    logger.error("Failed to restore deployment %s", dep_id, exc_info=True)
                    # Roll back to error — don't leave phantom "running" in DB
                    self.store.update_status(dep_id, "error", stop_reason="恢复引擎失败")
            return restored

    async def start_deployment(self, deployment_id: str) -> None:
        """Start approved deployment. Checks status=='approved' — hard gate.
        Engine init happens BEFORE status update to prevent phantom running.
        Locked with same _lock as tick() to prevent concurrent mutation."""
        async with self._lock:
            record = self.store.get_record(deployment_id)
            if record is None:
                raise ValueError(f"Deployment {deployment_id!r} not found")
            if record.status != "approved":
                raise ValueError(
                    f"Cannot start deployment {deployment_id!r}: "
                    f"status is {record.status!r}, must be 'approved'"
                )
            # Build engine FIRST — if it fails, status stays "approved"
            await self._start_engine(deployment_id)
            # Only update status after engine is successfully running
            self.store.update_status(deployment_id, "running")
            logger.info("Started deployment %s", deployment_id)

    async def pause_deployment(self, deployment_id: str) -> None:
        """Pause: engine stays in memory, tick skips. Locked."""
        async with self._lock:
            if deployment_id not in self._engines:
                raise ValueError(f"Deployment {deployment_id!r} not running")
            self._paused.add(deployment_id)
            self.store.update_status(deployment_id, "paused")
            logger.info("Paused deployment %s", deployment_id)

    async def resume_deployment(self, deployment_id: str) -> None:
        """Resume: only from 'paused' status. Locked."""
        async with self._lock:
            record = self.store.get_record(deployment_id)
            if record is None:
                raise ValueError(f"Deployment {deployment_id!r} not found")
            if record.status != "paused":
                raise ValueError(
                    f"Cannot resume deployment {deployment_id!r}: "
                    f"status is {record.status!r}, must be 'paused'"
                )
            self._paused.discard(deployment_id)
            if deployment_id not in self._engines:
                await self._start_engine(deployment_id)
            self.store.update_status(deployment_id, "running")
            logger.info("Resumed deployment %s", deployment_id)

    async def stop_deployment(self, deployment_id: str, reason: str, liquidate: bool = False) -> None:
        """Stop: release engine, update DB status. Validates record exists and is stoppable.

        Parameters
        ----------
        liquidate : bool
            If True, generate empty target weights to close all positions
            before stopping. The liquidation trades are saved as a final
            snapshot. If the engine is not found (e.g., after a restart),
            liquidation is skipped and a warning is logged.
        """
        async with self._lock:
            record = self.store.get_record(deployment_id)
            if record is None:
                raise ValueError(f"Deployment {deployment_id!r} not found")
            if record.status in ("stopped", "pending"):
                raise ValueError(
                    f"Cannot stop deployment {deployment_id!r}: "
                    f"status is {record.status!r}")

            liquidation_trades: list[dict] = []
            if liquidate and deployment_id in self._engines:
                engine = self._engines[deployment_id]
                if engine.holdings:
                    try:
                        from datetime import date as _date
                        from ez.portfolio.execution import CostModel, execute_portfolio_trades
                        today = _date.today()
                        prices = dict(engine._last_prices)
                        raw_closes = dict(prices)
                        prev_raw = dict(prices)
                        has_bar = set(engine.holdings.keys())
                        cost_model = CostModel(
                            buy_commission_rate=engine.spec.buy_commission_rate,
                            sell_commission_rate=engine.spec.sell_commission_rate,
                            min_commission=engine.spec.min_commission,
                            stamp_tax_rate=engine.spec.stamp_tax_rate,
                            slippage_rate=engine.spec.slippage_rate,
                        )
                        equity = engine._mark_to_market(prices)
                        exec_trades, new_holdings, new_cash, _ = execute_portfolio_trades(
                            target_weights={},
                            holdings=engine.holdings,
                            equity=equity,
                            cash=engine.cash,
                            prices=prices,
                            raw_close_today=raw_closes,
                            prev_raw_close=prev_raw,
                            has_bar_today=has_bar,
                            cost_model=cost_model,
                            lot_size=engine.spec.lot_size,
                            limit_pct=0.0,  # no limit check on liquidation
                            t_plus_1=False,  # allow selling everything
                        )
                        liquidation_trades = [
                            {"symbol": t.symbol, "side": t.side, "shares": t.shares,
                             "price": t.price, "cost": t.cost, "amount": t.amount}
                            for t in exec_trades
                        ]
                        engine.holdings = new_holdings
                        engine.cash = new_cash
                        # Save liquidation snapshot — use ACTUAL new_holdings (may be
                        # non-empty if some positions couldn't be sold due to missing prices)
                        post_equity = new_cash + sum(
                            new_holdings.get(s, 0) * prices.get(s, 0) for s in new_holdings)
                        self.store.save_daily_snapshot(deployment_id, today, {
                            "date": str(today), "equity": post_equity,
                            "cash": new_cash, "holdings": dict(new_holdings),
                            "weights": {}, "trades": liquidation_trades,
                            "risk_events": [], "rebalanced": False,
                            "liquidation": True,
                        })
                        logger.info("Liquidated %d positions for %s", len(liquidation_trades), deployment_id)
                    except Exception:
                        logger.error("Liquidation failed for %s", deployment_id, exc_info=True)
                else:
                    logger.info("No holdings to liquidate for %s", deployment_id)
            elif liquidate:
                logger.warning("Engine not found for %s, skipping liquidation", deployment_id)

            self._engines.pop(deployment_id, None)
            self._paused.discard(deployment_id)
            self.store.update_status(deployment_id, "stopped", stop_reason=reason)
            logger.info("Stopped deployment %s: %s", deployment_id, reason)

    # ------------------------------------------------------------------
    # Tick — daily execution
    # ------------------------------------------------------------------

    async def tick(self, business_date: date) -> list[dict]:
        """Daily execution. asyncio.Lock covers entire tick.
        Per-deployment: check paused -> check calendar -> check idempotent -> execute -> save."""
        results: list[dict] = []
        async with self._lock:
            # Snapshot engine keys to avoid mutation during iteration
            dep_ids = list(self._engines.keys())
            for dep_id in dep_ids:
                engine = self._engines.get(dep_id)
                if engine is None:
                    continue

                # 1. Skip paused
                if dep_id in self._paused:
                    logger.debug("Skipping paused deployment %s", dep_id)
                    continue

                # 2. Check calendar — per-deployment market
                calendar = self._get_calendar(engine.spec.market)
                if not calendar.is_trading_day(business_date):
                    logger.debug(
                        "Skipping %s: %s is not a trading day for %s",
                        dep_id, business_date, engine.spec.market,
                    )
                    continue

                # 3. Idempotency — skip if already processed
                last_date = self.store.get_last_processed_date(dep_id)
                if last_date is not None and last_date >= business_date:
                    logger.debug(
                        "Skipping %s: already processed %s (last=%s)",
                        dep_id, business_date, last_date,
                    )
                    continue

                # 4. Execute
                try:
                    t0 = time.monotonic()
                    result = engine.execute_day(business_date)
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    result["execution_ms"] = elapsed_ms

                    # 5. Save snapshot
                    self.store.save_daily_snapshot(dep_id, business_date, result)
                    self.store.reset_error_count(dep_id)

                    result["deployment_id"] = dep_id
                    results.append(result)
                    logger.info(
                        "Deployment %s executed %s (%.1fms, equity=%.2f)",
                        dep_id, business_date, elapsed_ms,
                        result.get("equity", 0),
                    )

                except Exception as e:
                    logger.error(
                        "Deployment %s failed on %s: %s",
                        dep_id, business_date, e, exc_info=True,
                    )
                    # Save error snapshot
                    self.store.save_error(dep_id, business_date, str(e))
                    error_count = self.store.increment_error_count(dep_id)

                    if error_count >= MAX_CONSECUTIVE_ERRORS:
                        logger.error(
                            "Deployment %s reached %d consecutive errors — setting to error state",
                            dep_id, error_count,
                        )
                        self.store.update_status(
                            dep_id, "error",
                            stop_reason=f"连续 {error_count} 次执行失败: {e}",
                        )
                        self._engines.pop(dep_id, None)
                        self._paused.discard(dep_id)

        return results

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    def _get_calendar(self, market: str) -> TradingCalendar:
        """Per-market calendar (cached)."""
        if market not in self._calendars:
            self._calendars[market] = TradingCalendar.from_market(market)
        return self._calendars[market]

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    async def _start_engine(self, deployment_id: str) -> None:
        """Instantiate strategy + engine + restore full state."""
        record = self.store.get_record(deployment_id)
        if record is None:
            raise ValueError(f"Deployment record {deployment_id!r} not found")

        spec = self.store.get_spec(record.spec_id)
        if spec is None:
            raise ValueError(f"Spec {record.spec_id!r} not found for deployment {deployment_id!r}")

        strategy, optimizer, risk_manager = self._instantiate(spec)

        engine = PaperTradingEngine(
            spec=spec,
            strategy=strategy,
            data_chain=self.data_chain,
            optimizer=optimizer,
            risk_manager=risk_manager,
        )

        # Restore full state from snapshots
        self._restore_full_state(engine, deployment_id)

        self._engines[deployment_id] = engine

    def _restore_full_state(self, engine: PaperTradingEngine, deployment_id: str):
        """Restore cash, holdings, prev_weights, prev_returns, equity_curve,
        dates, trades, risk_events from all snapshots. Call risk_manager.replay_equity()."""
        snapshots = self.store.get_all_snapshots(deployment_id)
        if not snapshots:
            return

        # Rebuild equity curve and dates from all snapshots
        equity_curve: list[float] = []
        dates: list[date] = []
        all_trades: list[dict] = []
        all_risk_events: list[dict] = []

        for snap in snapshots:
            equity_curve.append(snap["equity"])
            snap_date = snap["snapshot_date"]
            if isinstance(snap_date, str):
                snap_date = date.fromisoformat(snap_date)
            dates.append(snap_date)
            all_trades.extend(snap.get("trades", []))
            all_risk_events.extend(snap.get("risk_events", []))

        # Restore engine state from the latest snapshot
        latest = snapshots[-1]
        engine.cash = latest["cash"]
        engine.holdings = {
            sym: int(qty) for sym, qty in latest.get("holdings", {}).items()
        }
        engine.prev_weights = dict(latest.get("weights", {}))
        engine.prev_returns = dict(latest.get("prev_returns", {}))
        engine.equity_curve = equity_curve
        engine.dates = dates
        engine.trades = all_trades
        engine.risk_events = all_risk_events

        # Rebuild _last_prices from latest snapshot holdings + weights
        # This prevents mark-to-market from estimating holdings at 0 after restart
        if engine.holdings and latest.get("weights"):
            weights = latest["weights"]
            equity = latest["equity"]
            if equity > 0:
                for sym, shares in engine.holdings.items():
                    w = weights.get(sym, 0)
                    if shares > 0 and w > 0:
                        # Reconstruct price: price = (equity * weight) / shares
                        engine._last_prices[sym] = (equity * w) / shares

        # Replay equity curve into risk manager to restore drawdown state machine
        if engine.risk_manager and equity_curve:
            engine.risk_manager.replay_equity(equity_curve)

    def _instantiate(self, spec: DeploymentSpec) -> tuple:
        """Create strategy + optimizer + risk_manager from DeploymentSpec.

        Uses the same pattern as _create_strategy in ez/api/routes/portfolio.py
        to handle TopNRotation/MultiFactorRotation/StrategyEnsemble.

        Returns (strategy, optimizer | None, risk_manager | None).
        """
        # -- Strategy --
        strategy = self._create_strategy_from_spec(spec)

        # -- Optimizer --
        optimizer = None
        if spec.optimizer and spec.optimizer != "none":
            optimizer = self._create_optimizer(spec)

        # -- Risk Manager --
        risk_manager = None
        if spec.risk_control:
            risk_manager = self._create_risk_manager(spec)

        return strategy, optimizer, risk_manager

    def _create_strategy_from_spec(self, spec: DeploymentSpec):
        """Instantiate a PortfolioStrategy from spec. Mirrors _create_strategy()."""
        from ez.portfolio.portfolio_strategy import (
            PortfolioStrategy, TopNRotation, MultiFactorRotation,
        )

        name = spec.strategy_name
        params = dict(spec.strategy_params)

        if name == "TopNRotation":
            factor_name = params.pop("factor", "momentum_rank_20")
            factor = self._resolve_factor(factor_name)
            top_n = params.pop("top_n", 10)
            return TopNRotation(factor=factor, top_n=top_n, **params)

        elif name == "MultiFactorRotation":
            factor_names = params.pop("factors", ["momentum_rank_20"])
            factors = [self._resolve_factor(fn) for fn in factor_names]
            top_n = params.pop("top_n", 10)
            return MultiFactorRotation(factors=factors, top_n=top_n, **params)

        elif name == "StrategyEnsemble":
            from ez.portfolio.ensemble import StrategyEnsemble

            sub_defs = params.pop("sub_strategies", [])
            mode = params.pop("mode", "equal")
            ensemble_weights = params.pop("ensemble_weights", None)
            warmup_rebalances = params.pop("warmup_rebalances", 8)
            correlation_threshold = params.pop("correlation_threshold", 0.9)

            sub_strategies = []
            for sub_def in sub_defs:
                sub_name = sub_def.get("name", "")
                sub_params = dict(sub_def.get("params", {}))
                # Create a minimal sub-spec to reuse this method
                sub_spec_like = type("_SubSpec", (), {
                    "strategy_name": sub_name,
                    "strategy_params": sub_params,
                })()
                sub_strat = self._create_strategy_from_spec(sub_spec_like)
                sub_strategies.append(sub_strat)

            return StrategyEnsemble(
                strategies=sub_strategies,
                mode=mode,
                ensemble_weights=ensemble_weights,
                warmup_rebalances=warmup_rebalances,
                correlation_threshold=correlation_threshold,
            )

        else:
            # Fallback: lookup in registry
            registry = PortfolioStrategy.get_registry()
            if name in registry:
                cls = registry[name]
                return cls(**params)
            # Try resolve_class for key-based lookup
            cls = PortfolioStrategy.resolve_class(name)
            return cls(**params)

    def _resolve_factor(self, factor_name: str):
        """Resolve a cross-sectional factor by name."""
        from ez.portfolio.cross_factor import (
            CrossSectionalFactor, MomentumRank, VolumeRank,
            ReverseVolatilityRank,
        )

        builtin_map = {
            "momentum_rank_20": lambda: MomentumRank(lookback=20),
            "momentum_rank_60": lambda: MomentumRank(lookback=60),
            "volume_rank": VolumeRank,
            "reverse_volatility_rank": ReverseVolatilityRank,
        }
        if factor_name in builtin_map:
            return builtin_map[factor_name]()

        # Try registry
        registry = CrossSectionalFactor.get_registry()
        if factor_name in registry:
            cls = registry[factor_name]
            return cls()

        # Try resolve_class
        cls = CrossSectionalFactor.resolve_class(factor_name)
        return cls()

    def _create_optimizer(self, spec: DeploymentSpec):
        """Create a PortfolioOptimizer from spec."""
        from ez.portfolio.optimizer import (
            MeanVarianceOptimizer, MinVarianceOptimizer,
            RiskParityOptimizer, OptimizationConstraints,
        )

        opt_params = spec.optimizer_params
        constraints = OptimizationConstraints(
            max_weight=opt_params.get("max_weight", 0.10),
            max_industry_weight=opt_params.get("max_industry_weight", 0.30),
        )
        cov_lookback = opt_params.get("cov_lookback", 60)

        if spec.optimizer == "mean_variance":
            risk_aversion = opt_params.get("risk_aversion", 1.0)
            return MeanVarianceOptimizer(
                risk_aversion=risk_aversion,
                constraints=constraints,
                cov_lookback=cov_lookback,
            )
        elif spec.optimizer == "min_variance":
            return MinVarianceOptimizer(
                constraints=constraints,
                cov_lookback=cov_lookback,
            )
        else:
            return RiskParityOptimizer(
                constraints=constraints,
                cov_lookback=cov_lookback,
            )

    def _create_risk_manager(self, spec: DeploymentSpec):
        """Create a RiskManager from spec."""
        from ez.portfolio.risk_manager import RiskManager, RiskConfig

        risk_params = spec.risk_params
        return RiskManager(RiskConfig(
            max_drawdown_threshold=risk_params.get("max_drawdown_threshold", 0.20),
            drawdown_reduce_ratio=risk_params.get("drawdown_reduce_ratio", 0.50),
            drawdown_recovery_ratio=risk_params.get("drawdown_recovery_ratio", 0.10),
            max_turnover=risk_params.get("max_turnover", 0.50),
        ))
