"""Factor abstract base class.

[CORE] — interface frozen after V1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Factor(ABC):
    """Base class for all factors (technical indicators, alpha factors, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique factor name (e.g., 'ma_20')."""
        ...

    @property
    @abstractmethod
    def warmup_period(self) -> int:
        """Minimum historical bars needed before producing valid values."""
        ...

    @abstractmethod
    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """Compute factor and return DataFrame with new column(s) added.

        Input: DataFrame with at minimum 'adj_close' column.
        Output: Same DataFrame with factor column(s) appended.
        First `warmup_period` rows may have NaN for the new column(s).
        """
        ...
