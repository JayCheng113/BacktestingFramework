"""Golden bug 2: MLAlpha timedelta(days=N) calendar purge lookahead.

Historical context: MLAlpha V1 round 1 used ``timedelta(days=N)`` for
label purge, which lets labels cross weekends and point at prediction
windows. Fixed in round 2 by using positional ``iloc[:-purge_bars]``.

We encode a minimal Factor that uses a calendar-day offset to peek at
future closes and assert LookaheadGuard catches it.

(The original bug was in MLAlpha label construction, which is
internal to a CrossSectionalFactor. LookaheadGuard V1 does not apply
to cross_factor due to engine pre-slicing — see the lookahead.py
docstring — but the underlying bug pattern (positive shift on date
index) is catchable on any DataFrame-in factor.)
"""
from __future__ import annotations
from datetime import timedelta
from pathlib import Path

import pandas as pd

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.lookahead import LookaheadGuard


class _CalendarPurgeLookaheadRepro:
    """Factor that uses 'close N calendar days later' as the value at
    the current row — up to 2 extra trading days leak in due to weekends."""
    name = "calendar_purge_bug"
    warmup_period = 0

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        out = data.copy()
        # Create a future-5-calendar-days series by index lookup
        offset = timedelta(days=5)
        future_values = []
        for dt in data.index:
            target = dt + offset
            mask = data.index >= target
            if mask.any():
                future_values.append(float(data.loc[mask, "close"].iloc[0]))
            else:
                future_values.append(float(data["close"].iloc[-1]))
        out[self.name] = future_values
        return out


def test_mlalpha_calendar_purge_bug_is_blocked():
    guard = LookaheadGuard()
    ctx = GuardContext(
        filename="mlalpha_purge_bug.py",
        module_name="test_mlalpha_purge_bug",
        file_path=Path("/tmp/mlalpha_purge_bug.py"),
        kind="factor",
        user_class=_CalendarPurgeLookaheadRepro,
    )
    result = guard.check(ctx)
    assert result.severity == GuardSeverity.BLOCK, (
        f"Golden bug regression: MLAlpha calendar purge lookahead not caught. "
        f"Guard result: {result.message}"
    )
