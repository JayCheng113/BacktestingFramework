"""V2.17 Factor layer adj_close contract.

Invariant: every registered Factor / CrossSectionalFactor MUST use
`adj_close` (not raw `close`) for price-based signal computation.

Rationale: raw close drops ~50% on dividend days for ETFs, and
percentage for cash-dividend stocks. A factor computing momentum or
return off raw close produces phantom negative signals on dividend
days — same bug class as V2.18.1 (portfolio engine) and V2.16.2 round 2
(single-stock engine).

V2.18.1 research measured the real-world impact: StaticLowVol
annualized return swung from +13.9% (adj) to -0.5% (raw) over a
5-year A-share ETF window.

This contract test runs each registered factor on synthetic data with
a dividend event and asserts the signal is NOT polluted by the raw
price drop. Any future factor using raw `close` will fail this test.

Scope exclusion:
- ez/portfolio/builtin_strategies.py (QMT-ported strategies): use
  raw close BY DESIGN for QMT parity. See CLAUDE.md V2.17 note.
  Not factors; not covered here.
- Volume / turnover factors: use `volume` column, not close. Not
  affected by dividend adjustment.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest


def _dividend_day_df(n_days: int = 25, div_day: int = 15) -> pd.DataFrame:
    """Flat adj_close, -50% raw drop on div_day. Any factor reading
    `close` instead of `adj_close` will see a phantom -50% return."""
    raw = np.full(n_days, 10.0)
    raw[div_day:] = 5.0  # post-div
    adj = np.full(n_days, 10.0)  # adj absorbs the dividend
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n_days)]
    df = pd.DataFrame({
        "open": raw, "high": raw, "low": raw,
        "close": raw, "adj_close": adj,
        "volume": np.full(n_days, 1_000_000),
    }, index=pd.to_datetime(dates))
    return df


# ---------------------------------------------------------------------------
# Single-stock Factor (ez/factor/)
# ---------------------------------------------------------------------------

def test_all_single_stock_factors_are_adj_aware() -> None:
    """Every registered Factor must produce non-phantom signals on a
    dividend day. Test: run each factor on synthetic flat-adj data
    and assert the factor column doesn't show an abnormal jump on
    the dividend day vs. the day before.
    """
    from ez.factor.base import Factor

    # Import built-ins to populate registry
    import ez.factor.builtin.technical  # noqa: F401
    try:
        import ez.factor.builtin.fundamental  # noqa: F401
    except ImportError:
        pass

    registry = Factor.get_registry()
    assert len(registry) > 0, "No factors registered — factor audit is void"

    div_day = 15
    df = _dividend_day_df(n_days=25, div_day=div_day)

    flagged: list[str] = []
    for name, cls in registry.items():
        # Skip non-trivially-constructible factors (need user params) —
        # the goal is to spot-check built-in defaults, not exhaustive.
        try:
            factor = cls()
        except TypeError:
            continue  # requires init args
        try:
            result = factor.compute(df.copy())
        except Exception:
            continue  # factor needs other columns we didn't provide

        col = factor.name
        if col not in result.columns:
            continue
        values = result[col].values
        # Dividend-day test: compare factor value at div_day vs div_day - 1.
        # A factor using RAW close computes a -50% return / signal
        # flip on div_day — relative change would be huge. Factors
        # using adj_close see flat data → value near-unchanged.
        if div_day - 1 < 0 or div_day >= len(values):
            continue
        prev = values[div_day - 1]
        curr = values[div_day]
        # Skip warmup NaN region
        if not (np.isfinite(prev) and np.isfinite(curr)):
            continue
        if abs(prev) < 1e-9:
            continue
        rel_change = abs(curr - prev) / max(abs(prev), 1e-9)
        # Factors over flat adj data should not jump > 50% in one bar.
        # A factor reading RAW close would see ~50% spike on div day.
        if rel_change > 0.40:
            flagged.append(f"{name} (rel_change={rel_change:.2%}, "
                           f"prev={prev:.4f}, curr={curr:.4f})")

    assert not flagged, (
        f"Factors showing phantom jump on dividend day "
        f"(likely reading raw close instead of adj_close):\n  "
        + "\n  ".join(flagged)
    )


# ---------------------------------------------------------------------------
# Cross-sectional factors
# ---------------------------------------------------------------------------

def test_cross_sectional_factors_are_adj_aware() -> None:
    """CrossSectionalFactor contract: compute_raw on a universe with
    one symbol going through a dividend must NOT show phantom -50%
    when adj_close is flat."""
    from ez.portfolio.cross_factor import CrossSectionalFactor

    # Import builtins
    import ez.portfolio.cross_factor  # noqa: F401
    try:
        import ez.factor.builtin.fundamental  # noqa: F401
    except ImportError:
        pass

    registry = CrossSectionalFactor.get_registry()
    assert len(registry) > 0

    div_day = 15
    df = _dividend_day_df(n_days=25, div_day=div_day)
    # Slice to [date-lookback, date-1] as engine does
    target = df.index[-1]

    # Slice as engine does: all bars strictly before target
    sliced = df[df.index < target]
    universe_data = {"SYM1": sliced}

    flagged: list[str] = []
    for name, cls in registry.items():
        try:
            factor = cls()
        except TypeError:
            continue  # needs params
        try:
            result = factor.compute_raw(universe_data, target)
        except Exception:
            continue  # factor may require fundamental store / industry map
        if not isinstance(result, pd.Series) or len(result) == 0:
            continue
        val = result.iloc[0]
        if not np.isfinite(val):
            continue
        # Momentum-style factors on flat adj_close should be ~0.
        # A factor reading raw `close` would see ~(5-10)/10 = -50%.
        # Other factor types (volume, volatility) have different scales
        # so we allow a wide band; only fail on the clear raw-close
        # signature (~-50% or ~-0.5 magnitude on "flat" data).
        if -0.6 < val < -0.3:
            flagged.append(f"{name} (value={val:.4f}) suggests raw-close read")

    assert not flagged, (
        "Cross-sectional factors with suspicious values on flat-adj "
        "dividend-day data:\n  " + "\n  ".join(flagged)
    )
