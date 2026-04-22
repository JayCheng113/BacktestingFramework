"""Shared private utilities for the live trading module.

Extracted to eliminate cross-file duplication of identical helper functions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def coerce_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return utc_now()


def get_field(raw: Any, *names: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        for name in names:
            if name in raw:
                return raw[name]
        return default
    for name in names:
        if hasattr(raw, name):
            return getattr(raw, name)
    return default


def qmt_request_failed_immediately(result: Any) -> bool:
    if isinstance(result, bool):
        return not result
    if isinstance(result, (int, float)):
        return result < 0
    return False


def positive_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def fraction_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return min(value, 1.0)
