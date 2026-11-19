from __future__ import annotations


def free_pct(size_bytes: int, available_bytes: int) -> float:
    if size_bytes <= 0:
        raise ValueError("size_bytes must be positive")
    return available_bytes / size_bytes * 100.0


def used_pct(total_bytes: int, used_bytes: int) -> float:
    if total_bytes <= 0:
        raise ValueError("total_bytes must be positive")
    return used_bytes / total_bytes * 100.0
