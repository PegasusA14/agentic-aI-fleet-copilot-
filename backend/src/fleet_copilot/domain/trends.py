from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

MIN_SNAPSHOTS = 3

TrendClass = Literal[
    "insufficient", "none", "sustained", "recovering", "onset", "isolated", "intermittent"
]
ComplianceKind = Literal["passing", "persistent_failure", "transition", "recovered", "insufficient"]


def _trailing_run(flags: Sequence[bool]) -> int:
    run = 0
    for flag in reversed(flags):
        if not flag:
            break
        run += 1
    return run


def classify_breach_trend(breaches: Sequence[bool]) -> TrendClass:
    """Classify chronological breach flags (latest last). Integer math avoids float edge cases."""
    n = len(breaches)
    if n < MIN_SNAPSHOTS:
        return "insufficient"
    hits = sum(1 for b in breaches if b)
    if hits == 0:
        return "none"
    if hits * 5 >= n * 4:  # at least 80%
        return "sustained" if breaches[-1] else "recovering"
    run = _trailing_run(breaches)
    if breaches[-1] and hits == run and run >= MIN_SNAPSHOTS:
        return "onset"
    if hits * 5 < n:  # under 20%
        return "isolated"
    return "intermittent"


@dataclass(frozen=True)
class ComplianceTrend:
    kind: ComplianceKind
    change_index: int | None  # where the latest unbroken run begins; None for steady series


def classify_compliance(statuses: Sequence[str]) -> ComplianceTrend:
    """Distinguish a persistent failure from a transition. Statuses are chronological."""
    n = len(statuses)
    if n == 0:
        return ComplianceTrend("insufficient", None)
    latest = statuses[-1]
    run_start = n - 1
    while run_start > 0 and statuses[run_start - 1] == latest:
        run_start -= 1
    if run_start == 0:
        if n < MIN_SNAPSHOTS:
            return ComplianceTrend("insufficient", None)
        return ComplianceTrend("persistent_failure" if latest == "fail" else "passing", None)
    return ComplianceTrend("transition" if latest == "fail" else "recovered", run_start)
