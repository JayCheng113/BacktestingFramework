"""Unit tests for mock_data: deterministic fixtures used by all guards."""
from __future__ import annotations
import pandas as pd

from ez.testing.guards.mock_data import (
    build_mock_panel, build_shuffled_panel, target_date_at,
    MOCK_N_DAYS, MOCK_SYMBOLS,
)


def test_mock_panel_has_expected_shape():
    panel = build_mock_panel()
    assert set(panel.keys()) == set(MOCK_SYMBOLS)
    for sym, df in panel.items():
        assert len(df) == MOCK_N_DAYS
        assert set(df.columns) == {"open", "high", "low", "close", "adj_close", "volume"}
        assert isinstance(df.index, pd.DatetimeIndex)


def test_mock_panel_is_deterministic():
    """Two calls return identical data (cached via lru_cache)."""
    a = build_mock_panel()
    b = build_mock_panel()
    for sym in MOCK_SYMBOLS:
        pd.testing.assert_frame_equal(a[sym], b[sym])


def test_shuffled_panel_preserves_rows_at_and_before_cutoff():
    panel = build_mock_panel()
    shuffled = build_shuffled_panel(cutoff_idx=150)
    for sym in MOCK_SYMBOLS:
        # Rows 0..150 (inclusive) must be byte-identical.
        a = panel[sym].iloc[:151]
        b = shuffled[sym].iloc[:151]
        pd.testing.assert_frame_equal(a, b)


def test_shuffled_panel_changes_rows_after_cutoff():
    panel = build_mock_panel()
    shuffled = build_shuffled_panel(cutoff_idx=150)
    any_diff = False
    for sym in MOCK_SYMBOLS:
        a = panel[sym].iloc[151:].values
        b = shuffled[sym].iloc[151:].values
        if not (a == b).all():
            any_diff = True
            break
    assert any_diff, "Shuffled panel has identical post-cutoff rows (bad RNG seed?)"


def test_target_date_at_returns_expected_date():
    d0 = target_date_at(0)
    d150 = target_date_at(150)
    assert d0 < d150
    assert d0.year == 2024
