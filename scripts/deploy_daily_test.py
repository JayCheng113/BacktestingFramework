"""Minimal deploy helper for DailyEqualWeightTest — used to verify
paper trading logic on any trading day.

3 symbols (HS300 / 5Y bond / gold), equal weight, daily rebalance.
Skips DeployGate (test-only). Creates + starts the deployment
immediately so a single manual tick can exercise buy logic end-to-end.
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


SYMBOLS = ["510300.SH", "511010.SH", "518880.SH"]


def main():
    from ez.portfolio.loader import load_portfolio_strategies
    load_portfolio_strategies()

    from ez.portfolio.builtin_strategies import DailyEqualWeightTest
    from ez.data.store import DuckDBStore
    from ez.portfolio.calendar import TradingCalendar
    from ez.portfolio.universe import Universe
    from ez.portfolio.engine import run_portfolio_backtest
    from ez.portfolio.execution import CostModel

    # Use DataProviderChain (parquet → DB → provider fetch fallback) —
    # direct Store.query_kline only reads parquet+DB and silently misses
    # days after parquet's manifest_end. This is a real bug being tracked.
    from ez.api.deps import get_chain
    chain = get_chain()
    end = date.today()
    start = end - timedelta(days=200)
    universe_data = {}
    import pandas as pd
    for sym in SYMBOLS:
        bars = chain.get_kline(sym, "cn_stock", "daily", start, end)
        rows = [{"date": b.time, "open": b.open, "high": b.high, "low": b.low,
                 "close": b.close, "adj_close": b.adj_close, "volume": b.volume}
                for b in bars]
        df = pd.DataFrame(rows).set_index("date").sort_index()
        df.index = pd.to_datetime(df.index)
        universe_data[sym] = df
    trading_days = sorted({d.date() for df in universe_data.values() for d in df.index})
    print(f"Data: {len(universe_data)} symbols × {len(trading_days)} days "
          f"({trading_days[0]} → {trading_days[-1]})")

    calendar = TradingCalendar.from_dates(trading_days)
    universe = Universe(SYMBOLS)
    strategy = DailyEqualWeightTest(symbols=SYMBOLS)

    print("Running 200-day backtest (daily rebal, real A-share costs) ...")
    result = run_portfolio_backtest(
        strategy=strategy, universe=universe, universe_data=universe_data,
        calendar=calendar, start=trading_days[0], end=trading_days[-1],
        freq="daily", initial_cash=1_000_000.0,
        cost_model=CostModel(
            buy_commission_rate=0.00008, sell_commission_rate=0.00008,
            min_commission=0.0, stamp_tax_rate=0.0005, slippage_rate=0.001,
        ),
        lot_size=100, limit_pct=0.10, t_plus_1=True,
    )
    print(f"  equity: {result.equity_curve[0]:.0f} → {result.equity_curve[-1]:.0f}")
    print(f"  trades: {len(result.trades)}")

    # Save run
    from ez.api.routes.portfolio import _get_store, _get_current_data_hash
    pf_store = _get_store()
    run_id = pf_store.save_run({
        "strategy_name": "DailyEqualWeightTest",
        "strategy_params": {"symbols": SYMBOLS},
        "symbols": SYMBOLS,
        "start_date": trading_days[0],
        "end_date": trading_days[-1],
        "initial_cash": 1_000_000.0,
        "metrics": {},
        "equity_curve": list(result.equity_curve),
        "trades": [t if isinstance(t, dict) else {
            "symbol": getattr(t, "symbol", ""), "side": getattr(t, "side", ""),
            "shares": getattr(t, "shares", 0), "price": getattr(t, "price", 0.0),
        } for t in result.trades],
        "trade_count": len(result.trades),
        "dates": [str(d) for d in result.dates],
        "config": {
            "market": "cn_stock", "freq": "daily",
            "_cost": {
                "buy_commission_rate": 0.00008, "sell_commission_rate": 0.00008,
                "min_commission": 0.0, "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.001, "lot_size": 100, "limit_pct": 0.10,
            },
            "_data_hash": _get_current_data_hash(),
        },
    })
    print(f"  run_id: {run_id}")

    # Create Deployment directly in approved state
    from ez.live.deployment_spec import DeploymentSpec, DeploymentRecord
    from ez.api.routes.live import _get_deployment_store
    dep_store = _get_deployment_store()
    spec = DeploymentSpec(
        strategy_name="DailyEqualWeightTest",
        strategy_params={"symbols": SYMBOLS},
        symbols=tuple(SYMBOLS),
        market="cn_stock", freq="daily",
        t_plus_1=True, price_limit_pct=0.10, lot_size=100,
        buy_commission_rate=0.00008, sell_commission_rate=0.00008,
        stamp_tax_rate=0.0005, slippage_rate=0.001, min_commission=0.0,
        initial_cash=1_000_000.0,
    )
    dep_store.save_spec(spec)
    dep_id = f"dep-daily-{uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()
    record = DeploymentRecord(
        deployment_id=dep_id, spec_id=spec.spec_id,
        name="Daily 3-ETF Test (paper logic verification)",
        status="approved", stop_reason="",
        source_run_id=run_id, code_commit=None,
        gate_verdict='{"passed":true,"summary":"test-only, gate skipped"}',
        created_at=now, approved_at=now,
        started_at=None, stopped_at=None,
    )
    dep_store.save_record(record)
    print(f"\nDeployment: {dep_id} (status=approved)")
    print("Next: start backend, POST /api/live/deployments/{id}/start, then /tick")


if __name__ == "__main__":
    main()
