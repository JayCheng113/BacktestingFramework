"""Mock data provider for testing. Reads local CSV, zero network calls."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ez.data.provider import DataProvider
from ez.types import Bar


class MockDataProvider(DataProvider):
    """Reads from tests/fixtures/sample_kline.csv."""

    def __init__(self, csv_path: str = "tests/fixtures/sample_kline.csv"):
        self._df = pd.read_csv(csv_path, parse_dates=["time"])

    @property
    def name(self) -> str:
        return "mock"

    def get_kline(
        self, symbol: str, market: str, period: str,
        start_date: date, end_date: date,
    ) -> list[Bar]:
        df = self._df
        df_filtered = df[
            (df["symbol"] == symbol)
            & (df["market"] == market)
            & (df["time"].dt.date >= start_date)
            & (df["time"].dt.date <= end_date)
        ].sort_values("time")

        return [
            Bar(
                time=row["time"].to_pydatetime(),
                symbol=row["symbol"], market=row["market"],
                open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], adj_close=row["adj_close"],
                volume=int(row["volume"]),
            )
            for _, row in df_filtered.iterrows()
        ]

    def search_symbols(self, keyword: str, market: str = "") -> list[dict]:
        symbols = self._df["symbol"].unique()
        return [{"symbol": s, "name": f"Mock {s}"} for s in symbols if keyword.upper() in s]
