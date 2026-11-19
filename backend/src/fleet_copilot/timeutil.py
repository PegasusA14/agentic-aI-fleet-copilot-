from __future__ import annotations

from datetime import UTC, datetime

_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_ts(value: str) -> datetime:
    """Parse the dataset's `YYYY-MM-DDTHH:MM:SSZ` timestamps into aware UTC datetimes."""
    return datetime.strptime(value, _FORMAT).replace(tzinfo=UTC)


def iso_z(value: datetime) -> str:
    """Format an aware datetime as a UTC `...Z` string (lexicographically sortable).

    A naive datetime is refused: Python would read it in the machine's local zone, so the
    same value would format differently on different machines.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).strftime(_FORMAT)
