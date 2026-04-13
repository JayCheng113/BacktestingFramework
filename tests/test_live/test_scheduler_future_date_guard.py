"""V2.16.2 regression: Scheduler.tick rejects future business_date.

Prior behaviour: `scheduler.tick(future_date)` would run through the
loop, skip each deployment (calendar check false, or no data), advance
nothing, but then — because no deployment had `last_processed_date`
updated — the next tick with today's date would succeed. HOWEVER if
execute_day happened to succeed (calendar said "trading day", data
chain returned stale cached bars), `last_processed_date` would be set
to the future date. Subsequent correct ticks for today/yesterday would
be silently skipped by the idempotency gate (`last_date >= business_date`).

Lock the contract: future dates are refused up-front.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from ez.live.scheduler import Scheduler


def _fake_store() -> MagicMock:
    store = MagicMock()
    store.list_deployments.return_value = []
    return store


def test_tick_rejects_future_business_date() -> None:
    sched = Scheduler(store=_fake_store(), data_chain=MagicMock())
    future = date.today() + timedelta(days=1)
    with pytest.raises(ValueError, match="future"):
        asyncio.run(sched.tick(future))


def test_tick_accepts_today_and_past() -> None:
    """Today and past dates are valid — operator ticking retroactively
    for a missed day should still work (calendar + idempotency handle
    correctness)."""
    sched = Scheduler(store=_fake_store(), data_chain=MagicMock())
    # No engines loaded — both calls should return [] without raising
    assert asyncio.run(sched.tick(date.today())) == []
    assert asyncio.run(sched.tick(date.today() - timedelta(days=1))) == []


def test_tick_far_future_rejected() -> None:
    """Even well-intentioned far-future ticks (e.g., for testing)
    are refused; use a past date instead."""
    sched = Scheduler(store=_fake_store(), data_chain=MagicMock())
    with pytest.raises(ValueError):
        asyncio.run(sched.tick(date(2099, 12, 31)))
