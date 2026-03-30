"""Tests for Universe (V2.9 P1) + slice_universe_data."""
from datetime import date

import pandas as pd
import pytest

from ez.portfolio.universe import Universe, slice_universe_data


class TestUniverse:
    def test_basic(self):
        u = Universe(["A", "B", "C"])
        assert len(u) == 3
        assert u.tradeable_at(date(2024, 1, 1)) == ["A", "B", "C"]

    def test_dedup(self):
        u = Universe(["A", "A", "B"])
        assert len(u) == 2

    def test_delist_filter(self):
        u = Universe(["A", "B"], delist_dates={"A": date(2024, 6, 1)})
        assert u.tradeable_at(date(2024, 5, 31)) == ["A", "B"]
        assert u.tradeable_at(date(2024, 6, 2)) == ["B"]  # A delisted

    def test_ipo_filter(self):
        u = Universe(["A", "B"], ipo_dates={"B": date(2024, 3, 1)}, ipo_min_days=60)
        assert u.tradeable_at(date(2024, 4, 1)) == ["A"]  # B too new (31 days)
        assert u.tradeable_at(date(2024, 5, 1)) == ["A", "B"]  # B old enough (61 days)

    def test_combined_filters(self):
        u = Universe(
            ["A", "B", "C"],
            delist_dates={"A": date(2024, 6, 1)},
            ipo_dates={"C": date(2024, 5, 1)},
            ipo_min_days=30,
        )
        # June 15: A delisted, C is 45 days old (OK)
        assert set(u.tradeable_at(date(2024, 6, 15))) == {"B", "C"}


class TestSliceUniverseData:
    def test_slices_before_date(self):
        dates = pd.date_range("2024-01-01", "2024-01-31", freq="B")
        df = pd.DataFrame({"close": range(len(dates))}, index=dates)
        data = {"A": df}

        sliced = slice_universe_data(data, date(2024, 1, 15), lookback_days=5)
        assert "A" in sliced
        assert sliced["A"].index[-1].date() < date(2024, 1, 15)

    def test_respects_lookback(self):
        dates = pd.date_range("2024-01-01", "2024-03-31", freq="B")
        df = pd.DataFrame({"close": range(len(dates))}, index=dates)
        data = {"A": df}

        sliced = slice_universe_data(data, date(2024, 3, 15), lookback_days=10)
        assert len(sliced["A"]) == 10

    def test_empty_for_future_date(self):
        dates = pd.date_range("2024-01-01", "2024-01-10", freq="B")
        df = pd.DataFrame({"close": range(len(dates))}, index=dates)
        data = {"A": df}

        sliced = slice_universe_data(data, date(2024, 1, 1), lookback_days=5)
        assert "A" not in sliced  # no data before Jan 1
