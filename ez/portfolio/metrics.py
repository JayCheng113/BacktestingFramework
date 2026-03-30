"""V2.9 P6: Portfolio metrics + resample utility."""
from __future__ import annotations

import pandas as pd


def resample(df: pd.DataFrame, period: str = "W") -> pd.DataFrame:
    """Resample daily OHLCV to weekly/monthly/quarterly.

    Args:
        df: DataFrame with OHLCV columns and date index.
        period: 'W' (weekly), 'M' (monthly), 'Q' (quarterly).

    Returns:
        Resampled DataFrame with proper OHLC aggregation.
    """
    agg = {}
    if "open" in df.columns:
        agg["open"] = "first"
    if "high" in df.columns:
        agg["high"] = "max"
    if "low" in df.columns:
        agg["low"] = "min"
    if "close" in df.columns:
        agg["close"] = "last"
    if "adj_close" in df.columns:
        agg["adj_close"] = "last"
    if "volume" in df.columns:
        agg["volume"] = "sum"

    if not agg:
        return df

    return df.resample(period).agg(agg).dropna(how="all")
