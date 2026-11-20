from __future__ import annotations

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def fmt_bytes(n: int) -> str:
    """Binary units with one decimal; whole bytes below 1 KiB."""
    value, unit = float(n), 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    return f"{int(n)} B" if unit == 0 else f"{value:.1f} {_UNITS[unit]}"


def fmt_pct(x: float) -> str:
    return f"{x:.1f}%"


def fmt_signed_pct(x: float) -> str:
    return f"{x:+.1f}%"


def fmt_ts(iso: str) -> str:
    """`2026-06-12T09:02:00Z` -> `2026-06-12 09:02Z`."""
    return f"{iso[:10]} {iso[11:16]}Z"


def fmt_day(iso: str) -> str:
    return iso[:10]


def noun(n: int, word: str) -> str:
    return word if n == 1 else f"{word}s"
