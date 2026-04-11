"""Golden bug 1: v1 Dynamic EF lookahead.

Historical context (see validation/phase_o_nested_oos.py and
validation/report_charts/降回撤研究_v5.md):
  The original v1 Dynamic EF implementation computed weights using
  prices from date t, but the 'trading' happened at t+1. This silently
  inflated Sharpe by ~0.4 in backtest and would have destroyed live
  capital. Codex caught it during round-2 review.

We encode a minimal reproduction as a Factor (DataFrame-in, DataFrame-out)
and assert LookaheadGuard blocks it. If this test fails in the future,
LookaheadGuard has regressed.

(The original bug was in a cross_factor-style computation, but LookaheadGuard
V1 applies only to factor kind — see ez/testing/guards/lookahead.py docstring
for rationale. The lookahead pattern — iloc[target_idx + 1] — is the same.)
"""
from __future__ import annotations
from pathlib import Path

import pandas as pd

from ez.testing.guards.base import GuardContext, GuardSeverity
from ez.testing.guards.lookahead import LookaheadGuard


class _V1DynamicEFBugRepro:
    """Minimal reproduction of the v1 Dynamic EF lookahead pattern.

    BUG: writes ``close[t+1] / close[t] - 1`` as factor value at row t,
    using next-day close. In the original code this leaked ~0.4 Sharpe.
    """
    name = "v1_ef_bug"
    warmup_period = 0

    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        out = data.copy()
        closes = data["close"].values
        n = len(closes)
        # At row t, read close[t+1] — explicit future access.
        next_close = [closes[min(t + 1, n - 1)] for t in range(n)]
        out[self.name] = [
            (next_close[t] - closes[t]) / closes[t] if closes[t] != 0 else 0.0
            for t in range(n)
        ]
        return out


def test_v1_dynamic_ef_bug_is_blocked():
    guard = LookaheadGuard()
    ctx = GuardContext(
        filename="v1_ef_bug.py",
        module_name="test_v1_ef_bug",
        file_path=Path("/tmp/v1_ef_bug.py"),
        kind="factor",
        user_class=_V1DynamicEFBugRepro,
    )
    result = guard.check(ctx)
    assert result.severity == GuardSeverity.BLOCK, (
        f"Golden bug regression: v1 Dynamic EF lookahead not caught. "
        f"Guard result: {result.message}"
    )
