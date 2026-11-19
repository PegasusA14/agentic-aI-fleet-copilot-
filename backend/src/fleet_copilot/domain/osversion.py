from __future__ import annotations

import re
from typing import Literal

OsVerdict = Literal["older", "not_older", "unknown", "out_of_scope"]

_VERSION = re.compile(r"\d+(\.\d+)*")


def parse_version(text: str) -> tuple[int, ...] | None:
    text = text.strip()
    if not _VERSION.fullmatch(text):
        return None
    return tuple(int(part) for part in text.split("."))


def macos_older_than(os_name: str, version: str, target: str) -> OsVerdict:
    """Compare numerically and only for macOS. Windows strings like '10 22H2' are out of scope."""
    if os_name != "macOS":
        return "out_of_scope"
    limit = parse_version(target)
    if limit is None:
        raise ValueError(f"invalid target version: {target!r}")
    current = parse_version(version)
    if current is None:
        return "unknown"
    width = max(len(current), len(limit))
    pad = lambda parts: parts + (0,) * (width - len(parts))  # noqa: E731
    return "older" if pad(current) < pad(limit) else "not_older"
