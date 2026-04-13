"""V2.17 round 3: paper-trading auto-tick loop.

Contract:
1. Loop sleeps `interval_s`, then calls `scheduler.tick(date.today())`.
2. ValueError (future-date guard, other config) logged as warning,
   loop continues.
3. Arbitrary exception from tick() doesn't crash the loop — it keeps
   ticking next interval.
4. CancelledError returns cleanly (shutdown path).
5. Zero-arg tick is NOT called unless interval elapsed (no tick at
   startup).

No real timers — we monkey-patch asyncio.sleep to return immediately,
and wrap tick() in counters.
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Minimal fake Scheduler
# ---------------------------------------------------------------------------

class _FakeScheduler:
    def __init__(self, behavior: str = "ok"):
        self.calls: list[date] = []
        self.behavior = behavior  # "ok" / "value_error" / "exception"

    async def tick(self, business_date: date):
        self.calls.append(business_date)
        if self.behavior == "value_error":
            raise ValueError("future-date simulated")
        if self.behavior == "exception":
            raise RuntimeError("simulated crash")
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_calls_tick_with_today(monkeypatch) -> None:
    """Basic happy path: after `sleep(interval_s)`, loop calls
    scheduler.tick(date.today()). Returns nothing abnormal."""
    from ez.api.app import _auto_tick_loop

    # Monkey-patch asyncio.sleep so the loop doesn't actually wait.
    # After first sleep we raise CancelledError to exit cleanly.
    sleep_count = [0]
    orig_sleep = asyncio.sleep

    async def fast_sleep(s):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:  # Exit after second iteration
            raise asyncio.CancelledError()
        # Use the real sleep for a very short time to yield to event loop
        await orig_sleep(0)

    monkeypatch.setattr("ez.api.app.asyncio.sleep", fast_sleep)

    sched = _FakeScheduler("ok")
    await _auto_tick_loop(sched, interval_s=1)
    assert len(sched.calls) == 1
    assert sched.calls[0] == date.today()


@pytest.mark.asyncio
async def test_loop_survives_value_error(monkeypatch) -> None:
    """future-date guard or similar ValueError must not crash the
    loop — should warn + keep iterating."""
    from ez.api.app import _auto_tick_loop

    sleep_count = [0]
    orig_sleep = asyncio.sleep

    async def fast_sleep(s):
        sleep_count[0] += 1
        if sleep_count[0] >= 4:  # 3 ticks then exit
            raise asyncio.CancelledError()
        await orig_sleep(0)

    monkeypatch.setattr("ez.api.app.asyncio.sleep", fast_sleep)

    sched = _FakeScheduler("value_error")
    await _auto_tick_loop(sched, interval_s=1)
    # Loop survives all iterations despite ValueError each time
    assert len(sched.calls) == 3


@pytest.mark.asyncio
async def test_loop_survives_unexpected_exception(monkeypatch) -> None:
    """Unexpected RuntimeError from tick() should be caught + logged,
    loop continues. Critical: a crash would leave paper trading
    unattended forever until user notices."""
    from ez.api.app import _auto_tick_loop

    sleep_count = [0]
    orig_sleep = asyncio.sleep

    async def fast_sleep(s):
        sleep_count[0] += 1
        if sleep_count[0] >= 4:
            raise asyncio.CancelledError()
        await orig_sleep(0)

    monkeypatch.setattr("ez.api.app.asyncio.sleep", fast_sleep)

    sched = _FakeScheduler("exception")
    # Should NOT raise RuntimeError — loop catches everything
    await _auto_tick_loop(sched, interval_s=1)
    assert len(sched.calls) == 3


@pytest.mark.asyncio
async def test_loop_exits_cleanly_on_cancel(monkeypatch) -> None:
    """CancelledError during sleep OR during tick must propagate a
    clean return (not re-raise). Shutdown path relies on this."""
    from ez.api.app import _auto_tick_loop

    async def cancelled_sleep(s):
        raise asyncio.CancelledError()

    monkeypatch.setattr("ez.api.app.asyncio.sleep", cancelled_sleep)

    sched = _FakeScheduler("ok")
    # Should return without raising
    await _auto_tick_loop(sched, interval_s=1)
    # tick never invoked (cancel during first sleep)
    assert sched.calls == []


@pytest.mark.asyncio
async def test_loop_does_not_tick_before_first_interval(monkeypatch) -> None:
    """Loop must sleep BEFORE first tick, not after. Prevents startup
    from instantly ticking (which could hit a race with resume_all
    still loading engines)."""
    from ez.api.app import _auto_tick_loop

    call_order: list[str] = []
    orig_sleep = asyncio.sleep

    async def tracking_sleep(s):
        call_order.append("sleep")
        # Exit immediately after first sleep
        raise asyncio.CancelledError()

    monkeypatch.setattr("ez.api.app.asyncio.sleep", tracking_sleep)

    sched = _FakeScheduler("ok")
    await _auto_tick_loop(sched, interval_s=1)
    assert call_order == ["sleep"]
    assert sched.calls == []  # never ticked
