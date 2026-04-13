"""Simulate a 1-week backfill of paper trading: create fresh deployment
with DailyEqualWeightTest, tick each trading day in order, print result.

Purpose: verify end-to-end paper trading behavior across multiple days.
- Day 1: first rebalance = buys all 3 ETFs
- Day 2-N: equal weight (~33.3%) target unchanged, but actual weights
  drift with price moves → if drift > 1e-3 (engine threshold) trades fire
- Equity trajectory = fn of actual prices day-by-day
- Aggregate trades, costs, verify no error

Runs in-process — does NOT need backend running. Directly builds
scheduler/engine and calls tick() with each past date.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


SYMBOLS = ["510300.SH", "511010.SH", "518880.SH"]

# A-share trading days for the backfill window (清明假期 4-4 ~ 4-6 excluded)
REPLAY_DATES = [
    date(2026, 4, 3),   # Fri (day 1 — first buy)
    date(2026, 4, 7),   # Tue (Mon is 清明 holiday)
    date(2026, 4, 8),   # Wed
    date(2026, 4, 9),   # Thu
    date(2026, 4, 10),  # Fri
    date(2026, 4, 13),  # Mon (last — today)
]


def main():
    from ez.portfolio.loader import load_portfolio_strategies
    load_portfolio_strategies()

    from ez.live.deployment_spec import DeploymentSpec, DeploymentRecord
    from ez.live.deployment_store import DeploymentStore
    from ez.live.scheduler import Scheduler
    from ez.api.deps import get_store, get_chain

    base_store = get_store()
    chain = get_chain()
    # Fresh DeploymentStore sharing the same DuckDB connection
    dep_store = DeploymentStore(base_store._conn)

    # Create spec + record (fresh, no last_processed_date)
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

    dep_id = f"dep-replay-{uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()
    record = DeploymentRecord(
        deployment_id=dep_id, spec_id=spec.spec_id,
        name="Week replay verification",
        status="approved", stop_reason="",
        source_run_id=None, code_commit=None,
        gate_verdict='{"passed":true,"summary":"replay test"}',
        created_at=now, approved_at=now,
        started_at=None, stopped_at=None,
    )
    dep_store.save_record(record)

    print(f"Created: {dep_id}")
    print(f"{'=' * 78}")

    # Build scheduler, start deployment
    scheduler = Scheduler(store=dep_store, data_chain=chain)

    async def run_replay():
        await scheduler.start_deployment(dep_id)

        all_results = []
        for d in REPLAY_DATES:
            # Temporarily bypass future-date guard (these are all past dates
            # relative to wall clock but we need to tick in order).
            # tick checks `business_date > date.today()` — all our replay
            # dates are <= today so pass naturally.
            results = await scheduler.tick(d)
            if not results:
                print(f"{d} {d.strftime('%a')} — no result (non-trading day or idempotent skip)")
                continue
            r = results[0]
            # Compact summary
            n_trades = len(r.get("trades", []))
            total_cost = sum(t.get("cost", 0) for t in r.get("trades", []))
            n_pos = len([v for v in r.get("holdings", {}).values() if v > 0])
            print(
                f"{d} {d.strftime('%a')} "
                f"equity={r['equity']:>10,.0f} cash={r['cash']:>8,.0f} "
                f"pos={n_pos} trades={n_trades} cost={total_cost:>6.1f} "
                f"rebal={r['rebalanced']}"
            )
            # Print trades in detail only on days with trades
            if n_trades > 0 and n_trades <= 5:
                for t in r["trades"]:
                    print(f"    {t['side']:4s} {t['symbol']} {t['shares']:>6,} @ "
                          f"{t['price']:6.3f} cost={t['cost']:6.2f}")
            all_results.append((d, r))

        # Summary
        print(f"\n{'=' * 78}")
        if all_results:
            first_eq = all_results[0][1]["equity"]
            last_eq = all_results[-1][1]["equity"]
            total_ret = (last_eq - first_eq) / first_eq
            total_trades = sum(len(r.get("trades", [])) for _, r in all_results)
            total_costs = sum(
                sum(t.get("cost", 0) for t in r.get("trades", []))
                for _, r in all_results
            )
            print(f"Week summary:")
            print(f"  Day 1 equity: {first_eq:,.0f}")
            print(f"  Day {len(all_results)} equity: {last_eq:,.0f}")
            print(f"  Total return: {total_ret * 100:+.3f}%")
            print(f"  Total trades: {total_trades}")
            print(f"  Total costs: {total_costs:,.1f}")

    asyncio.run(run_replay())


if __name__ == "__main__":
    main()
