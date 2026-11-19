from __future__ import annotations

from datetime import datetime, timedelta

STALE_AFTER = timedelta(hours=48)


def window_start(as_of: datetime, days: int) -> datetime:
    """Inclusive start of a trailing window that ends at the pinned as-of."""
    if days <= 0:
        raise ValueError("window must be at least one day")
    return as_of - timedelta(days=days)


def is_stale(collected_at: datetime, as_of: datetime) -> bool:
    return as_of - collected_at > STALE_AFTER
